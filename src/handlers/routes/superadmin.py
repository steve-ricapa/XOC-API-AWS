import os
import secrets
import string
from datetime import datetime

import requests
from fastapi import APIRouter, Depends, Header, status
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import (
    AgentInstance, AgentSession, ActivationKey, AgentApiKey,
    AuditLog, Company, Integration, IntegrationCapabilityTemplate,
    IntegrationCapabilityTemplateAssignment, Ticket, User,
)
from src.shared.context import log_audit, require_superadmin, get_user_by_email
from src.shared.dependencies import get_current_user
from src.shared.errors import NotFoundError, ValidationError, ConflictError, ForbiddenError
from src.shared.integration_types import OFFICIAL_INTEGRATION_TYPES, normalize_integration_type
from src.shared.encryption import encrypt_agent_key, decrypt_agent_key, encrypt_credentials, decrypt_credentials
from src.shared.security_keys import generate_access_key, hash_access_key


router = APIRouter(prefix="/api/superadmin", tags=["superadmin"])


DEMO_PLAN_STATUS = "DEMO"
DEMO_FUNCTION_ROUTE_DEFAULT = "/api/agents/SophiaDurableAgent/run"


def _get_demo_function_settings() -> dict:
    return {
        "base_url": os.environ.get("DEMO_FUNCTION_BASE_URL", ""),
        "host_key": os.environ.get("DEMO_FUNCTION_HOST_KEY", ""),
        "route": os.environ.get("DEMO_FUNCTION_ROUTE", DEMO_FUNCTION_ROUTE_DEFAULT),
    }


def _normalize_provider(value: str) -> str:
    return value.strip().lower() if value else ""


def _parse_bool(value, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.strip().lower() in ("1", "true", "yes", "on"):
            return True
        if value.strip().lower() in ("0", "false", "no", "off"):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    raise ValidationError(f"{field_name} must be a boolean")


def _validate_capabilities(value):
    if value is None:
        return
    if isinstance(value, (list, dict, str)):
        return
    raise ValidationError("capabilities must be a list, dict, string, or null")


def _flatten_capabilities(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, str):
                result.append(item)
        return result
    return []


def _parse_int_param(value, field_name: str):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field_name} must be an integer")


def _parse_limit(value, default: int = 50, max_value: int = 200) -> int:
    if value is None:
        return default
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    if limit < 1:
        return 1
    if limit > max_value:
        return max_value
    return limit


def _parse_offset(value, default: int = 0) -> int:
    if value is None:
        return default
    try:
        offset = int(value)
    except (TypeError, ValueError):
        return default
    if offset < 0:
        return 0
    return offset


def _parse_order(value, default: str = "desc") -> str:
    if value and isinstance(value, str) and value.strip().lower() in ("asc", "desc"):
        return value.strip().lower()
    return default


def _parse_iso_datetime(value, field_name: str):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            raise ValidationError(f"{field_name} must be a valid ISO datetime string")
    raise ValidationError(f"{field_name} must be a valid ISO datetime string")


def _serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "company_id": user.company_id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _serialize_company(company: Company) -> dict:
    return {
        "id": company.id,
        "name": company.name,
        "created_at": company.created_at.isoformat() if company.created_at else None,
    }


def _build_assignments_map(assignments: list) -> dict:
    result = {}
    for a in assignments:
        t_id = a.template_id
        if t_id not in result:
            result[t_id] = set()
        result[t_id].add(a.company_id)
    return result


def _template_scope(template_id: int, assignments_by_template: dict) -> str:
    if template_id in assignments_by_template:
        return "selected"
    return "all"


def _template_applies(template, company_id: int, assignments_by_template: dict) -> bool:
    tid = template.id
    if tid in assignments_by_template:
        return company_id in assignments_by_template[tid]
    return True


def _parse_company_ids(value) -> list[int]:
    if not isinstance(value, list):
        raise ValidationError("companies must be a list of integers")
    if not value:
        return []
    seen = set()
    result = []
    for item in value:
        try:
            cid = int(item)
        except (TypeError, ValueError):
            raise ValidationError("companies must contain only integers")
        if cid in seen:
            raise ValidationError("Duplicate company_id in companies")
        seen.add(cid)
        result.append(cid)
    return result


def _require_confirm(x_superadmin_confirm: bool = Header(None, alias="X-Superadmin-Confirm")) -> bool:
    if not x_superadmin_confirm:
        raise ValidationError("X-Superadmin-Confirm header is required for this operation")
    return True


def _ensure_demo_agent_instance(company_id: int, session: Session):
    demo_settings = _get_demo_function_settings()
    if not demo_settings["base_url"]:
        return
    existing = session.query(AgentInstance).filter_by(company_id=company_id, agent_type="SVAFUNC").first()
    if existing:
        return
    access_key = generate_access_key()
    access_key_hash = hash_access_key(access_key)
    access_key_encrypted = encrypt_agent_key(access_key)
    instance = AgentInstance(
        company_id=company_id,
        agent_type="SVAFUNC",
        client_access_key_hash=access_key_hash,
        client_access_key_encrypted=access_key_encrypted,
        status="ACTIVE",
        settings={
            "function_base_url": demo_settings["base_url"],
            "function_host_key": demo_settings["host_key"],
            "function_route": demo_settings["route"],
        },
    )
    session.add(instance)
    session.flush()


def _get_active_agent_instance(company_id: int, session: Session):
    return session.query(AgentInstance).filter_by(company_id=company_id, agent_type="SVAFUNC", status="ACTIVE").first()


def _build_integration_capability_payload(
    integration, templates_by_provider, assignments_by_template, company_id,
    include_templates=True, include_effective=True,
) -> dict:
    data = integration.to_dict()
    provider = integration.provider
    if include_templates and provider in templates_by_provider:
        template_list = []
        for t in templates_by_provider[provider]:
            tdata = t.to_dict()
            tdata["scope"] = _template_scope(t.id, assignments_by_template)
            tdata["applies"] = _template_applies(t, company_id, assignments_by_template)
            template_list.append(tdata)
        data["templates"] = template_list
    if include_effective:
        caps = _flatten_capabilities(integration.capabilities) if integration.capabilities else []
        if provider in templates_by_provider:
            for t in templates_by_provider[provider]:
                if _template_applies(t, company_id, assignments_by_template):
                    caps.extend(_flatten_capabilities(t.capabilities))
        data["effective_capabilities"] = list(sorted(set(caps)))
    return data


@router.get("/companies")
def list_companies(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    q: str = None,
    created_from: str = None,
    created_to: str = None,
    limit: int = None,
    offset: int = None,
) -> dict:
    require_superadmin(current_user)
    query = session.query(Company)
    if q:
        query = query.filter(Company.name.ilike(f"%{q}%"))
    created_from_dt = _parse_iso_datetime(created_from, "created_from")
    created_to_dt = _parse_iso_datetime(created_to, "created_to")
    if created_from_dt:
        query = query.filter(Company.created_at >= created_from_dt)
    if created_to_dt:
        query = query.filter(Company.created_at <= created_to_dt)
    query = query.order_by(Company.created_at.desc())
    total = query.count()
    limit_val = _parse_limit(limit)
    offset_val = _parse_offset(offset)
    companies = query.offset(offset_val).limit(limit_val).all()
    return {"companies": [c.to_dict() for c in companies], "total": total, "limit": limit_val, "offset": offset_val}


@router.post("/companies", status_code=status.HTTP_201_CREATED)
def create_company(
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    if not payload:
        raise ValidationError("Request body is required")
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValidationError("name is required")
    existing = session.query(Company).filter(Company.name == name).first()
    if existing:
        raise ConflictError("Company name already exists")
    plan_status = (payload.get("plan_status") or "INACTIVE").strip().upper()
    company = Company(name=name, plan_status=plan_status)
    session.add(company)
    session.flush()
    if plan_status == DEMO_PLAN_STATUS:
        _ensure_demo_agent_instance(company.id, session)
    log_audit(session, actor_user_id=current_user.id, action="CREATE", entity_type="COMPANY", entity_id=company.id, payload={"name": name, "plan_status": plan_status})
    session.commit()
    return {"message": "Company created successfully", "company": company.to_dict()}


@router.get("/companies/{company_id}")
def get_company(
    company_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    company = session.get(Company, company_id)
    if not company:
        raise NotFoundError("Company not found")
    users_count = session.query(func.count(User.id)).filter(User.company_id == company_id).scalar() or 0
    integrations_count = session.query(func.count(Integration.id)).filter(Integration.company_id == company_id).scalar() or 0
    tickets_count = session.query(func.count(Ticket.id)).filter(Ticket.company_id == company_id).scalar() or 0
    agent_sessions_count = session.query(func.count(AgentSession.id)).filter(AgentSession.company_id == company_id).scalar() or 0
    agent_instances_count = session.query(func.count(AgentInstance.id)).filter(AgentInstance.company_id == company_id).scalar() or 0
    data = company.to_dict()
    data["users_count"] = users_count
    data["integrations_count"] = integrations_count
    data["tickets_count"] = tickets_count
    data["sessions_count"] = agent_sessions_count
    data["agents_count"] = agent_instances_count
    return data


@router.patch("/companies/{company_id}")
def update_company(
    company_id: int,
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    company = session.get(Company, company_id)
    if not company:
        raise NotFoundError("Company not found")
    if not payload:
        raise ValidationError("Request body is required")
    if "name" in payload:
        name = (payload["name"] or "").strip()
        if not name:
            raise ValidationError("name cannot be empty")
        existing = session.query(Company).filter(Company.name == name, Company.id != company_id).first()
        if existing:
            raise ConflictError("Company name already exists")
        company.name = name
    if "plan_status" in payload:
        plan_status = payload["plan_status"].strip().upper()
        company.plan_status = plan_status
    log_audit(session, actor_user_id=current_user.id, action="UPDATE", entity_type="COMPANY", entity_id=company.id, payload={"name": company.name, "plan_status": company.plan_status})
    session.commit()
    if company.plan_status == DEMO_PLAN_STATUS:
        _ensure_demo_agent_instance(company.id, session)
    return {"message": "Company updated successfully", "company": company.to_dict()}


@router.get("/companies/{company_id}/integrations")
def list_company_integrations(
    company_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    company = session.get(Company, company_id)
    if not company:
        raise NotFoundError("Company not found")
    integrations = session.query(Integration).filter(Integration.company_id == company_id).order_by(Integration.created_at.desc()).all()
    templates = session.query(IntegrationCapabilityTemplate).all()
    assignments = session.query(IntegrationCapabilityTemplateAssignment).all()
    templates_by_provider = {}
    for t in templates:
        prov = t.provider
        if prov not in templates_by_provider:
            templates_by_provider[prov] = []
        templates_by_provider[prov].append(t)
    assignments_by_template = _build_assignments_map(assignments)
    return {
        "integrations": [
            _build_integration_capability_payload(
                integration, templates_by_provider, assignments_by_template, company_id,
            ) for integration in integrations
        ],
    }


@router.get("/companies/{company_id}/capabilities")
def get_company_capabilities(
    company_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    company = session.get(Company, company_id)
    if not company:
        raise NotFoundError("Company not found")
    integrations = session.query(Integration).filter(Integration.company_id == company_id).all()
    templates = session.query(IntegrationCapabilityTemplate).filter_by(is_active=True).all()
    assignments = session.query(IntegrationCapabilityTemplateAssignment).filter_by(company_id=company_id).all()
    assignments_by_template = _build_assignments_map(assignments)
    all_capabilities = set()
    for integration in integrations:
        caps = _flatten_capabilities(integration.capabilities) if integration.capabilities else []
        all_capabilities.update(caps)
        for t in templates:
            if _template_applies(t, company_id, assignments_by_template) and t.provider == integration.provider:
                all_capabilities.update(_flatten_capabilities(t.capabilities))
    return {"capabilities": sorted(all_capabilities)}


@router.get("/companies/{company_id}/capability-templates")
def list_company_capability_templates(
    company_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    company = session.get(Company, company_id)
    if not company:
        raise NotFoundError("Company not found")
    templates = session.query(IntegrationCapabilityTemplate).all()
    assignments = session.query(IntegrationCapabilityTemplateAssignment).filter_by(company_id=company_id).all()
    assigned_template_ids = {a.template_id for a in assignments}
    result = []
    for t in templates:
        tdata = t.to_dict()
        tdata["applies_to_company"] = t.id in assigned_template_ids or t.id not in {a.template_id for a in session.query(IntegrationCapabilityTemplateAssignment).all()}
        tdata["assigned"] = t.id in assigned_template_ids
        result.append(tdata)
    return {"capability_templates": result}


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    if not payload:
        raise ValidationError("Request body is required")
    company_id = payload.get("company_id")
    if not company_id:
        raise ValidationError("company_id is required")
    company = session.get(Company, int(company_id))
    if not company:
        raise NotFoundError("Company not found")
    username = (payload.get("username") or "").strip()
    if not username:
        raise ValidationError("username is required")
    email = (payload.get("email") or "").strip()
    if not email:
        raise ValidationError("email is required")
    password = payload.get("password")
    if not password:
        raise ValidationError("password is required")
    role = (payload.get("role") or "USER").strip().upper()
    if role not in ("ADMIN", "USER", "SUPERADMIN"):
        raise ValidationError("role must be ADMIN, USER, or SUPERADMIN")
    existing = session.query(User).filter(User.username == username).first()
    if existing:
        raise ConflictError("Username already exists")
    existing_email = session.query(User).filter(User.email == email).first()
    if existing_email:
        raise ConflictError("Email already exists")
    user = User(company_id=int(company_id), username=username, email=email, role=role)
    user.set_password(password)
    session.add(user)
    session.flush()
    log_audit(session, actor_user_id=current_user.id, action="CREATE", entity_type="USER", entity_id=user.id, payload={"username": username, "role": role, "company_id": company_id})
    session.commit()
    return {"message": "User created successfully", "user": _serialize_user(user)}


@router.get("/users")
def list_users(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    company_id: int = None,
    role: str = None,
    q: str = None,
    limit: int = None,
    offset: int = None,
) -> dict:
    require_superadmin(current_user)
    query = session.query(User)
    if company_id is not None:
        query = query.filter(User.company_id == int(company_id))
    if role:
        query = query.filter(User.role == role.strip().upper())
    if q:
        query = query.filter(
            or_(User.username.ilike(f"%{q}%"), User.email.ilike(f"%{q}%"))
        )
    query = query.order_by(User.created_at.desc())
    total = query.count()
    limit_val = _parse_limit(limit)
    offset_val = _parse_offset(offset)
    users = query.offset(offset_val).limit(limit_val).all()
    return {"users": [_serialize_user(u) for u in users], "total": total, "limit": limit_val, "offset": offset_val}


@router.get("/users/{user_id}")
def get_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    user = session.get(User, user_id)
    if not user:
        raise NotFoundError("User not found")
    return _serialize_user(user)


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    user = session.get(User, user_id)
    if not user:
        raise NotFoundError("User not found")
    if not payload:
        raise ValidationError("Request body is required")
    if "email" in payload:
        email = (payload["email"] or "").strip()
        if not email:
            raise ValidationError("email cannot be empty")
        existing = session.query(User).filter(User.email == email, User.id != user_id).first()
        if existing:
            raise ConflictError("Email already exists")
        user.email = email
    if "username" in payload:
        username = (payload["username"] or "").strip()
        if not username:
            raise ValidationError("username cannot be empty")
        existing = session.query(User).filter(User.username == username, User.id != user_id).first()
        if existing:
            raise ConflictError("Username already exists")
        user.username = username
    if "role" in payload:
        new_role = payload["role"].strip().upper()
        if new_role not in ("ADMIN", "USER", "SUPERADMIN"):
            raise ValidationError("role must be ADMIN, USER, or SUPERADMIN")
        if user.role == "SUPERADMIN" and new_role != "SUPERADMIN":
            other_superadmins = session.query(User).filter(User.role == "SUPERADMIN", User.id != user_id).count()
            if other_superadmins == 0:
                raise ValidationError("Cannot remove the last SUPERADMIN user")
        user.role = new_role
    log_audit(session, actor_user_id=current_user.id, action="UPDATE", entity_type="USER", entity_id=user.id, payload={"email": user.email, "role": user.role})
    session.commit()
    return {"message": "User updated successfully", "user": _serialize_user(user)}


@router.post("/users/{user_id}/password-reset")
def reset_user_password(
    user_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    x_superadmin_confirm: bool = Header(None, alias="X-Superadmin-Confirm"),
) -> dict:
    require_superadmin(current_user)
    _require_confirm(x_superadmin_confirm)
    user = session.get(User, user_id)
    if not user:
        raise NotFoundError("User not found")
    alphabet = string.ascii_letters + string.digits
    temp_password = "".join(secrets.choice(alphabet) for _ in range(16))
    user.set_password(temp_password)
    log_audit(session, actor_user_id=current_user.id, action="PASSWORD_RESET", entity_type="USER", entity_id=user.id, payload={"username": user.username})
    session.commit()
    return {"message": "Password reset successfully", "temporary_password": temp_password}


@router.get("/integrations")
def list_integrations(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    company_id: int = None,
    provider: str = None,
    type: str = None,
    limit: int = None,
    offset: int = None,
) -> dict:
    require_superadmin(current_user)
    query = session.query(Integration)
    if company_id is not None:
        query = query.filter(Integration.company_id == int(company_id))
    if provider:
        query = query.filter(Integration.provider == provider)
    if type:
        query = query.filter(Integration.type == type)
    query = query.order_by(Integration.created_at.desc())
    total = query.count()
    limit_val = _parse_limit(limit)
    offset_val = _parse_offset(offset)
    integrations = query.offset(offset_val).limit(limit_val).all()
    return {"integrations": [i.to_dict() for i in integrations], "total": total, "limit": limit_val, "offset": offset_val}


@router.get("/integrations/{integration_id}")
def get_integration(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    integration = session.get(Integration, integration_id)
    if not integration:
        raise NotFoundError("Integration not found")
    return integration.to_dict()


@router.patch("/integrations/{integration_id}")
def update_integration(
    integration_id: int,
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    integration = session.get(Integration, integration_id)
    if not integration:
        raise NotFoundError("Integration not found")
    if not payload:
        raise ValidationError("Request body is required")
    if "type" in payload:
        integration_type = payload.get("type")
        if integration_type is not None:
            integration_type = normalize_integration_type(integration_type) or integration_type
        integration.type = integration_type
    if "capabilities" in payload:
        _validate_capabilities(payload["capabilities"])
        integration.capabilities = payload["capabilities"]
    if "config" in payload:
        config = payload["config"]
        if config is not None and not isinstance(config, dict):
            raise ValidationError("config must be an object")
        integration.config = config
    if "extra_json" in payload:
        extra = payload["extra_json"]
        if extra is not None and not isinstance(extra, dict):
            raise ValidationError("extra_json must be an object")
        integration.extra_json = extra
    log_audit(session, actor_user_id=current_user.id, action="UPDATE", entity_type="INTEGRATION", entity_id=integration.id, payload={"provider": integration.provider})
    session.commit()
    return {"message": "Integration updated successfully", "integration": integration.to_dict()}


@router.get("/integrations/{integration_id}/credentials")
def get_integration_credentials(
    integration_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    x_superadmin_confirm: bool = Header(None, alias="X-Superadmin-Confirm"),
) -> dict:
    require_superadmin(current_user)
    _require_confirm(x_superadmin_confirm)
    integration = session.get(Integration, integration_id)
    if not integration:
        raise NotFoundError("Integration not found")
    if not integration.credentials_encrypted:
        raise ValidationError("No credentials configured for this integration")
    credentials = decrypt_credentials(integration.credentials_encrypted)
    if credentials is None:
        raise ValidationError("Failed to decrypt credentials")
    return {"credentials": credentials}


@router.post("/integrations/{integration_id}/credentials")
def set_integration_credentials(
    integration_id: int,
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    x_superadmin_confirm: bool = Header(None, alias="X-Superadmin-Confirm"),
) -> dict:
    require_superadmin(current_user)
    _require_confirm(x_superadmin_confirm)
    integration = session.get(Integration, integration_id)
    if not integration:
        raise NotFoundError("Integration not found")
    if not payload:
        raise ValidationError("Request body is required")
    credentials = payload.get("credentials")
    if not credentials:
        raise ValidationError("credentials is required in request body")
    if not isinstance(credentials, dict):
        raise ValidationError("credentials must be an object")
    integration.credentials_encrypted = encrypt_credentials(credentials)
    log_audit(session, actor_user_id=current_user.id, action="UPDATE_CREDENTIALS", entity_type="INTEGRATION", entity_id=integration.id, payload={"provider": integration.provider})
    session.commit()
    return {"message": "Credentials updated successfully"}


@router.get("/agent-instances")
def list_agent_instances(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    company_id: int = None,
    agent_type: str = None,
    status: str = None,
    limit: int = None,
    offset: int = None,
) -> dict:
    require_superadmin(current_user)
    query = session.query(AgentInstance)
    if company_id is not None:
        query = query.filter(AgentInstance.company_id == int(company_id))
    if agent_type:
        query = query.filter(AgentInstance.agent_type == agent_type.upper())
    if status:
        query = query.filter(AgentInstance.status == status.upper())
    query = query.order_by(AgentInstance.created_at.desc())
    total = query.count()
    limit_val = _parse_limit(limit)
    offset_val = _parse_offset(offset)
    instances = query.offset(offset_val).limit(limit_val).all()
    return {"agent_instances": [i.to_dict() for i in instances], "total": total, "limit": limit_val, "offset": offset_val}


@router.get("/agent-instances/{instance_id}")
def get_agent_instance(
    instance_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    instance = session.get(AgentInstance, instance_id)
    if not instance:
        raise NotFoundError("Agent instance not found")
    return instance.to_dict()


@router.patch("/agent-instances/{instance_id}")
def update_agent_instance(
    instance_id: str,
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    instance = session.get(AgentInstance, instance_id)
    if not instance:
        raise NotFoundError("Agent instance not found")
    if not payload:
        raise ValidationError("Request body is required")
    if "status" in payload:
        instance.status = payload["status"].strip().upper()
    if "settings" in payload:
        settings = payload["settings"]
        if not isinstance(settings, dict):
            raise ValidationError("settings must be an object")
        instance.settings = settings
    if "speech_settings" in payload:
        speech = payload["speech_settings"]
        if speech is not None and not isinstance(speech, dict):
            raise ValidationError("speech_settings must be an object or null")
        instance.speech_settings = speech
    if "function_base_url" in payload or "function_host_key" in payload or "function_route" in payload:
        current_settings = instance.settings or {}
        if "function_base_url" in payload:
            current_settings["function_base_url"] = payload["function_base_url"]
        if "function_host_key" in payload:
            current_settings["function_host_key"] = payload["function_host_key"]
        if "function_route" in payload:
            current_settings["function_route"] = payload["function_route"]
        instance.settings = current_settings
    log_audit(session, actor_user_id=current_user.id, action="UPDATE", entity_type="AGENT_INSTANCE", entity_id=instance.id, payload={"agent_type": instance.agent_type, "status": instance.status})
    session.commit()
    return {"message": "Agent instance updated successfully", "agent_instance": instance.to_dict()}


@router.get("/agent-instances/{instance_id}/access-key", status_code=status.HTTP_410_GONE)
def get_agent_instance_access_key(
    instance_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    raise ValidationError("This endpoint is no longer available")


@router.post("/agent-instances/{instance_id}/rotate-key", status_code=status.HTTP_410_GONE)
def rotate_agent_instance_key(
    instance_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    raise ValidationError("This endpoint is no longer available")


@router.post("/activation-keys", status_code=status.HTTP_410_GONE)
def create_activation_key(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    raise ValidationError("This endpoint is no longer available")


@router.get("/activation-keys", status_code=status.HTTP_410_GONE)
def list_activation_keys(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    raise ValidationError("This endpoint is no longer available")


@router.get("/activation-keys/{key_id}/reveal", status_code=status.HTTP_410_GONE)
def reveal_activation_key(
    key_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    raise ValidationError("This endpoint is no longer available")


@router.patch("/activation-keys/{key_id}", status_code=status.HTTP_410_GONE)
def update_activation_key(
    key_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    raise ValidationError("This endpoint is no longer available")


@router.get("/agent-keys", status_code=status.HTTP_410_GONE)
def list_agent_keys(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    raise ValidationError("This endpoint is no longer available")


@router.get("/agent-keys/{key_id}", status_code=status.HTTP_410_GONE)
def get_agent_key(
    key_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    raise ValidationError("This endpoint is no longer available")


@router.post("/agent-keys/{key_id}/regenerate", status_code=status.HTTP_410_GONE)
def regenerate_agent_key(
    key_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    raise ValidationError("This endpoint is no longer available")


@router.get("/tickets")
def list_tickets(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    company_id: int = None,
    status: str = None,
    q: str = None,
    created_from: str = None,
    created_to: str = None,
    limit: int = None,
    offset: int = None,
) -> dict:
    require_superadmin(current_user)
    query = session.query(Ticket)
    if company_id is not None:
        query = query.filter(Ticket.company_id == int(company_id))
    if status:
        query = query.filter(Ticket.status == status.upper())
    if q:
        query = query.filter(Ticket.subject.ilike(f"%{q}%"))
    created_from_dt = _parse_iso_datetime(created_from, "created_from")
    created_to_dt = _parse_iso_datetime(created_to, "created_to")
    if created_from_dt:
        query = query.filter(Ticket.created_at >= created_from_dt)
    if created_to_dt:
        query = query.filter(Ticket.created_at <= created_to_dt)
    query = query.order_by(Ticket.created_at.desc())
    total = query.count()
    limit_val = _parse_limit(limit)
    offset_val = _parse_offset(offset)
    tickets = query.offset(offset_val).limit(limit_val).all()
    return {
        "tickets": [t.to_dict(include_creator=True, creator=session.get(User, t.created_by_user_id) if t.created_by_user_id else None) for t in tickets],
        "total": total, "limit": limit_val, "offset": offset_val,
    }


@router.get("/tickets/{ticket_id}")
def get_ticket(
    ticket_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise NotFoundError("Ticket not found")
    creator = session.get(User, ticket.created_by_user_id) if ticket.created_by_user_id else None
    return ticket.to_dict(include_creator=True, creator=creator)


@router.patch("/tickets/{ticket_id}")
def update_ticket(
    ticket_id: int,
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise NotFoundError("Ticket not found")
    if not payload:
        raise ValidationError("Request body is required")
    if "status" not in payload:
        raise ValidationError("Only status can be updated via superadmin")
    new_status = payload["status"].strip().upper()
    ticket.status = new_status
    if new_status == "EXECUTED":
        ticket.executed_at = datetime.utcnow()
    log_audit(session, actor_user_id=current_user.id, action="UPDATE", entity_type="TICKET", entity_id=ticket.id, payload={"status": ticket.status})
    session.commit()
    creator = session.get(User, ticket.created_by_user_id) if ticket.created_by_user_id else None
    return {"message": "Ticket updated successfully", "ticket": ticket.to_dict(include_creator=True, creator=creator)}


@router.get("/chat/sessions")
def list_chat_sessions(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    company_id: int = None,
    user_id: int = None,
    q: str = None,
    order: str = None,
    limit: int = None,
    offset: int = None,
) -> dict:
    require_superadmin(current_user)
    query = session.query(AgentSession)
    if company_id is not None:
        query = query.filter(AgentSession.company_id == int(company_id))
    if user_id is not None:
        query = query.filter(AgentSession.user_id == int(user_id))
    if q:
        query = query.filter(AgentSession.title.ilike(f"%{q}%"))
    order_val = _parse_order(order)
    if order_val == "asc":
        query = query.order_by(AgentSession.last_activity_at.asc())
    else:
        query = query.order_by(AgentSession.last_activity_at.desc())
    total = query.count()
    limit_val = _parse_limit(limit)
    offset_val = _parse_offset(offset)
    sessions = query.offset(offset_val).limit(limit_val).all()
    result = []
    for s in sessions:
        sdata = s.to_dict()
        sdata["user"] = _serialize_user(s.user) if s.user else None
        result.append(sdata)
    return {"sessions": result, "total": total, "limit": limit_val, "offset": offset_val}


@router.get("/chat/sessions/{session_id}")
def get_chat_session(
    session_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    chat_session = session.get(AgentSession, session_id)
    if not chat_session:
        raise NotFoundError("Session not found")
    sdata = chat_session.to_dict()
    sdata["user"] = _serialize_user(chat_session.user) if chat_session.user else None
    return sdata


@router.delete("/chat/sessions/{session_id}")
def delete_chat_session(
    session_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    chat_session = session.get(AgentSession, session_id)
    if not chat_session:
        raise NotFoundError("Session not found")
    remote_deleted = False
    remote_error = None
    if chat_session.external_thread_id:
        try:
            instance = _get_active_agent_instance(chat_session.company_id, session)
            if instance and instance.settings:
                base_url = instance.settings.get("function_base_url", "")
                host_key = instance.settings.get("function_host_key", "")
                delete_route = instance.settings.get("function_route", "").rstrip("/")
                if delete_route.endswith("/run"):
                    delete_route = delete_route[:-4]
                delete_route = delete_route.rstrip("/") + "/threads/" + chat_session.external_thread_id
                url = base_url.rstrip("/") + "/" + delete_route.lstrip("/")
                headers = {"x-functions-key": host_key} if host_key else {}
                resp = requests.delete(url, headers=headers, timeout=30)
                if resp.status_code < 500:
                    remote_deleted = True
                else:
                    remote_error = f"Remote delete returned status {resp.status_code}"
        except Exception as e:
            remote_error = str(e)
    session.delete(chat_session)
    session.commit()
    return {
        "message": "Session deleted successfully",
        "remote_deleted": remote_deleted,
        "remote_error": remote_error,
    }


@router.get("/chat/history")
def get_chat_history(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    session_id: int = None,
    thread_id: str = None,
    company_id: int = None,
    limit: int = None,
    order: str = None,
) -> dict:
    require_superadmin(current_user)
    target_company_id = int(company_id) if company_id else None
    if not target_company_id and session_id:
        chat_session = session.get(AgentSession, session_id)
        if chat_session:
            target_company_id = chat_session.company_id
    if not target_company_id and thread_id:
        chat_session = session.query(AgentSession).filter(AgentSession.external_thread_id == thread_id).first()
        if chat_session:
            target_company_id = chat_session.company_id
    if not target_company_id:
        raise ValidationError("Could not determine company_id from parameters")
    instance = _get_active_agent_instance(target_company_id, session)
    if not instance or not instance.settings:
        raise ValidationError("No active agent instance configured for this company")
    base_url = instance.settings.get("function_base_url", "")
    host_key = instance.settings.get("function_host_key", "")
    history_route = instance.settings.get("function_route", "").rstrip("/")
    if history_route.endswith("/run"):
        history_route = history_route[:-4]
    history_route = history_route.rstrip("/") + "/history"
    params = {}
    if session_id:
        params["session_id"] = session_id
    if thread_id:
        params["thread_id"] = thread_id
    if limit:
        params["limit"] = _parse_limit(limit)
    if order:
        params["order"] = _parse_order(order)
    url = base_url.rstrip("/") + "/" + history_route.lstrip("/")
    headers = {"x-functions-key": host_key} if host_key else {}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        raise ValidationError(f"Failed to fetch chat history: {str(e)}")


@router.get("/audit-logs")
def list_audit_logs(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    company_id: int = None,
    actor_user_id: int = None,
    action: str = None,
    created_from: str = None,
    created_to: str = None,
    limit: int = None,
    offset: int = None,
) -> dict:
    require_superadmin(current_user)
    query = session.query(AuditLog)
    if company_id is not None or actor_user_id is not None:
        query = query.outerjoin(User, AuditLog.actor_user_id == User.id)
        if company_id is not None:
            query = query.filter(User.company_id == int(company_id))
        if actor_user_id is not None:
            query = query.filter(AuditLog.actor_user_id == int(actor_user_id))
    if action:
        query = query.filter(AuditLog.action == action)
    created_from_dt = _parse_iso_datetime(created_from, "created_from")
    created_to_dt = _parse_iso_datetime(created_to, "created_to")
    if created_from_dt:
        query = query.filter(AuditLog.created_at >= created_from_dt)
    if created_to_dt:
        query = query.filter(AuditLog.created_at <= created_to_dt)
    query = query.order_by(AuditLog.created_at.desc())
    total = query.count()
    limit_val = _parse_limit(limit)
    offset_val = _parse_offset(offset)
    logs = query.offset(offset_val).limit(limit_val).all()
    result = []
    for log_entry in logs:
        entry = {
            "id": log_entry.id,
            "actor_user_id": log_entry.actor_user_id,
            "action": log_entry.action,
            "entity_type": log_entry.entity_type,
            "entity_id": log_entry.entity_id,
            "payload": log_entry.payload,
            "created_at": log_entry.created_at.isoformat() if log_entry.created_at else None,
        }
        if log_entry.actor_user_id:
            actor = session.get(User, log_entry.actor_user_id)
            entry["actor"] = _serialize_user(actor) if actor else None
        else:
            entry["actor"] = None
        result.append(entry)
    return {"audit_logs": result, "total": total, "limit": limit_val, "offset": offset_val}


@router.get("/capability-templates")
def list_capability_templates(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    provider: str = None,
    active: bool = None,
    include_companies: bool = False,
    limit: int = None,
    offset: int = None,
) -> dict:
    require_superadmin(current_user)
    query = session.query(IntegrationCapabilityTemplate)
    if provider:
        query = query.filter(IntegrationCapabilityTemplate.provider == provider)
    if active is not None:
        query = query.filter(IntegrationCapabilityTemplate.is_active == _parse_bool(active, "active"))
    query = query.order_by(IntegrationCapabilityTemplate.provider.asc())
    total = query.count()
    limit_val = _parse_limit(limit)
    offset_val = _parse_offset(offset)
    templates = query.offset(offset_val).limit(limit_val).all()
    assignments = session.query(IntegrationCapabilityTemplateAssignment).all()
    assignments_by_template = _build_assignments_map(assignments)
    result = []
    for t in templates:
        tdata = t.to_dict()
        tdata["scope"] = _template_scope(t.id, assignments_by_template)
        tdata["assigned_companies_count"] = len(assignments_by_template.get(t.id, set()))
        if include_companies:
            company_ids = list(assignments_by_template.get(t.id, set()))
            companies = session.query(Company).filter(Company.id.in_(company_ids)).all() if company_ids else []
            tdata["companies"] = [_serialize_company(c) for c in companies]
        result.append(tdata)
    return {"capability_templates": result, "total": total, "limit": limit_val, "offset": offset_val}


@router.get("/capability-templates/{template_id}")
def get_capability_template(
    template_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    template = session.get(IntegrationCapabilityTemplate, template_id)
    if not template:
        raise NotFoundError("Capability template not found")
    assignments = session.query(IntegrationCapabilityTemplateAssignment).filter_by(template_id=template_id).all()
    assignments_by_template = {template_id: {a.company_id for a in assignments}}
    tdata = template.to_dict()
    tdata["scope"] = _template_scope(template_id, assignments_by_template)
    tdata["assigned_companies_count"] = len(assignments)
    return tdata


@router.post("/capability-templates", status_code=status.HTTP_201_CREATED)
def create_capability_template(
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    if not payload:
        raise ValidationError("Request body is required")
    provider = _normalize_provider(payload.get("provider", ""))
    if not provider:
        raise ValidationError("provider is required")
    existing = session.query(IntegrationCapabilityTemplate).filter(IntegrationCapabilityTemplate.provider == provider).first()
    if existing:
        raise ConflictError(f"Capability template for provider '{provider}' already exists")
    capabilities = payload.get("capabilities")
    _validate_capabilities(capabilities)
    description = payload.get("description")
    if description is not None and not isinstance(description, str):
        raise ValidationError("description must be a string")
    is_active = payload.get("is_active", True)
    if not isinstance(is_active, bool):
        is_active = True
    template = IntegrationCapabilityTemplate(
        provider=provider,
        capabilities=capabilities,
        description=description,
        is_active=is_active,
    )
    session.add(template)
    session.flush()
    log_audit(session, actor_user_id=current_user.id, action="CREATE", entity_type="CAPABILITY_TEMPLATE", entity_id=template.id, payload={"provider": provider})
    session.commit()
    assignments = session.query(IntegrationCapabilityTemplateAssignment).filter_by(template_id=template.id).all()
    assignments_by_template = {template.id: {a.company_id for a in assignments}}
    tdata = template.to_dict()
    tdata["scope"] = _template_scope(template.id, assignments_by_template)
    tdata["assigned_companies_count"] = len(assignments)
    return {"message": "Capability template created successfully", "capability_template": tdata}


@router.patch("/capability-templates/{template_id}")
def update_capability_template(
    template_id: int,
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    template = session.get(IntegrationCapabilityTemplate, template_id)
    if not template:
        raise NotFoundError("Capability template not found")
    if not payload:
        raise ValidationError("Request body is required")
    if "provider" in payload:
        provider = _normalize_provider(payload["provider"])
        if not provider:
            raise ValidationError("provider cannot be empty")
        existing = session.query(IntegrationCapabilityTemplate).filter(
            IntegrationCapabilityTemplate.provider == provider, IntegrationCapabilityTemplate.id != template_id
        ).first()
        if existing:
            raise ConflictError(f"Capability template for provider '{provider}' already exists")
        template.provider = provider
    if "capabilities" in payload:
        _validate_capabilities(payload["capabilities"])
        template.capabilities = payload["capabilities"]
    if "description" in payload:
        desc = payload["description"]
        if desc is not None and not isinstance(desc, str):
            raise ValidationError("description must be a string")
        template.description = desc
    if "is_active" in payload:
        template.is_active = _parse_bool(payload["is_active"], "is_active")
    log_audit(session, actor_user_id=current_user.id, action="UPDATE", entity_type="CAPABILITY_TEMPLATE", entity_id=template.id, payload={"provider": template.provider})
    session.commit()
    assignments = session.query(IntegrationCapabilityTemplateAssignment).filter_by(template_id=template_id).all()
    assignments_by_template = {template_id: {a.company_id for a in assignments}}
    tdata = template.to_dict()
    tdata["scope"] = _template_scope(template_id, assignments_by_template)
    tdata["assigned_companies_count"] = len(assignments)
    return {"message": "Capability template updated successfully", "capability_template": tdata}


@router.delete("/capability-templates/{template_id}")
def delete_capability_template(
    template_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    template = session.get(IntegrationCapabilityTemplate, template_id)
    if not template:
        raise NotFoundError("Capability template not found")
    log_audit(session, actor_user_id=current_user.id, action="DELETE", entity_type="CAPABILITY_TEMPLATE", entity_id=template.id, payload={"provider": template.provider})
    session.delete(template)
    session.commit()
    return {"message": "Capability template deleted successfully"}


@router.get("/capability-templates/{template_id}/companies")
def list_capability_template_companies(
    template_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    include_all: bool = False,
    limit: int = None,
    offset: int = None,
) -> dict:
    require_superadmin(current_user)
    template = session.get(IntegrationCapabilityTemplate, template_id)
    if not template:
        raise NotFoundError("Capability template not found")
    _include_all = _parse_bool(include_all, "include_all") if not isinstance(include_all, bool) else include_all
    if _include_all:
        query = session.query(Company).order_by(Company.name.asc())
        total = query.count()
        limit_val = _parse_limit(limit)
        offset_val = _parse_offset(offset)
        companies = query.offset(offset_val).limit(limit_val).all()
        assignments = session.query(IntegrationCapabilityTemplateAssignment).filter_by(template_id=template_id).all()
        assigned_company_ids = {a.company_id for a in assignments}
        result = []
        for c in companies:
            cdata = _serialize_company(c)
            cdata["assigned"] = c.id in assigned_company_ids
            result.append(cdata)
        return {"companies": result, "total": total, "limit": limit_val, "offset": offset_val}
    else:
        assignments = session.query(IntegrationCapabilityTemplateAssignment).filter_by(template_id=template_id).order_by(
            IntegrationCapabilityTemplateAssignment.created_at.desc()
        ).all()
        total = len(assignments)
        limit_val = _parse_limit(limit)
        offset_val = _parse_offset(offset)
        paged_assignments = assignments[offset_val:offset_val + limit_val]
        result = []
        for a in paged_assignments:
            c = session.get(Company, a.company_id)
            if c:
                cdata = _serialize_company(c)
                cdata["assigned"] = True
                cdata["assignment_id"] = a.id
                result.append(cdata)
        return {"companies": result, "total": total, "limit": limit_val, "offset": offset_val}


@router.put("/capability-templates/{template_id}/companies")
def set_capability_template_companies(
    template_id: int,
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_superadmin(current_user)
    template = session.get(IntegrationCapabilityTemplate, template_id)
    if not template:
        raise NotFoundError("Capability template not found")
    if not payload:
        raise ValidationError("Request body is required")
    mode = (payload.get("mode") or "replace").strip().lower()
    if mode not in ("replace", "add", "remove"):
        raise ValidationError('mode must be one of: replace, add, remove')
    company_ids = _parse_company_ids(payload.get("companies", []))
    for cid in company_ids:
        company = session.get(Company, cid)
        if not company:
            raise ValidationError(f"Company with id {cid} not found")
    if mode == "replace":
        session.query(IntegrationCapabilityTemplateAssignment).filter_by(template_id=template_id).delete()
        for cid in company_ids:
            session.add(IntegrationCapabilityTemplateAssignment(template_id=template_id, company_id=cid))
    elif mode == "add":
        for cid in company_ids:
            existing = session.query(IntegrationCapabilityTemplateAssignment).filter_by(
                template_id=template_id, company_id=cid
            ).first()
            if not existing:
                session.add(IntegrationCapabilityTemplateAssignment(template_id=template_id, company_id=cid))
    elif mode == "remove":
        session.query(IntegrationCapabilityTemplateAssignment).filter(
            IntegrationCapabilityTemplateAssignment.template_id == template_id,
            IntegrationCapabilityTemplateAssignment.company_id.in_(company_ids),
        ).delete(synchronize_session=False)
    log_audit(session, actor_user_id=current_user.id, action="UPDATE_COMPANIES", entity_type="CAPABILITY_TEMPLATE", entity_id=template_id, payload={"mode": mode, "company_ids": company_ids})
    session.commit()
    return {"message": "Template companies updated successfully"}
