import logging

from src.shared.cases_store import search_similar_cases
from src.shared.errors import ValidationError

logger = logging.getLogger(__name__)


def handler(event: dict, context) -> dict:
    ticket_id = event.get("ticketId")
    tenant_id = event.get("tenantId")
    subject = event.get("subject", "")

    if not ticket_id or not tenant_id:
        raise ValidationError("ticketId and tenantId are required")

    similar = search_similar_cases(int(tenant_id), subject)

    if similar:
        return {
            "similarCaseFound": True,
            "similarCase": {
                "case_id": similar.get("case_id"),
                "subject": similar.get("subject"),
                "plan_used": similar.get("plan_used"),
                "solution_applied": similar.get("solution_applied"),
                "status": similar.get("status"),
            },
            "ticketId": ticket_id,
            "tenantId": int(tenant_id),
        }

    return {
        "similarCaseFound": False,
        "similarCase": None,
        "ticketId": ticket_id,
        "tenantId": int(tenant_id),
    }
