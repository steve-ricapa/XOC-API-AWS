import logging

from fastapi import APIRouter, Depends

from src.handlers.workers.approval_callback import handler as approval_callback_handler
from src.shared.dependencies import require_access_claims
from src.shared.errors import ValidationError

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

    result = approval_callback_handler({
        "taskToken": task_token,
        "approved": approved,
        "ticketId": ticket_id,
    }, None)

    return result
