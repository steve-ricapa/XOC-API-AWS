"""Single-use LAB_ONLY MCP client, official Streamable HTTP and ClientSession."""
import asyncio
import base64
import datetime
import json
import time
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from xoc_mcp_lab.request_envelope import canonical, verify_envelope

HEADER = 'X-XOC-Lab-Envelope'
PROTOCOL = '2025-11-25'
OUTPUT_LIMIT = 262144


def local_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            parsed.path != '/mcp' or not parsed.port):
        raise ValueError('LAB_LOOPBACK_URL_REQUIRED')
    return url


def headers_for(envelope):
    data = canonical(envelope).encode('utf-8')
    if len(data) > 16384:
        raise ValueError('LAB_ENVELOPE_LIMIT')
    return {HEADER: base64.urlsafe_b64encode(data).decode('ascii').rstrip('='),
            'MCP-Protocol-Version': PROTOCOL}


class LimitedStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > OUTPUT_LIMIT:
                raise ValueError('LAB_HTTP_RESPONSE_LIMIT')
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class LoopbackTransport(httpx.AsyncBaseTransport):
    def __init__(self, approved_url):
        self.approved_url = local_url(approved_url)
        self.inner = httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        if local_url(str(request.url)) != self.approved_url:
            raise ValueError('LAB_APPROVED_ENDPOINT_REQUIRED')
        response = await self.inner.handle_async_request(request)
        # The SDK follows eligible redirects itself, independently of HTTPX's
        # follow_redirects setting. Reject before it can replay a signed header,
        # including a Location that points back to this exact approved endpoint.
        if 300 <= response.status_code < 400:
            try:
                await response.aclose()
            except Exception:
                pass
            raise ValueError('LAB_REDIRECT_BLOCKED') from None
        response.stream = LimitedStream(response.stream)
        return response

    async def aclose(self):
        await self.inner.aclose()


class QueryClient:
    """One call per wrapper, no retries/chains/replay ledger. New wrapper=new request."""
    def __init__(self, url):
        self.url = local_url(url)
        self.used = False
        self.measurements = {}

    async def run(self, envelope, *, timeout=3.5, include_list=False):
        started = time.monotonic()
        if self.used:
            return {'status': 'denied', 'code': 'lab_call_limit', 'total_seconds': time.monotonic()-started}
        self.used = True
        timeout = min(float(timeout), 5.0)
        try:
            verified = verify_envelope(envelope)
            headers = headers_for(envelope)
            timeout = min(timeout, verified.deadline-time.time())
            if timeout <= 0:
                raise ValueError('LAB_DEADLINE')
        except Exception:
            return {'status': 'denied', 'code': 'lab_invalid_context', 'total_seconds': time.monotonic()-started}
        self.measurements['preparation_seconds'] = time.monotonic()-started
        final_work = None

        async def operation():
            nonlocal final_work
            async with httpx.AsyncClient(headers=headers, timeout=timeout, trust_env=False,
                    follow_redirects=False, transport=LoopbackTransport(self.url)) as client:
                async with streamable_http_client(self.url, http_client=client,
                        terminate_on_close=False) as (read, write, session_id):
                    async with ClientSession(read, write,
                            read_timeout_seconds=datetime.timedelta(seconds=timeout)) as session:
                        begin = time.monotonic()
                        initialized = await session.initialize()
                        self.measurements['initialize_seconds'] = time.monotonic()-begin
                        if initialized.protocolVersion != PROTOCOL or session_id() is not None:
                            raise ValueError('LAB_PROTOCOL_OR_SESSION')
                        tools = await session.list_tools() if include_list else None
                        begin = time.monotonic()
                        result = await session.call_tool(verified.tool_name,
                            json.loads(verified.arguments_json))
                        self.measurements['call_seconds'] = time.monotonic()-begin
                        if result.structuredContent is not None:
                            if len(result.content) != 1 or json.loads(result.content[0].text) != result.structuredContent:
                                raise ValueError('LAB_TEXT_STRUCTURED_MISMATCH')
                        final_work = time.monotonic()
                        return result, tools
        try:
            remaining = timeout-(time.monotonic()-started)
            if remaining <= 0:
                raise asyncio.TimeoutError()
            result, tools = await asyncio.wait_for(operation(), remaining)
            payload = result.structuredContent or {}
            tool_status = payload.get('status', 'error')
            code = payload.get('code', 'lab_missing_result')
            # Canonical projection may represent execution_failed as "denied".
            # Preserve that wire contract, but do not count a provider failure as
            # an authorization denial in laboratory acceptance statistics.
            status = 'error' if code in {'execution_failed','unsafe_tool_result','unavailable'} else tool_status
            outcome = {'status': status, 'tool_status': tool_status, 'code': code,
                'result': payload, 'is_error': result.isError,
                'tools': [t.name for t in tools.tools] if tools else None}
        except asyncio.TimeoutError:
            outcome = {'status': 'timeout', 'code': 'lab_deadline'}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            def safe_code(error):
                from mcp.shared.exceptions import McpError
                if isinstance(error, McpError):
                    message = error.error.message
                    if message in {'LAB_CAPACITY_BUSY', 'LAB_REQUEST_TIMEOUT', 'LAB_OUTPUT_LIMIT'}:
                        return message.lower()
                for child in getattr(error, 'exceptions', ()):
                    found = safe_code(child)
                    if found:
                        return found
                return None
            code = safe_code(exc) or 'lab_transport_error'
            outcome = {'status': 'timeout' if code=='lab_request_timeout' else 'denied' if code=='lab_capacity_busy' else 'error',
                'code': code, 'exception_type': type(exc).__name__}
        done = time.monotonic()
        self.measurements['request_cleanup_seconds'] = done-final_work if final_work else None
        outcome['total_seconds'] = done-started
        outcome['measurements'] = dict(self.measurements)
        outcome['over_3_5'] = outcome['total_seconds'] > 3.5
        outcome['over_5'] = outcome['total_seconds'] > 5
        return outcome
