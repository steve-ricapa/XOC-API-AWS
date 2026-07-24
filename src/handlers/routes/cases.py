import logging

from fastapi import APIRouter, Depends

from src.shared.cases_store import get_case_by_ticket, get_case_or_404, list_tenant_cases
from src.shared.dependencies import require_access_claims
from src.shared.errors import ValidationError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cases", tags=["cases"])


@router.get("")
def list_cases(
    claims: dict = Depends(require_access_claims),
    status: str | None = None,
    limit: int = 50,
):
    tenant_id = claims.get("tenantId") or claims.get("tenant_id")
    if not tenant_id:
        raise ValidationError("tenant_id not found in request context")
    return list_tenant_cases(int(tenant_id), status=status, limit=limit)


@router.get("/ticket/{ticket_id}")
def get_case_by_ticket_endpoint(
    ticket_id: str,
    claims: dict = Depends(require_access_claims),
):
    case = get_case_by_ticket(ticket_id)
    if not case:
        from src.shared.errors import NotFoundError
        raise NotFoundError("No case found for this ticket")
    return case


@router.get("/{case_id}")
def get_case(
    case_id: str,
    claims: dict = Depends(require_access_claims),
):
    tenant_id = int(claims.get("tenantId") or claims.get("tenant_id") or 0)
    return get_case_or_404(tenant_id, case_id)
