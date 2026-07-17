from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import User
from src.shared.context import require_platform_operator
from src.shared.dependencies import get_current_user
from src.shared.errors import NotFoundError
from src.xoc_ops.summary_store import get_xoc_client_by_tenant_id, get_xoc_clients_kpis, list_xoc_clients


router = APIRouter(prefix="/xoc-ops", tags=["xoc-ops"])


@router.get("/clients")
def list_clients(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_platform_operator(current_user)
    return {"clients": list_xoc_clients(session)}


@router.get("/kpis")
def get_kpis(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_platform_operator(current_user)
    return get_xoc_clients_kpis(session)


@router.get("/clients/{tenant_id}")
def get_client_by_tenant_id(tenant_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_platform_operator(current_user)
    client = get_xoc_client_by_tenant_id(session, tenant_id)
    if not client:
        raise NotFoundError("Tenant not found")
    return {"client": client}
