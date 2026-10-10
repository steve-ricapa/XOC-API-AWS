"""SOPHIA-only, opt-in two-turn transport. XOC remains the tool authority.

No endpoint, credentials exchange, persistence, or runtime-selected destination.
The caller supplies its authenticated ToolContext and existing thread validator.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

import jwt
import requests

from src.shared.config import get_jwt_secret_key
from src.shared.context import normalize_role
from src.shared.errors import AppError
from src.tool_gateway.executor import ToolExecutor
from src.tool_gateway.schemas import ToolContext, ToolRequest, ToolResult, ToolDecision

PROTOCOL = "xoc-tool-gateway-v1"
AUDIENCE = "xoc-ai-tool-gateway"
CAPABILITY_TYPE = "sophia_tool_capability"
CAPABILITY_SECONDS = 120
TOOL_FIELDS = ("tool_request", "toolRequest", "tool_call", "toolCall")
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_SECRET = re.compile(r"(?i)(bearer\s+\S+|(?:access[_-]?token|refresh[_-]?token|password|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+)")


def enabled() -> bool:
    return os.getenv("SOPHIA_TOOL_GATEWAY_ENABLED", "false").strip().lower() == "true"


def body_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def capability(context: ToolContext, payload: dict) -> str:
    """Not an access token: no agent:invoke scope, no token-from-user changes."""
    now = int(time.time())
    return jwt.encode({
        "sub": "xoc-sophia-chat", "type": CAPABILITY_TYPE,
        "aud": AUDIENCE, "scope": "ai:tools", "agent_type": "SOPHIA",
        "actor_user_id": context.user_id, "actor_role": normalize_role(context.role),
        "effective_tenant_id": context.effective_tenant_id,
        "delegation_active": context.delegation_active,
        "request_id": context.request_id, "source": context.source,
        "phase": payload["phase"], "body_sha256": body_hash(payload),
        "iat": now, "exp": now + CAPABILITY_SECONDS,
    }, get_jwt_secret_key(), algorithm="HS256")


def safe_text(value, limit=1000) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid text")
    # Defense in depth; free text is not a DLP boundary. Never project token fields.
    return _SECRET.sub("[REDACTED]", _JWT.sub("[REDACTED]", value))[:limit]


def _row(row, fields, tenant_id):
    if not isinstance(row, dict):
        raise ValueError("Invalid tool result")
    for key in ("tenant_id", "tenantId"):
        if key in row and str(row[key]) != str(tenant_id):
            raise ValueError("Invalid result ownership")
    return {key: safe_text(value, 240) for key in fields
            if isinstance((value := row.get(key)), str)}


def model_result(result: ToolResult, tool_name: str, context: ToolContext) -> dict:
    """Project the existing ToolResult, not raw store rows or policy internals."""
    output = {"request_id": context.request_id, "tool_name": tool_name,
              "status": result.status.value, "code": "denied",
              "audit_id": result.audit_id, "data": None}
    if result.status is not ToolDecision.ALLOWED:
        return output
    try:
        data = result.data
        if not isinstance(data, dict):
            raise ValueError("Invalid result")
        ticket_fields = ("ticket_id", "id", "subject", "status", "severity", "created_at", "updated_at")
        document_fields = ("document_id", "document_type", "variant", "status", "created_at", "updated_at", "completed_at")
        if tool_name == "xoc.tickets.get":
            projected = {"ticket": _row(data["ticket"], ticket_fields, context.effective_tenant_id)}
        elif tool_name in ("xoc.tickets.list", "xoc.documents.list"):
            key = "tickets" if tool_name == "xoc.tickets.list" else "documents"
            rows = data[key]
            if not isinstance(rows, list) or len(rows) > 200:
                raise ValueError("Invalid result")
            fields = ticket_fields if key == "tickets" else document_fields
            # Check ownership for ALL rows before truncating the model projection.
            projected_rows = [_row(row, fields, context.effective_tenant_id) for row in rows]
            projected = {key: projected_rows[:50], "count": len(projected_rows[:50]), "truncated": len(rows) > 50}
        elif tool_name == "xoc.notifications.unread_count":
            count = data["count"]
            if type(count) is not int or count < 0:
                raise ValueError("Invalid count")
            projected = {"count": count}
        else:
            raise ValueError("No model projection for this tool")
        if len(json.dumps(projected, ensure_ascii=True)) > 64000:
            raise ValueError("Model projection too large")
        output.update(code="allowed", data=projected)
    except (ValueError, TypeError, KeyError):
        output.update(status="denied", code="unavailable", data=None)
    return output


def protocol_error(code="sophia_protocol_error"):
    return AppError("SOPHIA no pudo completar la consulta de forma segura. Intenta nuevamente.",
                    status_code=502, code=code)


def _validate_envelope(payload, context):
    if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL or payload.get("request_id") != context.request_id:
        raise protocol_error()


def _tool_request(payload, cleaner):
    present = [key for key in TOOL_FIELDS if key in payload]
    if not present:
        return None
    if len(present) != 1 or not isinstance(payload[present[0]], dict):
        raise protocol_error("sophia_malformed_tool_request")
    # Reuse the existing Chat alias parser. No second parser for those aliases.
    raw = cleaner(payload)["tool_request"]
    names = [raw[key] for key in ("tool_name", "toolName", "name") if key in raw]
    if not names or any(not isinstance(name, str) or not _NAME.fullmatch(name) for name in names) or len(set(names)) != 1:
        raise protocol_error("sophia_malformed_tool_request")
    if set(raw) - {"tool_name", "toolName", "name", "arguments"}:
        raise protocol_error("sophia_malformed_tool_request")
    return ToolRequest(tool_name=names[0], arguments=raw.get("arguments", {}))


def _public_reply(payload, *, allow_proposal):
    """Never return opaque continuation, runtime metadata or capability to clients."""
    text = payload.get("text") or payload.get("message")
    try:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Invalid text")
        result = {"text": safe_text(text, 8000)}
    except ValueError:
        raise protocol_error() from None
    if payload.get("thread_id"):
        result["thread_id"] = payload["thread_id"]
    if allow_proposal and isinstance(payload.get("action_plan"), dict):
        result["action_plan"] = {key: safe_text(value, 4000 if key == "description" else 240)
                                 for key in ("subject", "description", "severity")
                                 if isinstance((value := payload["action_plan"].get(key)), str)}
    return result


def finish_turn(payload, *, context, cleaner, validate_thread, post_result, allow_tools=True, deadline=None):
    """One executor invocation and at most one model continuation. No fallback."""
    _validate_envelope(payload, context)
    validate_thread(payload.get("thread_id"))
    tool_request = _tool_request(payload, cleaner)
    if tool_request is None:
        return _public_reply(payload, allow_proposal=True)
    if not allow_tools:
        raise protocol_error("sophia_tools_unavailable")
    continuation = payload.get("continuation")
    if not isinstance(continuation, str) or not 1 <= len(continuation) <= 8192 or not payload.get("thread_id"):
        raise protocol_error()

    from src.shared.sophia_tool_execution import selected_execution
    execution = selected_execution()
    request = ToolRequest(tool_request.tool_name, tool_request.arguments, context.request_id)
    if execution is None:
        result = ToolExecutor().execute(context, request)
    else:
        # Transport is selected by trusted composition/configuration, never body.
        # A missing budget or execution error is fail-closed, never local fallback.
        try:
            result = execution.execute(context, request, deadline=deadline)
        except Exception:
            result = ToolResult(ToolDecision.DENIED, code="mcp_unavailable")
    sanitized = model_result(result, tool_request.tool_name, context)
    body = {"protocol": PROTOCOL, "phase": "result", "request_id": context.request_id,
            "thread_id": payload["thread_id"], "continuation": continuation, "tool_result": sanitized}
    metadata = {"toolGateway": {"toolName": tool_request.tool_name,
                "status": sanitized["status"], "code": result.code if sanitized["code"] != "unavailable" else "unsafe_tool_result",
                "auditId": result.audit_id}}
    try:
        final = post_result(body)
        _validate_envelope(final, context)
        if final.get("thread_id") != payload["thread_id"]:
            raise protocol_error("sophia_thread_mismatch")
        # Do not parse/execute a second tool, even if the runtime misbehaves.
        if any(field in final for field in TOOL_FIELDS):
            answer = {"text": "La consulta se detuvo: SOPHIA solicito otra herramienta. No se ejecuto.",
                      "thread_id": payload["thread_id"]}
            metadata["toolGateway"]["completionCode"] = "tool_call_limit"
        else:
            answer = _public_reply(final, allow_proposal=False)
    except (requests.RequestException, ValueError, AppError):
        answer = {"text": "La herramienta fue procesada, pero SOPHIA no pudo preparar la respuesta final. No se reintento la herramienta.",
                  "thread_id": payload["thread_id"]}
        metadata["toolGateway"]["completionCode"] = "runtime_unavailable"
    answer["metadata"] = metadata
    return answer
