from __future__ import annotations

import argparse
import json
from pathlib import Path

from chief.ops.recovery import SQLiteRecoveryService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chief-recovery",
        description="Create, verify, stage, and offline-activate CHIEF SQLite backups.",
    )
    parser.add_argument("--database", default="data/chief.db")
    subcommands = parser.add_subparsers(dest="command", required=True)

    backup = subcommands.add_parser("backup")
    backup.add_argument("path")

    verify = subcommands.add_parser("verify")
    verify.add_argument("path")

    stage = subcommands.add_parser("stage-restore")
    stage.add_argument("path")

    subcommands.add_parser("verify-stage")

    activate = subcommands.add_parser("activate-restore")
    activate.add_argument("--lock-timeout", type=float, default=0.25)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    service = SQLiteRecoveryService(Path(args.database))
    if args.command == "backup":
        payload = service.create_backup(args.path).as_dict()
    elif args.command == "verify":
        payload = service.verify_backup(args.path).as_dict()
    elif args.command == "stage-restore":
        payload = service.stage_restore(args.path).as_dict()
    elif args.command == "verify-stage":
        payload = service.verify_staged_restore().as_dict()
    else:
        previous = service.activate_staged_restore(lock_timeout_seconds=args.lock_timeout)
        payload = {
            "activated": True,
            "database": str(service.database_path.resolve()),
            "preserved_previous": str(previous.resolve()) if previous is not None else None,
        }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
