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


@dataclass(frozen=True, slots=True)
class StagedRestore:
    database: str
    staged: str
    source_backup: str
    staged_sha256: str
    created_at: datetime

    def as_dict(self) -> dict[str, str]:
        return {
            "database": self.database,
            "staged": self.staged,
            "source_backup": self.source_backup,
            "staged_sha256": self.staged_sha256,
            "created_at": self.created_at.isoformat(),
        }


class SQLiteRecoveryService:
    """Create verified online backups and stage restores for offline activation."""

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
        manifest_path.write_text(
            json.dumps(manifest.as_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
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

    def stage_restore(self, backup_path: str | Path) -> StagedRestore:
        """Verify and copy a backup beside the live DB without activating it."""

        backup_path = Path(backup_path)
        self.verify_backup(backup_path)
        staged = self.database_path.with_suffix(self.database_path.suffix + ".restore-ready")
        staged_partial = staged.with_suffix(staged.suffix + ".partial")
        marker = staged.with_suffix(staged.suffix + ".json")
        staged_partial.unlink(missing_ok=True)
        shutil.copy2(backup_path, staged_partial)
        self._integrity(staged_partial)
        digest = self._digest(staged_partial)
        os.replace(staged_partial, staged)
        restore = StagedRestore(
            database=str(self.database_path.resolve()),
            staged=str(staged.resolve()),
            source_backup=str(backup_path.resolve()),
            staged_sha256=digest,
            created_at=datetime.now(UTC),
        )
        marker.write_text(
            json.dumps(restore.as_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return restore

    def verify_staged_restore(self) -> StagedRestore:
        staged = self.database_path.with_suffix(self.database_path.suffix + ".restore-ready")
        marker = staged.with_suffix(staged.suffix + ".json")
        if not marker.is_file():
            raise FileNotFoundError(f"Staged restore marker is missing: {marker}")
        raw = json.loads(marker.read_text(encoding="utf-8"))
        self._integrity(staged)
        digest = self._digest(staged)
        if digest != str(raw.get("staged_sha256") or ""):
            raise RuntimeError("Staged restore SHA-256 does not match its marker.")
        return StagedRestore(
            database=str(raw["database"]),
            staged=str(staged.resolve()),
            source_backup=str(raw["source_backup"]),
            staged_sha256=digest,
            created_at=datetime.fromisoformat(str(raw["created_at"])),
        )

    def activate_staged_restore(self, *, lock_timeout_seconds: float = 0.25) -> Path | None:
        """Activate a verified stage only when the live database can be locked exclusively."""

        if lock_timeout_seconds < 0:
            raise ValueError("lock_timeout_seconds cannot be negative")
        restore = self.verify_staged_restore()
        staged = Path(restore.staged)
        previous: Path | None = None

        if self.database_path.exists():
            connection = sqlite3.connect(self.database_path, timeout=lock_timeout_seconds)
            try:
                connection.execute(f"PRAGMA busy_timeout = {int(lock_timeout_seconds * 1000)}")
                connection.execute("BEGIN EXCLUSIVE")
                connection.rollback()
            except sqlite3.OperationalError as exc:
                raise RuntimeError(
                    "Live CHIEF database is busy. Stop API/runtime processes before restore activation."
                ) from exc
            finally:
                connection.close()
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            previous = self.database_path.with_suffix(
                self.database_path.suffix + f".pre-restore-{timestamp}"
            )
            shutil.copy2(self.database_path, previous)
            self._integrity(previous)

        self._integrity(staged)
        if self._digest(staged) != restore.staged_sha256:
            raise RuntimeError("Staged restore changed after verification.")
        os.replace(staged, self.database_path)
        self._integrity(self.database_path)
        marker = staged.with_suffix(staged.suffix + ".json")
        marker.unlink(missing_ok=True)
        return previous
