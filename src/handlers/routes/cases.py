import logging

from fastapi import APIRouter, Depends

from src.shared.cases_store import get_case_by_ticket, get_case_or_404, list_tenant_cases
from src.shared.dependencies import get_current_tenant_id, require_access_claims
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
    # API Gateway projects the effective tenant as tenantId. Local JWT auth
    # supplies verified raw claims instead; honor its explicit delegation only.
    tenant_claims = claims
    if not claims.get("tenantId") and claims.get("delegation") in (True, "true", "True", "1", 1):
        acting_tenant_id = claims.get("actingTenantId") or claims.get("acting_tenant_id")
        if acting_tenant_id:
            tenant_claims = {**claims, "tenantId": acting_tenant_id}
    tenant_id = get_current_tenant_id(tenant_claims)
    case = get_case_by_ticket(ticket_id)
    # TicketIndex is global: never return a result before checking ownership.
    # DynamoDB stores tenant_id as a Number (int/Decimal).
    if not case or case.get("tenant_id") != tenant_id:
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
