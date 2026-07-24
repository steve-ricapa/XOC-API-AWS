import logging

from src.shared.cases_store import create_case
from src.shared.errors import ValidationError

logger = logging.getLogger(__name__)


def handler(event: dict, context) -> dict:
    ticket_id = event.get("ticket_id") or event.get("ticketId")
    tenant_id = event.get("tenant_id") or event.get("tenantId")
    subject = event.get("subject", "")
    action = event.get("action", "success")
    total_attempts = event.get("total_attempts", 0)
    solution_applied = event.get("solution_applied")
    plan_used = event.get("plan_used")
    attempts_log = event.get("attempts_log")
    similar_case_id = event.get("similar_case_id") or event.get("similarCaseId")

    if not ticket_id or not tenant_id:
        raise ValidationError("ticket_id and tenant_id are required")
    if action not in ("success", "failed_after_attempts"):
        raise ValidationError("action must be 'success' or 'failed_after_attempts'")

    item = create_case(
        tenant_id=int(tenant_id),
        ticket_id=ticket_id,
        subject=subject,
        action=action,
        total_attempts=int(total_attempts),
        solution_applied=solution_applied,
        plan_used=plan_used,
        attempts_log=attempts_log,
        similar_case_id=similar_case_id,
    )

    logger.info("Case created: %s for ticket %s (action=%s)", item["case_id"], ticket_id, action)

    return {
        "caseId": item["case_id"],
        "status": item["status"],
        "createdAt": item["created_at"],
    }
