from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import AgentApiKey, Tenant, TenantRuntimeSettings, User
from src.shared.dependencies import get_current_user
from src.shared.encryption import encrypt_agent_key
from src.shared.errors import ForbiddenError, NotFoundError, ValidationError
from src.shared.integration_types import OFFICIAL_INTEGRATION_TYPES, normalize_integration_type
from src.shared.security_keys import generate_access_key, hash_access_key
from src.shared.schemas import TenantsListResponse, TenantResponse, RuntimeSettingsEnvelope, RuntimeSettingsResponse, UpdateTenantRequest, UpdateTenantResponse, UpsertRuntimeSettingsRequest, UpsertRuntimeSettingsResponse
from src.shared.context import get_tenant, log_audit, require_admin, require_same_tenant


router = APIRouter(prefix="/tenant", tags=["tenant"])


def _serialize_runtime_settings(runtime_settings: TenantRuntimeSettings | None) -> RuntimeSettingsResponse | None:
    if not runtime_settings:
        return None
    return RuntimeSettingsResponse(
        id=runtime_settings.id,
        tenant_id=runtime_settings.tenant_id,
        function_base_url=runtime_settings.function_base_url,
        function_route_sophia=runtime_settings.function_route_sophia,
        function_route_sophia_history=runtime_settings.function_route_sophia_history,
        function_route_sophia_delete=runtime_settings.function_route_sophia_delete,
        function_route_victor=runtime_settings.function_route_victor,
        speech_settings=runtime_settings.speech_settings,
        extra_json=runtime_settings.extra_json,
        is_active=runtime_settings.is_active,
        created_at=runtime_settings.created_at.isoformat() if runtime_settings.created_at else None,
        updated_at=runtime_settings.updated_at.isoformat() if runtime_settings.updated_at else None,
    )


def _get_agent_key_or_404(session: Session, tenant_id: int, key_id: int) -> AgentApiKey:
    agent_key = session.scalar(select(AgentApiKey).where(AgentApiKey.id == key_id, AgentApiKey.tenant_id == tenant_id))
    if not agent_key:
        raise NotFoundError("Agent API key not found")
    return agent_key


@router.get("", response_model=TenantsListResponse)
def get_tenants(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> TenantsListResponse:
    if current_user.role == "ADMIN":
        tenant = session.get(Tenant, current_user.tenant_id)
        tenants = [tenant] if tenant else []
    else:
        tenants = []
    return TenantsListResponse(tenants=[TenantResponse(**tenant.to_dict()) for tenant in tenants])


@router.put("", response_model=UpdateTenantResponse)
def update_tenant(payload: UpdateTenantRequest, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> UpdateTenantResponse:
    require_admin(current_user)
    tenant = get_tenant(session, current_user.tenant_id)
    if payload.name is not None:
        tenant.name = payload.name
    log_audit(session, actor_user_id=current_user.id, action="UPDATE", entity_type="TENANT", entity_id=tenant.id, payload={"name": tenant.name})
    session.commit()
    return UpdateTenantResponse(message="Tenant updated successfully", tenant=TenantResponse(**tenant.to_dict()))


@router.get("/agent-keys")
def list_agent_api_keys(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    tenant_id = current_user.tenant_id
    agent_keys = session.scalars(
        select(AgentApiKey).where(AgentApiKey.tenant_id == tenant_id).order_by(AgentApiKey.created_at.desc())
    ).all()
    return {"agent_keys": [key.to_dict() for key in agent_keys], "count": len(agent_keys)}


@router.post("/agent-keys", status_code=status.HTTP_201_CREATED)
def create_agent_api_key(payload: dict, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    tenant_id = current_user.tenant_id

    tenant = session.get(Tenant, tenant_id)
    if not tenant:
        raise NotFoundError("Tenant not found")
    if not payload:
        raise ValidationError("Request body is required")

    name = payload.get("name")
    integration_type_raw = payload.get("integration_type") or payload.get("integrationType")
    if not name or not str(name).strip():
        raise ValidationError('Field "name" is required')
    if not integration_type_raw:
        raise ValidationError('Field "integration_type" is required')

    integration_type = normalize_integration_type(integration_type_raw)
    if not integration_type:
        raise ValidationError(f'Invalid integration_type. Allowed: {", ".join(OFFICIAL_INTEGRATION_TYPES)}')

    trimmed_name = str(name).strip()
    existing = session.scalar(select(AgentApiKey).where(AgentApiKey.tenant_id == tenant_id, AgentApiKey.name == trimmed_name))
    if existing:
        raise ValidationError(f'An agent key with name "{trimmed_name}" already exists')

    new_key = generate_access_key(40)
    agent_key = AgentApiKey(
        tenant_id=tenant_id,
        name=trimmed_name,
        integration_type=integration_type,
        api_key_hash=hash_access_key(new_key),
        api_key_encrypted=encrypt_agent_key(new_key),
        is_active=True,
    )
    session.add(agent_key)
    session.flush()
    log_audit(session, actor_user_id=current_user.id, action="CREATE", entity_type="AGENT_API_KEY", entity_id=agent_key.id, payload={"name": trimmed_name, "integration_type": integration_type})
    session.commit()
    return {"message": "Agent API key created successfully", "agent_key": {**agent_key.to_dict(), "api_key": new_key}}


@router.get("/agent-keys/{key_id}")
def get_agent_api_key(key_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    return _get_agent_key_or_404(session, current_user.tenant_id, key_id).to_dict()


@router.delete("/agent-keys/{key_id}")
def delete_agent_api_key(key_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    agent_key = _get_agent_key_or_404(session, current_user.tenant_id, key_id)
    log_audit(session, actor_user_id=current_user.id, action="DELETE", entity_type="AGENT_API_KEY", entity_id=key_id, payload={"name": agent_key.name})
    session.delete(agent_key)
    session.commit()
    return {"message": "Agent API key deleted successfully"}


@router.post("/agent-keys/{key_id}/regenerate")
def regenerate_agent_api_key(key_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    agent_key = _get_agent_key_or_404(session, current_user.tenant_id, key_id)
    new_key = generate_access_key(40)
    agent_key.api_key_hash = hash_access_key(new_key)
    agent_key.api_key_encrypted = encrypt_agent_key(new_key)
    log_audit(session, actor_user_id=current_user.id, action="REGENERATE", entity_type="AGENT_API_KEY", entity_id=key_id, payload={"name": agent_key.name})
    session.commit()
    return {"message": "Agent API key regenerated successfully", "agent_key": {**agent_key.to_dict(), "api_key": new_key}}


@router.post("/agent-keys/{key_id}/toggle")
def toggle_agent_api_key(key_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    agent_key = _get_agent_key_or_404(session, current_user.tenant_id, key_id)
    agent_key.is_active = not agent_key.is_active
    status = "activated" if agent_key.is_active else "deactivated"
    log_audit(session, actor_user_id=current_user.id, action="TOGGLE", entity_type="AGENT_API_KEY", entity_id=key_id, payload={"name": agent_key.name, "is_active": agent_key.is_active})
    session.commit()
    return {"message": f"Agent API key {status} successfully", "agent_key": agent_key.to_dict()}


@router.get("/runtime-settings", response_model=RuntimeSettingsEnvelope)
def get_runtime_settings(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> RuntimeSettingsEnvelope:
    require_admin(current_user)
    runtime_settings = session.scalar(select(TenantRuntimeSettings).where(TenantRuntimeSettings.tenant_id == current_user.tenant_id))
    return RuntimeSettingsEnvelope(runtime_settings=_serialize_runtime_settings(runtime_settings))


@router.put("/runtime-settings", response_model=UpsertRuntimeSettingsResponse)
def upsert_runtime_settings(payload: UpsertRuntimeSettingsRequest, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> UpsertRuntimeSettingsResponse:
    require_admin(current_user)

    data = payload.model_dump(by_alias=False, exclude_none=True)
    function_base_url = (data.get("function_base_url") or "").strip()
    function_route_sophia = (data.get("function_route_sophia") or "/api/agents/SophiaDurableAgent/run").strip()
    function_route_sophia_history = (data.get("function_route_sophia_history") or "/api/agents/SophiaDurableAgent/history").strip()
    function_route_sophia_delete = (data.get("function_route_sophia_delete") or "/api/agents/SophiaDurableAgent/threads").strip()
    function_route_victor = (data.get("function_route_victor") or "/api/agents/VictorDurableAgent/run").strip()
    is_active = data.get("is_active", True)

    if not function_base_url:
        raise ValidationError("function_base_url is required")

    runtime_settings = session.scalar(select(TenantRuntimeSettings).where(TenantRuntimeSettings.tenant_id == current_user.tenant_id))
    created = False
    if not runtime_settings:
        runtime_settings = TenantRuntimeSettings(
            tenant_id=current_user.tenant_id,
            function_base_url=function_base_url,
            function_route_sophia=function_route_sophia,
            function_route_sophia_history=function_route_sophia_history,
            function_route_sophia_delete=function_route_sophia_delete,
            function_route_victor=function_route_victor,
            is_active=bool(is_active),
        )
        session.add(runtime_settings)
        created = True

    runtime_settings.function_base_url = function_base_url
    runtime_settings.function_route_sophia = function_route_sophia
    runtime_settings.function_route_sophia_history = function_route_sophia_history
    runtime_settings.function_route_sophia_delete = function_route_sophia_delete
    runtime_settings.function_route_victor = function_route_victor
    runtime_settings.is_active = bool(is_active)
    if isinstance(data.get("speech_settings"), dict):
        runtime_settings.speech_settings = data.get("speech_settings")
    if isinstance(data.get("extra_json"), dict):
        runtime_settings.extra_json = data.get("extra_json")

    session.flush()
    log_audit(session, actor_user_id=current_user.id, action="CREATE" if created else "UPDATE", entity_type="TENANT_RUNTIME_SETTINGS", entity_id=runtime_settings.id, payload={"tenant_id": current_user.tenant_id, "is_active": runtime_settings.is_active})
    session.commit()
    return UpsertRuntimeSettingsResponse(message="Runtime settings saved successfully", runtime_settings=_serialize_runtime_settings(runtime_settings))
