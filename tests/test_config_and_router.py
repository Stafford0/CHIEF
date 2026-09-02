from datetime import UTC, datetime, timedelta

import pytest

from chief.core.config import Settings
from chief.core.session import PendingToolCall
from chief.core.tool_planner import PlannedToolCall
from chief.models.base import (
    ModelCapabilities,
    ModelPrivacy,
    ModelProvider,
    ModelResponse,
    RouteRequirements,
)
from chief.models.router import ModelRouter


class FakeProvider(ModelProvider):
    def __init__(
        self,
        name: str,
        error: str | None = None,
        capabilities: ModelCapabilities | None = None,
    ) -> None:
        self._name = name
        self.error = error
        self._capabilities = capabilities or ModelCapabilities(privacy=ModelPrivacy.LOCAL)

    @property
    def name(self) -> str:
        return self._name

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._capabilities

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


def test_private_lan_requires_strong_api_token(monkeypatch):
    monkeypatch.setenv("CHIEF_ALLOW_PRIVATE_LAN_UI", "true")
    monkeypatch.delenv("CHIEF_API_TOKEN", raising=False)
    with pytest.raises(ValueError, match="CHIEF_API_TOKEN is required"):
        Settings.from_env()


def test_api_token_rejects_short_secret(monkeypatch):
    monkeypatch.setenv("CHIEF_API_TOKEN", "too-short")
    with pytest.raises(ValueError, match="at least 32 bytes"):
        Settings.from_env()


def test_execution_kill_switch_and_rate_limit_are_validated(monkeypatch):
    monkeypatch.setenv("CHIEF_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("CHIEF_REMOTE_RATE_LIMIT_PER_MINUTE", "7")
    settings = Settings.from_env()
    assert settings.execution_enabled is False
    assert settings.remote_rate_limit_per_minute == 7

    monkeypatch.setenv("CHIEF_EXECUTION_ENABLED", "sometimes")
    with pytest.raises(ValueError, match="must be true or false"):
        Settings.from_env()


def test_cloud_fallback_requires_an_explicit_model(monkeypatch):
    monkeypatch.setenv("CHIEF_CLOUD_MODEL_FALLBACK_ENABLED", "true")
    monkeypatch.delenv("CHIEF_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("CHIEF_ANTHROPIC_MODEL", raising=False)
    with pytest.raises(ValueError, match="requires CHIEF_OPENAI_MODEL"):
        Settings.from_env()


def test_cloud_fallback_is_opt_in(monkeypatch):
    monkeypatch.delenv("CHIEF_CLOUD_MODEL_FALLBACK_ENABLED", raising=False)
    monkeypatch.setenv("CHIEF_OPENAI_MODEL", "configured-model")
    settings = Settings.from_env()
    assert settings.cloud_model_fallback_enabled is False
    assert settings.openai_model == "configured-model"


def test_router_falls_back_and_records_attempts():
    router = ModelRouter([FakeProvider("bad", "offline"), FakeProvider("good")])
    assert router.generate("hello").provider == "good"
    assert [attempt.succeeded for attempt in router.last_attempts] == [False, True]


def test_router_requires_provider():
    with pytest.raises(ValueError):
        ModelRouter([])


def test_router_filters_by_capability_and_privacy():
    cloud = FakeProvider("cloud", capabilities=ModelCapabilities())
    local = FakeProvider(
        "local",
        capabilities=ModelCapabilities(
            privacy=ModelPrivacy.LOCAL,
            structured_output=True,
            cost_tier=0,
        ),
    )
    router = ModelRouter([cloud, local])
    requirements = RouteRequirements(
        allowed_privacy=frozenset({ModelPrivacy.LOCAL}),
        structured_output=True,
        max_cost_tier=0,
    )

    assert router.generate("hello", requirements=requirements).provider == "local"


def test_router_rejects_cloud_without_per_route_authorization():
    cloud = FakeProvider("cloud", capabilities=ModelCapabilities(privacy=ModelPrivacy.CLOUD))
    router = ModelRouter([cloud])

    with pytest.raises(RuntimeError, match="No configured model provider"):
        router.generate(
            "private context",
            requirements=RouteRequirements(
                allowed_privacy=frozenset({ModelPrivacy.CLOUD}),
                cloud_authorized=False,
            ),
        )

    result = router.generate(
        "explicitly shareable context",
        requirements=RouteRequirements(
            allowed_privacy=frozenset({ModelPrivacy.CLOUD}),
            cloud_authorized=True,
        ),
    )
    assert result.provider == "cloud"


def test_router_opens_and_recovers_circuit_breaker():
    clock = [0.0]
    provider = FakeProvider("unstable", error="offline")
    router = ModelRouter(
        [provider],
        failure_threshold=1,
        cooldown_seconds=10,
        clock=lambda: clock[0],
    )

    with pytest.raises(RuntimeError, match="offline"):
        router.generate("first")
    with pytest.raises(RuntimeError, match="cooling down"):
        router.generate("second")
    assert router.last_attempts[0].skipped is True

    provider.error = None
    clock[0] = 11
    assert router.generate("third").provider == "unstable"
    assert router.provider_states()[0]["circuit_open"] is False


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
