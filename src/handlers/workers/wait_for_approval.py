import json
import logging

from src.shared.errors import ValidationError
from src.shared.tickets_store import get_tenant_ticket_or_none, update_ticket_fields

logger = logging.getLogger(__name__)

RISK_LEVEL_TO_ROLE = {
    "basic": "USER",
    "controlled": "ADMIN",
    "risky": "ADMIN",
}


def handler(event: dict, context) -> dict:
    ticket_id = event.get("ticketId")
    tenant_id = event.get("tenantId")
    max_risk_level = (event.get("maxRiskLevel") or "basic").lower()

    if not ticket_id or not tenant_id:
        raise ValidationError("ticketId and tenantId are required")

    tenant_id = int(tenant_id)

    task_token = event.get("taskToken") or event.get("task_token")
    if not task_token:
        logger.warning("No task_token found in event for ticket %s", ticket_id)
        task_token = "pending"

    required_role = RISK_LEVEL_TO_ROLE.get(max_risk_level, "USER")

    item = get_tenant_ticket_or_none(tenant_id, ticket_id)
    if item:
        pending_decision = item.get("pending_decision") or {}
        if not isinstance(pending_decision, dict):
            pending_decision = {}
        pending_decision["max_risk_level"] = max_risk_level
        pending_decision["required_approver_role"] = required_role
        update_ticket_fields(tenant_id, ticket_id, {"pending_decision": pending_decision})
        logger.info("Updated pending_decision for ticket %s: risk=%s role=%s", ticket_id, max_risk_level, required_role)

    return {
        "taskToken": task_token,
        "ticketId": ticket_id,
        "tenantId": tenant_id,
    }
