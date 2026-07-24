import logging

from src.shared.errors import ValidationError
from src.shared.tickets_store import get_tenant_ticket_or_404

logger = logging.getLogger(__name__)


def handler(event: dict, context) -> dict:
    ticket_id = event.get("ticketId")
    tenant_id = event.get("tenantId")

    if not ticket_id or not tenant_id:
        raise ValidationError("ticketId and tenantId are required")

    item = get_tenant_ticket_or_404(int(tenant_id), ticket_id)
    current_status = item.get("status", "")
    execution_summary = item.get("execution_summary")

    if current_status == "RESUELTO":
        resolution_status = "resolved"
    elif current_status in ("FALLIDO", "FAILED"):
        resolution_status = "failed"
    else:
        resolution_status = "pending"

    return {
        "resolutionStatus": resolution_status,
        "currentStatus": current_status,
        "solutionApplied": execution_summary,
        "ticketId": ticket_id,
        "tenantId": int(tenant_id),
    }
