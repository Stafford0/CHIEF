"""HTTP adapters for CHIEF domain services."""

from __future__ import annotations

import os
from typing import Any

from chief.api.approvals import create_approvals_router
from chief.api.integrations import create_integrations_router
from chief.api.operating import create_operating_router as _create_operating_router
from chief.api.portfolio import create_portfolio_router
from chief.core.config import Settings
from chief.integrations.evidence_plane import BusinessEvidencePlane
from chief.integrations.github import GitHubReadOnlyConnector
from chief.integrations.gmail import GmailReadOnlyConnector
from chief.integrations.google_calendar import GoogleCalendarReadOnlyConnector
from chief.integrations.parcelsignals import ParcelSignalsReadOnlyConnector
from chief.integrations.registry import ConnectorRegistry
from chief.integrations.stripe import StripeReadOnlyConnector


def _secret(name: str) -> str | None:
    return os.getenv(name, "").strip() or None


def create_operating_router(*args: Any, **kwargs: Any):
    """Compose operating domains, consented integrations, and owner approval controls."""

    session_store = kwargs.pop("session_store", None)
    tool_registry = kwargs.pop("tool_registry", None)
    execution_control = kwargs.pop("execution_control", None)
    configured_execution_enabled = kwargs.pop("configured_execution_enabled", True)

    router = _create_operating_router(*args, **kwargs)
    business_store = kwargs.get("business_store")
    if business_store is None:
        raise TypeError("create_operating_router requires business_store")

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
    if session_store is not None and tool_registry is not None and execution_control is not None:
        router.include_router(
            create_approvals_router(
                session_store=session_store,
                tool_registry=tool_registry,
                execution_control=execution_control,
                configured_execution_enabled=bool(configured_execution_enabled),
            )
        )
    return router


__all__ = [
    "create_approvals_router",
    "create_integrations_router",
    "create_operating_router",
    "create_portfolio_router",
]
