import asyncio
from contextlib import asynccontextmanager
import tempfile
import time
from unittest.mock import patch
import pytest
from mcp.client import stdio
from xoc_mcp_lab.query_worker import QueryWorker
from xoc_mcp_lab_harness.signing import envelope
from xoc_mcp_lab_harness.observe import ProcessObserver, audit_events


def test_spawn_error_zero_child_and_safe_result(backend):
    async def fail(**kwargs): raise OSError("DO_NOT_LOG_THIS")
    with patch.object(stdio, "_create_platform_compatible_process", fail):
        result = asyncio.run(QueryWorker().run(envelope(), backend))
    assert result["code"] == "lab_worker_unavailable" and "DO_NOT_LOG_THIS" not in str(result)


def test_cleanup_error_after_reap_still_safe_and_slot_released(backend):
    original = stdio.stdio_client
    @asynccontextmanager
    async def faulty(*args, **kwargs):
        async with original(*args, **kwargs) as streams:
            yield streams
        raise RuntimeError("DO_NOT_LOG_THIS cleanup fault")
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log, ProcessObserver() as observer:
        with patch.object(stdio, "stdio_client", faulty):
            result = asyncio.run(QueryWorker().run(envelope(), backend, errlog=log))
        assert result["code"] == "lab_worker_unavailable"
        assert len(observer.processes) == 1 and observer.processes[0][0].returncode == 0
        assert asyncio.run(QueryWorker().run(envelope(), backend, errlog=log))["status"] == "completed"


def test_concurrent_calls_same_instance_only_one_child(backend):
    async def scenario(log):
        worker = QueryWorker()
        results = await asyncio.gather(worker.run(envelope(), backend, errlog=log), worker.run(envelope(), backend, errlog=log))
        assert sum(r.get("code") == "lab_call_limit" for r in results) == 1
        assert sum(r["status"] == "completed" for r in results) == 1
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log, ProcessObserver() as observer:
        asyncio.run(scenario(log))
        assert len(observer.processes) == 1
        assert len(audit_events(log)) == 1


def test_monotonic_total_includes_cleanup(backend):
    worker = QueryWorker()
    start = time.monotonic()
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log, ProcessObserver() as observer:
        assert asyncio.run(worker.run(envelope(), backend, errlog=log))["status"] == "completed"
        marks = worker.measurements[0]
        assert list(marks.values()) == sorted(marks.values())
        assert marks["finished"] >= marks["reaped"] >= marks["cleanup_started"] >= marks["tool_result"]
        assert marks["finished"] <= time.monotonic() - start
        assert observer.evidence()[0]["reaped"]
