"""Provider contracts for CHIEF integrations.

Implementations live outside this foundation. This module performs no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from .schema import (
    ConnectorHealth,
    ConnectorManifest,
    ConnectorReadResult,
    ConnectorWriteResult,
    IdempotencyMetadata,
    SyncCursor,
)


@runtime_checkable
class Connector(Protocol):
    """Minimal interface implemented by any external-system adapter."""

    @property
    def manifest(self) -> ConnectorManifest:
        """Return a static description containing no credentials."""
        ...

    def health(self) -> ConnectorHealth:
        """Return the adapter's latest provider health observation."""
        ...

    def read(
        self,
        scope: str,
        *,
        cursor: SyncCursor | None = None,
        limit: int = 100,
    ) -> ConnectorReadResult:
        """Retrieve evidence under an explicitly granted read scope."""
        ...

    def write(
        self,
        scope: str,
        payload: Mapping[str, object],
        *,
        idempotency: IdempotencyMetadata,
    ) -> ConnectorWriteResult:
        """Mutate an upstream system under an explicitly granted write scope."""
        ...
