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


@dataclass(frozen=True)
class Settings:
    """Validated runtime settings; secrets stay in the environment."""

    environment: str = "development"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b"
    model_timeout_seconds: int = 120
    max_model_response_bytes: int = 2_000_000
    cors_origins: tuple[str, ...] = ("http://127.0.0.1:5173", "http://localhost:5173")
    allow_private_lan_ui: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        origins = tuple(
            x.strip() for x in os.getenv("CHIEF_CORS_ORIGINS", "").split(",") if x.strip()
        )
        lan = os.getenv("CHIEF_ALLOW_PRIVATE_LAN_UI", "false").casefold() in {"1", "true", "yes"}
        return cls(
            environment=os.getenv("CHIEF_ENVIRONMENT", "development"),
            ollama_url=os.getenv("CHIEF_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_model=os.getenv("CHIEF_OLLAMA_MODEL", "qwen3:4b"),
            model_timeout_seconds=_int_env(
                "CHIEF_MODEL_TIMEOUT_SECONDS", 120, minimum=1, maximum=600
            ),
            max_model_response_bytes=_int_env(
                "CHIEF_MAX_MODEL_RESPONSE_BYTES", 2_000_000, minimum=1024, maximum=20_000_000
            ),
            cors_origins=origins or cls.cors_origins,
            allow_private_lan_ui=lan,
        )
