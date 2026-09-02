from __future__ import annotations

import logging
import os
import signal
import threading
from pathlib import Path

from chief.core.config import Settings
from chief.runtime.supervisor import build_runtime_supervisor

logger = logging.getLogger("chief.runtime")


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def main() -> int:
    logging.basicConfig(
        level=os.getenv("CHIEF_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    stop_event = threading.Event()

    def stop(_signum=None, _frame=None) -> None:
        logger.info("runtime_stop_requested")
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)

    if not settings.execution_enabled:
        logger.warning("CHIEF execution is disabled; runtime worker will not start.")
        return 2

    database_path = Path(os.getenv("CHIEF_DATABASE_PATH", "data/chief.db"))
    supervisor = build_runtime_supervisor(
        database_path=database_path,
        worker_id=os.getenv("CHIEF_RUNTIME_WORKER_ID", "chief-runtime").strip() or "chief-runtime",
        min_free_disk_bytes=_int_env(
            "CHIEF_RUNTIME_MIN_FREE_DISK_BYTES",
            512 * 1024 * 1024,
            minimum=0,
            maximum=10 * 1024**4,
        ),
    )
    interval_ms = _int_env(
        "CHIEF_RUNTIME_INTERVAL_MS",
        2000,
        minimum=100,
        maximum=3_600_000,
    )
    logger.info(
        "runtime_started",
        extra={"database_path": str(database_path), "interval_ms": interval_ms},
    )
    supervisor.run_forever(
        stop_event=stop_event,
        interval_seconds=interval_ms / 1000,
    )
    logger.info("runtime_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
