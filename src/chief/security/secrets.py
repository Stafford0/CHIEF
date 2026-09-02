from __future__ import annotations

import ctypes
import os
import platform
import re
import sqlite3
from ctypes import wintypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

_SECRET_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_MAX_SECRET_BYTES = 1_048_576


class SecretCipher(Protocol):
    def encrypt(self, plaintext: bytes) -> bytes: ...

    def decrypt(self, ciphertext: bytes) -> bytes: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return (
        _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))),
        buffer,
    )


class WindowsDpapiCipher:
    """Encrypt secrets to the current Windows user profile with DPAPI."""

    def __init__(self) -> None:
        if platform.system() != "Windows":
            raise RuntimeError("Windows DPAPI is available only on Windows.")
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32

    def _transform(self, data: bytes, *, protect: bool) -> bytes:
        input_blob, _buffer = _blob(data)
        output_blob = _DataBlob()
        if protect:
            ok = self._crypt32.CryptProtectData(
                ctypes.byref(input_blob),
                "CHIEF secret",
                None,
                None,
                None,
                0,
                ctypes.byref(output_blob),
            )
        else:
            description = ctypes.c_wchar_p()
            ok = self._crypt32.CryptUnprotectData(
                ctypes.byref(input_blob),
                ctypes.byref(description),
                None,
                None,
                None,
                0,
                ctypes.byref(output_blob),
            )
        if not ok:
            raise OSError(ctypes.get_last_error(), "Windows DPAPI operation failed")
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            self._kernel32.LocalFree(output_blob.pbData)

    def encrypt(self, plaintext: bytes) -> bytes:
        return self._transform(plaintext, protect=True)

    def decrypt(self, ciphertext: bytes) -> bytes:
        return self._transform(ciphertext, protect=False)


@dataclass(frozen=True, slots=True)
class SecretMetadata:
    name: str
    created_at: datetime
    updated_at: datetime
    cipher: str


class EncryptedSecretStore:
    """SQLite metadata + ciphertext store. Plaintext is never persisted."""

    def __init__(
        self,
        database_path: str | Path = "data/chief.db",
        *,
        cipher: SecretCipher | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.cipher = cipher or WindowsDpapiCipher()
        self.cipher_name = self.cipher.__class__.__name__
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS encrypted_secrets (
                    name TEXT PRIMARY KEY,
                    ciphertext BLOB NOT NULL,
                    cipher TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _name(name: str) -> str:
        value = name.strip()
        if _SECRET_NAME.fullmatch(value) is None:
            raise ValueError("Secret name must be 1-128 letters, digits, dots, dashes, or underscores.")
        return value

    @staticmethod
    def _plaintext(value: str) -> bytes:
        if not isinstance(value, str) or not value:
            raise ValueError("Secret value cannot be empty.")
        encoded = value.encode("utf-8")
        if len(encoded) > _MAX_SECRET_BYTES:
            raise ValueError("Secret value exceeds the 1 MiB limit.")
        return encoded

    def put(self, name: str, value: str) -> SecretMetadata:
        name = self._name(name)
        ciphertext = self.cipher.encrypt(self._plaintext(value))
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO encrypted_secrets(name, ciphertext, cipher, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    ciphertext=excluded.ciphertext,
                    cipher=excluded.cipher,
                    updated_at=excluded.updated_at
                """,
                (name, sqlite3.Binary(ciphertext), self.cipher_name, now, now),
            )
        metadata = self.metadata(name)
        assert metadata is not None
        return metadata

    def get(self, name: str) -> str | None:
        name = self._name(name)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT ciphertext, cipher FROM encrypted_secrets WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            return None
        if row["cipher"] != self.cipher_name:
            raise RuntimeError(
                f"Secret '{name}' was encrypted with {row['cipher']}, not {self.cipher_name}."
            )
        try:
            return self.cipher.decrypt(bytes(row["ciphertext"])).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"Secret '{name}' decrypted to invalid UTF-8.") from exc

    def delete(self, name: str) -> bool:
        name = self._name(name)
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM encrypted_secrets WHERE name = ?", (name,))
        return cursor.rowcount > 0

    def metadata(self, name: str) -> SecretMetadata | None:
        name = self._name(name)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT name, cipher, created_at, updated_at FROM encrypted_secrets WHERE name = ?",
                (name,),
            ).fetchone()
        return self._metadata(row) if row is not None else None

    def list_metadata(self) -> list[SecretMetadata]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT name, cipher, created_at, updated_at FROM encrypted_secrets ORDER BY name"
            ).fetchall()
        return [self._metadata(row) for row in rows]

    @staticmethod
    def _metadata(row: sqlite3.Row) -> SecretMetadata:
        return SecretMetadata(
            name=row["name"],
            cipher=row["cipher"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


class SecretResolver:
    """Resolve vault values first, then temporary environment migration fallbacks."""

    def __init__(
        self,
        store: EncryptedSecretStore | None,
        *,
        allow_environment_fallback: bool = True,
    ) -> None:
        self.store = store
        self.allow_environment_fallback = allow_environment_fallback

    def get(self, name: str) -> str | None:
        if self.store is not None:
            value = self.store.get(name)
            if value is not None:
                return value
        if self.allow_environment_fallback:
            return os.getenv(name, "").strip() or None
        return None


def build_secret_store(
    database_path: str | Path = "data/chief.db",
    *,
    cipher: SecretCipher | None = None,
) -> EncryptedSecretStore:
    """Build production DPAPI storage or an explicitly injected test/platform cipher."""

    return EncryptedSecretStore(database_path, cipher=cipher)


def metadata_json(metadata: SecretMetadata) -> dict[str, str]:
    return {
        "name": metadata.name,
        "cipher": metadata.cipher,
        "created_at": metadata.created_at.isoformat(),
        "updated_at": metadata.updated_at.isoformat(),
    }
