from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import Alert, User
from src.shared.context import log_audit
from src.shared.dependencies import get_current_user
from src.shared.errors import NotFoundError, ValidationError


router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/active")
def get_active_alerts(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    since: str | None = None,
    limit: int = 100,
) -> dict:
    query = select(Alert).where(Alert.tenant_id == current_user.tenant_id, Alert.status == "active")
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except Exception as exc:
            raise ValidationError("Invalid since timestamp format") from exc
        query = query.where(Alert.created_at >= since_dt)
    query = query.order_by(Alert.created_at.desc()).limit(limit)
    alerts = session.scalars(query).all()
    log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="ACTIVE_ALERTS", payload={"count": len(alerts)})
    session.commit()
    return {"alerts": [alert.to_dict() for alert in alerts]}


@router.post("/{alert_id}/resolve")
def resolve_alert(alert_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    alert = session.get(Alert, alert_id)
    if not alert or alert.tenant_id != current_user.tenant_id:
        raise NotFoundError("Alert not found")
    if alert.status == "resolved":
        raise ValidationError("Alert is already resolved")
    alert.status = "resolved"
    alert.resolved_at = datetime.utcnow()
    alert.resolved_by_user_id = current_user.id
    log_audit(session, actor_user_id=current_user.id, action="RESOLVE", entity_type="ALERT", entity_id=alert.id, payload={"title": alert.title})
    session.commit()
    return {"message": "Alert resolved successfully", "alert": alert.to_dict()}
