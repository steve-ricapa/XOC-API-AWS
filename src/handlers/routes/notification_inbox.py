"""Authenticated API for the user-visible mobile notification inbox."""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends

from src.handlers.routes.devices import _request_identity
from src.shared.dependencies import require_access_claims
from src.shared.errors import NotFoundError, ValidationError
from src.shared.user_notification_inbox import (
    archive_user_notification,
    list_user_notifications,
    mark_user_notification_read,
    serialize_user_notification,
    unread_count_for_user,
)


router = APIRouter(prefix="/notifications/inbox", tags=["notifications"])
_NOTIFICATION_ID_RE = re.compile(r"^[a-f0-9]{64}$")


def _notification_id_or_error(notification_id: str) -> str:
    normalized = notification_id.strip().lower()
    if not _NOTIFICATION_ID_RE.fullmatch(normalized):
        raise ValidationError("notificationId is invalid")
    return normalized


def _identity(claims: dict[str, Any]) -> tuple[str, str]:
    tenant_id, user_id, _role = _request_identity(claims)
    return tenant_id, user_id


@router.get("")
def get_notification_inbox(
    status: str = "all",
    limit: int = 25,
    cursor: str | None = None,
    claims: dict[str, Any] = Depends(require_access_claims),
) -> dict[str, Any]:
    if limit < 1 or limit > 100:
        raise ValidationError("limit must be between 1 and 100")
    tenant_id, user_id = _identity(claims)
    try:
        notifications, next_cursor = list_user_notifications(
            tenant_id,
            user_id,
            status=status,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return {"notifications": notifications, "nextCursor": next_cursor}


@router.get("/unread-count")
def get_unread_notification_count(
    claims: dict[str, Any] = Depends(require_access_claims),
) -> dict[str, int]:
    tenant_id, user_id = _identity(claims)
    return {"count": unread_count_for_user(tenant_id, user_id)}


@router.patch("/{notification_id}/read")
def mark_notification_read(
    notification_id: str,
    payload: dict[str, Any] | None = None,
    claims: dict[str, Any] = Depends(require_access_claims),
) -> dict[str, Any]:
    tenant_id, user_id = _identity(claims)
    item = mark_user_notification_read(
        tenant_id,
        user_id,
        _notification_id_or_error(notification_id),
        opened=bool((payload or {}).get("opened")),
    )
    if not item:
        raise NotFoundError("Notification not found")
    return {"notification": serialize_user_notification(item)}


@router.patch("/{notification_id}/archive")
def archive_notification(
    notification_id: str,
    claims: dict[str, Any] = Depends(require_access_claims),
) -> dict[str, Any]:
    tenant_id, user_id = _identity(claims)
    item = archive_user_notification(tenant_id, user_id, _notification_id_or_error(notification_id))
    if not item:
        raise NotFoundError("Notification not found")
    return {"notification": serialize_user_notification(item)}
