import json
import logging

import boto3

from src.shared.errors import ValidationError
from src.shared.tickets_store import get_ticket_by_id_or_none, resolve_executable_plan

logger = logging.getLogger(__name__)

stepfunctions = boto3.client("stepfunctions")


def handler(event: dict, context) -> dict:
    ticket_id = event.get("ticketId")
    task_token = event.get("taskToken")
    approved = event.get("approved", False)

    if not ticket_id:
        raise ValidationError("ticketId is required")

    tenant_id = event.get("tenantId")
    ticket = get_ticket_by_id_or_none(ticket_id)
    if not tenant_id and ticket:
        tenant_id = ticket.get("tenant_id")

    selected_plan = None
    if approved:
        selected_plan = resolve_executable_plan(ticket)
        if selected_plan is None:
            raise ValidationError("No action plan is available to execute for this ticket")

    if task_token and task_token != "pending":
        output = {"approved": bool(approved)}
        if approved and selected_plan is not None:
            output["selected_plan"] = selected_plan
        try:
            stepfunctions.send_task_success(
                taskToken=task_token,
                output=json.dumps(output),
            )
            logger.info("Step Functions callback sent for ticket %s (approved=%s)", ticket_id, approved)
        except Exception as exc:
            logger.error("Failed to send task success for ticket %s: %s", ticket_id, exc)
            raise

    return {
        "message": "Approval callback processed",
        "ticketId": ticket_id,
        "approved": bool(approved),
    }
