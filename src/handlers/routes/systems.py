from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import System, User
from src.shared.context import log_audit
from src.shared.dependencies import get_current_user
from src.shared.errors import NotFoundError


router = APIRouter(prefix="/api/systems", tags=["systems"])


@router.get("/status")
def get_systems_status(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    systems = session.scalars(select(System).where(System.company_id == current_user.company_id)).all()
    systems_with_health = [system for system in systems if system.health_score is not None]
    avg_health = sum(system.health_score for system in systems_with_health) / len(systems_with_health) if systems_with_health else 0
    status_summary = {
        "total_systems": len(systems),
        "online": len([system for system in systems if system.status == "online"]),
        "offline": len([system for system in systems if system.status == "offline"]),
        "degraded": len([system for system in systems if system.status == "degraded"]),
        "unknown": len([system for system in systems if system.status == "unknown"]),
        "average_health_score": round(avg_health, 2),
    }
    log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="SYSTEMS_STATUS", payload=status_summary)
    session.commit()
    return status_summary


@router.get("")
def get_systems(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    systems = session.scalars(select(System).where(System.company_id == current_user.company_id)).all()
    log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="SYSTEMS")
    session.commit()
    return {"systems": [system.to_dict() for system in systems]}


@router.get("/{system_id}")
def get_system(system_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    system = session.get(System, system_id)
    if not system or system.company_id != current_user.company_id:
        raise NotFoundError("System not found")
    return system.to_dict()
