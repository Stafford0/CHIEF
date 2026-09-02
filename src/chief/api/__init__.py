"""HTTP adapters for CHIEF domain services."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from chief.api.approvals import create_approvals_router
from chief.api.browser import create_browser_router
from chief.api.integrations import create_integrations_router
from chief.api.models import create_models_router
from chief.api.notification_delivery import create_notification_delivery_router
from chief.api.operating import create_operating_router as _create_operating_router
from chief.api.portfolio import create_portfolio_router
from chief.api.secrets import create_secrets_router
from chief.api.voice import create_voice_router
from chief.audit.sqlite import SQLiteAuditLog
from chief.browser.research import BrowserResearchService, PlaywrightReadOnlyDriver
from chief.core.config import Settings
from chief.core.execution_control import ExecutionControlStore
from chief.core.sqlite_session_store import SQLiteSessionStore
from chief.integrations.evidence_plane import BusinessEvidencePlane
from chief.integrations.github import GitHubReadOnlyConnector
from chief.integrations.gmail import GmailReadOnlyConnector
from chief.integrations.google_calendar import GoogleCalendarReadOnlyConnector
from chief.integrations.parcelsignals import ParcelSignalsReadOnlyConnector
from chief.integrations.registry import ConnectorRegistry
from chief.integrations.stripe import StripeReadOnlyConnector
from chief.notifications.delivery import NotificationDispatcher, SMTPEmailProvider
from chief.security.secrets import EncryptedSecretStore, SecretResolver
from chief.tools.registry import create_standard_registry


def _sync_core_execution_setting(enabled: bool) -> None:
    """Bridge durable operator state into the current global-app composition."""

    module = sys.modules.get("chief.core.app")
    settings = getattr(module, "settings", None) if module is not None else None
    if settings is not None:
        object.__setattr__(settings, "execution_enabled", bool(enabled))


def _secret_components(database_path):
    """Use DPAPI vault when supported; keep env only as a temporary migration fallback."""

    try:
        store = EncryptedSecretStore(database_path)
    except RuntimeError:
        store = None
    resolver = SecretResolver(store, allow_environment_fallback=True)
    return store, resolver


def _bool_environment(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


def create_operating_router(*args: Any, **kwargs: Any):
    """Compose operating domains, consented integrations, and owner approval controls."""

    session_store = kwargs.pop("session_store", None)
    tool_registry = kwargs.pop("tool_registry", None)
    execution_control = kwargs.pop("execution_control", None)
    secret_store = kwargs.pop("secret_store", None)
    browser_service = kwargs.pop("browser_service", None)
    voice_coordinator_factory = kwargs.pop("voice_coordinator_factory", None)
    configured_execution_enabled = bool(kwargs.pop("configured_execution_enabled", True))

    router = _create_operating_router(*args, **kwargs)
    business_store = kwargs.get("business_store")
    notification_store = kwargs.get("notification_store")
    if business_store is None:
        raise TypeError("create_operating_router requires business_store")
    if notification_store is None:
        raise TypeError("create_operating_router requires notification_store")

    database_path = business_store.database_path
    if session_store is None:
        session_store = SQLiteSessionStore(database_path)
    audit_log = SQLiteAuditLog(database_path)
    if tool_registry is None:
        project_root = Path(__file__).resolve().parents[3]
        tool_registry = create_standard_registry(
            [str(project_root)],
            audit_log=audit_log,
        )
    if execution_control is None:
        execution_control = ExecutionControlStore(
            database_path,
            initial_enabled=configured_execution_enabled,
        )
    persisted_execution = execution_control.get().enabled
    _sync_core_execution_setting(configured_execution_enabled and persisted_execution)

    if secret_store is None:
        secret_store, secret_resolver = _secret_components(database_path)
    else:
        secret_resolver = SecretResolver(secret_store, allow_environment_fallback=True)

    settings = Settings.from_env()
    registry = ConnectorRegistry()
    if settings.github_repositories:
        registry.register(
            GitHubReadOnlyConnector(
                repositories=settings.github_repositories,
                token_provider=lambda: secret_resolver.get("CHIEF_GITHUB_TOKEN"),
            )
        )
    if secret_resolver.get("CHIEF_GMAIL_ACCESS_TOKEN") is not None:
        registry.register(
            GmailReadOnlyConnector(
                token_provider=lambda: secret_resolver.get("CHIEF_GMAIL_ACCESS_TOKEN"),
            )
        )
    if secret_resolver.get("CHIEF_GOOGLE_CALENDAR_ACCESS_TOKEN") is not None:
        registry.register(
            GoogleCalendarReadOnlyConnector(
                token_provider=lambda: secret_resolver.get("CHIEF_GOOGLE_CALENDAR_ACCESS_TOKEN"),
                calendar_id=os.getenv("CHIEF_GOOGLE_CALENDAR_ID", "primary").strip() or "primary",
            )
        )
    if secret_resolver.get("CHIEF_STRIPE_RESTRICTED_KEY") is not None:
        registry.register(
            StripeReadOnlyConnector(
                api_key_provider=lambda: secret_resolver.get("CHIEF_STRIPE_RESTRICTED_KEY"),
            )
        )
    parcelsignals_url = os.getenv("CHIEF_PARCELSIGNALS_SUPABASE_URL", "").strip()
    if (
        parcelsignals_url
        and secret_resolver.get("CHIEF_PARCELSIGNALS_SUPABASE_SECRET") is not None
    ):
        registry.register(
            ParcelSignalsReadOnlyConnector(
                supabase_url=parcelsignals_url,
                secret_provider=lambda: secret_resolver.get(
                    "CHIEF_PARCELSIGNALS_SUPABASE_SECRET"
                ),
            )
        )

    evidence_plane = BusinessEvidencePlane(
        registry=registry,
        business_store=business_store,
    )
    router.include_router(
        create_integrations_router(
            registry=registry,
            evidence_plane=evidence_plane,
        )
    )
    router.include_router(
        create_approvals_router(
            session_store=session_store,
            tool_registry=tool_registry,
            execution_control=execution_control,
            configured_execution_enabled=configured_execution_enabled,
            on_execution_change=lambda enabled: _sync_core_execution_setting(
                configured_execution_enabled and enabled
            ),
        )
    )
    router.include_router(
        create_models_router(settings=settings, secret_getter=secret_resolver.get)
    )

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
                password_provider=lambda: secret_resolver.get("CHIEF_SMTP_PASSWORD"),
                use_ssl=_bool_environment("CHIEF_SMTP_USE_SSL", False),
                starttls=_bool_environment("CHIEF_SMTP_STARTTLS", True),
            )
        )
    router.include_router(
        create_notification_delivery_router(
            notification_store=notification_store,
            dispatcher=NotificationDispatcher(notification_store, providers),
        )
    )

    browser_service = browser_service or BrowserResearchService(PlaywrightReadOnlyDriver())
    router.include_router(create_browser_router(service=browser_service))
    router.include_router(
        create_voice_router(coordinator_factory=voice_coordinator_factory)
    )

    if secret_store is not None:
        router.include_router(
            create_secrets_router(
                secret_store=secret_store,
                audit_log=audit_log,
            )
        )
    return router


__all__ = [
    "create_approvals_router",
    "create_browser_router",
    "create_integrations_router",
    "create_models_router",
    "create_notification_delivery_router",
    "create_operating_router",
    "create_portfolio_router",
    "create_secrets_router",
    "create_voice_router",
]
