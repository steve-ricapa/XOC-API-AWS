import json
import logging

import boto3

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
