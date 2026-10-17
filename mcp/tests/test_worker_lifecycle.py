import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
import anyio
import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client import stdio
from xoc_mcp_lab.isolation import LAB_ROOT, clean_environment
from xoc_mcp_lab.query_worker import QueryWorker, _SLOTS, validate_result
from xoc_mcp_lab.request_envelope import canonical, verify_envelope
from xoc_mcp_lab.verified_server import ENVELOPE_ENV
from l1_fixtures import envelope
from test_stdio import audit_events


def observe_processes(processes, fault=None):
    original = stdio._create_platform_compatible_process
    async def observed(**kwargs):
        assert kwargs["command"] == sys.executable
        assert kwargs["env"].get("AWS_PROFILE") is None
        assert kwargs["env"].get("AZURE_CLIENT_SECRET") is None
        if fault:  # Test injection only, never a worker option.
            kwargs["args"] = ["-B", "-c", fault]
        process = await original(**kwargs)
        processes.append(process)
        return process
    return observed


@pytest.mark.parametrize("profile,name,args,expected", [
    ("admin_tenant_a", "xoc.tickets.list", {}, "lab-ticket-a"),
    ("admin_tenant_b", "xoc.tickets.list", {}, "lab-ticket-b"),
    ("user_a1", "xoc.notifications.unread_count", {}, 2),
    ("user_a2", "xoc.notifications.unread_count", {}, 5),
    ("admin_xoc_delegated_a", "xoc.documents.list", {}, "lab-document-a"),
    ("admin_tenant_a", "xoc.tickets.get", {"ticket_id": "lab-ticket-a1"}, "lab-ticket-a1"),
])
def test_worker_one_session_call_handler_and_closed_child(backend, profile, name, args, expected):
    processes, value, worker = [], envelope(profile, name, args), QueryWorker()
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        with patch.object(stdio, "_create_platform_compatible_process", observe_processes(processes)):
            result = asyncio.run(worker.run(value, backend, errlog=log))
            assert result["status"] == "completed", result
            assert asyncio.run(worker.run(value, backend, errlog=log))["code"] == "lab_call_limit"
        data = result["result"]["data"]
        assert data["count"] == expected if type(expected) is int else expected in json.dumps(data)
        events = audit_events(log)
        assert len(events) == 1 and events[0]["handler_calls"] == events[0]["executor_calls"] == 1
        assert events[0]["request_id"] == result["request_id"] == value["request_id"]
        assert events[0]["audit_id"] == result["result"]["audit_id"]
    assert len(processes) == 1 and processes[0].returncode == 0


def test_meta_cannot_override_verified_identity_or_call_twice(backend):
    value = envelope("user_a2", "xoc.notifications.unread_count")
    env = clean_environment()
    env[ENVELOPE_ENV] = canonical(value)
    params = StdioServerParameters(command=sys.executable,
        args=["-B", "-m", "xoc_mcp_lab.verified_server", "--lab", "--backend", str(backend)],
        env=env, cwd=str(LAB_ROOT))
    async def scenario(log):
        async with stdio.stdio_client(params, errlog=log) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                req = types.ClientRequest(types.CallToolRequest(params=types.CallToolRequestParams(
                    name=value["tool"]["name"], arguments={},
                    _meta={"tenantId": 91002, "userId": 92003, "role": "SUPERADMIN"})))
                first = await session.send_request(req, types.CallToolResult)
                assert first.structuredContent["data"]["count"] == 5
                second = await session.call_tool(value["tool"]["name"], {})
                assert second.isError and second.structuredContent["code"] == "lab_call_limit"
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        asyncio.run(scenario(log))
        assert [x["handler_calls"] for x in audit_events(log)] == [1, 0]


@pytest.mark.parametrize("fault", ["import time; time.sleep(60)", "raise SystemExit(7)", "pass"])
def test_hung_dead_or_eof_no_retry_and_reaped(backend, fault):
    processes, started = [], time.monotonic()
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        with patch.object(stdio, "_create_platform_compatible_process", observe_processes(processes, fault)):
            result = asyncio.run(QueryWorker().run(envelope(), backend, timeout_seconds=0.4, errlog=log))
        assert result["code"] == "lab_worker_unavailable"
        assert audit_events(log) == []
    assert len(processes) == 1 and processes[0].returncode is not None
    assert time.monotonic() - started < 8  # includes SDK cleanup, not a 0.4s total promise


def test_saturation_rejects_before_child(backend):
    for _ in range(4): assert _SLOTS.acquire(blocking=False)
    try:
        with patch.object(stdio, "stdio_client") as launch:
            assert asyncio.run(QueryWorker().run(envelope(), backend))["code"] == "lab_busy"
            launch.assert_not_called()
    finally:
        for _ in range(4): _SLOTS.release()


def test_cancellation_uses_local_deadline_and_reaps_child(backend):
    processes = []
    async def scenario(log):
        async with anyio.create_task_group() as group:
            async def work():
                await QueryWorker().run(envelope(), backend, timeout_seconds=0.4, errlog=log)
            group.start_soon(work)
            await anyio.sleep(0.1)
            group.cancel_scope.cancel()
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        with patch.object(stdio, "_create_platform_compatible_process",
                observe_processes(processes, "import time; time.sleep(60)")):
            asyncio.run(scenario(log))
    assert len(processes) == 1 and processes[0].returncode is not None


def test_invalid_child_envelope_never_starts_business(backend):
    env = clean_environment()
    env[ENVELOPE_ENV] = "DO_NOT_LOG_THIS"
    proc = subprocess.run([sys.executable, "-B", "-m", "xoc_mcp_lab.verified_server", "--lab",
        "--backend", str(backend)], cwd=LAB_ROOT, env=env, input="", text=True,
        capture_output=True, timeout=8)
    assert proc.returncode == 2 and proc.stdout == ""
    assert proc.stderr.strip() == "LAB_VERIFIED_CHILD_FAILED"


def test_inherited_secrets_never_used(backend):
    processes = []
    with patch.dict(os.environ, {"AWS_PROFILE": "DO_NOT_USE", "AZURE_CLIENT_SECRET": "DO_NOT_LOG_THIS",
            "DATABASE_URL": "DO_NOT_LOG_THIS", "PYTHONPATH": "DO_NOT_USE"}):
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
            with patch.object(stdio, "_create_platform_compatible_process", observe_processes(processes)):
                assert asyncio.run(QueryWorker().run(envelope(), backend, errlog=log))["status"] == "completed"
            audit_events(log)
    assert processes[0].returncode == 0


def test_two_workers_concurrently_isolate_users(backend):
    async def scenario(log):
        values = [envelope(p, "xoc.notifications.unread_count") for p in ("user_a1", "user_a2")]
        results = await asyncio.gather(*(QueryWorker().run(v, backend, errlog=log) for v in values))
        assert [r["result"]["data"]["count"] for r in results] == [2, 5]
        assert len({r["request_id"] for r in results}) == 2
    processes = []
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        with patch.object(stdio, "_create_platform_compatible_process", observe_processes(processes)):
            asyncio.run(scenario(log))
        assert len(audit_events(log)) == 2
    assert len(processes) == 2 and all(p.returncode == 0 for p in processes)


def test_unknown_role_and_foreign_ticket(backend):
    async def scenario(log):
        result = await QueryWorker().run(envelope(claims_patch={"actor_role": "BOGUS"}), backend, errlog=log)
        assert result["result"]["status"] == "denied"
        for tid in ("lab-ticket-b1", "missing"):
            result = await QueryWorker().run(envelope(name="xoc.tickets.get", arguments={"ticket_id": tid}),
                                              backend, errlog=log)
            assert result["result"]["code"] == "execution_failed" and result["result"]["data"] is None
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        asyncio.run(scenario(log))
        assert [e["reads"] for e in audit_events(log)] == [0, 1, 1]


def test_client_rejects_wrong_correlation_and_text_mismatch():
    verified = verify_envelope(envelope())
    payload = dict(request_id=verified.request_id, tool_name=verified.tool_name,
        status="allowed", code="ok", data={"tickets": []}, audit_id="lab-audit")
    for change in ({"request_id": "lab-other"}, {"tool_name": "xoc.documents.list"}, {"extra": 1},
                   {"data": None}, {"data": []}, {"code": ""}, {"status": "denied"}):
        bad = dict(payload, **change)
        result = types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(bad))],
                                     structuredContent=bad, isError=False)
        with pytest.raises(ValueError): validate_result(result, verified)
    result = types.CallToolResult(content=[types.TextContent(type="text", text="{}")],
                                 structuredContent=payload, isError=False)
    with pytest.raises(ValueError): validate_result(result, verified)


def test_same_request_replay_is_not_distributed_deduplication(backend):
    value = envelope()
    async def scenario(log):
        for _ in range(2):
            result = await QueryWorker().run(value, backend, errlog=log)
            assert result["status"] == "completed" and result["request_id"] == value["request_id"]
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        asyncio.run(scenario(log))
        events = audit_events(log)
        assert len(events) == 2 and all(e["handler_calls"] == 1 for e in events)


def test_failed_provider_no_retry_or_fallback(backend):
    processes = []
    original = stdio._create_platform_compatible_process
    async def faulty_provider(**kwargs):
        # Fault the fake boundary in the fixed verified child, not business handlers.
        code = ('import sys; from xoc_mcp_lab.wiring import Wiring; '
                'original=Wiring.__init__; '
                'Wiring.__init__=lambda self:(original(self),setattr(self,"fail_reads",True))[-1]; '
                'from xoc_mcp_lab.verified_server import main; raise SystemExit(main())')
        kwargs["args"] = ["-B", "-c", code, "--lab", "--backend", str(backend)]
        process = await original(**kwargs)
        processes.append(process)
        return process
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        with patch.object(stdio, "_create_platform_compatible_process", faulty_provider):
            result = asyncio.run(QueryWorker().run(envelope(), backend, errlog=log))
        assert result["result"]["code"] == "execution_failed" and result["result"]["data"] is None
        events = audit_events(log)
        assert len(events) == 1 and events[0]["handler_calls"] == events[0]["reads"] == 1
    assert len(processes) == 1 and processes[0].returncode == 0
