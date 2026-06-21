import json
import logging
import os
import re
import time
from datetime import datetime, timedelta

import requests
from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import AgentSession, Company, CompanyRuntimeSettings
from src.shared.auth import create_access_token
from src.shared.capabilities import collect_automation_capabilities
from src.shared.config import get_settings
from src.shared.dependencies import get_current_user
from src.shared.errors import AppError, UnauthorizedError, ValidationError


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])


_AFFINITY_COOKIE_KEYS = ("ARRAffinity", "ARRAffinitySameSite")
_SESSION_AFFINITY: dict = {}
_DEFAULT_CHAT_TIMEOUT = 60
_RUN_ACTIVE_MESSAGE = "Can't add messages to thread"
_THREAD_ID_PATTERN = re.compile(r"(thread_[A-Za-z0-9]+)")
_RUN_ID_PATTERN = re.compile(r"(run_[A-Za-z0-9]+)")
_RUNTIME_SETTINGS_MISSING_MESSAGE = "Runtime settings not configured for this company"


def _get_runtime_settings(session: Session, company_id: int) -> CompanyRuntimeSettings:
    settings = session.scalar(
        select(CompanyRuntimeSettings).where(
            CompanyRuntimeSettings.company_id == company_id,
            CompanyRuntimeSettings.is_active == True,
        )
    )
    if not settings:
        raise UnauthorizedError(_RUNTIME_SETTINGS_MISSING_MESSAGE)
    return settings


def _resolve_agent_routes(session: Session, company_id: int) -> dict[str, str]:
    settings = get_settings()
    global_base_url = (settings.agents_function_base_url or "").strip()
    global_sophia = (settings.agents_function_route_sophia or "/api/agents/SophiaDurableAgent/run").strip()
    global_history = (settings.agents_function_route_sophia_history or "/api/agents/SophiaDurableAgent/history").strip()
    global_delete = (settings.agents_function_route_sophia_delete or "/api/agents/SophiaDurableAgent/threads").strip()
    global_victor = (settings.agents_function_route_victor or "/api/agents/VictorDurableAgent/run").strip()

    if global_base_url:
        return {
            "function_base_url": global_base_url,
            "function_route_sophia": global_sophia,
            "function_route_sophia_history": global_history,
            "function_route_sophia_delete": global_delete,
            "function_route_victor": global_victor,
        }

    runtime_settings = _get_runtime_settings(session, company_id)
    return {
        "function_base_url": runtime_settings.function_base_url,
        "function_route_sophia": runtime_settings.function_route_sophia or "/api/agents/SophiaDurableAgent/run",
        "function_route_sophia_history": runtime_settings.function_route_sophia_history or "/api/agents/SophiaDurableAgent/history",
        "function_route_sophia_delete": runtime_settings.function_route_sophia_delete or "/api/agents/SophiaDurableAgent/threads",
        "function_route_victor": runtime_settings.function_route_victor or "/api/agents/VictorDurableAgent/run",
    }


def _is_demo_company(current_user, db_session: Session) -> bool:
    user_company = getattr(current_user, "company", None)
    if not user_company:
        user_company = db_session.get(Company, current_user.company_id)
    plan_status = (user_company.plan_status or "").strip().upper() if user_company else ""
    return plan_status == "DEMO"


def _build_agent_invoke_token(company_id: int, agent_type: str) -> str:
    claims = {
        "scopes": ["agent:invoke"],
        "company_id": company_id,
        "agent_type": agent_type,
    }
    return create_access_token(
        identity=f"agent-runtime-{company_id}-{agent_type}",
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
    return cleaned or payload


def _build_session_title(message: str, max_length: int = 160) -> str:
    normalized = (message or "").strip()
    if not normalized:
        return "Conversacion SOPHIA"
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length].rstrip()


@router.get("/sessions")
def list_chat_sessions(current_user=Depends(get_current_user), db_session: Session = Depends(get_db_session)) -> dict:
    limit_raw = None
    try:
        limit = int(limit_raw) if limit_raw else 50
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))

    sessions = db_session.execute(
        select(AgentSession).where(
            AgentSession.company_id == current_user.company_id,
            AgentSession.user_id == current_user.id,
            AgentSession.purpose == "sophia_chat",
        ).order_by(AgentSession.last_activity_at.desc()).limit(limit)
    ).scalars().all()

    return {"sessions": [s.to_dict() for s in sessions], "count": len(sessions)}


@router.get("/history")
def chat_history(
    current_user=Depends(get_current_user),
    db_session: Session = Depends(get_db_session),
    companyId: str = None,
    session_id: str = None,
    sessionId: str = None,
    thread_id: str = None,
    threadId: str = None,
    limit: int = None,
    order: str = None,
) -> dict:
    if _is_demo_company(current_user, db_session):
        raise ValidationError("Chat history is disabled in demo mode")

    company_id = companyId or current_user.company_id
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
                AgentSession.company_id == company_id,
                AgentSession.user_id == current_user.id,
            )
        ).scalar_one_or_none()
        if not chat_session:
            raise ValidationError("Agent session not found")
        if not resolved_thread_id:
            resolved_thread_id = chat_session.external_thread_id

    if not resolved_thread_id:
        raise ValidationError("thread_id or session_id is required")

    runtime_settings = _resolve_agent_routes(db_session, int(company_id))
    function_base_url = runtime_settings["function_base_url"]
    history_route = runtime_settings["function_route_sophia_history"]

    if not function_base_url:
        raise ValidationError("SVAFUNC function_base_url is not configured for this company")

    params = {"thread_id": resolved_thread_id, "limit": str(limit), "order": order}
    full_url = f"{function_base_url.rstrip('/')}{history_route}"
    service_token = _build_agent_invoke_token(int(company_id), "SOPHIA")

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
    chat_session = db_session.execute(
        select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.company_id == current_user.company_id,
            AgentSession.user_id == current_user.id,
        )
    ).scalar_one_or_none()
    if not chat_session:
        raise ValidationError("Agent session not found")

    runtime_settings = _resolve_agent_routes(db_session, current_user.company_id)
    function_base_url = runtime_settings["function_base_url"]
    delete_route = runtime_settings["function_route_sophia_delete"]

    if not function_base_url:
        raise ValidationError("SVAFUNC function_base_url is not configured for this company")

    service_token = _build_agent_invoke_token(current_user.company_id, "SOPHIA")

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
) -> dict:
    if not payload:
        raise ValidationError("Request body is required")

    message = payload.get("message")
    if not message:
        raise ValidationError("Missing required field: message")

    company_id = payload.get("companyId") or current_user.company_id
    demo_mode = _is_demo_company(current_user, db_session)

    runtime_settings = _resolve_agent_routes(db_session, int(company_id))

    chat_session = None
    session_id = payload.get("sessionId") or payload.get("session_id")
    force_new_session = bool(payload.get("new_session") or payload.get("newSession"))
    session_key = _normalize_session_id(session_id)

    if demo_mode:
        force_new_session = False
        chat_session = db_session.execute(
            select(AgentSession).where(
                AgentSession.company_id == company_id,
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
                AgentSession.company_id == company_id,
                AgentSession.user_id == current_user.id,
            )
        ).scalar_one_or_none()
        if not chat_session:
            raise ValidationError("Agent session not found")
    else:
        chat_session = db_session.execute(
            select(AgentSession).where(
                AgentSession.company_id == company_id,
                AgentSession.user_id == current_user.id,
                AgentSession.purpose == "sophia_chat",
            ).order_by(AgentSession.last_activity_at.desc())
        ).scalars().first()

    function_base_url = runtime_settings["function_base_url"]
    function_route = runtime_settings["function_route_sophia"]

    if not function_base_url:
        raise ValidationError("SVAFUNC function_base_url is not configured for this company")

    service_token = _build_agent_invoke_token(int(company_id), "SOPHIA")

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
    else:
        automation_capabilities = collect_automation_capabilities(db_session, company_id)
        if automation_capabilities:
            sophia_payload["automation_capabilities"] = automation_capabilities
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
            response_thread_id = response_payload.get("thread_id") if isinstance(response_payload, dict) else None

            if response_thread_id:
                if chat_session is None:
                    chat_session = AgentSession(
                        company_id=company_id,
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
