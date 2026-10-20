import json
import logging

import boto3

from src.shared.risk_config import is_publicly_approvable
from src.shared.tickets_store import get_ticket_by_id_or_none, resolve_executable_plan

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
        max_risk_level = (pending_decision.get("max_risk_level") or "basic").lower()
        if not is_publicly_approvable(max_risk_level):
            return {"statusCode": 403, "body": json.dumps({"error": "Public approval not allowed for this risk level"})}

    selected_plan = None
    if approved:
        selected_plan = resolve_executable_plan(ticket)
        if selected_plan is None:
            return {"statusCode": 400, "body": json.dumps({"error": "No action plan is available to execute for this ticket"})}

    if task_token:
        try:
            output = {"approved": bool(approved)}
            if approved and selected_plan is not None:
                output["selected_plan"] = selected_plan
            stepfunctions.send_task_success(
                taskToken=task_token,
                output=json.dumps(output),
            )
            logger.info("Task success sent for ticket %s (approved=%s)", ticket_id, approved)
        except Exception as e:
            logger.error("Failed to send task success for ticket %s: %s", ticket_id, e)
            return {"statusCode": 500, "body": json.dumps({"error": "Internal error processing approval"})}

    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Approval processed",
            "ticketId": ticket_id,
            "approved": bool(approved),
        }),
    }
