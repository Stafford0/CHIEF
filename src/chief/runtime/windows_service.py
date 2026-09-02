from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from chief.core.config import Settings
from chief.runtime.supervisor import build_runtime_supervisor

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
except ImportError:  # pragma: no cover - exercised only on Windows with optional dependency
    servicemanager = None
    win32event = None
    win32service = None
    win32serviceutil = None


def _build():
    settings = Settings.from_env()
    if not settings.execution_enabled:
        raise RuntimeError("CHIEF execution is disabled by CHIEF_EXECUTION_ENABLED")
    database_path = Path(os.getenv("CHIEF_DATABASE_PATH", "data/chief.db"))
    minimum = int(os.getenv("CHIEF_RUNTIME_MIN_FREE_DISK_BYTES", str(512 * 1024 * 1024)))
    return build_runtime_supervisor(
        database_path=database_path,
        worker_id="chief-windows-service",
        min_free_disk_bytes=minimum,
    )


if win32serviceutil is not None:

    class ChiefRuntimeService(win32serviceutil.ServiceFramework):
        _svc_name_ = "CHIEFRuntime"
        _svc_display_name_ = "CHIEF Runtime"
        _svc_description_ = (
            "Advances CHIEF durable schedules, events, and verified run steps continuously."
        )

        def __init__(self, args: Any) -> None:
            super().__init__(args)
            self._stop = threading.Event()
            self._stop_handle = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._stop.set()
            win32event.SetEvent(self._stop_handle)

        def SvcDoRun(self) -> None:
            servicemanager.LogInfoMsg("CHIEF Runtime service starting")
            try:
                interval_ms = int(os.getenv("CHIEF_RUNTIME_INTERVAL_MS", "2000"))
                _build().run_forever(
                    stop_event=self._stop,
                    interval_seconds=max(0.1, interval_ms / 1000),
                )
            except Exception as exc:
                servicemanager.LogErrorMsg(f"CHIEF Runtime failed: {exc}")
                raise
            finally:
                servicemanager.LogInfoMsg("CHIEF Runtime service stopped")

else:

    class ChiefRuntimeService:  # pragma: no cover - import placeholder on non-Windows CI
        pass


def main() -> int:
    if win32serviceutil is None:
        raise RuntimeError(
            "The Windows Service wrapper requires the optional 'windows' dependency (pywin32)."
        )
    win32serviceutil.HandleCommandLine(ChiefRuntimeService)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
