from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import AgentInstance, LiveVoiceMessage, LiveVoiceSession, User
from src.shared.context import effective_tenant_id_of, log_audit, require_tenant_read_access
from src.shared.dependencies import get_current_user
from src.shared.errors import ForbiddenError, NotFoundError, ValidationError


router = APIRouter(prefix="/live-voice-sessions", tags=["live-voice"])

_ALLOWED_ROLES = {"USER", "ASSISTANT"}
_ALLOWED_STATUSES = {"ACTIVE", "ENDED"}


def _resolve_tenant_id(current_user: User, tenant_raw) -> int:
    require_tenant_read_access(current_user)
    effective_tenant_id = effective_tenant_id_of(current_user)
    if tenant_raw is None or tenant_raw == "":
        return effective_tenant_id
    try:
        requested_tenant_id = int(tenant_raw)
    except (TypeError, ValueError):
        raise ValidationError("tenantId must be an integer")
    if requested_tenant_id != int(effective_tenant_id):
        raise ValidationError("Requested tenant does not match delegated tenant context")
    return requested_tenant_id


def _get_session_or_404(session: Session, tenant_id: int, session_id: str) -> LiveVoiceSession:
    voice_session = session.scalar(
        select(LiveVoiceSession).where(
            LiveVoiceSession.id == session_id,
            LiveVoiceSession.tenant_id == tenant_id,
        )
    )
    if not voice_session:
        raise NotFoundError("Live voice session not found")
    return voice_session


def _normalize_message_role(value) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in _ALLOWED_ROLES:
        raise ValidationError("role must be USER or ASSISTANT")
    return normalized


def _normalize_status(value) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in _ALLOWED_STATUSES:
        raise ValidationError("status must be ACTIVE or ENDED")
    return normalized


@router.post("")
def create_live_voice_session(
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    data = payload or {}
    tenant_id = _resolve_tenant_id(current_user, data.get("tenantId") or data.get("tenant_id"))
    agent_instance_id = data.get("agentInstanceId") or data.get("agent_instance_id")
    session_name = data.get("sessionName") or data.get("session_name")
    agent_type = str(data.get("agentType") or data.get("agent_type") or "SOPHIA").strip().upper() or "SOPHIA"

    if agent_instance_id:
        agent_instance = session.scalar(
            select(AgentInstance).where(
                AgentInstance.id == str(agent_instance_id),
                AgentInstance.tenant_id == tenant_id,
            )
        )
        if not agent_instance:
            raise ForbiddenError("Agent instance access forbidden")

    voice_session = LiveVoiceSession(
        tenant_id=tenant_id,
        agent_type=agent_type,
        agent_instance_id=str(agent_instance_id) if agent_instance_id else None,
        session_name=str(session_name).strip() if session_name else None,
        status="ACTIVE",
    )
    session.add(voice_session)
    session.flush()
    log_audit(
        session,
        actor_user_id=current_user.id,
        action="CREATE",
        entity_type="LIVE_VOICE_SESSION",
        entity_id=voice_session.id,
        payload={"tenant_id": tenant_id, "agent_type": voice_session.agent_type},
    )
    session.commit()
    session.refresh(voice_session)
    return voice_session.to_dict()


@router.post("/{session_id}/messages")
def create_live_voice_message(
    session_id: str,
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    data = payload or {}
    tenant_id = _resolve_tenant_id(current_user, None)
    voice_session = _get_session_or_404(session, tenant_id, session_id)
    if voice_session.status != "ACTIVE":
        raise ValidationError("Live voice session is not active")

    role = _normalize_message_role(data.get("role"))
    content = str(data.get("content") or "").strip()
    if not content:
        raise ValidationError("content is required")

    message = LiveVoiceMessage(session_id=voice_session.id, role=role, content=content)
    session.add(message)
    log_audit(
        session,
        actor_user_id=current_user.id,
        action="CREATE",
        entity_type="LIVE_VOICE_MESSAGE",
        entity_id=message.id,
        payload={"session_id": voice_session.id, "role": role},
    )
    session.commit()
    session.refresh(message)
    return message.to_dict()


@router.get("/{session_id}")
def get_live_voice_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    tenant_id = _resolve_tenant_id(current_user, None)
    voice_session = _get_session_or_404(session, tenant_id, session_id)
    return voice_session.to_dict(include_messages=True)


@router.get("")
def list_live_voice_sessions(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    tenantId: str | None = None,
    tenant_id: str | None = None,
) -> dict:
    resolved_tenant_id = _resolve_tenant_id(current_user, tenantId or tenant_id)
    sessions = session.scalars(
        select(LiveVoiceSession)
        .where(LiveVoiceSession.tenant_id == resolved_tenant_id)
        .order_by(LiveVoiceSession.created_at.desc())
    ).all()
    return {"sessions": [item.to_dict() for item in sessions], "count": len(sessions)}


@router.patch("/{session_id}")
def update_live_voice_session(
    session_id: str,
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    data = payload or {}
    tenant_id = _resolve_tenant_id(current_user, None)
    voice_session = _get_session_or_404(session, tenant_id, session_id)

    status = data.get("status")
    session_name = data.get("sessionName") or data.get("session_name")
    if status is not None:
        normalized_status = _normalize_status(status)
        voice_session.status = normalized_status
        if normalized_status == "ENDED":
            voice_session.ended_at = voice_session.ended_at or datetime.utcnow()
        else:
            voice_session.ended_at = None
    if session_name is not None:
        voice_session.session_name = str(session_name).strip() or None

    log_audit(
        session,
        actor_user_id=current_user.id,
        action="UPDATE",
        entity_type="LIVE_VOICE_SESSION",
        entity_id=voice_session.id,
        payload={"status": voice_session.status, "ended_at": voice_session.ended_at.isoformat() if voice_session.ended_at else None},
    )
    session.commit()
    session.refresh(voice_session)
    return voice_session.to_dict()
