import logging
from datetime import datetime, timedelta, timezone

from src.shared.errors import ValidationError
from src.shared.risk_config import approval_requirement, DEFAULT_RISK_LEVEL
from src.shared.tickets_store import get_tenant_ticket_or_none, update_ticket_fields

logger = logging.getLogger(__name__)

APPROVAL_TIMEOUT_DAYS = 7


def handler(event: dict, context) -> dict:
    ticket_id = event.get("ticketId")
    tenant_id = event.get("tenantId")
    max_risk_level = (event.get("maxRiskLevel") or DEFAULT_RISK_LEVEL).lower()

    if not ticket_id or not tenant_id:
        raise ValidationError("ticketId and tenantId are required")

    tenant_id = int(tenant_id)

    task_token = event.get("taskToken") or event.get("task_token")
    if not task_token:
        raise ValidationError("taskToken not found in event")

    requirement = approval_requirement({"risk_level": max_risk_level})

    item = get_tenant_ticket_or_none(tenant_id, ticket_id)
    if item:
        now = datetime.now(timezone.utc)
        requested_at = now.isoformat()
        approval_deadline = (now + timedelta(days=APPROVAL_TIMEOUT_DAYS)).isoformat()
        pending_decision = item.get("pending_decision") or {}
        if not isinstance(pending_decision, dict):
            pending_decision = {}
        pending_decision.update({
            "max_risk_level": max_risk_level,
            "required_approver_role": requirement["required_approver_role"],
            "approver_label": requirement["approver_label"],
            "publicly_approvable": requirement["publicly_approvable"],
            "task_token": task_token,
            "requested_at": requested_at,
            "approval_deadline": approval_deadline,
        })
        update_ticket_fields(tenant_id, ticket_id, {
            "status": "PREAPROBADO",
            "execution_status": "AWAITING_APPROVAL",
            "pending_decision": pending_decision,
        })
        logger.info(
            "Updated pending_decision for ticket %s: risk=%s role=%s",
            ticket_id,
            max_risk_level,
            requirement["required_approver_role"],
        )

    return {
        "taskToken": task_token,
        "ticketId": ticket_id,
        "tenantId": tenant_id,
    }
