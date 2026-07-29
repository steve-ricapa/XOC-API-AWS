import json
import logging

from src.shared.errors import ValidationError

logger = logging.getLogger(__name__)


def handler(event: dict, context) -> dict:
    ticket_id = event.get("ticketId")
    tenant_id = event.get("tenantId")

    if not ticket_id or not tenant_id:
        raise ValidationError("ticketId and tenantId are required")

    task_token = event.get("taskToken") or event.get("task_token")
    if not task_token:
        logger.warning("No task_token found in event for ticket %s", ticket_id)
        task_token = "pending"

    return {
        "taskToken": task_token,
        "ticketId": ticket_id,
        "tenantId": int(tenant_id),
    }
