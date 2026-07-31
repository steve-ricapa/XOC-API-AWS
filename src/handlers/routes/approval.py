import logging

from fastapi import APIRouter, Depends

from src.handlers.workers.approval_callback import handler as approval_callback_handler
from src.shared.dependencies import require_access_claims
from src.shared.errors import ForbiddenError, ValidationError
from src.shared.risk_config import is_role_sufficient
from src.shared.tickets_store import get_ticket_by_id_or_none

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/automation", tags=["automation"])


@router.post("/approval/callback")
def approval_callback(
    payload: dict,
    claims: dict = Depends(require_access_claims),
):
    task_token = payload.get("taskToken")
    approved = payload.get("approved")
    ticket_id = payload.get("ticketId")

    if not task_token:
        raise ValidationError("taskToken is required")
    if approved is None:
        raise ValidationError("approved is required")
    if not ticket_id:
        raise ValidationError("ticketId is required")

    user_role = (claims.get("role") or "").upper()
    ticket = get_ticket_by_id_or_none(ticket_id)
    if ticket:
        pending_decision = ticket.get("pending_decision") or {}
        required_role = pending_decision.get("required_approver_role", "USER")
        if not is_role_sufficient(user_role, required_role):
            raise ForbiddenError("Insufficient role to approve this decision")

    tenant_id = ticket.get("tenant_id") if ticket else None
    result = approval_callback_handler({
        "taskToken": task_token,
        "approved": approved,
        "ticketId": ticket_id,
        "tenantId": tenant_id,
    }, None)

    return result
