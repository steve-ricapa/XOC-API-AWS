from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.integrations.dashboard_store import build_home_dashboard, build_provider_dashboard
from src.persistence.db import get_db_session
from src.persistence.models import User
from src.shared.context import effective_tenant_id_of, require_tenant_read_access
from src.shared.dependencies import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/home")
def get_home_dashboard(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_tenant_read_access(current_user)
    return build_home_dashboard(session, effective_tenant_id_of(current_user))


@router.get("/providers/{provider}")
def get_provider_dashboard(
    provider: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    preset: str | None = Query(default=None),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
) -> dict:
    require_tenant_read_access(current_user)
    return build_provider_dashboard(session, effective_tenant_id_of(current_user), provider, preset, from_date, to_date)
