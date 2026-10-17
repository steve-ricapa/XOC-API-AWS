import asyncio
import math
import tempfile
import time
from unittest.mock import patch
import pytest
from mcp import ClientSession
from mcp.client import stdio
from xoc_mcp_lab.budget import TotalBudget
from xoc_mcp_lab.query_worker import QueryWorker
from xoc_mcp_lab_harness.observe import ProcessObserver, audit_events
from xoc_mcp_lab_harness.signing import envelope


@pytest.mark.parametrize("requested", [0, -1, 5.01, math.inf, math.nan, True, "5"])
def test_invalid_total_never_spawns(backend, requested):
    with patch.object(stdio, "stdio_client") as start:
        assert asyncio.run(QueryWorker().run(envelope(), backend, timeout_seconds=requested))["code"] == "lab_invalid_deadline"
        start.assert_not_called()


def test_insufficient_total_before_spawn(backend):
    with ProcessObserver() as observer:
        worker = QueryWorker()
        result = asyncio.run(worker.run(envelope(), backend, timeout_seconds=0.001))
        assert result["code"] == "lab_worker_unavailable"
        assert observer.processes == []
        assert worker.lifecycle[0]["reaped"]


def test_signed_deadline_is_upper_bound_not_new_budget():
    class Signed: deadline = 105
    budget = TotalBudget(90, 5)
    with patch("xoc_mcp_lab.budget.time.time", return_value=103), patch("xoc_mcp_lab.budget.time.monotonic", return_value=92):
        budget.bind(Signed())
    assert budget.deadline == 94 and budget.deadline <= 95
    assert budget.work_deadline < budget.deadline


def test_insufficient_after_initialize_does_not_call_handler(backend):
    original = ClientSession.initialize
    async def exhausted(self):
        info = await original(self)
        # Inject only remaining-time admission; execute the REAL handshake first.
        from xoc_mcp_lab.budget import _OWNER
        owner = _OWNER.get()
        owner.budget.deadline = time.monotonic() + owner.budget.reserve + 0.001
        return info
    worker = QueryWorker()
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log, ProcessObserver() as observer:
        with patch.object(ClientSession, "initialize", exhausted):
            result = asyncio.run(worker.run(envelope(), backend, errlog=log))
        assert result["code"] == "lab_worker_unavailable"
        assert "initialized" in worker.measurements[0]
        assert audit_events(log) == []
        assert len(observer.processes) == 1 and observer.evidence()[0]["reaped"]


@pytest.mark.parametrize("limit", [0.4, 3.5, 5])
def test_total_hang_includes_reap_without_extra_two_seconds(backend, limit):
    worker = QueryWorker()
    started = time.monotonic()
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log, ProcessObserver("import time; time.sleep(60)") as observer:
        result = asyncio.run(worker.run(envelope(), backend, timeout_seconds=limit, errlog=log))
        assert result["code"] == "lab_worker_unavailable"
        assert len(observer.processes) == 1 and observer.evidence()[0]["reaped"]
        assert audit_events(log) == []
    elapsed = time.monotonic() - started
    # Scheduling tolerance is NOT excluded from reports or called a hard guarantee.
    assert elapsed < limit + 0.25, (elapsed, limit)
    assert worker.lifecycle[0]["exchange_finished"] and worker.lifecycle[0]["supervisor_finished"]


def test_no_security_checks_removed():
    # Invalid signature must fail before a process/path/provider can start.
    value = envelope(scalar=2)
    with patch.object(stdio, "stdio_client") as start:
        assert asyncio.run(QueryWorker().run(value))["code"] == "lab_invalid_envelope"
        start.assert_not_called()
