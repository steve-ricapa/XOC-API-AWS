from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import AuditLog
from src.shared.dependencies import get_current_user_optional
from src.shared.errors import ValidationError


router = APIRouter(prefix="/api", tags=["audit"])


@router.post("/audit", status_code=status.HTTP_201_CREATED)
def create_audit_log(
    payload: dict,
    current_user=Depends(get_current_user_optional),
    session: Session = Depends(get_db_session),
) -> dict:
    if not payload:
        raise ValidationError("Request body is required")

    action = payload.get("action")
    entity_type = payload.get("entity_type")
    entity_id = payload.get("entity_id")
    extra_payload = payload.get("payload", {})

    if not action:
        raise ValidationError("action is required")
    if not entity_type:
        raise ValidationError("entity_type is required")
    if extra_payload is not None and not isinstance(extra_payload, dict):
        raise ValidationError("payload must be a dictionary")

    actor_user_id = current_user.id if current_user else None

    audit = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        payload=extra_payload,
    )
    session.add(audit)
    session.commit()

    return {
        "message": "Audit log created successfully",
        "audit_log": audit.to_dict(),
    }
