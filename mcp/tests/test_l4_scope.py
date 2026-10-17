"""L4-only ownership tests. Integration needs installed core; never a backend path."""
import asyncio
from dataclasses import FrozenInstanceError
import gc
import threading
import time
import weakref

import pytest

from xoc_mcp_lab.request_envelope import verify_envelope
from xoc_mcp_lab_harness.signing import envelope
from xoc_mcp_lab_l4.pool import BoundedPool, BusyError
from xoc_mcp_lab_l4.scope import FaultPlan, RequestScope, ScopeError, bind_scope, current_scope
from xoc_mcp_lab_l4.wiring import PersistentWiring, ScopedIsolation


def make_scope(profile="admin_tenant_a", *, name="xoc.tickets.list", faults=None):
    return RequestScope(verify_envelope(envelope(profile, name)), faults=faults)


async def eventually(predicate, timeout=2):
    until = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > until:
            raise AssertionError("fixture condition did not become true")
        await asyncio.sleep(0.005)


def test_scope_is_frozen_detached_and_restored():
    first, second = make_scope(), make_scope("admin_tenant_b")
    with pytest.raises(FrozenInstanceError):
        first.verified = second.verified
    first.wiring.rows["LAB_TICKETS"].clear()
    assert len(second.wiring.rows["LAB_TICKETS"]) == 4
    with pytest.raises(ScopeError):
        current_scope()
    with bind_scope(first):
        assert current_scope() is first
        with bind_scope(second):
            assert current_scope() is second
        assert current_scope() is first
    with pytest.raises(ScopeError):
        current_scope()
    assert first.safe_summary()["handler_calls"] == 0


def test_timeout_keeps_all_four_slots_and_no_context_crossing():
    async def scenario():
        release, started = threading.Event(), threading.Event()
        lock = threading.Lock()
        count = 0
        records = []
        pool = BoundedPool(observer=records.append)
        scopes = [make_scope() for _ in range(4)]

        def blocked(scope):
            nonlocal count
            assert current_scope() is scope
            with lock:
                count += 1
                if count == 4:
                    started.set()
            release.wait()
            assert current_scope() is scope
            return scope.verified.request_id

        try:
            tasks = [asyncio.create_task(pool.run(scope, lambda s=scope: blocked(s), 0.1))
                     for scope in scopes]
            await eventually(started.is_set)
            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert all(isinstance(result, TimeoutError) for result in results)
            assert pool.snapshot() == dict(active=4, max_active=4, submitted=4, completed=0)
            with pytest.raises(BusyError):
                await pool.run(make_scope(), lambda: None, 1)
            assert pool.snapshot()["submitted"] == 4
            release.set()
            await eventually(lambda: pool.snapshot()["active"] == 0 and len(records) == 4)
            assert all(record["worker_context_cleared"] for record in records)
            with bind_scope(scopes[0]):
                result = await pool.run(scopes[1], lambda: current_scope().verified.request_id, 1)
                assert current_scope() is scopes[0]
            assert result == scopes[1].verified.request_id
            assert records[-1]["worker_context_cleared"]
        finally:
            release.set()
            pool.shutdown(wait=True)
    asyncio.run(scenario())


def test_cancelled_awaiter_does_not_free_worker_and_b_continues():
    async def scenario():
        release, entered = threading.Event(), threading.Event()
        pool = BoundedPool()
        a, b = make_scope(), make_scope("admin_tenant_b")

        def blocked():
            entered.set()
            release.wait()
            return current_scope().verified.request_id

        try:
            task = asyncio.create_task(pool.run(a, blocked, 5))
            await eventually(entered.is_set)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert pool.snapshot()["active"] == 1
            assert await pool.run(b, lambda: current_scope().verified.request_id, 1) == b.verified.request_id
            assert pool.snapshot()["active"] == 1
        finally:
            release.set()
            pool.shutdown(wait=True)
        assert pool.snapshot()["active"] == 0
    asyncio.run(scenario())


def test_pool_does_not_retain_completed_scope_or_late_exception():
    async def scenario():
        pool = BoundedPool()
        scope = make_scope()
        reference = weakref.ref(scope)
        assert await pool.run(scope, lambda: 7, 1) == 7
        del scope
        await asyncio.sleep(0)
        gc.collect()
        assert reference() is None
        errors = []
        asyncio.get_running_loop().set_exception_handler(lambda _loop, context: errors.append(context))

        def late_error():
            time.sleep(0.05)
            raise RuntimeError("LAB_PRIVATE_FIXTURE_ERROR")

        try:
            with pytest.raises(TimeoutError):
                await pool.run(make_scope(), late_error, 0.005)
            await eventually(lambda: pool.snapshot()["active"] == 0)
            await asyncio.sleep(0.02)
            gc.collect()
            assert errors == []
        finally:
            pool.shutdown(wait=True)
    asyncio.run(scenario())


@pytest.fixture
def persistent():
    from xoc_mcp_lab.installed import is_installed
    if not is_installed():
        pytest.skip("Requires installed canonical core artifact; no backend fallback")
    with ScopedIsolation() as isolation:
        with PersistentWiring() as wiring:
            yield wiring, isolation


def test_provider_without_scope_fails_closed_and_lifetime_is_not_nested(persistent):
    wiring, _ = persistent
    with pytest.raises(ScopeError):
        wiring.Table("LAB_TICKETS").get_item(Key={"pk": "TICKET#91001", "sk": "none"})
    with pytest.raises(RuntimeError, match="ALREADY_ACTIVE"):
        with PersistentWiring():
            pass


def test_real_ab_barrier_partitions_counters_audit_and_single_wrapper(persistent):
    async def scenario():
        barrier = threading.Barrier(2)
        plan = FaultPlan(first_read=lambda scope: barrier.wait(timeout=2))
        a, b = make_scope(faults=plan), make_scope("admin_tenant_b", faults=plan)
        records = []
        pool = BoundedPool(observer=records.append)
        try:
            results = await asyncio.gather(*[
                pool.run(scope, lambda s=scope: s.adapter.call("xoc.tickets.list", {}).structuredContent, 3)
                for scope in (a, b)])
            for scope, result, suffix in zip((a, b), results, ("a", "b")):
                assert result["status"] == "allowed"
                assert all(row["ticket_id"].startswith("lab-ticket-" + suffix) for row in result["data"]["tickets"])
                summary = scope.safe_summary()
                assert summary["handler_calls"] == summary["executor_calls"] == summary["reads"] == 1
                assert summary["provider_read_count"] == 1
                assert summary["audit_records"][0]["request_id"] == scope.verified.request_id
                assert summary["audit_records"][0]["effective_tenant_id"] == scope.verified.effective_tenant_id
                assert result["audit_id"] == summary["audit_records"][0]["audit_id"]
            assert pool.snapshot()["max_active"] == 2
            assert all(record["worker_context_cleared"] for record in records)
            with bind_scope(a):
                assert a.adapter.call("xoc.tickets.list", {}).structuredContent["code"] == "lab_call_limit"
            # A NEW request wrapper may reuse the same still-valid assertion.
            fresh = RequestScope(a.verified)
            with bind_scope(fresh):
                assert fresh.adapter.call("xoc.tickets.list", {}).structuredContent["status"] == "allowed"
            assert fresh.safe_summary()["handler_calls"] == 1
        finally:
            pool.shutdown(wait=True)
    asyncio.run(scenario())


def test_error_and_noncooperative_a_leave_b_available(persistent):
    async def scenario():
        release = threading.Event()
        pool = BoundedPool()
        a = make_scope(faults=FaultPlan(first_read=lambda scope: release.wait()))
        b = make_scope("admin_tenant_b")
        try:
            timed = asyncio.create_task(pool.run(a, lambda: a.adapter.call("xoc.tickets.list", {}), 0.05))
            await eventually(a.provider_entered.is_set)
            with pytest.raises(TimeoutError):
                await timed
            assert pool.snapshot()["active"] == 1
            out = await pool.run(b, lambda: b.adapter.call("xoc.tickets.list", {}).structuredContent, 1)
            assert out["status"] == "allowed" and pool.snapshot()["active"] == 1
            release.set()
            await eventually(lambda: pool.snapshot()["active"] == 0)

            def fail(scope):
                raise RuntimeError("LAB_CANARY_NEVER_PUBLIC")

            failed = make_scope(faults=FaultPlan(first_read=fail))
            result = await pool.run(failed, lambda: failed.adapter.call("xoc.tickets.list", {}).structuredContent, 1)
            assert result["code"] == "execution_failed"
            assert "LAB_CANARY" not in str(result)
            assert failed.safe_summary()["reads"] == 1
            assert failed.safe_summary()["handler_calls"] == 1
        finally:
            release.set()
            pool.shutdown(wait=True)
    asyncio.run(scenario())


def test_guard_counters_are_request_scoped(persistent):
    import boto3
    import socket
    _, isolation = persistent
    a, b = make_scope(), make_scope("admin_tenant_b")
    with bind_scope(a):
        with pytest.raises(RuntimeError, match="PROVIDER"):
            boto3.client("secretsmanager")
        with pytest.raises(RuntimeError, match="DNS"):
            socket.getaddrinfo("metadata.invalid", 443)
        with pytest.raises(RuntimeError, match="SECRET_FILE"):
            open("NO_CREDENTIALS")
    assert a.safe_summary()["provider_attempts"] == 1
    assert a.safe_summary()["network_attempts"] == 1
    assert a.safe_summary()["secret_file_attempts"] == 1
    assert b.safe_summary()["provider_attempts"] == b.safe_summary()["network_attempts"] == 0
    assert isolation.network_attempts == isolation.secret_file_attempts == 1
