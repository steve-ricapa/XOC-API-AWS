import asyncio
import tempfile
import time
from unittest.mock import patch
import anyio
import pytest
from mcp import ClientSession
from mcp.client import stdio
from xoc_mcp_lab.query_worker import QueryWorker
from xoc_mcp_lab.budget import ProcessOwner
from xoc_mcp_lab_harness.observe import ProcessObserver, audit_events
from xoc_mcp_lab_harness.signing import envelope


@pytest.mark.parametrize("phase", ["startup", "query", "cleanup"])
def test_cancellation_at_each_phase_is_bounded_and_reaped(backend, phase):
    original_init, original_call = ClientSession.initialize, ClientSession.call_tool
    original_wait = ProcessOwner._wait
    ready = None
    async def init(self):
        if phase == "startup":
            ready.set()
            await anyio.sleep(60)
        return await original_init(self)
    async def call(self, *args, **kwargs):
        if phase == "query":
            ready.set()
            await anyio.sleep(60)
        return await original_call(self, *args, **kwargs)
    async def wait(self, seconds):
        if phase == "cleanup": ready.set()
        return await original_wait(self, seconds)
    worker = QueryWorker()
    async def run(log):
        nonlocal ready
        ready = asyncio.Event()
        task = asyncio.create_task(worker.run(envelope(), backend, errlog=log))
        await asyncio.wait_for(ready.wait(), 5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.done()
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()
                    and not t.done() and t.get_name().startswith("xoc-l3-")]
    started = time.monotonic()
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log, ProcessObserver() as observer:
        with patch.object(ClientSession, "initialize", init), patch.object(ClientSession, "call_tool", call), patch.object(ProcessOwner, "_wait", wait):
            asyncio.run(run(log))
        assert len(observer.processes) == 1 and observer.evidence()[0]["reaped"]
        if phase in ("startup", "query"): assert audit_events(log) == []
    assert time.monotonic() - started < 5.25
    assert worker.lifecycle[0]["exchange_finished"]


def test_owner_is_context_local_and_other_sdk_clients_untouched(backend):
    # Existing two-worker/same-instance and MCP-1 tests exercise real delegation;
    # verify explicit ownership never leaks back to the caller after an invocation.
    from xoc_mcp_lab.budget import _OWNER
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log, ProcessObserver() as observer:
        result = asyncio.run(QueryWorker().run(envelope(), backend, errlog=log))
        assert result["status"] == "completed"
        assert _OWNER.get() is None
        assert observer.evidence()[0]["reaped"]


def test_cancellation_during_process_factory_is_reaped(backend):
    worker = QueryWorker()
    ready = None
    original = stdio._create_platform_compatible_process
    async def slow_factory(**kwargs):
        process = await original(**kwargs)
        ready.set()
        await anyio.sleep(60)
        return process
    async def run(log):
        nonlocal ready
        ready = asyncio.Event()
        task = asyncio.create_task(worker.run(envelope(), backend, errlog=log))
        await asyncio.wait_for(ready.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log, ProcessObserver() as observer:
        original = stdio._create_platform_compatible_process
        with patch.object(stdio, "_create_platform_compatible_process", slow_factory):
            asyncio.run(run(log))
        assert len(observer.processes) == 1 and observer.evidence()[0]["reaped"]
        assert audit_events(log) == []


def test_cleanup_stdin_error_is_safe_and_child_still_reaped(backend):
    failures = []
    original = stdio._create_platform_compatible_process
    async def broken(**kwargs):
        process = await original(**kwargs)
        close = process.stdin.aclose
        failed = False
        async def fail():
            nonlocal failed
            if not failed:
                failed = True
                failures.append("stdin_close")
                raise OSError("DO_NOT_LOG_THIS")
            return await close()
        # One transient error before SDK cleanup. A permanently replaced close
        # method cannot prove recovery of a real OS handle; do not claim that case.
        process.stdin.aclose = fail
        return process
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log, ProcessObserver() as observer:
        original = stdio._create_platform_compatible_process
        with patch.object(stdio, "_create_platform_compatible_process", broken):
            result = asyncio.run(QueryWorker().run(envelope(), backend, errlog=log))
        # SDK explicitly tolerates a failed stdin close, then retries/reaps. A
        # recovered close may preserve a valid result, but never an unreaped child.
        if result["status"] == "completed":
            assert result["result"]["status"] == "allowed"
            assert observer.evidence()[0]["returncode"] == 0
        else:
            assert result["code"] == "lab_worker_unavailable"
        assert failures == ["stdin_close"]
        assert "DO_NOT_LOG_THIS" not in str(result)
        assert observer.evidence()[0]["reaped"]


def test_supervisor_wait_error_does_not_return_success(backend):
    async def fail(self, seconds): raise OSError("DO_NOT_LOG_THIS wait")
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log, ProcessObserver() as observer:
        with patch.object(ProcessOwner, "_wait", fail):
            result = asyncio.run(QueryWorker().run(envelope(), backend, errlog=log))
        assert result["code"] == "lab_worker_unavailable"
        assert observer.evidence()[0]["reaped"]
        assert "DO_NOT_LOG_THIS" not in str(result)
