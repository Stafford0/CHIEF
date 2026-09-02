from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from chief.core.config import Settings
from chief.models.base import ModelPrivacy, RouteRequirements
from chief.models.cloud_factory import build_cloud_model_router
from chief.models.route_audit import SQLiteModelRouteStore

SecretGetter = Callable[[str], str | None]


class CloudGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=200_000)
    system_prompt: str | None = Field(default=None, max_length=200_000)
    cloud_authorized: bool = False
    max_cost_tier: int | None = Field(default=None, ge=0, le=100)


def _actor(request: Request) -> str:
    actor_id = getattr(request.state, "actor_id", None)
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise HTTPException(status_code=401, detail="An authenticated CHIEF actor is required.")
    return actor_id.strip()


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, str) and request_id else "unknown"


def create_models_router(
    *,
    settings: Settings | None = None,
    secret_getter: SecretGetter | None = None,
    database_path: str | Path | None = None,
) -> APIRouter:
    """Expose cloud reasoning only through an explicit per-call privacy gate."""

    settings = settings or Settings.from_env()
    router = APIRouter(tags=["models"])
    route_store = SQLiteModelRouteStore(database_path) if database_path is not None else None

    def build_router():
        if secret_getter is None:
            return build_cloud_model_router(settings)
        return build_cloud_model_router(settings, secret_getter=secret_getter)

    @router.get("/models/cloud")
    def cloud_status() -> dict[str, object]:
        model_router = build_router()
        return {
            "configured": model_router is not None,
            "global_fallback_enabled": settings.cloud_model_fallback_enabled,
            "providers": model_router.provider_states() if model_router is not None else [],
            "per_call_authorization_required": True,
            "route_receipts_enabled": route_store is not None,
        }

    @router.get("/models/routes")
    def route_receipts(request: Request, limit: int = 100):
        if route_store is None:
            raise HTTPException(status_code=503, detail="Model route receipt storage is unavailable.")
        try:
            return route_store.list(actor_id=_actor(request), limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/models/cloud/generate")
    def cloud_generate(payload: CloudGenerateRequest, request: Request) -> dict[str, object]:
        actor_id = _actor(request)
        request_id = _request_id(request)
        if not settings.cloud_model_fallback_enabled:
            raise HTTPException(
                status_code=403,
                detail="Cloud model use is disabled by CHIEF_CLOUD_MODEL_FALLBACK_ENABLED.",
            )
        if not payload.cloud_authorized:
            raise HTTPException(
                status_code=403,
                detail="This request did not explicitly authorize cloud transmission.",
            )
        model_router = build_router()
        if model_router is None:
            raise HTTPException(status_code=503, detail="No configured cloud model provider is available.")
        try:
            result = model_router.generate(
                payload.prompt,
                payload.system_prompt,
                requirements=RouteRequirements(
                    allowed_privacy=frozenset({ModelPrivacy.CLOUD}),
                    cloud_authorized=True,
                    max_cost_tier=payload.max_cost_tier,
                ),
            )
        except RuntimeError as exc:
            if route_store is not None:
                route_store.record(
                    actor_id=actor_id,
                    request_id=request_id,
                    attempts=model_router.last_attempts,
                    selected_provider=None,
                    selected_model=None,
                    selected_privacy=None,
                    latency_ms=None,
                    max_cost_tier=payload.max_cost_tier,
                    cloud_authorized=True,
                    succeeded=False,
                )
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        selected_privacy = None
        for provider in model_router.providers:
            provider_name = getattr(provider, "name", provider.__class__.__name__)
            if provider_name == result.provider:
                selected_privacy = model_router._capabilities(provider).privacy
                break
        receipt = None
        if route_store is not None:
            receipt = route_store.record(
                actor_id=actor_id,
                request_id=request_id,
                attempts=model_router.last_attempts,
                selected_provider=result.provider,
                selected_model=result.model,
                selected_privacy=selected_privacy,
                latency_ms=result.latency_ms,
                max_cost_tier=payload.max_cost_tier,
                cloud_authorized=True,
                succeeded=True,
            )
        return {
            "content": result.content,
            "provider": result.provider,
            "model": result.model,
            "latency_ms": result.latency_ms,
            "cloud_transmission_authorized": True,
            "route_receipt_id": str(receipt.id) if receipt is not None else None,
        }

    return router
