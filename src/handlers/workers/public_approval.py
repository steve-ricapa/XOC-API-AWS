import json
import logging

import boto3

from src.shared.tickets_store import get_ticket_by_id_or_none

logger = logging.getLogger(__name__)
stepfunctions = boto3.client("stepfunctions")


def handler(event: dict, context) -> dict:
    body = {}
    if isinstance(event.get("body"), str):
        body = json.loads(event["body"])
    else:
        body = event.get("body", event)

    ticket_id = body.get("ticketId") or body.get("ticket_id")
    approved = body.get("approved", True)
    task_token = body.get("taskToken") or body.get("task_token")

    if not ticket_id:
        return {"statusCode": 400, "body": json.dumps({"error": "ticketId required"})}

    ticket = get_ticket_by_id_or_none(ticket_id)
    if ticket:
        pending_decision = ticket.get("pending_decision") or {}
        required_role = pending_decision.get("required_approver_role", "USER")
        if required_role.upper() != "USER":
            return {"statusCode": 403, "body": json.dumps({"error": "Public approval not allowed for this risk level"})}

    if task_token:
        try:
            stepfunctions.send_task_success(
                taskToken=task_token,
                output=json.dumps({"approved": bool(approved)}),
            )
            logger.info("Task success sent for ticket %s (approved=%s)", ticket_id, approved)
        except Exception as e:
            logger.error("Failed to send task success: %s", e)
            return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Approval processed",
            "ticketId": ticket_id,
            "approved": bool(approved),
        }),
    }
