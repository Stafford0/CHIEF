from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


def _run(command: list[str], timeout: float = 2.5) -> str:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _powershell(script: str) -> str:
    if os.name != "nt":
        return ""
    return _run(["powershell", "-NoProfile", "-Command", script])


def _cpu_percent() -> float | None:
    if os.name == "nt":
        value = _powershell(
            "(Get-Counter '\\Processor(_Total)\\% Processor Time').CounterSamples.CookedValue"
        )
        try:
            return round(float(value), 1)
        except ValueError:
            return None

    try:
        load_1, _, _ = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        return round(min(100.0, (load_1 / cpu_count) * 100.0), 1)
    except (AttributeError, OSError):
        return None


def _memory() -> dict[str, Any]:
    if os.name == "nt":
        raw = _powershell(
            "$o=Get-CimInstance Win32_OperatingSystem; "
            "[pscustomobject]@{total=[math]::Round($o.TotalVisibleMemorySize/1MB,2);"
            "free=[math]::Round($o.FreePhysicalMemory/1MB,2)} | ConvertTo-Json -Compress"
        )
        try:
            data = json.loads(raw)
            total = float(data["total"])
            free = float(data["free"])
            used = max(0.0, total - free)
            pct = round((used / total) * 100.0, 1) if total else 0.0
            return {"total_gb": total, "used_gb": round(used, 2), "percent": pct}
        except (ValueError, KeyError, json.JSONDecodeError):
            pass
    return {"total_gb": None, "used_gb": None, "percent": None}


def _disk(project_root: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(project_root.anchor or project_root)
    pct = round((usage.used / usage.total) * 100.0, 1) if usage.total else 0.0
    gib = 1024**3
    return {
        "total_gb": round(usage.total / gib, 1),
        "used_gb": round(usage.used / gib, 1),
        "free_gb": round(usage.free / gib, 1),
        "percent": pct,
    }


def _gpu() -> dict[str, Any]:
    raw = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
        ]
    )
    if not raw:
        return {"available": False}
    first = raw.splitlines()[0]
    parts = [part.strip() for part in first.split(",")]
    if len(parts) < 5:
        return {"available": False}
    try:
        return {
            "available": True,
            "name": parts[0],
            "utilization_percent": float(parts[1]),
            "memory_used_mb": float(parts[2]),
            "memory_total_mb": float(parts[3]),
            "temperature_c": float(parts[4]),
        }
    except ValueError:
        return {"available": False, "name": parts[0]}


def _network() -> dict[str, Any]:
    hostname = socket.gethostname()
    addresses: list[str] = []
    try:
        addresses = sorted({addr for addr in socket.gethostbyname_ex(hostname)[2] if addr})
    except OSError:
        pass

    adapters: list[dict[str, str]] = []
    if os.name == "nt":
        raw = _powershell(
            "Get-NetAdapter | Where-Object Status -eq 'Up' | "
            "Select-Object Name,InterfaceDescription,LinkSpeed | ConvertTo-Json -Compress"
        )
        try:
            parsed = json.loads(raw) if raw else []
            if isinstance(parsed, dict):
                parsed = [parsed]
            adapters = [
                {
                    "name": str(item.get("Name", "Adapter")),
                    "description": str(item.get("InterfaceDescription", "")),
                    "link_speed": str(item.get("LinkSpeed", "")),
                }
                for item in parsed
            ]
        except json.JSONDecodeError:
            pass

    return {"hostname": hostname, "addresses": addresses, "adapters": adapters}


def _ollama_models(base_url: str = "http://127.0.0.1:11434") -> dict[str, Any]:
    try:
        with urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=1.2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError):
        return {"online": False, "models": []}

    models = []
    for item in payload.get("models", []):
        name = item.get("name") or item.get("model")
        if name:
            models.append(str(name))
    return {"online": True, "models": models}


def collect_dashboard_snapshot(project_root: Path) -> dict[str, Any]:
    memory = _memory()
    disk = _disk(project_root)
    gpu = _gpu()
    network = _network()
    ollama = _ollama_models()

    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "host": {
            "hostname": network["hostname"],
            "os": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "cpu": {"percent": _cpu_percent()},
        "memory": memory,
        "disk": disk,
        "gpu": gpu,
        "network": network,
        "ollama": ollama,
    }
