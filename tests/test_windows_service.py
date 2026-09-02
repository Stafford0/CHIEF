from __future__ import annotations

from pathlib import Path

import pytest

from chief.runtime.windows_service import (
    apply_service_configuration,
    load_service_configuration,
)


def test_service_configuration_resolves_paths_and_keeps_api_on_loopback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='chief-ai'\n", encoding="utf-8")
    monkeypatch.setenv("CHIEF_HOME", str(tmp_path))
    monkeypatch.setenv("CHIEF_DATABASE_PATH", "state/chief.db")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    configuration = load_service_configuration()

    assert configuration.home == tmp_path.resolve()
    assert configuration.database_path == (tmp_path / "state" / "chief.db").resolve()
    assert configuration.playwright_browsers_path == (
        tmp_path / "work" / "ms-playwright"
    ).resolve()
    assert configuration.api_host == "127.0.0.1"
    assert configuration.api_port == 8000


def test_service_configuration_rejects_non_loopback_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='chief-ai'\n", encoding="utf-8")
    monkeypatch.setenv("CHIEF_HOME", str(tmp_path))
    monkeypatch.setenv("CHIEF_SERVICE_API_HOST", "0.0.0.0")

    with pytest.raises(ValueError, match="loopback-only"):
        load_service_configuration()


def test_apply_service_configuration_exports_absolute_runtime_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='chief-ai'\n", encoding="utf-8")
    monkeypatch.setenv("CHIEF_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path.parent)
    configuration = load_service_configuration()

    apply_service_configuration(configuration)

    assert Path.cwd() == tmp_path.resolve()
    assert Path(configuration.database_path).is_absolute()
    assert Path(configuration.playwright_browsers_path).is_absolute()
