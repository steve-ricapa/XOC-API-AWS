import logging

from src.shared.cases_store import create_case
from src.shared.errors import ValidationError
from src.shared.tickets_store import update_ticket_fields

logger = logging.getLogger(__name__)


def handler(event: dict, context) -> dict:
    ticket_id = event.get("ticketId") or event.get("ticket_id")
    tenant_id = event.get("tenantId") or event.get("tenant_id")
    subject = event.get("subject", "")

    if not ticket_id or not tenant_id:
        raise ValidationError("ticketId and tenantId are required")

    tenant_id = int(tenant_id)

    update_ticket_fields(tenant_id, ticket_id, {"status": "DERIVADO", "execution_status": "TIMED_OUT"})

    item = create_case(
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        subject=subject,
        action="derivado",
        total_attempts=1,
        solution_applied=None,
        plan_used=None,
        attempts_log=None,
        similar_case_id=None,
    )

    logger.info("Timeout case created: %s for ticket %s", item["case_id"], ticket_id)

    return {
        "caseId": item["case_id"],
        "status": item["status"],
        "createdAt": item["created_at"],
    }
