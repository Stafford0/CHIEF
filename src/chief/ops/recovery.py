from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BackupManifest:
    source: str
    backup: str
    created_at: datetime
    sha256: str
    bytes: int
    sqlite_integrity: str

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "backup": self.backup,
            "created_at": self.created_at.isoformat(),
            "sha256": self.sha256,
            "bytes": self.bytes,
            "sqlite_integrity": self.sqlite_integrity,
        }


class SQLiteRecoveryService:
    """Create verified online SQLite backups and stage atomic restores."""

    def __init__(self, database_path: str | Path = "data/chief.db") -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _integrity(path: Path) -> str:
        if not path.is_file():
            raise FileNotFoundError(path)
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            row = connection.execute("PRAGMA quick_check(1)").fetchone()
        finally:
            connection.close()
        result = str(row[0]) if row else "missing_result"
        if result.casefold() != "ok":
            raise RuntimeError(f"SQLite integrity check failed for {path}: {result}")
        return result

    def create_backup(self, backup_path: str | Path) -> BackupManifest:
        backup_path = Path(backup_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if backup_path.resolve() == self.database_path.resolve():
            raise ValueError("Backup destination cannot be the live database.")
        temporary = backup_path.with_suffix(backup_path.suffix + ".partial")
        temporary.unlink(missing_ok=True)
        source = sqlite3.connect(self.database_path, timeout=5)
        destination = sqlite3.connect(temporary, timeout=5)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        integrity = self._integrity(temporary)
        digest = self._digest(temporary)
        size = temporary.stat().st_size
        os.replace(temporary, backup_path)
        manifest = BackupManifest(
            source=str(self.database_path.resolve()),
            backup=str(backup_path.resolve()),
            created_at=datetime.now(UTC),
            sha256=digest,
            bytes=size,
            sqlite_integrity=integrity,
        )
        manifest_path = backup_path.with_suffix(backup_path.suffix + ".manifest.json")
        manifest_path.write_text(json.dumps(manifest.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return manifest

    def verify_backup(self, backup_path: str | Path) -> BackupManifest:
        backup_path = Path(backup_path)
        integrity = self._integrity(backup_path)
        manifest_path = backup_path.with_suffix(backup_path.suffix + ".manifest.json")
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Backup manifest is missing: {manifest_path}")
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_digest = str(raw.get("sha256") or "")
        digest = self._digest(backup_path)
        if expected_digest != digest:
            raise RuntimeError("Backup SHA-256 does not match its manifest.")
        return BackupManifest(
            source=str(raw["source"]),
            backup=str(backup_path.resolve()),
            created_at=datetime.fromisoformat(str(raw["created_at"])),
            sha256=digest,
            bytes=backup_path.stat().st_size,
            sqlite_integrity=integrity,
        )

    def restore_backup(self, backup_path: str | Path, *, preserve_current: bool = True) -> Path | None:
        backup_path = Path(backup_path)
        self.verify_backup(backup_path)
        staged = self.database_path.with_suffix(self.database_path.suffix + ".restore-staged")
        staged.unlink(missing_ok=True)
        shutil.copy2(backup_path, staged)
        self._integrity(staged)
        previous: Path | None = None
        if self.database_path.exists() and preserve_current:
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            previous = self.database_path.with_suffix(self.database_path.suffix + f".pre-restore-{timestamp}")
            shutil.copy2(self.database_path, previous)
            self._integrity(previous)
        os.replace(staged, self.database_path)
        self._integrity(self.database_path)
        return previous
