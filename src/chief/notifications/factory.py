from __future__ import annotations

import os
from pathlib import Path

from chief.notifications.delivery import NotificationDispatcher, SMTPEmailProvider
from chief.notifications.store import NotificationStore
from chief.security.secrets import EncryptedSecretStore, SecretResolver


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


def build_notification_dispatcher(
    database_path: str | Path = "data/chief.db",
    *,
    store: NotificationStore | None = None,
) -> NotificationDispatcher:
    """Build configured notification providers with vault-first secret resolution."""

    database_path = Path(database_path)
    store = store or NotificationStore(database_path)
    try:
        secret_store = EncryptedSecretStore(database_path)
    except RuntimeError:
        secret_store = None
    resolver = SecretResolver(secret_store, allow_environment_fallback=True)

    smtp_host = os.getenv("CHIEF_SMTP_HOST", "").strip()
    smtp_sender = os.getenv("CHIEF_SMTP_FROM", "").strip()
    smtp_recipient = os.getenv("CHIEF_NOTIFICATION_EMAIL_TO", "").strip()
    providers = []
    if smtp_host and smtp_sender and smtp_recipient:
        providers.append(
            SMTPEmailProvider(
                host=smtp_host,
                port=int(os.getenv("CHIEF_SMTP_PORT", "587")),
                sender=smtp_sender,
                recipient=smtp_recipient,
                username=os.getenv("CHIEF_SMTP_USERNAME", "").strip() or None,
                password_provider=lambda: resolver.get("CHIEF_SMTP_PASSWORD"),
                use_ssl=_bool_env("CHIEF_SMTP_USE_SSL", False),
                starttls=_bool_env("CHIEF_SMTP_STARTTLS", True),
            )
        )
    return NotificationDispatcher(store, providers)
