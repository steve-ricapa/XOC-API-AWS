from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import User
from src.shared.context import effective_tenant_id_of, require_tenant_read_access
from src.shared.dependencies import get_current_user
from src.shared.preventions_issues_store import get_preventions_issues_overview, list_preventions_issues


router = APIRouter(prefix="/preventions/issues", tags=["preventions-issues"])


@router.get("/overview")
def get_overview(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_tenant_read_access(current_user)
    return get_preventions_issues_overview(session, effective_tenant_id_of(current_user))


@router.get("")
def list_all_issues(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    search: str | None = None,
    severity: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    host: str | None = None,
    service: str | None = None,
    limit: int = 100,
    offset: int = 0,
    snapshot_mode: str | None = None,
) -> dict:
    require_tenant_read_access(current_user)
    return list_preventions_issues(
        session,
        effective_tenant_id_of(current_user),
        tab="all",
        search=search,
        severity=severity,
        provider=provider,
        status=status,
        host=host,
        service=service,
        limit=limit,
        offset=offset,
        snapshot_mode=snapshot_mode,
    )


@router.get("/vulnerabilities")
def list_vulnerabilities(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    search: str | None = None,
    severity: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    host: str | None = None,
    limit: int = 100,
    offset: int = 0,
    snapshot_mode: str | None = None,
) -> dict:
    require_tenant_read_access(current_user)
    return list_preventions_issues(
        session,
        effective_tenant_id_of(current_user),
        tab="vulnerabilities",
        search=search,
        severity=severity,
        provider=provider,
        status=status,
        host=host,
        limit=limit,
        offset=offset,
        snapshot_mode=snapshot_mode,
    )


@router.get("/security-events")
def list_security_events(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    search: str | None = None,
    severity: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    host: str | None = None,
    limit: int = 100,
    offset: int = 0,
    snapshot_mode: str | None = None,
) -> dict:
    require_tenant_read_access(current_user)
    return list_preventions_issues(
        session,
        effective_tenant_id_of(current_user),
        tab="security-events",
        search=search,
        severity=severity,
        provider=provider,
        status=status,
        host=host,
        limit=limit,
        offset=offset,
        snapshot_mode=snapshot_mode,
    )


@router.get("/monitoring")
def list_monitoring_issues(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    search: str | None = None,
    severity: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    host: str | None = None,
    service: str | None = None,
    limit: int = 100,
    offset: int = 0,
    snapshot_mode: str | None = None,
) -> dict:
    require_tenant_read_access(current_user)
    return list_preventions_issues(
        session,
        effective_tenant_id_of(current_user),
        tab="monitoring",
        search=search,
        severity=severity,
        provider=provider,
        status=status,
        host=host,
        service=service,
        limit=limit,
        offset=offset,
        snapshot_mode=snapshot_mode,
    )
