from datetime import UTC, datetime, timedelta

import pytest

from chief.core.config import Settings
from chief.core.session import PendingToolCall
from chief.core.tool_planner import PlannedToolCall
from chief.models.base import ModelProvider, ModelResponse
from chief.models.router import ModelRouter


class FakeProvider(ModelProvider):
    def __init__(self, name: str, error: str | None = None) -> None:
        self._name = name
        self.error = error

    @property
    def name(self) -> str:
        return self._name

    def generate(self, prompt: str, system_prompt: str | None = None) -> ModelResponse:
        if self.error:
            raise RuntimeError(self.error)
        return ModelResponse(prompt, self.name, "fake")


def test_settings_reject_invalid_timeout(monkeypatch):
    monkeypatch.setenv("CHIEF_MODEL_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_private_lan_is_opt_in(monkeypatch):
    monkeypatch.delenv("CHIEF_ALLOW_PRIVATE_LAN_UI", raising=False)
    assert Settings.from_env().allow_private_lan_ui is False


def test_router_falls_back_and_records_attempts():
    router = ModelRouter([FakeProvider("bad", "offline"), FakeProvider("good")])
    assert router.generate("hello").provider == "good"
    assert [attempt.succeeded for attempt in router.last_attempts] == [False, True]


def test_router_requires_provider():
    with pytest.raises(ValueError):
        ModelRouter([])


def test_pending_tool_digest_binds_exact_arguments():
    first = PendingToolCall(PlannedToolCall("x", "tool", {"value": 1}, "do it"))
    second = PendingToolCall(PlannedToolCall("x", "tool", {"value": 2}, "do it"))
    assert first.digest != second.digest


def test_pending_tool_expires():
    pending = PendingToolCall(
        PlannedToolCall("x", "tool", {}, "do it"),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert pending.expired()
