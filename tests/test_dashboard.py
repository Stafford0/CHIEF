from chief.core import dashboard


def test_windows_null_memory_telemetry_degrades_cleanly(monkeypatch) -> None:
    monkeypatch.setattr(dashboard.os, "name", "nt")
    monkeypatch.setattr(dashboard, "_powershell", lambda _script: "null")

    assert dashboard._memory() == {
        "total_gb": None,
        "used_gb": None,
        "percent": None,
    }


def test_windows_null_adapter_telemetry_degrades_cleanly(monkeypatch) -> None:
    monkeypatch.setattr(dashboard.os, "name", "nt")
    monkeypatch.setattr(dashboard, "_powershell", lambda _script: "null")

    assert dashboard._network()["adapters"] == []


def test_windows_adapter_telemetry_ignores_non_object_rows(monkeypatch) -> None:
    monkeypatch.setattr(dashboard.os, "name", "nt")
    monkeypatch.setattr(
        dashboard,
        "_powershell",
        lambda _script: '[null,{"Name":"Tailscale","InterfaceDescription":"VPN","LinkSpeed":"1 Gbps"}]',
    )

    assert dashboard._network()["adapters"] == [
        {
            "name": "Tailscale",
            "description": "VPN",
            "link_speed": "1 Gbps",
        }
    ]


def test_snapshot_degrades_only_the_failed_metric(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(dashboard, "_memory", lambda: (_ for _ in ()).throw(PermissionError()))
    monkeypatch.setattr(dashboard, "_disk", lambda _root: {"percent": 10.0})
    monkeypatch.setattr(dashboard, "_gpu", lambda: {"available": False})
    monkeypatch.setattr(
        dashboard,
        "_network",
        lambda: {"hostname": "test", "addresses": [], "adapters": []},
    )
    monkeypatch.setattr(dashboard, "_ollama_models", lambda: {"online": False, "models": []})
    monkeypatch.setattr(dashboard, "_cpu_percent", lambda: 5.0)
    monkeypatch.setattr(dashboard, "_projects", lambda _root: [])

    snapshot = dashboard.collect_dashboard_snapshot(tmp_path)

    assert snapshot["degraded_components"] == ["memory"]
    assert snapshot["memory"]["percent"] is None
    assert snapshot["disk"]["percent"] == 10.0


def test_project_discovery_skips_inaccessible_git_markers(monkeypatch, tmp_path) -> None:
    candidate = tmp_path / "restricted"
    candidate.mkdir()

    original_exists = dashboard.Path.exists

    def guarded_exists(path):
        if path == candidate / ".git":
            raise PermissionError("service identity cannot inspect this directory")
        return original_exists(path)

    monkeypatch.setattr(dashboard.Path, "exists", guarded_exists)

    projects = dashboard._projects(tmp_path / "CHIEF")

    assert all(project["name"] != "restricted" for project in projects)
