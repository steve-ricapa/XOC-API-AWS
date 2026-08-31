"""Execution of the small, fixed read-only XOC tool allowlist."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from src.tool_gateway.audit import log_tool_decision
from src.tool_gateway.policy import evaluate_tool_request
from src.tool_gateway.registry import TOOL_REGISTRY, get_tool_definition
from src.tool_gateway.schemas import ToolContext, ToolDecision, ToolHandler, ToolRequest, ToolResult


logger = logging.getLogger(__name__)


def _allowed_arguments(arguments: Mapping[str, Any], allowed: set[str]) -> dict[str, Any]:
    unknown = set(arguments) - allowed
    if unknown:
        raise ValueError("Unsupported tool arguments")
    return dict(arguments)


def _bounded_limit(value: Any, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if limit < 1 or limit > maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return limit


def _tickets_list(context: ToolContext, arguments: Mapping[str, Any]) -> dict[str, Any]:
    values = _allowed_arguments(arguments, {"status", "limit"})
    from src.shared.tickets_store import list_tenant_tickets

    status = values.get("status")
    if status is not None and not isinstance(status, str):
        raise ValueError("status must be a string")
    return list_tenant_tickets(
        int(context.effective_tenant_id),
        status=status,
        limit=_bounded_limit(values.get("limit"), default=50, maximum=200),
    )


def _tickets_get(context: ToolContext, arguments: Mapping[str, Any]) -> dict[str, Any]:
    values = _allowed_arguments(arguments, {"ticket_id"})
    ticket_id = str(values.get("ticket_id") or "").strip()
    if not ticket_id or len(ticket_id) > 256:
        raise ValueError("ticket_id is required")
    from src.shared.tickets_store import get_tenant_ticket_or_404, serialize_ticket

    return {"ticket": serialize_ticket(get_tenant_ticket_or_404(int(context.effective_tenant_id), ticket_id))}


def _documents_list(context: ToolContext, arguments: Mapping[str, Any]) -> dict[str, Any]:
    values = _allowed_arguments(arguments, {"status", "limit"})
    from src.reports.store import list_tenant_document_jobs

    status = values.get("status")
    if status is not None and not isinstance(status, str):
        raise ValueError("status must be a string")
    documents = list_tenant_document_jobs(
        int(context.effective_tenant_id),
        status=status,
        limit=_bounded_limit(values.get("limit"), default=50, maximum=200),
    )
    return {"documents": documents, "count": len(documents)}


def _notifications_unread_count(context: ToolContext, arguments: Mapping[str, Any]) -> dict[str, Any]:
    _allowed_arguments(arguments, set())
    from src.shared.user_notification_inbox import unread_count_for_user

    return {"count": unread_count_for_user(int(context.effective_tenant_id), int(context.user_id))}


DEFAULT_HANDLERS: dict[str, ToolHandler] = {
    "tickets.list": _tickets_list,
    "tickets.get": _tickets_get,
    "documents.list": _documents_list,
    "notifications.unread_count": _notifications_unread_count,
}


class ToolExecutor:
    """Policy-first executor with no dynamic imports, HTTP, SQL, or shell paths."""

    def __init__(
        self,
        *,
        registry: Mapping[str, Any] | None = None,
        handlers: Mapping[str, ToolHandler] | None = None,
    ) -> None:
        self._registry = registry or TOOL_REGISTRY
        self._handlers = dict(DEFAULT_HANDLERS)
        if handlers:
            self._handlers.update(handlers)

    def execute(self, context: ToolContext, request: ToolRequest) -> ToolResult:
        definition = self._registry.get(str(request.tool_name or "").strip())
        decision = evaluate_tool_request(context, request, definition=definition)
        audit_id = log_tool_decision(
            context=context,
            request=request,
            decision=decision,
            definition=definition,
        )
        if decision.decision is not ToolDecision.ALLOWED:
            return ToolResult(
                status=decision.decision,
                error=decision.reason,
                code=decision.code,
                audit_id=audit_id,
            )

        handler = self._handlers.get(definition.handler_name or "") if definition else None
        if handler is None:
            logger.error("tool_gateway_handler_missing audit_id=%s tool_name=%s", audit_id, request.tool_name)
            return ToolResult(
                status=ToolDecision.DENIED,
                error="Tool is not executable",
                code="handler_unavailable",
                audit_id=audit_id,
            )

        try:
            data = handler(context, request.arguments)
        except ValueError:
            logger.info("tool_gateway_invalid_arguments audit_id=%s tool_name=%s", audit_id, request.tool_name)
            return ToolResult(
                status=ToolDecision.DENIED,
                error="Tool arguments are invalid",
                code="invalid_arguments",
                audit_id=audit_id,
            )
        except Exception as exc:  # Do not surface provider, data, or credential details.
            logger.error(
                "tool_gateway_execution_failed audit_id=%s tool_name=%s error_type=%s",
                audit_id,
                request.tool_name,
                type(exc).__name__,
            )
            return ToolResult(
                status=ToolDecision.DENIED,
                error="Tool execution failed",
                code="execution_failed",
                audit_id=audit_id,
            )

        return ToolResult(status=ToolDecision.ALLOWED, data=data, code="allowed", audit_id=audit_id)
