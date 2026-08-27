import os
from dataclasses import dataclass


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


@dataclass(frozen=True)
class Settings:
    """Validated runtime settings; secrets stay in the environment."""

    environment: str = "development"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b"
    ultron_ollama_model: str = "llama3.1:8b"
    ultron_enabled: bool = True
    model_timeout_seconds: int = 120
    max_model_response_bytes: int = 2_000_000
    cors_origins: tuple[str, ...] = ("http://127.0.0.1:5173", "http://localhost:5173")
    allow_private_lan_ui: bool = False
    api_token: str | None = None
    trusted_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver")
    execution_enabled: bool = True
    remote_rate_limit_per_minute: int = 120
    max_request_bytes: int = 2_000_000

    @classmethod
    def from_env(cls) -> "Settings":
        origins = tuple(
            x.strip() for x in os.getenv("CHIEF_CORS_ORIGINS", "").split(",") if x.strip()
        )
        lan = _bool_env("CHIEF_ALLOW_PRIVATE_LAN_UI", False)
        api_token = os.getenv("CHIEF_API_TOKEN", "").strip() or None
        trusted_hosts = tuple(
            item.strip() for item in os.getenv("CHIEF_TRUSTED_HOSTS", "").split(",") if item.strip()
        )
        if api_token is not None and len(api_token.encode("utf-8")) < 32:
            raise ValueError("CHIEF_API_TOKEN must be at least 32 bytes.")
        if lan and api_token is None:
            raise ValueError(
                "CHIEF_API_TOKEN is required when CHIEF_ALLOW_PRIVATE_LAN_UI is enabled."
            )
        return cls(
            environment=os.getenv("CHIEF_ENVIRONMENT", "development"),
            ollama_url=os.getenv("CHIEF_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_model=os.getenv("CHIEF_OLLAMA_MODEL", "qwen3:4b"),
            ultron_ollama_model=os.getenv(
                "CHIEF_ULTRON_OLLAMA_MODEL",
                "llama3.1:8b",
            ),
            ultron_enabled=_bool_env("CHIEF_ULTRON_ENABLED", True),
            model_timeout_seconds=_int_env(
                "CHIEF_MODEL_TIMEOUT_SECONDS", 120, minimum=1, maximum=600
            ),
            max_model_response_bytes=_int_env(
                "CHIEF_MAX_MODEL_RESPONSE_BYTES", 2_000_000, minimum=1024, maximum=20_000_000
            ),
            cors_origins=origins or cls.cors_origins,
            allow_private_lan_ui=lan,
            api_token=api_token,
            trusted_hosts=trusted_hosts or cls.trusted_hosts,
            execution_enabled=_bool_env("CHIEF_EXECUTION_ENABLED", True),
            remote_rate_limit_per_minute=_int_env(
                "CHIEF_REMOTE_RATE_LIMIT_PER_MINUTE", 120, minimum=1, maximum=10_000
            ),
            max_request_bytes=_int_env(
                "CHIEF_MAX_REQUEST_BYTES", 2_000_000, minimum=1_024, maximum=100_000_000
            ),
        )
