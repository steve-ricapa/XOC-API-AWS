"""Governed internal tools for XOC AI runtimes.

This package is intentionally transport-agnostic.  It is not an MCP server and
does not expose an HTTP endpoint by itself; callers must build ``ToolContext``
from an authenticated XOC request before invoking it.
"""

from src.tool_gateway.executor import ToolExecutor
from src.tool_gateway.schemas import ToolContext, ToolRequest, ToolResult

__all__ = ["ToolContext", "ToolExecutor", "ToolRequest", "ToolResult"]
