import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import requests
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import AgentSession, Tenant, TenantRuntimeSettings
from src.shared.auth import create_access_token
from src.shared.config import get_jwt_secret_key, get_settings
from src.shared.context import effective_tenant_id_of, normalize_role, require_tenant_read_access
from src.shared.dependencies import get_current_user
from src.shared.errors import AppError, ForbiddenError, UnauthorizedError, ValidationError
from src.tool_gateway.executor import ToolExecutor
from src.tool_gateway.schemas import ToolContext, ToolRequest


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


_AFFINITY_COOKIE_KEYS = ("ARRAffinity", "ARRAffinitySameSite")
_SESSION_AFFINITY: dict = {}
_DEFAULT_CHAT_TIMEOUT = 60
_RUN_ACTIVE_MESSAGE = "Can't add messages to thread"
_THREAD_ID_PATTERN = re.compile(r"(thread_[A-Za-z0-9]+)")
_RUN_ID_PATTERN = re.compile(r"(run_[A-Za-z0-9]+)")
_RUNTIME_SETTINGS_MISSING_MESSAGE = "Runtime settings not configured for this company"
_TICKET_CONFIRMATION_SCOPE = "ai:ticket:confirm"
_TICKET_CONFIRMATION_TYPE = "ai_ticket_confirmation"
_TICKET_CONFIRMATION_SECONDS = 300


def _get_runtime_settings(session: Session, tenant_id: int) -> TenantRuntimeSettings:
    settings = session.scalar(
        select(TenantRuntimeSettings).where(
            TenantRuntimeSettings.tenant_id == tenant_id,
            TenantRuntimeSettings.is_active == True,
        )
    )
    if not settings:
        raise UnauthorizedError(_RUNTIME_SETTINGS_MISSING_MESSAGE)
    return settings


def _resolve_agent_routes(session: Session, tenant_id: int) -> dict[str, str]:
    settings = get_settings()
    global_base_url = (settings.agents_function_base_url or "").strip()
    global_sophia = (settings.agents_function_route_sophia or "/api/agents/SophiaDurableAgent/run").strip()
    global_history = (settings.agents_function_route_sophia_history or "/api/agents/SophiaDurableAgent/history").strip()
    global_delete = (settings.agents_function_route_sophia_delete or "/api/agents/SophiaDurableAgent/threads").strip()
    global_victor = (settings.agents_function_route_victor or "/api/agents/VictorDurableAgent/run").strip()

    runtime_settings = None
    try:
        runtime_settings = _get_runtime_settings(session, tenant_id)
    except Exception:
        pass

    if runtime_settings and getattr(runtime_settings, "function_base_url", None):
        return {
            "function_base_url": runtime_settings.function_base_url,
            "function_route_sophia": runtime_settings.function_route_sophia or global_sophia,
            "function_route_sophia_history": runtime_settings.function_route_sophia_history or global_history,
            "function_route_sophia_delete": runtime_settings.function_route_sophia_delete or global_delete,
            "function_route_victor": runtime_settings.function_route_victor or global_victor,
            "extra_json": getattr(runtime_settings, "extra_json", None) or {},
        }

    if global_base_url:
        return {
            "function_base_url": global_base_url,
            "function_route_sophia": global_sophia,
            "function_route_sophia_history": global_history,
            "function_route_sophia_delete": global_delete,
            "function_route_victor": global_victor,
            "extra_json": {},
        }

    return {
        "function_base_url": "",
        "function_route_sophia": global_sophia,
        "function_route_sophia_history": global_history,
        "function_route_sophia_delete": global_delete,
        "function_route_victor": global_victor,
        "extra_json": {},
    }


def _is_demo_tenant(current_user, db_session: Session) -> bool:
    user_tenant = getattr(current_user, "tenant", None)
    if not user_tenant:
        user_tenant = db_session.get(Tenant, effective_tenant_id_of(current_user))
    plan_status = (user_tenant.plan_status or "").strip().upper() if user_tenant else ""
    return plan_status == "DEMO"


def _build_agent_invoke_token(tenant_id: int, agent_type: str) -> str:
    claims = {
        "scopes": ["agent:invoke"],
        "tenant_id": tenant_id,
        "agent_type": agent_type,
    }
    return create_access_token(
        identity=f"agent-runtime-{tenant_id}-{agent_type}",
        additional_claims=claims,
        expires_delta=timedelta(minutes=15),
    )


def _normalize_session_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_affinity_cookies(cookie_jar):
    cookies = {}
    for key in _AFFINITY_COOKIE_KEYS:
        value = cookie_jar.get(key)
        if value:
            cookies[key] = value
    return cookies


def _stringify_error_payload(payload):
    if isinstance(payload, (dict, list)):
        try:
            return json.dumps(payload)
        except TypeError:
            return str(payload)
    return str(payload)


def _extract_run_active_context(payload_text):
    thread_match = _THREAD_ID_PATTERN.search(payload_text or "")
    run_match = _RUN_ID_PATTERN.search(payload_text or "")
    return (
        thread_match.group(1) if thread_match else None,
        run_match.group(1) if run_match else None,
    )


def _is_run_active_error(payload_text):
    if not payload_text:
        return False
    return _RUN_ACTIVE_MESSAGE in payload_text and "run_" in payload_text and "thread_" in payload_text


def _get_run_active_retry_config():
    retries_raw = os.environ.get("SOPHIA_RUN_ACTIVE_RETRIES", "2")
    delay_raw = os.environ.get("SOPHIA_RUN_ACTIVE_DELAY_SECONDS", "1.5")
    try:
        retries = int(retries_raw)
    except (TypeError, ValueError):
        retries = 2
    try:
        delay = float(delay_raw)
    except (TypeError, ValueError):
        delay = 1.5
    retries = max(0, min(retries, 5))
    delay = max(0.2, min(delay, 5.0))
    return retries, delay


def _clean_agent_response(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"text": str(payload)}
    cleaned: dict = {}
    text = payload.get("text") or payload.get("message")
    if text is not None:
        cleaned["text"] = text
    thread_id = payload.get("thread_id")
    if thread_id:
        cleaned["thread_id"] = thread_id
    action_plan = payload.get("action_plan")
    if action_plan:
        cleaned["action_plan"] = action_plan
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and metadata:
        cleaned["metadata"] = metadata
    for field in ("tool_request", "toolRequest", "tool_call", "toolCall"):
        if isinstance(payload.get(field), dict):
            cleaned["tool_request"] = payload[field]
            break
    return cleaned or payload


def _chat_tool_context(current_user, tenant_id: int, request_id: str | None) -> ToolContext:
    return ToolContext(
        tenant_id=getattr(current_user, "tenant_id", None),
        effective_tenant_id=int(tenant_id),
        user_id=getattr(current_user, "id", None),
        role=getattr(current_user, "role", None),
        delegation_active=bool(getattr(current_user, "delegation_active", False)),
        request_id=request_id,
        source="sophia_chat",
    )


def _maybe_execute_chat_tool_request(cleaned_payload: dict, current_user, tenant_id: int, request_id: str | None) -> dict:
    """Execute only an explicit, structured read tool through the gateway.

    The external SOPHIA runtime does not yet use this contract.  Supporting it
    here makes a future runtime integration policy-first without giving the
    model any direct route to XOC services.
    """
    raw_request = cleaned_payload.pop("tool_request", None)
    if not isinstance(raw_request, dict):
        return cleaned_payload
    tool_name = raw_request.get("tool_name") or raw_request.get("toolName") or raw_request.get("name")
    arguments = raw_request.get("arguments") or {}
    if not isinstance(arguments, dict):
        arguments = {"_invalid": True}
    result = ToolExecutor().execute(
        _chat_tool_context(current_user, tenant_id, request_id),
        ToolRequest(tool_name=str(tool_name or ""), arguments=arguments, request_id=request_id),
    )
    metadata = dict(cleaned_payload.get("metadata") or {})
    metadata["toolGateway"] = {
        "toolName": str(tool_name or ""),
        "status": result.status.value,
        "code": result.code,
        "auditId": result.audit_id,
    }
    if result.data is not None:
        metadata["toolGateway"]["data"] = result.data
    cleaned_payload["metadata"] = metadata
    return cleaned_payload


def _ticket_confirmation_token(*, action_plan: dict, tenant_id: int, current_user, request_id: str | None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(current_user.id),
        "type": _TICKET_CONFIRMATION_TYPE,
        "scope": _TICKET_CONFIRMATION_SCOPE,
        "actor_user_id": int(current_user.id),
        "actor_role": normalize_role(current_user.role),
        "effective_tenant_id": int(tenant_id),
        "delegation_active": bool(getattr(current_user, "delegation_active", False)),
        "request_id": request_id or str(uuid.uuid4()),
        "action_plan": action_plan,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=_TICKET_CONFIRMATION_SECONDS)).timestamp()),
    }
    return jwt.encode(payload, get_jwt_secret_key(), algorithm="HS256")


def _can_confirm_chat_ticket(current_user) -> bool:
    role = normalize_role(getattr(current_user, "role", None))
    return role == "ADMIN" or (role == "ADMIN_XOC" and bool(getattr(current_user, "delegation_active", False)))


def _prepare_ticket_proposal(
    cleaned_payload: dict,
    *,
    action_plan: dict,
    tenant_id: int,
    current_user,
    request_id: str | None,
) -> dict:
    """Replace model-triggered writes with a short-lived explicit proposal."""
    subject = str(action_plan.get("subject") or "").strip()
    if not subject:
        return cleaned_payload
    proposal = {
        "subject": subject[:240],
        "description": str(action_plan.get("description") or "")[:4000],
        "severity": str(action_plan.get("severity") or "medium")[:32],
        "status": "NEEDS_CONFIRMATION",
    }
    cleaned_payload["ticket_proposal"] = proposal
    if _can_confirm_chat_ticket(current_user):
        cleaned_payload["ticket_proposal"]["confirmation_token"] = _ticket_confirmation_token(
            action_plan=proposal,
            tenant_id=tenant_id,
            current_user=current_user,
            request_id=request_id,
        )
        cleaned_payload["text"] = (
            f"SOPHIA propone crear un ticket: **{proposal['subject']}**. "
            "Requiere tu confirmacion explicita antes de crearlo."
        )
    else:
        cleaned_payload["text"] = (
            f"SOPHIA propone un ticket: **{proposal['subject']}**. "
            "Un administrador del tenant debe confirmarlo."
        )
    return cleaned_payload


def _maybe_create_ticket_from_action_plan(
    cleaned_payload: dict,
    tenant_id: int,
    current_user,
    request_id: str | None,
) -> dict:
    """Prepare, but never auto-create, a ticket proposed by SOPHIA."""
    action_plan = cleaned_payload.get("action_plan")
    if not action_plan or not isinstance(action_plan, dict):
        return cleaned_payload

    subject = action_plan.get("subject")
    if not subject:
        return cleaned_payload

    return _prepare_ticket_proposal(
        cleaned_payload,
        action_plan=action_plan,
        tenant_id=tenant_id,
        current_user=current_user,
        request_id=request_id,
    )


_TICKET_CREATION_TRIGGERS = [
    "crear ticket", "create ticket", "generar ticket", "abrir ticket",
    "eliminar archivo", "remove file", "delete file", "borrar archivo",
    "eliminar malware", "remove malware", "quitar archivo", "remover archivo",
    "clean up", "remover", "eliminar script", "remove script", "delete script",
    "limpiar servidor", "clean server", "fix server", "arreglar servidor",
    "malicious", "malware", "virus", "trojan", "backdoor", "ransomware",
    "sospechoso", "suspicious", "amenaza", "threat", "compromiso", "compromised",
    "desinstalar", "uninstall", "instalar", "install",
    "revisar", "check", "analizar", "analyze", "escanear", "scan",
    "proteger", "protect", "bloquear", "block", "aislar", "isolate",
    "amenaza detectada", "threat detected", "alerta", "alert",
    "incursion", "breach", "intrusion", "intruso",
    "script malicioso", "malicious script", "codigo malicioso", "malicious code",
    "archivo infectado", "infected file", "archivo corrupto", "corrupted file",
    "puerta trasera", "rootkit", "keylogger", "spyware", "adware",
    "actualizar", "update", "patch", "parche",
    "configurar", "configure", "setup", "config",
]


def _extract_filename(message: str) -> str | None:
    """Try to extract a filename from the user message."""
    patterns = [
        r"(?:archivo|file)\s+(?:llamado?|named?|called?)?\s*[`:]*\s*[\"']?([^\s\"']+)[\"']?",
        r"([\/\w\-\.]+\.(?:sh|py|exe|bat|ps1|js|php|pl|rb|c|cpp|java|tmp|bak|log|elf|deb|rpm|tar|gz|zip))",
        r"(?:suspicious|malicioso|infected|compromised)[_\w]*\.\w+",
        r"\b([\/\w\-\.]*(?:malware|virus|trojan|backdoor|ransomware|keylogger|spyware)[\/\w\-\.]*)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return None


def _detect_ticket_creation_intent(message: str) -> dict | None:
    lower = (message or "").strip().lower()
    if not lower or len(lower) < 5:
        return None
    if not any(t in lower for t in _TICKET_CREATION_TRIGGERS):
        return None
    filename = _extract_filename(message)
    if filename:
        subject = f"Eliminacion de archivo sospechoso: {filename}"
    else:
        subject = "Incidente de seguridad reportado"
    description = message.strip()
    severity = "high"
    if any(w in lower for w in ["instalar", "install", "actualizar", "update", "configurar", "configure", "setup"]):
        severity = "medium"
    return {"subject": subject, "description": description, "severity": severity}


def _maybe_create_ticket_from_intent(
    cleaned_payload: dict,
    user_message: str,
    tenant_id: int,
    current_user,
    tenant_extra: dict | None = None,
    request_id: str | None = None,
) -> dict:
    if cleaned_payload.get("ticket_created") or cleaned_payload.get("ticket_proposal"):
        return cleaned_payload
    intent = _detect_ticket_creation_intent(user_message)
    if not intent:
        return cleaned_payload
    server_ip = (tenant_extra or {}).get("default_server_ip", "")
    server_name = (tenant_extra or {}).get("server_name", "")
    description_parts = [intent["description"]]
    if server_ip:
        description_parts.append(f"Servidor: {server_ip}" + (f" ({server_name})" if server_name else ""))
    full_description = ". ".join(description_parts)
    metadata = {"source": "sophia_chat_intent_detection"}
    if server_ip:
        metadata["server_ip"] = server_ip
    if server_name:
        metadata["server_name"] = server_name
    return _prepare_ticket_proposal(
        cleaned_payload,
        action_plan={
            "subject": intent["subject"],
            "description": full_description,
            "severity": intent["severity"],
            "metadata": metadata,
        },
        tenant_id=int(tenant_id),
        current_user=current_user,
        request_id=request_id,
    )


def _build_session_title(message: str, max_length: int = 160) -> str:
    normalized = (message or "").strip()
    if not normalized:
        return "Conversacion SOPHIA"
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length].rstrip()


@router.get("/sessions")
def list_chat_sessions(current_user=Depends(get_current_user), db_session: Session = Depends(get_db_session)) -> dict:
    require_tenant_read_access(current_user)
    tenant_id = effective_tenant_id_of(current_user)
    limit_raw = None
    try:
        limit = int(limit_raw) if limit_raw else 50
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))

    sessions = db_session.execute(
        select(AgentSession).where(
            AgentSession.tenant_id == tenant_id,
            AgentSession.user_id == current_user.id,
            AgentSession.purpose == "sophia_chat",
        ).order_by(AgentSession.last_activity_at.desc()).limit(limit)
    ).scalars().all()

    return {"sessions": [s.to_dict() for s in sessions], "count": len(sessions)}


@router.get("/history")
def chat_history(
    current_user=Depends(get_current_user),
    db_session: Session = Depends(get_db_session),
    tenantId: str = None,
    session_id: str = None,
    sessionId: str = None,
    thread_id: str = None,
    threadId: str = None,
    limit: int = None,
    order: str = None,
) -> dict:
    require_tenant_read_access(current_user)
    if _is_demo_tenant(current_user, db_session):
        raise ValidationError("Chat history is disabled in demo mode")

    effective_tenant_id = effective_tenant_id_of(current_user)
    tenant_id = tenantId or effective_tenant_id
    if int(tenant_id) != int(effective_tenant_id):
        raise ValidationError("Requested tenant does not match delegated tenant context")
    resolved_session_id = session_id or sessionId
    resolved_thread_id = thread_id or threadId

    if not limit:
        limit = 20
    limit = max(1, min(limit, 100))
    if order not in ("asc", "desc"):
        order = "desc"

    chat_session = None
    session_key = _normalize_session_id(resolved_session_id)
    if session_key:
        chat_session = db_session.execute(
            select(AgentSession).where(
                AgentSession.id == session_key,
                AgentSession.tenant_id == tenant_id,
                AgentSession.user_id == current_user.id,
            )
        ).scalar_one_or_none()
        if not chat_session:
            raise ValidationError("Agent session not found")
        if not resolved_thread_id:
            resolved_thread_id = chat_session.external_thread_id

    if not resolved_thread_id:
        raise ValidationError("thread_id or session_id is required")

    runtime_settings = _resolve_agent_routes(db_session, int(tenant_id))
    function_base_url = runtime_settings["function_base_url"]
    history_route = runtime_settings["function_route_sophia_history"]

    if not function_base_url:
        raise ValidationError("SVAFUNC function_base_url is not configured for this company")

    params = {"thread_id": resolved_thread_id, "limit": str(limit), "order": order}
    full_url = f"{function_base_url.rstrip('/')}{history_route}"
    service_token = _build_agent_invoke_token(int(tenant_id), "SOPHIA")

    try:
        response = requests.get(
            full_url,
            params=params,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {service_token}"},
            timeout=30,
        )
    except Exception as exc:
        raise AppError(f"Error communicating with SOPHIA: {str(exc)}", status_code=500)

    if response.status_code != 200:
        error_data = response.json() if response.headers.get("content-type") == "application/json" else {"error": response.text}
        raise AppError(
            "SOPHIA function error",
            status_code=response.status_code,
            code="sophia_error",
        )

    payload = response.json()
    if chat_session:
        payload["session_id"] = chat_session.id
    return payload


@router.delete("/sessions/{session_id}")
def delete_chat_session(
    session_id: int,
    current_user=Depends(get_current_user),
    db_session: Session = Depends(get_db_session),
) -> dict:
    require_tenant_read_access(current_user)
    tenant_id = effective_tenant_id_of(current_user)
    chat_session = db_session.execute(
        select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.tenant_id == tenant_id,
            AgentSession.user_id == current_user.id,
        )
    ).scalar_one_or_none()
    if not chat_session:
        raise ValidationError("Agent session not found")

    runtime_settings = _resolve_agent_routes(db_session, tenant_id)
    function_base_url = runtime_settings["function_base_url"]
    delete_route = runtime_settings["function_route_sophia_delete"]

    if not function_base_url:
        raise ValidationError("SVAFUNC function_base_url is not configured for this company")

    service_token = _build_agent_invoke_token(tenant_id, "SOPHIA")

    remote_deleted = False
    remote_error = None
    if chat_session.external_thread_id:
        thread_id = chat_session.external_thread_id
        full_url = f"{function_base_url.rstrip('/')}{delete_route.rstrip('/')}/{thread_id}"
        try:
            response = requests.delete(
                full_url,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {service_token}"},
                timeout=30,
            )
            remote_deleted = response.status_code in (200, 204)
            if not remote_deleted:
                remote_error = response.text
        except Exception as exc:
            remote_error = str(exc)

    db_session.delete(chat_session)
    db_session.commit()

    return {
        "message": "Chat session deleted",
        "session_id": session_id,
        "remote_deleted": remote_deleted,
        "remote_error": remote_error,
    }


@router.post("")
@router.post("/")
def proxy_chat(
    payload: dict,
    current_user=Depends(get_current_user),
    db_session: Session = Depends(get_db_session),
    request: Request = None,
) -> dict:
    if not payload:
        raise ValidationError("Request body is required")
    require_tenant_read_access(current_user)

    message = payload.get("message")
    if not message:
        raise ValidationError("Missing required field: message")

    effective_tenant_id = effective_tenant_id_of(current_user)
    tenant_id = payload.get("tenantId") or effective_tenant_id
    if int(tenant_id) != int(effective_tenant_id):
        raise ValidationError("Requested tenant does not match delegated tenant context")
    demo_mode = _is_demo_tenant(current_user, db_session)
    request_id = request.headers.get("x-request-id") if request else None

    runtime_settings = _resolve_agent_routes(db_session, int(tenant_id))

    chat_session = None
    session_id = payload.get("sessionId") or payload.get("session_id")
    force_new_session = bool(payload.get("new_session") or payload.get("newSession"))
    session_key = _normalize_session_id(session_id)

    if demo_mode:
        force_new_session = False
        chat_session = db_session.execute(
            select(AgentSession).where(
                AgentSession.tenant_id == tenant_id,
                AgentSession.user_id == current_user.id,
                AgentSession.purpose == "sophia_demo",
            ).order_by(AgentSession.last_activity_at.desc())
        ).scalars().first()
    elif force_new_session:
        pass
    elif session_key:
        chat_session = db_session.execute(
            select(AgentSession).where(
                AgentSession.id == session_key,
                AgentSession.tenant_id == tenant_id,
                AgentSession.user_id == current_user.id,
            )
        ).scalar_one_or_none()
        if not chat_session:
            raise ValidationError("Agent session not found")
    else:
        chat_session = db_session.execute(
            select(AgentSession).where(
                AgentSession.tenant_id == tenant_id,
                AgentSession.user_id == current_user.id,
                AgentSession.purpose == "sophia_chat",
            ).order_by(AgentSession.last_activity_at.desc())
        ).scalars().first()

    function_base_url = runtime_settings["function_base_url"]
    function_route = runtime_settings["function_route_sophia"]

    if not function_base_url:
        raise ValidationError("SVAFUNC function_base_url is not configured for this company")

    service_token = _build_agent_invoke_token(int(tenant_id), "SOPHIA")

    params = {}
    thread_id = None
    if not demo_mode:
        thread_id = payload.get("threadId") or payload.get("thread_id")
    if force_new_session:
        thread_id = None
    if not thread_id and chat_session and chat_session.external_thread_id:
        thread_id = chat_session.external_thread_id
    if thread_id:
        params["thread_id"] = thread_id

    full_url = f"{function_base_url.rstrip('/')}{function_route}"
    user_name = current_user.username or current_user.email

    sophia_payload = {"message": message, "user_name": user_name}
    if demo_mode:
        sophia_payload["chat_mode"] = "consulta"
    if thread_id:
        sophia_payload["thread_id"] = thread_id

    affinity_cookies = None
    if session_key:
        affinity_cookies = _SESSION_AFFINITY.get(session_key)

    retry_attempts, retry_delay = _get_run_active_retry_config()
    attempt = 0
    timeout_seconds = int(os.environ.get("SOPHIA_CHAT_TIMEOUT_SECONDS", str(_DEFAULT_CHAT_TIMEOUT)))

    while True:
        try:
            sophia_response = requests.post(
                full_url,
                json=sophia_payload,
                params=params,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {service_token}",
                },
                cookies=affinity_cookies,
                timeout=timeout_seconds,
            )
        except requests.exceptions.Timeout:
            logger.warning("[chat] SOPHIA timeout after %ss", timeout_seconds)
            response_payload = {
                "text": "SOPHIA esta procesando tu solicitud. Intenta nuevamente en unos segundos."
            }
            if chat_session is not None:
                response_payload["session_id"] = chat_session.id
            if thread_id:
                response_payload["thread_id"] = thread_id
            return response_payload

        response_cookies = _extract_affinity_cookies(sophia_response.cookies)
        if response_cookies:
            affinity_cookies = response_cookies

        if sophia_response.status_code == 200:
            response_payload = sophia_response.json()
            cleaned_payload = _clean_agent_response(response_payload)
            cleaned_payload = _maybe_execute_chat_tool_request(
                cleaned_payload, current_user, int(tenant_id), request_id
            )
            cleaned_payload = _maybe_create_ticket_from_action_plan(
                cleaned_payload, int(tenant_id), current_user, request_id
            )
            cleaned_payload = _maybe_create_ticket_from_intent(
                cleaned_payload, message, int(tenant_id), current_user,
                tenant_extra=runtime_settings.get("extra_json"),
                request_id=request_id,
            )
            response_thread_id = response_payload.get("thread_id") if isinstance(response_payload, dict) else None

            if response_thread_id:
                if chat_session is None:
                    chat_session = AgentSession(
                        tenant_id=tenant_id,
                        user_id=current_user.id,
                        external_thread_id=response_thread_id,
                        title=_build_session_title(message),
                        purpose="sophia_demo" if demo_mode else "sophia_chat",
                        last_activity_at=datetime.utcnow(),
                    )
                    db_session.add(chat_session)
                else:
                    chat_session.external_thread_id = response_thread_id
                    if not chat_session.title:
                        chat_session.title = _build_session_title(message)
                    chat_session.last_activity_at = datetime.utcnow()
            elif chat_session is not None:
                if not chat_session.title:
                    chat_session.title = _build_session_title(message)
                chat_session.last_activity_at = datetime.utcnow()

            if chat_session is not None:
                try:
                    db_session.commit()
                except Exception:
                    db_session.rollback()
                else:
                    if response_cookies:
                        _SESSION_AFFINITY[chat_session.id] = response_cookies
                    if isinstance(cleaned_payload, dict):
                        cleaned_payload["session_id"] = chat_session.id

            return cleaned_payload

        error_data = (
            sophia_response.json()
            if sophia_response.headers.get("content-type") == "application/json"
            else {"error": sophia_response.text}
        )
        payload_text = _stringify_error_payload(error_data)

        if _is_run_active_error(payload_text):
            error_thread_id, run_id = _extract_run_active_context(payload_text)
            if attempt < retry_attempts:
                attempt += 1
                sleep_seconds = retry_delay * attempt
                logger.warning(
                    "[chat] run active for thread=%s run=%s, retrying in %.1fs (%s/%s)",
                    error_thread_id or thread_id,
                    run_id,
                    sleep_seconds,
                    attempt,
                    retry_attempts,
                )
                time.sleep(sleep_seconds)
                continue

            return {
                "error": "SOPHIA function error",
                "error_code": "thread_run_active",
                "details": error_data,
                "thread_id": error_thread_id or thread_id,
                "run_id": run_id,
                "retry_after": retry_delay,
                "status_code": 409,
            }

        return {
            "error": "SOPHIA function error",
            "details": error_data,
            "status_code": sophia_response.status_code,
        }


@router.post("/tickets/confirm")
def confirm_chat_ticket_proposal(
    payload: dict,
    current_user=Depends(get_current_user),
) -> dict:
    """Create a Chat-proposed ticket only after the same user confirms it."""
    token = str((payload or {}).get("confirmation_token") or (payload or {}).get("confirmationToken") or "").strip()
    if not token:
        raise ValidationError("confirmation_token is required")
    if not _can_confirm_chat_ticket(current_user):
        raise ForbiddenError("Role is not allowed to confirm AI ticket proposals")
    try:
        claims = jwt.decode(token, get_jwt_secret_key(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise ValidationError("Ticket confirmation is invalid or expired") from exc

    tenant_id = effective_tenant_id_of(current_user)
    if (
        claims.get("type") != _TICKET_CONFIRMATION_TYPE
        or claims.get("scope") != _TICKET_CONFIRMATION_SCOPE
        or str(claims.get("sub") or "") != str(current_user.id)
        or int(claims.get("actor_user_id") or 0) != int(current_user.id)
        or int(claims.get("effective_tenant_id") or 0) != int(tenant_id)
        or normalize_role(claims.get("actor_role")) != normalize_role(current_user.role)
        or bool(claims.get("delegation_active")) != bool(getattr(current_user, "delegation_active", False))
    ):
        raise ForbiddenError("Ticket confirmation does not match authenticated context")

    action_plan = claims.get("action_plan")
    if not isinstance(action_plan, dict):
        raise ValidationError("Ticket confirmation proposal is invalid")
    subject = str(action_plan.get("subject") or "").strip()
    if not subject:
        raise ValidationError("Ticket proposal subject is required")

    result = _create_ticket_from_confirmed_proposal(
        tenant_id=int(tenant_id),
        subject=subject,
        description=str(action_plan.get("description") or ""),
        severity=str(action_plan.get("severity") or "medium"),
        metadata={
            "source": "sophia_chat_confirmed",
            "proposal_request_id": claims.get("request_id"),
        },
        user_id=int(current_user.id),
    )
    logger.info(
        "Confirmed SOPHIA ticket proposal: tenant=%s user=%s ticket=%s",
        tenant_id,
        current_user.id,
        result["ticket_id"],
    )
    return {
        "message": "Ticket created from confirmed SOPHIA proposal",
        "ticket_created": True,
        "ticket_id": result["ticket_id"],
        "ticket": result["ticket"],
    }


def _create_ticket_from_confirmed_proposal(**kwargs) -> dict:
    """Late import keeps the Chat route lightweight; use the existing ticket store only after approval."""
    from src.shared.tickets_store import create_ticket_from_agent

    return create_ticket_from_agent(**kwargs)
