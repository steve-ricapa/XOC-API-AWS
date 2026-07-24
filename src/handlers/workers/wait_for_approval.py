import json
import logging

from src.shared.errors import ValidationError
from src.shared.tickets_store import get_tenant_ticket_or_404, update_ticket_fields

logger = logging.getLogger(__name__)


def handler(event: dict, context) -> dict:
    ticket_id = event.get("ticketId")
    tenant_id = event.get("tenantId")

    if not ticket_id or not tenant_id:
        raise ValidationError("ticketId and tenantId are required")

    task_token = event.get("task_token") or context.get("task_token")
    if not task_token:
        logger.warning("No task_token found in event or context for ticket %s", ticket_id)
        task_token = "pending"

    update_ticket_fields(int(tenant_id), ticket_id, {
        "approval_task_token": task_token,
    })

    return {
        "taskToken": task_token,
        "ticketId": ticket_id,
        "tenantId": int(tenant_id),
    }
