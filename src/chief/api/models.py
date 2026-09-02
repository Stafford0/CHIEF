from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from chief.core.config import Settings
from chief.models.base import ModelPrivacy, RouteRequirements
from chief.models.cloud_factory import build_cloud_model_router

SecretGetter = Callable[[str], str | None]


class CloudGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=200_000)
    system_prompt: str | None = Field(default=None, max_length=200_000)
    cloud_authorized: bool = False
    max_cost_tier: int | None = Field(default=None, ge=0, le=100)


def create_models_router(
    *,
    settings: Settings | None = None,
    secret_getter: SecretGetter | None = None,
) -> APIRouter:
    """Expose cloud reasoning only through an explicit per-call privacy gate."""

    settings = settings or Settings.from_env()
    router = APIRouter(tags=["models"])

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
        }

    @router.post("/models/cloud/generate")
    def cloud_generate(payload: CloudGenerateRequest) -> dict[str, object]:
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
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "content": result.content,
            "provider": result.provider,
            "model": result.model,
            "latency_ms": result.latency_ms,
            "cloud_transmission_authorized": True,
        }

    return router
