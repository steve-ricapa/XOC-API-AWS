"""IC-1 LOCAL backend client. No key, provider, policy copy or cloud configuration.

SDK dependencies are optional and imported only in local MCP composition. The
assertion issuer is injected by the test harness, from authenticated ToolContext.
"""
import asyncio
import base64
from datetime import timedelta
import json
import math
import re
import time
from urllib.parse import urlsplit

from src.tool_gateway.schemas import ToolDecision, ToolResult

PROTOCOL = "2025-11-25"
MAX_BYTES = 262144
READ_TOOLS = frozenset(("xoc.tickets.list", "xoc.tickets.get", "xoc.documents.list",
                        "xoc.notifications.unread_count"))


def local_endpoint(url):
    p = urlsplit(url)
    if (p.scheme != "http" or p.hostname != "127.0.0.1" or not p.port
            or p.username or p.password or p.query or p.fragment or p.path != "/mcp"
            or p.netloc != f"127.0.0.1:{p.port}"):
        raise ValueError("IC1_LOOPBACK_ENDPOINT_REQUIRED")
    return url


def unavailable(code="mcp_unavailable"):
    return ToolResult(ToolDecision.DENIED, code=code)


def validate_result(wire, context, request):
    """Validate exact L6 model projection, including request/tool binding."""
    p = wire.structuredContent
    if (not isinstance(p, dict) or set(p) != {
            "request_id", "tool_name", "status", "code", "data", "audit_id"}
            or p["request_id"] != context.request_id or p["tool_name"] != request.tool_name
            or p["status"] not in ("allowed", "denied")
            or type(p["code"]) is not str or not re.fullmatch(r"[a-z0-9_]{1,80}", p["code"])
            or type(p["audit_id"]) is not str or not re.fullmatch(r"[a-f0-9]{24}", p["audit_id"])
            or wire.isError != (p["status"] != "allowed")
            or len(wire.content) != 1 or wire.content[0].type != "text"
            or json.loads(wire.content[0].text) != p
            or len(json.dumps(p, allow_nan=False).encode()) > MAX_BYTES):
        raise ValueError("IC1_INVALID_RESULT")
    if p["status"] == "allowed":
        if request.tool_name not in READ_TOOLS or p["code"] != "allowed" or type(p["data"]) is not dict:
            raise ValueError("IC1_INVALID_RESULT")
    elif p["data"] is not None:
        raise ValueError("IC1_INVALID_RESULT")
    return ToolResult(ToolDecision(p["status"]), data=p["data"], code=p["code"], audit_id=p["audit_id"])


class LocalMCPExecution:
    """Trusted endpoint + fixture issuer. One fresh SDK session per execution."""
    def __init__(self, endpoint, assertion_issuer):
        self.endpoint = local_endpoint(endpoint)
        self.assertion_issuer = assertion_issuer
        self.measurements = []  # safe stage timings only; bounded for local harness

    def execute(self, context, request, *, deadline):
        started = time.monotonic()
        row = {"transport": "mcp", "initialize_count": 0, "tools_call_count": 0}
        try:
            remaining = deadline - started
            if not math.isfinite(remaining) or remaining < 1.25:
                return unavailable("mcp_deadline")
            remaining = min(3.5, remaining)
            if context.source != "sophia_chat" or request.request_id != context.request_id:
                return unavailable("mcp_invalid_context")
            # Signed integer deadline is rounded DOWN, never extending Chat's budget.
            wall_deadline = int(time.time() + remaining)
            envelope = self.assertion_issuer(context, request, wall_deadline)
            if (envelope["request_id"] != context.request_id
                    or envelope["tool"] != {"name": request.tool_name, "arguments": request.arguments}):
                return unavailable("mcp_invalid_context")
            encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=True, allow_nan=False).encode()
            if len(encoded) > 16384:
                return unavailable("mcp_invalid_context")
            timeout = min(deadline-time.monotonic(), wall_deadline-time.time())
            if timeout <= 0:
                return unavailable("mcp_deadline")
            # Absolute monotonic end includes lazy SDK import/session setup time.
            wire = asyncio.run(self._call(encoded, request, time.monotonic()+timeout, row))
            result = validate_result(wire, context, request)
            row["code"] = result.code
            return result
        except TimeoutError:
            row["code"] = "mcp_deadline"
            return unavailable("mcp_deadline")
        except Exception:
            # No raw SDK/provider exception, no alternate executor, no replay.
            row["code"] = "mcp_unavailable"
            return unavailable()
        finally:
            row["total_seconds"] = time.monotonic()-started
            if len(self.measurements) < 100:
                self.measurements.append(row)

    async def _call(self, encoded, request, end, row):
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        endpoint = self.endpoint
        timeout = end-time.monotonic()
        if timeout <= 0:
            raise TimeoutError()

        class BoundedStream(httpx.AsyncByteStream):
            def __init__(self, stream):
                self.stream = stream

            async def __aiter__(self):
                size = 0
                async for chunk in self.stream:
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("IC1_RESPONSE_LIMIT")
                    yield chunk

            async def aclose(self):
                await self.stream.aclose()

        class LocalTransport(httpx.AsyncBaseTransport):
            def __init__(self):
                self.inner = httpx.AsyncHTTPTransport(retries=0)

            async def handle_async_request(self, req):
                if str(req.url) != endpoint:
                    raise ValueError("IC1_ENDPOINT_MISMATCH")
                response = await self.inner.handle_async_request(req)
                # Also block SDK-managed redirects before signed headers can replay.
                if 300 <= response.status_code < 400:
                    await response.aclose()
                    raise ValueError("IC1_REDIRECT_BLOCKED")
                response.stream = BoundedStream(response.stream)
                return response

            async def aclose(self):
                await self.inner.aclose()

        async def operation():
            headers = {"X-XOC-Lab-Envelope": base64.urlsafe_b64encode(encoded).decode().rstrip("="),
                       "MCP-Protocol-Version": PROTOCOL}
            async with httpx.AsyncClient(headers=headers, timeout=timeout, trust_env=False,
                    follow_redirects=False, transport=LocalTransport()) as http:
                async with streamable_http_client(endpoint, http_client=http,
                        terminate_on_close=False) as (read, write, session_id):
                    async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=timeout)) as session:
                        begin = time.monotonic()
                        row["initialize_count"] += 1
                        initialized = await session.initialize()
                        row["initialize_seconds"] = time.monotonic()-begin
                        if initialized.protocolVersion != PROTOCOL or session_id() is not None:
                            raise ValueError("IC1_PROTOCOL_MISMATCH")
                        begin = time.monotonic()
                        row["tools_call_count"] += 1
                        result = await session.call_tool(request.tool_name, request.arguments)
                        row["call_seconds"] = time.monotonic()-begin
                        return result

        return await asyncio.wait_for(operation(), timeout)
