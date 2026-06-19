from datetime import timedelta

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import CompanyRuntimeSettings, User
from src.shared.auth import create_access_token
from src.shared.capabilities import collect_automation_capabilities
from src.shared.context import log_audit
from src.shared.dependencies import get_current_user
from src.shared.errors import AppError, UnauthorizedError, ValidationError


router = APIRouter(prefix="/api/agents", tags=["agents"])


RUNTIME_SETTINGS_MISSING_MESSAGE = "Runtime settings not configured for this company"


def get_company_runtime_settings(session: Session, company_id: int) -> CompanyRuntimeSettings | None:
    return session.scalar(
        select(CompanyRuntimeSettings).where(
            CompanyRuntimeSettings.company_id == company_id,
            CompanyRuntimeSettings.is_active == True,
        )
    )


@router.post("/auth/token")
def authenticate_agent_legacy() -> None:
    raise AppError(
        "Legacy API-key agent auth is disabled. Use /api/agents/auth/token-from-user or backend-orchestrated service tokens.",
        status_code=status.HTTP_410_GONE,
        code="LEGACY_AGENT_AUTH_DISABLED",
    )


@router.post("/auth/token-from-user")
def authenticate_agent_from_user(
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    agent_type = (payload or {}).get("agentType", "SOPHIA")

    runtime_settings = get_company_runtime_settings(session, current_user.company_id)
    if not runtime_settings:
        raise UnauthorizedError(RUNTIME_SETTINGS_MISSING_MESSAGE)

    additional_claims = {
        "scopes": ["agent:invoke"],
        "company_id": current_user.company_id,
        "agent_type": agent_type,
    }

    service_token = create_access_token(
        identity=f"agent-runtime-{current_user.company_id}-{str(agent_type).upper()}",
        additional_claims=additional_claims,
        expires_delta=timedelta(hours=1),
    )

    capabilities = collect_automation_capabilities(session, current_user.company_id)
    user_name = current_user.username or current_user.email
    user_company = getattr(current_user, "company", None)
    if not user_company:
        from src.persistence.models import Company as CompanyModel
        user_company = session.get(CompanyModel, current_user.company_id)
    plan_status = (user_company.plan_status or "").strip().upper() if user_company else ""

    log_audit(session, actor_user_id=current_user.id, action="AUTH", entity_type="AGENT_TOKEN", entity_id=None, payload={"company_id": current_user.company_id, "agent_type": agent_type, "source": "user_exchange"})
    session.commit()

    return {
        "access_token": service_token,
        "token_type": "Bearer",
        "expires_in": 3600,
        "company_id": current_user.company_id,
        "user_name": user_name,
        "role": current_user.role,
        "plan_status": plan_status,
        "capabilities": capabilities,
    }


@router.get("/instance/{instance_id}")
def get_agent_instance_metadata(instance_id: str) -> None:
    raise AppError(
        "Agent instance metadata endpoint is deprecated in runtime-settings mode.",
        status_code=status.HTTP_410_GONE,
        code="ENDPOINT_DEPRECATED",
    )
