from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import User, Vulnerability
from src.shared.context import effective_tenant_id_of, log_audit, require_admin, require_tenant_read_access
from src.shared.dependencies import get_current_user
from src.shared.errors import NotFoundError, ValidationError


router = APIRouter(prefix="/vulnerabilities", tags=["vulnerabilities"])


@router.get("")
def get_vulnerabilities(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    status: str | None = None,
    severity: str | None = None,
) -> dict:
    require_tenant_read_access(current_user)
    query = select(Vulnerability).where(Vulnerability.tenant_id == effective_tenant_id_of(current_user))
    if status:
        query = query.where(Vulnerability.status == status)
    if severity:
        query = query.where(Vulnerability.severity == severity)
    query = query.order_by(Vulnerability.created_at.desc())
    vulnerabilities = session.scalars(query).all()
    log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="VULNERABILITIES", payload={"count": len(vulnerabilities)})
    session.commit()
    return {"vulnerabilities": [vulnerability.to_dict() for vulnerability in vulnerabilities]}


@router.get("/{vuln_id}")
def get_vulnerability(vuln_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_tenant_read_access(current_user)
    vulnerability = session.get(Vulnerability, vuln_id)
    if not vulnerability or vulnerability.tenant_id != effective_tenant_id_of(current_user):
        raise NotFoundError("Vulnerability not found")
    return vulnerability.to_dict()


@router.post("/{vuln_id}/patch")
def patch_vulnerability(vuln_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    vulnerability = session.get(Vulnerability, vuln_id)
    if not vulnerability or vulnerability.tenant_id != effective_tenant_id_of(current_user):
        raise NotFoundError("Vulnerability not found")
    if vulnerability.status == "resolved":
        raise ValidationError("Vulnerability is already resolved")
    vulnerability.status = "patching"
    vulnerability.patch_status = "initiated"
    log_audit(session, actor_user_id=current_user.id, action="PATCH", entity_type="VULNERABILITY", entity_id=vulnerability.id, payload={"cve_id": vulnerability.cve_id})
    session.commit()
    return {"message": "Patch process initiated", "vulnerability": vulnerability.to_dict()}
