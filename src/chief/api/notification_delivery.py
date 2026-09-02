from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from chief.notifications.delivery import NotificationDispatcher
from chief.notifications.store import NotificationStore


def _actor(request: Request) -> str:
    actor_id = getattr(request.state, "actor_id", None)
    if not isinstance(actor_id, str) or not actor_id:
        raise HTTPException(status_code=401, detail="An authenticated CHIEF actor is required.")
    return actor_id


def create_notification_delivery_router(
    *,
    notification_store: NotificationStore,
    dispatcher: NotificationDispatcher,
) -> APIRouter:
    router = APIRouter(tags=["notification-delivery"])

    @router.get("/notifications/delivery/status")
    def delivery_status() -> dict[str, object]:
        return {
            "channels": sorted(channel.value for channel in dispatcher.providers),
            "receipt_tracking": True,
        }

    @router.post("/notifications/{notification_id}/deliver")
    def deliver(notification_id: UUID, request: Request) -> dict[str, object]:
        notification = notification_store.get(notification_id)
        if notification is None or notification.recipient_id != _actor(request):
            raise HTTPException(status_code=404, detail="Notification not found.")
        try:
            receipts = dispatcher.deliver(notification_id)
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "notification_id": str(notification_id),
            "receipts": [
                {
                    "id": str(receipt.id),
                    "attempt_id": str(receipt.attempt_id),
                    "status": receipt.status.value,
                    "received_at": receipt.received_at.isoformat(),
                    "provider_reference": receipt.provider_reference,
                    "detail": receipt.detail,
                }
                for receipt in receipts
            ],
        }

    return router
