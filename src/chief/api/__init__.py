"""HTTP adapters for CHIEF domain services."""

from __future__ import annotations

import os
from typing import Any

from chief.api.integrations import create_integrations_router
from chief.api.operating import create_operating_router as _create_operating_router
from chief.api.portfolio import create_portfolio_router
from chief.core.config import Settings
from chief.integrations.evidence_plane import BusinessEvidencePlane
from chief.integrations.github import GitHubReadOnlyConnector
from chief.integrations.registry import ConnectorRegistry


def create_operating_router(*args: Any, **kwargs: Any):
    """Compose operating domains plus configured, consent-gated integrations."""

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
                token_provider=lambda: os.getenv("CHIEF_GITHUB_TOKEN", "").strip() or None,
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
    return router


__all__ = [
    "create_integrations_router",
    "create_operating_router",
    "create_portfolio_router",
]
