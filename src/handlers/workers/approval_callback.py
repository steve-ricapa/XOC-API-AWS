import json
import logging

import boto3

from src.shared.errors import ValidationError
from src.shared.tickets_store import get_tenant_ticket_or_404, update_ticket_fields

logger = logging.getLogger(__name__)

stepfunctions = boto3.client("stepfunctions")


def handler(event: dict, context) -> dict:
    ticket_id = event.get("ticketId")
    task_token = event.get("taskToken")
    approved = event.get("approved", False)

    if not ticket_id:
        raise ValidationError("ticketId is required")

    tenant_id = event.get("tenantId")
    if not tenant_id:
        item = get_tenant_ticket_or_404(0, ticket_id)
        tenant_id = item.get("tenant_id")

    if task_token and task_token != "pending":
        output = json.dumps({"approved": bool(approved)})
        try:
            stepfunctions.send_task_success(
                taskToken=task_token,
                output=output,
            )
            logger.info("Step Functions callback sent for ticket %s (approved=%s)", ticket_id, approved)
        except Exception as exc:
            logger.error("Failed to send task success for ticket %s: %s", ticket_id, exc)
            raise

    if tenant_id:
        update_ticket_fields(int(tenant_id), ticket_id, {
            "approval_task_token": None,
        })

    return {
        "message": "Approval callback processed",
        "ticketId": ticket_id,
        "approved": bool(approved),
    }
