from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from chief.ops.recovery import SQLiteRecoveryService


def _write_value(path: Path, value: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS state(value TEXT NOT NULL)")
        connection.execute("DELETE FROM state")
        connection.execute("INSERT INTO state(value) VALUES (?)", (value,))


def _read_value(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT value FROM state").fetchone()
    assert row is not None
    return str(row[0])


def test_online_backup_has_manifest_digest_and_integrity(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    backup = tmp_path / "backups" / "chief-1.db"
    _write_value(database, "alpha")
    service = SQLiteRecoveryService(database)

    manifest = service.create_backup(backup)
    verified = service.verify_backup(backup)

    assert backup.is_file()
    assert manifest.sha256 == verified.sha256
    assert verified.sqlite_integrity == "ok"
    assert verified.bytes == backup.stat().st_size
    marker = json.loads(
        backup.with_suffix(".db.manifest.json").read_text(encoding="utf-8")
    )
    assert marker["sha256"] == verified.sha256
    assert _read_value(backup) == "alpha"


def test_backup_digest_tampering_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    backup = tmp_path / "chief-backup.db"
    _write_value(database, "alpha")
    service = SQLiteRecoveryService(database)
    service.create_backup(backup)

    manifest_path = backup.with_suffix(".db.manifest.json")
    marker = json.loads(manifest_path.read_text(encoding="utf-8"))
    marker["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(RuntimeError, match="SHA-256"):
        service.verify_backup(backup)


def test_restore_is_staged_without_touching_live_database(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    backup = tmp_path / "chief-backup.db"
    _write_value(database, "backup-state")
    service = SQLiteRecoveryService(database)
    service.create_backup(backup)
    _write_value(database, "live-state")

    staged = service.stage_restore(backup)

    assert _read_value(database) == "live-state"
    assert _read_value(Path(staged.staged)) == "backup-state"
    assert service.verify_staged_restore().staged_sha256 == staged.staged_sha256


def test_offline_activation_preserves_current_and_activates_stage(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    backup = tmp_path / "chief-backup.db"
    _write_value(database, "backup-state")
    service = SQLiteRecoveryService(database)
    service.create_backup(backup)
    _write_value(database, "live-state")
    service.stage_restore(backup)

    previous = service.activate_staged_restore()

    assert previous is not None and previous.is_file()
    assert _read_value(previous) == "live-state"
    assert _read_value(database) == "backup-state"
    assert not database.with_suffix(".db.restore-ready").exists()


def test_activation_refuses_busy_live_database(tmp_path: Path) -> None:
    database = tmp_path / "chief.db"
    backup = tmp_path / "chief-backup.db"
    _write_value(database, "backup-state")
    service = SQLiteRecoveryService(database)
    service.create_backup(backup)
    _write_value(database, "live-state")
    service.stage_restore(backup)

    holder = sqlite3.connect(database, timeout=0.1)
    try:
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError, match="busy"):
            service.activate_staged_restore(lock_timeout_seconds=0.01)
    finally:
        holder.rollback()
        holder.close()
    assert _read_value(database) == "live-state"
