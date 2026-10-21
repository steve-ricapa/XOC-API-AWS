import logging

from src.shared.cases_store import create_case
from src.shared.errors import ValidationError
from src.shared.tickets_store import get_tenant_ticket_or_none, update_ticket_fields
from src.notifications.tickets import publish_ticket_status_notification

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

    final_status = {
        "success": "RESUELTO",
        "failed_after_attempts": "FALLIDO",
        "rejected": "RECHAZADO",
    }.get(action)
    if final_status:
        ticket = get_tenant_ticket_or_none(int(tenant_id), ticket_id)
        # Keep the DynamoDB ticket detail aligned with the terminal Case
        # result. This does not access or modify RDS.
        if ticket and ticket.get("status") != final_status:
            fields = {
                "status": final_status,
                "execution_status": "EXECUTED" if final_status == "RESUELTO" else "FAILED",
            }
            if action == "success" and solution_applied:
                fields["execution_summary"] = solution_applied
            elif action == "failed_after_attempts":
                # Se conserva el detalle de la ejecucion fallida (pasos, causa)
                # en vez de Replace el summary con un mensaje generico.
                previous_summary = ticket.get("execution_summary")
                detail = str(previous_summary).strip() if previous_summary else ""
                base = f"Failed after {total_attempts} attempts"
                fields["execution_summary"] = f"{base}: {detail}"[:500] if detail else base
            update_ticket_fields(int(tenant_id), ticket_id, fields)
        # The F3 inbox uses a deterministic key, so an already-published
        # RESUELTO/RECHAZADO event is a no-op for duplicate deliveries.
        publish_ticket_status_notification(
            tenant_id=int(tenant_id),
            ticket_id=ticket_id,
            status=final_status,
        )

    logger.info("Case created: %s for ticket %s (action=%s)", item["case_id"], ticket_id, action)

    return {
        "caseId": item["case_id"],
        "status": item["status"],
        "createdAt": item["created_at"],
    }
