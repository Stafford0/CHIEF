from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from chief.audit.log import AuditEvent
from chief.audit.sqlite import SQLiteAuditLog
from chief.security.secrets import EncryptedSecretStore, metadata_json


class SecretPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=1_048_576)


class SecretRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2_000)


def _actor(request: Request) -> str:
    actor_id = getattr(request.state, "actor_id", None)
    if not isinstance(actor_id, str) or not actor_id:
        raise HTTPException(status_code=401, detail="An authenticated CHIEF actor is required.")
    return actor_id


def create_secrets_router(
    *,
    secret_store: EncryptedSecretStore,
    audit_log: SQLiteAuditLog,
    on_change: Callable[[], None] | None = None,
) -> APIRouter:
    """Manage encrypted secrets without ever returning plaintext values."""

    router = APIRouter(prefix="/secrets", tags=["secrets"])

    def audit(
        request: Request,
        name: str,
        decision: str,
        *,
        extra_metadata: dict[str, str] | None = None,
    ) -> None:
        metadata = {"event_type": f"secret.{decision}", "secret_name": name}
        if extra_metadata:
            metadata.update(extra_metadata)
        audit_log.record(
            AuditEvent(
                tool_name="config.secret",
                approved=True,
                decision=decision,
                success=True,
                request_id=str(request.state.request_id),
                actor_id=_actor(request),
                metadata=metadata,
            )
        )

    @router.get("")
    def list_secrets() -> list[dict[str, str]]:
        return [metadata_json(item) for item in secret_store.list_metadata()]

    @router.get("/{name}")
    def get_secret_metadata(name: str) -> dict[str, str]:
        try:
            metadata = secret_store.metadata(name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if metadata is None:
            raise HTTPException(status_code=404, detail="Secret not found.")
        return metadata_json(metadata)

    @router.put("/{name}")
    def put_secret(name: str, payload: SecretPutRequest, request: Request) -> dict[str, str]:
        try:
            metadata = secret_store.put(name, payload.value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        audit(request, name, "stored")
        if on_change is not None:
            on_change()
        return metadata_json(metadata)

    @router.post("/{name}/rotate")
    def rotate_secret(name: str, payload: SecretPutRequest, request: Request) -> dict[str, str]:
        try:
            previous = secret_store.metadata(name)
            if previous is None:
                raise HTTPException(status_code=404, detail="Secret not found; create it before rotation.")
            metadata = secret_store.put(name, payload.value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        audit(
            request,
            name,
            "rotated",
            extra_metadata={"previous_updated_at": previous.updated_at.isoformat()},
        )
        if on_change is not None:
            on_change()
        return metadata_json(metadata)

    @router.post("/{name}/revoke")
    def revoke_secret(
        name: str,
        payload: SecretRevokeRequest,
        request: Request,
    ) -> dict[str, object]:
        try:
            deleted = secret_store.delete(name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Secret not found.")
        audit(request, name, "revoked", extra_metadata={"reason": payload.reason})
        if on_change is not None:
            on_change()
        return {"name": name, "revoked": True}

    @router.delete("/{name}")
    def delete_secret(name: str, request: Request) -> dict[str, object]:
        try:
            deleted = secret_store.delete(name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Secret not found.")
        audit(request, name, "deleted")
        if on_change is not None:
            on_change()
        return {"name": name, "deleted": True}

    return router
