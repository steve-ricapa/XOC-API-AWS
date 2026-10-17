"""One fresh authorized fixture query over public SDK + HTTPX ASGI APIs."""
from __future__ import annotations

import asyncio
import datetime
import json
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from xoc_mcp_lab_harness.signing import envelope
from xoc_mcp_lab_l4.client import PROTOCOL, LimitedStream, headers_for


class _PrivateASGI:
    """Only the in-process transport holds this unforgeable object reference."""
    def __init__(self, app, capability):
        self.app, self.capability = app, capability

    async def __call__(self, scope, receive, send):
        await self.app(dict(scope, xoc_l6_private_capability=self.capability), receive, send)


class _BoundedASGITransport(httpx.AsyncBaseTransport):
    def __init__(self, app, capability):
        self.inner = httpx.ASGITransport(app=_PrivateASGI(app, capability))

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise ValueError("LAB_REDIRECT_BLOCKED")
        response.stream = LimitedStream(response.stream)
        return response

    async def aclose(self):
        await self.inner.aclose()


def _validate(payload, request_id):
    """Expected canonical fixture content, not a second business execution."""
    from xoc_mcp_lab.fixtures import dataset
    assert payload["request_id"] == request_id
    assert payload["tool_name"] == "xoc.tickets.list"
    assert payload["status"] == "allowed" and payload["code"] == "allowed"
    rows = payload["data"]["tickets"]
    expected = [row for row in dataset()["LAB_TICKETS"] if row["tenant_id"] == 91001]
    assert len(rows) == len(expected) == 2
    fields = ("ticket_id", "subject", "status", "severity", "created_at", "updated_at")
    expected_rows = [dict({key: source[key] for key in fields}, id=source["ticket_id"])
                     for source in reversed(expected)]
    assert payload["data"] == {"tickets": expected_rows, "count": 2, "truncated": False}
    assert "LAB_CANARY_NEVER_PUBLIC" not in json.dumps(payload)
    assert payload.get("audit_id")


async def run_private_probe(app, *, negative=None):
    """Exactly one call; initialize does not spend a business execution.

    The ordinary L1 verification and normal boundary, SDK, adapter, Policy,
    Executor, scoped fixture provider and canonical projection all run.
    """
    if app.probe_attempted:
        return dict(ok=False, occupancy=app.pool.snapshot()["active"], safe_reason="probe_already_used")
    app.probe_attempted = True
    started = time.monotonic()
    value = envelope("superadmin" if negative == "denied" else "admin_tenant_a", "xoc.tickets.list")
    app.probe_request_id = value["request_id"]
    before = app.pool.snapshot()["completed"]

    async def operation():
        async with httpx.AsyncClient(headers=headers_for(value), timeout=3.5, trust_env=False,
                follow_redirects=False, transport=_BoundedASGITransport(app, app.probe_capability)) as client:
            async with streamable_http_client(f"http://127.0.0.1:{app.port}/mcp", http_client=client,
                    terminate_on_close=False) as (read, write, session_id):
                async with ClientSession(read, write,
                        read_timeout_seconds=datetime.timedelta(seconds=3.5)) as session:
                    initialized = await session.initialize()
                    assert initialized.protocolVersion == PROTOCOL and session_id() is None
                    listed = await session.list_tools()
                    if negative != "denied":
                        assert {tool.name for tool in listed.tools} == {
                            "xoc.tickets.list", "xoc.tickets.get", "xoc.documents.list", "xoc.notifications.unread_count"}
                    result = await session.call_tool("xoc.tickets.list", {})
                    assert result.structuredContent is not None and not result.isError
                    assert len(result.content) == 1 and json.loads(result.content[0].text) == result.structuredContent
                    _validate(result.structuredContent, value["request_id"])

    try:
        await asyncio.wait_for(operation(), 3.5)
        snapshot = app.pool.snapshot()
        assert snapshot["active"] == 0 and snapshot["permits"] == 4 and snapshot["reconciled"]
        # Public ClientSession validates the returned tool schema using its
        # list_tools cache. This technical list is one real slot, not business.
        assert snapshot["completed"] == before + 2
        completed = app.pool.completed_metadata()[-2:]
        assert [item["provider_read_count"] for item in completed] == [0, 1]
        finished = completed[-1]
        assert finished["worker_started_at"] <= finished["callable_finished_at"]
        assert finished["callable_finished_at"] <= finished["context_reset_at"]
        assert finished["context_reset_at"] <= finished["future_done_at"] <= finished["slot_released_at"]
        app.probe_ok = True
        return dict(ok=True, occupancy=0, safe_reason="probe_passed", started_at=started,
                    finished_at=time.monotonic(), slot_released_at=finished["slot_released_at"])
    except Exception:
        app.pool.gate.close("probe_failed")
        return dict(ok=False, occupancy=app.pool.snapshot()["active"], safe_reason="probe_failed",
                    started_at=started, finished_at=time.monotonic())
