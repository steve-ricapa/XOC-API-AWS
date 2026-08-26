import logging

from src.shared.cases_store import create_case
from src.shared.errors import ValidationError

logger = logging.getLogger(__name__)


def _update_ticket_status(tenant_id: int, ticket_id: str, status: str, execution_status: str, summary: str | None = None):
    try:
        from src.shared.tickets_store import update_ticket_fields
        fields = {"status": status, "execution_status": execution_status}
        if summary:
            fields["execution_summary"] = summary
        update_ticket_fields(tenant_id, ticket_id, fields)
        logger.info("Ticket %s updated to %s (execution_status=%s)", ticket_id, status, execution_status)
    except Exception as exc:
        logger.error("Failed to update ticket %s to %s: %s", ticket_id, status, exc)


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
    if action not in ("success", "failed_after_attempts", "rejected", "derivado"):
        raise ValidationError("action must be 'success', 'failed_after_attempts', 'rejected' or 'derivado'")

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

    if action == "success":
        _update_ticket_status(int(tenant_id), ticket_id, "RESUELTO", "EXECUTED", solution_applied)
    elif action == "failed_after_attempts":
        _update_ticket_status(int(tenant_id), ticket_id, "FALLIDO", "FAILED", f"Failed after {total_attempts} attempts")

    return {
        "caseId": item["case_id"],
        "status": item["status"],
        "createdAt": item["created_at"],
    }
