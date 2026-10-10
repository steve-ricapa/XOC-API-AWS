"""Small execution seam. No client-controlled transport selection or fallback."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from typing import Protocol

from src.tool_gateway.schemas import ToolContext, ToolRequest, ToolResult


class SophiaToolExecutionPort(Protocol):
    def execute(self, context: ToolContext, request: ToolRequest, *, deadline: float) -> ToolResult: ...


# Configuration installed by trusted LOCAL test composition, never request fields.
# Per-context, not a mutable process-wide human identity / shared connection.
_local_port = ContextVar("sophia_local_execution_port", default=None)


@contextmanager
def local_execution(port: SophiaToolExecutionPort):
    """IC-1 only; no env-driven plugin loading or production activation."""
    if os.getenv("AWS_LAMBDA_FUNCTION_NAME") or os.getenv("WEBSITE_INSTANCE_ID"):
        raise RuntimeError("Local execution is not available in cloud runtimes")
    token = _local_port.set(port)
    try:
        yield
    finally:
        _local_port.reset(token)


def selected_local_port():
    return _local_port.get()
