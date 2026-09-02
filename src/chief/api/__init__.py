"""HTTP adapters for CHIEF domain services."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from chief.api.approvals import create_approvals_router
from chief.api.integrations import create_integrations_router
from chief.api.models import create_models_router
from chief.api.operating import create_operating_router as _create_operating_router
from chief.api.portfolio import create_portfolio_router
from chief.audit.sqlite import SQLiteAuditLog
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
from chief.tools.registry import create_standard_registry


def _secret(name: str) -> str | None:
    return os.getenv(name, "").strip() or None


def _sync_core_execution_setting(enabled: bool) -> None:
    """Bridge durable operator state into the current global-app composition."""

    module = sys.modules.get("chief.core.app")
    settings = getattr(module, "settings", None) if module is not None else None
    if settings is not None:
        object.__setattr__(settings, "execution_enabled", bool(enabled))


def create_operating_router(*args: Any, **kwargs: Any):
    """Compose operating domains, consented integrations, and owner approval controls."""

    session_store = kwargs.pop("session_store", None)
    tool_registry = kwargs.pop("tool_registry", None)
    execution_control = kwargs.pop("execution_control", None)
    configured_execution_enabled = bool(kwargs.pop("configured_execution_enabled", True))

    router = _create_operating_router(*args, **kwargs)
    business_store = kwargs.get("business_store")
    if business_store is None:
        raise TypeError("create_operating_router requires business_store")

    database_path = business_store.database_path
    if session_store is None:
        session_store = SQLiteSessionStore(database_path)
    if tool_registry is None:
        project_root = Path(__file__).resolve().parents[3]
        tool_registry = create_standard_registry(
            [str(project_root)],
            audit_log=SQLiteAuditLog(database_path),
        )
    if execution_control is None:
        execution_control = ExecutionControlStore(
            database_path,
            initial_enabled=configured_execution_enabled,
        )
    persisted_execution = execution_control.get().enabled
    _sync_core_execution_setting(configured_execution_enabled and persisted_execution)

    settings = Settings.from_env()
    registry = ConnectorRegistry()
    if settings.github_repositories:
        registry.register(
            GitHubReadOnlyConnector(
                repositories=settings.github_repositories,
                token_provider=lambda: _secret("CHIEF_GITHUB_TOKEN"),
            )
        )
    if _secret("CHIEF_GMAIL_ACCESS_TOKEN") is not None:
        registry.register(
            GmailReadOnlyConnector(
                token_provider=lambda: _secret("CHIEF_GMAIL_ACCESS_TOKEN"),
            )
        )
    if _secret("CHIEF_GOOGLE_CALENDAR_ACCESS_TOKEN") is not None:
        registry.register(
            GoogleCalendarReadOnlyConnector(
                token_provider=lambda: _secret("CHIEF_GOOGLE_CALENDAR_ACCESS_TOKEN"),
                calendar_id=os.getenv("CHIEF_GOOGLE_CALENDAR_ID", "primary").strip() or "primary",
            )
        )
    if _secret("CHIEF_STRIPE_RESTRICTED_KEY") is not None:
        registry.register(
            StripeReadOnlyConnector(
                api_key_provider=lambda: _secret("CHIEF_STRIPE_RESTRICTED_KEY"),
            )
        )
    parcelsignals_url = os.getenv("CHIEF_PARCELSIGNALS_SUPABASE_URL", "").strip()
    if parcelsignals_url and _secret("CHIEF_PARCELSIGNALS_SUPABASE_SECRET") is not None:
        registry.register(
            ParcelSignalsReadOnlyConnector(
                supabase_url=parcelsignals_url,
                secret_provider=lambda: _secret("CHIEF_PARCELSIGNALS_SUPABASE_SECRET"),
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
    router.include_router(create_models_router(settings=settings))
    return router


__all__ = [
    "create_approvals_router",
    "create_integrations_router",
    "create_models_router",
    "create_operating_router",
    "create_portfolio_router",
]
