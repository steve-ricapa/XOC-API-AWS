"""Best-effort creator-only ticket push notifications."""
from __future__ import annotations

import logging

from src.notifications.events import (
    build_notification_event_for_ticket_status,
    publish_notification_requested,
)


logger = logging.getLogger(__name__)


def _get_ticket(tenant_id: int, ticket_id: str) -> dict | None:
    """Import the Dynamo store only when a notification is actually needed."""
    from src.shared.tickets_store import get_tenant_ticket_or_none

    return get_tenant_ticket_or_none(tenant_id, ticket_id)


def publish_ticket_status_notification(
    *,
    tenant_id: int | str,
    ticket_id: str,
    status: str,
    attempt_count: int | None = None,
) -> bool:
    """Publish one notification to the ticket creator without affecting the flow.

    The ticket record is the authority for the tenant and creator.  Callers do
    not pass a recipient, which prevents a request payload or an approver from
    changing who receives a ticket notification.
    """
    try:
        normalized_ticket_id = str(ticket_id).strip()
        normalized_tenant_id = int(tenant_id)
        ticket = _get_ticket(normalized_tenant_id, normalized_ticket_id)
    except Exception as exc:
        logger.warning(
            "ticket_notification_lookup_failed ticket=%s errorType=%s",
            str(ticket_id)[:64],
            type(exc).__name__,
        )
        return False
    if not ticket:
        logger.warning(
            "ticket_notification_skipped ticket_not_found tenant=%s ticket=%s",
            normalized_tenant_id,
            normalized_ticket_id,
        )
        return False

    creator_user_id = ticket.get("created_by_user_id")
    if creator_user_id is None or not str(creator_user_id).strip():
        logger.info(
            "ticket_notification_skipped missing_creator tenant=%s ticket=%s status=%s",
            normalized_tenant_id,
            normalized_ticket_id,
            status,
        )
        return False

    try:
        event = build_notification_event_for_ticket_status(
            tenant_id=normalized_tenant_id,
            ticket_id=normalized_ticket_id,
            recipient_user_id=creator_user_id,
            status=status,
            attempt_count=attempt_count,
        )
        if event is None:
            return False
        publish_notification_requested(event)
        logger.info(
            "ticket_notification_published tenant=%s ticket=%s eventType=%s status=%s",
            normalized_tenant_id,
            normalized_ticket_id,
            event["eventType"],
            status,
        )
        return True
    except Exception as exc:  # Notifications must never fail the ticket workflow.
        logger.warning(
            "ticket_notification_publish_failed tenant=%s ticket=%s status=%s errorType=%s",
            normalized_tenant_id,
            normalized_ticket_id,
            status,
            type(exc).__name__,
        )
        return False
