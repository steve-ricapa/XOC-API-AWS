"""Safe structured logging for tool-policy decisions.

Arguments are never logged here: they may contain customer data or sensitive
content.  This is intentionally log-only in Fase B; it adds no new RDS table
or persistence path.
"""
from __future__ import annotations

import logging
from hashlib import sha256

from src.tool_gateway.schemas import ToolContext, ToolDefinition, ToolPolicyDecision, ToolRequest


logger = logging.getLogger(__name__)


def tool_audit_id(context: ToolContext, request: ToolRequest) -> str:
    request_id = request.request_id or context.request_id or "no-request-id"
    material = f"{request_id}|{request.tool_name}|{context.effective_tenant_id}|{context.user_id}"
    return sha256(material.encode("utf-8")).hexdigest()[:24]


def log_tool_decision(
    *,
    context: ToolContext,
    request: ToolRequest,
    decision: ToolPolicyDecision,
    definition: ToolDefinition | None,
) -> str:
    audit_id = tool_audit_id(context, request)
    logger.info(
        "tool_gateway_decision audit_id=%s request_id=%s tool_name=%s effective_tenant_id=%s user_id=%s role=%s source=%s decision=%s code=%s risk_level=%s access_level=%s",
        audit_id,
        request.request_id or context.request_id or "",
        request.tool_name,
        context.effective_tenant_id,
        context.user_id,
        context.role,
        context.source,
        decision.decision.value,
        decision.code,
        definition.risk_level.value if definition else "UNKNOWN",
        definition.access_level.value if definition else "UNKNOWN",
    )
    return audit_id
