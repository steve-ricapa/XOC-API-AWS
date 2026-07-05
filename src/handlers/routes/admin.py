from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import AgentInstance, User
from src.shared.context import log_audit, require_admin
from src.shared.dependencies import get_current_user
from src.shared.encryption import encrypt_agent_key
from src.shared.errors import NotFoundError, ValidationError, AppError
from src.shared.security_keys import generate_access_key, hash_access_key


router = APIRouter(prefix="/admin", tags=["admin"])


def _get_instance_or_404(session: Session, user: User, instance_id: str) -> AgentInstance:
    instance = session.get(AgentInstance, instance_id)
    if not instance or instance.tenant_id != user.tenant_id:
        raise NotFoundError("Agent instance not found")
    return instance


@router.post("/agent-instances", status_code=status.HTTP_201_CREATED)
def create_agent_instance(payload: dict, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    agent_type = payload.get("agentType", "SVAFUNC")
    function_base_url = payload.get("functionBaseUrl")
    function_route = payload.get("functionRoute")
    function_key = payload.get("functionKey")

    if not function_base_url:
        raise ValidationError("functionBaseUrl is required")
    if not function_route:
        raise ValidationError("functionRoute is required")
    if not function_key:
        raise ValidationError("functionKey is required")

    access_key = generate_access_key(40)
    access_key_hash = hash_access_key(access_key)
    access_key_encrypted = encrypt_agent_key(access_key)
    function_key_encrypted = encrypt_agent_key(function_key)

    settings = {
        "function_base_url": function_base_url,
        "function_route": function_route,
        "function_key_encrypted": function_key_encrypted,
    }

    instance = AgentInstance(
        tenant_id=current_user.tenant_id,
        agent_type=agent_type,
        client_access_key_hash=access_key_hash,
        client_access_key_encrypted=access_key_encrypted,
        settings=settings,
    )
    session.add(instance)
    session.flush()
    log_audit(session, actor_user_id=current_user.id, action="CREATE", entity_type="AGENT_INSTANCE", entity_id=instance.id, payload={"agent_type": agent_type})
    session.commit()
    return {"message": "Agent instance created", "agent_instance": instance.to_dict(), "agent_access_key": access_key}


@router.get("/agent-instances")
def list_agent_instances(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    instances = session.query(AgentInstance).filter_by(tenant_id=current_user.tenant_id).all()
    log_audit(session, actor_user_id=current_user.id, action="LIST", entity_type="AGENT_INSTANCE", entity_id=None, payload={"count": len(instances)})
    return {"agent_instances": [instance.to_dict() for instance in instances]}


@router.get("/agent-instances/{instance_id}")
def get_agent_instance(instance_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    instance = _get_instance_or_404(session, current_user, instance_id)
    log_audit(session, actor_user_id=current_user.id, action="READ", entity_type="AGENT_INSTANCE", entity_id=instance.id, payload={})
    return instance.to_dict()


@router.patch("/agent-instances/{instance_id}")
def update_agent_instance(instance_id: str, payload: dict, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    instance = _get_instance_or_404(session, current_user, instance_id)

    if "status" in payload:
        raise ValidationError("Status cannot be updated through this endpoint. Use the /status endpoint instead.")

    if any(k in payload for k in ("settings", "functionBaseUrl", "functionRoute", "functionKey")):
        settings = dict(instance.settings) if instance.settings else {}

        if "functionBaseUrl" in payload:
            settings["function_base_url"] = payload["functionBaseUrl"]
        if "functionRoute" in payload:
            settings["function_route"] = payload["functionRoute"]
        if "functionKey" in payload:
            settings["function_key_encrypted"] = encrypt_agent_key(payload["functionKey"])
        if "settings" in payload and isinstance(payload["settings"], dict):
            settings.update(payload["settings"])

        instance.settings = settings

    session.flush()
    log_audit(session, actor_user_id=current_user.id, action="UPDATE", entity_type="AGENT_INSTANCE", entity_id=instance.id, payload={"agent_type": instance.agent_type})
    session.commit()
    return {"message": "Agent instance updated successfully", "agent_instance": instance.to_dict()}


@router.patch("/agent-instances/{instance_id}/status")
def update_agent_instance_status(instance_id: str, payload: dict, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    instance = _get_instance_or_404(session, current_user, instance_id)

    new_status = payload.get("status")
    valid_statuses = ["ACTIVE", "TO_PROVISION", "DISABLED"]
    if new_status not in valid_statuses:
        raise ValidationError(f"Invalid status. Must be one of: {', '.join(valid_statuses)}")

    old_status = instance.status
    instance.status = new_status

    session.flush()
    log_audit(session, actor_user_id=current_user.id, action="UPDATE_STATUS", entity_type="AGENT_INSTANCE", entity_id=instance.id, payload={"old_status": old_status, "new_status": new_status})
    session.commit()
    return {"message": "Agent instance status updated successfully", "agent_instance": instance.to_dict()}


@router.delete("/agent-instances/{instance_id}")
def disable_agent_instance(instance_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    require_admin(current_user)
    instance = _get_instance_or_404(session, current_user, instance_id)
    instance.status = "DISABLED"
    session.flush()
    log_audit(session, actor_user_id=current_user.id, action="DISABLE", entity_type="AGENT_INSTANCE", entity_id=instance.id, payload={})
    session.commit()
    return {"message": "Agent instance disabled successfully"}


@router.post("/agent-instances/{instance_id}/rotate-key")
def rotate_agent_key(instance_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    raise AppError("This endpoint has been disabled. API key rotation is no longer supported.", status_code=status.HTTP_410_GONE, code="LEGACY_API_KEYS_DISABLED")


@router.get("/agent-instances/{instance_id}/access-key")
def get_agent_access_key(instance_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    raise AppError("This endpoint has been disabled. Direct access key retrieval is no longer supported.", status_code=status.HTTP_410_GONE, code="LEGACY_API_KEYS_DISABLED")


@router.get("/activation-keys")
def list_activation_keys(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    raise AppError("This endpoint has been disabled. Activation keys are no longer supported.", status_code=status.HTTP_410_GONE, code="LEGACY_API_KEYS_DISABLED")


@router.get("/activation-keys/{key_id}/reveal")
def reveal_activation_key(key_id: str, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    raise AppError("This endpoint has been disabled. Activation keys are no longer supported.", status_code=status.HTTP_410_GONE, code="LEGACY_API_KEYS_DISABLED")
