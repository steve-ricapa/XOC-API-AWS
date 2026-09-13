"""Server-only thread permissions, after the existing AgentSession ownership check."""
from __future__ import annotations

import os
import re
import time
import uuid

import jwt

from src.shared.config import get_jwt_secret_key
from src.shared.context import effective_tenant_id_of, require_tenant_read_access
from src.shared.errors import AppError, ForbiddenError, ValidationError
from src.shared.sophia_tool_chat import body_hash, safe_text

AUDIENCE = 'xoc-sophia-thread-operation'
CAPABILITY_TYPE = 'sophia_thread_capability'
REQUEST_HEADER = 'X-XOC-Request-Id'


def enabled() -> bool:
    value = os.getenv('SOPHIA_THREAD_CAPABILITIES_ENABLED', 'false').strip().lower()
    if value not in ('true', 'false'):
        raise AppError('Invalid SOPHIA thread configuration', status_code=503,
                       code='sophia_thread_configuration')
    return value == 'true'


def unavailable():
    return AppError('SOPHIA no pudo completar la operacion de conversacion. Intenta nuevamente.',
                    status_code=502, code='sophia_thread_unavailable')


def headers_for_owned_session(current_user, chat_session, operation, *, limit=None, order=None):
    require_tenant_read_access(current_user)
    tenant_id, user_id = effective_tenant_id_of(current_user), current_user.id
    if (type(tenant_id) is not int or tenant_id <= 0 or type(user_id) is not int or user_id <= 0
            or chat_session.tenant_id != tenant_id or chat_session.user_id != user_id):
        raise ForbiddenError('Agent session ownership required')
    thread_id = chat_session.external_thread_id
    if not isinstance(thread_id, str) or not re.fullmatch(r'thread[_-][A-Za-z0-9_-]{1,190}', thread_id):
        raise ValidationError('Invalid SOPHIA thread_id')
    if operation not in ('history', 'delete'):
        raise ValidationError('Invalid SOPHIA thread operation')
    params = {'operation': operation, 'thread_id': thread_id}
    if operation == 'history':
        if type(limit) is not int or not 1 <= limit <= 100 or order not in ('asc', 'desc'):
            raise ValidationError('Invalid SOPHIA history parameters')
        params.update(limit=limit, order=order)
    now, request_id = int(time.time()), uuid.uuid4().hex
    claims = {
        'sub': 'xoc-sophia-chat', 'type': CAPABILITY_TYPE, 'aud': AUDIENCE,
        'scope': 'sophia:thread:' + operation, 'agent_type': 'SOPHIA', 'source': 'sophia_chat',
        'operation': operation, 'thread_id': thread_id, 'actor_user_id': user_id,
        'effective_tenant_id': tenant_id, 'request_id': request_id,
        'parameters_sha256': body_hash(params), 'iat': now, 'exp': now + 60,
    }
    try:
        token = jwt.encode(claims, get_jwt_secret_key(), algorithm='HS256')
    except Exception:
        raise AppError('SOPHIA thread signing unavailable', status_code=503,
                       code='sophia_thread_configuration') from None
    return {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token,
            REQUEST_HEADER: request_id}


def history_payload(payload, thread_id):
    """Only the existing public history fields, never arbitrary provider metadata."""
    if (not isinstance(payload, dict) or payload.get('thread_id') != thread_id
            or not isinstance(payload.get('messages'), list) or len(payload['messages']) > 100):
        raise unavailable()
    messages = []
    for item in payload['messages']:
        if not isinstance(item, dict) or not isinstance(item.get('text'), str):
            raise unavailable()
        row = {}
        for key in ('id', 'role', 'text', 'created_at'):
            value = item.get(key)
            if value is not None and not isinstance(value, (str, int, float)):
                raise unavailable()
            row[key] = safe_text(value, len(value)) if isinstance(value, str) else value
        messages.append(row)
    return {'thread_id': thread_id, 'messages': messages, 'count': len(messages)}
