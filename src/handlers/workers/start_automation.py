import json
import logging
import os

import boto3

from src.shared.tickets_store import get_tenant_ticket_or_none

logger = logging.getLogger(__name__)
stepfunctions = boto3.client("stepfunctions")
sts = boto3.client("sts")


def _automation_workflow_arn() -> str:
    stage = os.environ.get("APP_STAGE", "prod")
    region = os.environ.get("APP_REGION", "us-east-1")
    account_id = sts.get_caller_identity()["Account"]
    return f"arn:aws:states:{region}:{account_id}:stateMachine:xoc-api-automation-{stage}-workflow"


def handler(event: dict, context) -> dict:
    ticket_id = event.get("ticketId")
    tenant_id = event.get("tenantId")
    subject = event.get("subject", "")
    event_type = event.get("eventType", "")

    if not ticket_id or not tenant_id:
        logger.warning("Missing ticketId or tenantId, skipping automation")
        return {"status": "skipped", "reason": "missing_required_fields"}

    tenant_id = int(tenant_id)

    if event_type and event_type != "ticket.created":
        logger.info("Skipping automation for event type: %s", event_type)
        return {"status": "skipped", "reason": f"unsupported_event_{event_type}"}

    ticket = get_tenant_ticket_or_none(tenant_id, ticket_id)
    description = (ticket or {}).get("description", "")

    automation_arn = os.environ.get("AUTOMATION_WORKFLOW_ARN", "") or _automation_workflow_arn()

    payload = {
        "input": {
            "ticketId": ticket_id,
            "tenantId": tenant_id,
            "subject": subject,
            "description": description,
        }
    }

    try:
        response = stepfunctions.start_execution(
            stateMachineArn=automation_arn,
            name=f"ticket-{ticket_id[:30]}",
            input=json.dumps(payload),
        )
        logger.info("Started automation workflow for ticket %s: %s", ticket_id, response["executionArn"])
        return {"status": "started", "executionArn": response["executionArn"]}
    except Exception as e:
        logger.error("Failed to start automation workflow for ticket %s: %s", ticket_id, e)
        return {"status": "error", "reason": "Failed to start automation workflow"}
