from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class WindowsServiceConfiguration:
    home: Path
    database_path: Path
    playwright_browsers_path: Path
    api_host: str
    api_port: int
    interval_ms: int
    min_free_disk_bytes: int


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _service_home() -> Path:
    configured = os.getenv("CHIEF_HOME", "").strip()
    if configured:
        home = Path(configured).expanduser().resolve()
    else:
        home = Path(__file__).resolve().parents[3]
    if not (home / "pyproject.toml").is_file():
        raise RuntimeError(
            "CHIEF_HOME must identify the CHIEF checkout containing pyproject.toml."
        )
    return home


def load_service_configuration() -> WindowsServiceConfiguration:
    home = _service_home()
    database_path = Path(os.getenv("CHIEF_DATABASE_PATH", "data/chief.db"))
    if not database_path.is_absolute():
        database_path = home / database_path
    browsers_path = Path(
        os.getenv("PLAYWRIGHT_BROWSERS_PATH", str(home / "work" / "ms-playwright"))
    )
    if not browsers_path.is_absolute():
        browsers_path = home / browsers_path
    api_host = os.getenv("CHIEF_SERVICE_API_HOST", "127.0.0.1").strip()
    if api_host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("CHIEF_SERVICE_API_HOST must remain loopback-only")
    return WindowsServiceConfiguration(
        home=home,
        database_path=database_path.resolve(),
        playwright_browsers_path=browsers_path.resolve(),
        api_host=api_host,
        api_port=_bounded_int("CHIEF_SERVICE_API_PORT", 8000, minimum=1, maximum=65535),
        interval_ms=_bounded_int(
            "CHIEF_RUNTIME_INTERVAL_MS", 2000, minimum=100, maximum=3_600_000
        ),
        min_free_disk_bytes=_bounded_int(
            "CHIEF_RUNTIME_MIN_FREE_DISK_BYTES",
            512 * 1024 * 1024,
            minimum=0,
            maximum=10 * 1024**4,
        ),
    )


def apply_service_configuration(configuration: WindowsServiceConfiguration) -> None:
    configuration.database_path.parent.mkdir(parents=True, exist_ok=True)
    configuration.playwright_browsers_path.mkdir(parents=True, exist_ok=True)
    os.chdir(configuration.home)
    os.environ["CHIEF_HOME"] = str(configuration.home)
    os.environ["CHIEF_DATABASE_PATH"] = str(configuration.database_path)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(configuration.playwright_browsers_path)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


def _build(configuration: WindowsServiceConfiguration):
    settings = Settings.from_env()
    if not settings.execution_enabled:
        raise RuntimeError("CHIEF execution is disabled by CHIEF_EXECUTION_ENABLED")
    return build_runtime_supervisor(
        database_path=configuration.database_path,
        worker_id="chief-windows-service",
        min_free_disk_bytes=configuration.min_free_disk_bytes,
    )


def _build_api_server(configuration: WindowsServiceConfiguration):
    import uvicorn

    return uvicorn.Server(
        uvicorn.Config(
            "chief.core.app:app",
            host=configuration.api_host,
            port=configuration.api_port,
            access_log=False,
            log_level=os.getenv("CHIEF_LOG_LEVEL", "INFO").casefold(),
        )
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
            self._api_server = None
            self._api_thread = None
            self._api_error: Exception | None = None

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._stop.set()
            if self._api_server is not None:
                self._api_server.should_exit = True
            win32event.SetEvent(self._stop_handle)

        def _run_api(self) -> None:
            assert self._api_server is not None
            try:
                self._api_server.run()
            except Exception as exc:  # noqa: BLE001  # pragma: no cover - service boundary
                self._api_error = exc
                servicemanager.LogErrorMsg(f"CHIEF API failed: {exc}")
            finally:
                if not self._stop.is_set():
                    self._stop.set()

        def SvcDoRun(self) -> None:
            servicemanager.LogInfoMsg("CHIEF Runtime service starting")
            try:
                configuration = load_service_configuration()
                apply_service_configuration(configuration)
                self._api_server = _build_api_server(configuration)
                self._api_thread = threading.Thread(
                    target=self._run_api,
                    name="chief-loopback-api",
                    daemon=True,
                )
                self._api_thread.start()
                deadline = time.monotonic() + 30
                while (
                    not self._api_server.started
                    and self._api_thread.is_alive()
                    and time.monotonic() < deadline
                ):
                    self._stop.wait(0.1)
                if not self._api_server.started:
                    if self._api_error is not None:
                        raise RuntimeError("CHIEF loopback API failed to start") from self._api_error
                    raise RuntimeError("CHIEF loopback API did not start within 30 seconds")
                servicemanager.LogInfoMsg(
                    "CHIEF Runtime service ready "
                    f"(home={configuration.home}, database={configuration.database_path}, "
                    f"api=http://{configuration.api_host}:{configuration.api_port})"
                )
                _build(configuration).run_forever(
                    stop_event=self._stop,
                    interval_seconds=configuration.interval_ms / 1000,
                )
            except Exception as exc:
                servicemanager.LogErrorMsg(f"CHIEF Runtime failed: {exc}")
                raise
            finally:
                self._stop.set()
                if self._api_server is not None:
                    self._api_server.should_exit = True
                if self._api_thread is not None:
                    self._api_thread.join(timeout=15)
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
