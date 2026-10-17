"""C09/C20/C21/C23: real threads and deterministic admission race units."""
import asyncio
from concurrent.futures import Future
import pickle
import threading
import time

import pytest

from xoc_mcp_lab.request_envelope import verify_envelope
from xoc_mcp_lab_harness.signing import envelope
from xoc_mcp_lab_l4.scope import RequestScope, current_scope
from xoc_mcp_lab_l6.pool import (AdmissionGate, BoundedObserver, BoundedPool, BusyError,
                                PrivateProbeCapability)


def scope(profile="admin_tenant_a"):
    return RequestScope(verify_envelope(envelope(profile, "xoc.tickets.list")))


def opened_pool(**kwargs):
    pool = BoundedPool(**kwargs)
    assert pool.gate.open(pool.generation, 1, time.monotonic() + 1, ready=True)
    return pool


async def eventually(predicate, timeout=2):
    until = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < until
        await asyncio.sleep(.002)


def test_C20_absolute_lease_closed_latch_and_wrong_generation():
    gate = AdmissionGate(3, threading.RLock())
    issued = time.monotonic()
    assert not gate.open(3, 1, issued + 1, ready=False)
    assert gate.open(3, 1, issued + 1, ready=True)
    assert not gate.renew(2, 99, issued + 1)
    assert not gate.renew(3, 1, issued + 1)
    assert not gate.renew(3, 2, issued - .01)
    assert gate.lease_until == issued + 1
    gate.close()
    assert gate.renew(3, 3, time.monotonic() + 1)
    assert not gate.permits()
    assert not gate.open(3, 4, time.monotonic() + 1, ready=True)


def test_C20_lease_guard_expires_without_supervisor():
    gate = AdmissionGate(1, threading.RLock())
    assert gate.open(1, 1, time.monotonic() + .02, ready=True)
    time.sleep(.025)
    assert not gate.permits()
    assert gate.safe_reason == "lease_expired"


def test_C22_private_capability_is_not_serializable_or_public_data():
    capability = PrivateProbeCapability()
    with pytest.raises(TypeError):
        pickle.dumps(capability)
    pool = BoundedPool(capability=capability)
    try:
        assert not pool.is_private()
        assert not pool.gate.permits()
    finally:
        pool.shutdown(wait=True)


def test_C22_closure_during_startup_revokes_even_private_probe_admission():
    gate = AdmissionGate(1, threading.RLock())
    assert gate.permits(private=True)
    gate.close("control_close")
    assert not gate.permits(private=True)
    assert not gate.open(1, 1, time.monotonic() + 1, ready=True)


def test_C09_four_timeout_threads_keep_permits_until_real_future_done():
    async def run():
        pool = opened_pool()
        release = threading.Event()
        entered = [threading.Event() for _ in range(4)]
        contexts = [scope("admin_tenant_a" if i % 2 == 0 else "admin_tenant_b") for i in range(4)]

        def work(index):
            assert current_scope() is contexts[index]
            entered[index].set()
            release.wait()
            assert current_scope() is contexts[index]

        try:
            tasks = [asyncio.create_task(pool.run(contexts[i], lambda i=i: work(i), .04)) for i in range(4)]
            await eventually(lambda: all(event.is_set() for event in entered))
            assert all(isinstance(item, TimeoutError) for item in await asyncio.gather(*tasks, return_exceptions=True))
            snapshot = pool.snapshot()
            assert snapshot["active"] == snapshot["futures"] == snapshot["abandoned"] == 4
            assert snapshot["permits"] == 0 and snapshot["reconciled"]
            with pytest.raises(BusyError):
                await pool.run(scope(), lambda: None, .1)
            release.set()
            await eventually(lambda: pool.snapshot()["active"] == 0)
            assert pool.snapshot()["permits"] == 4
            for job in pool.completed_metadata():
                assert job["admitted_at"] <= job["worker_started_at"] <= job["callable_finished_at"]
                assert job["callable_finished_at"] <= job["context_reset_at"] <= job["future_done_at"]
                assert job["future_done_at"] <= job["slot_released_at"]
        finally:
            release.set()
            pool.shutdown(wait=True)
    asyncio.run(run())


def test_C09_cancelled_awaiter_does_not_release_or_mix_context():
    async def run():
        pool = opened_pool()
        entered, release = threading.Event(), threading.Event()
        a, b = scope(), scope("admin_tenant_b")

        def work():
            entered.set()
            release.wait()
            return current_scope().verified.user_id

        try:
            task = asyncio.create_task(pool.run(a, work, 1))
            await eventually(entered.is_set)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert pool.snapshot()["active"] == 1
            assert await pool.run(b, lambda: current_scope().verified.user_id, 1) == 92003
            assert pool.snapshot()["active"] == 1
            release.set()
            await eventually(lambda: pool.snapshot()["active"] == 0)
        finally:
            release.set()
            pool.shutdown(wait=True)
    asyncio.run(run())


class HeldExecutor:
    """A deterministic submit/start gap, not fake process recovery evidence."""
    def submit(self, function, *args):
        self.future = Future()
        self.callable = lambda: function(*args)
        return self.future

    def start(self):
        self.future.set_running_or_notify_cancel()
        try:
            self.future.set_result(self.callable())
        except BaseException as error:
            self.future.set_exception(error)

    def shutdown(self, **kwargs):
        pass


@pytest.mark.parametrize("close_kind", ["ack", "lease", "assertion"])
def test_C21_revalidate_after_submit_before_worker_execution(close_kind, monkeypatch):
    async def run():
        pool = opened_pool()
        pool._executor.shutdown(wait=True)
        executor = pool._executor = HeldExecutor()
        invoked = []
        request_scope = scope()
        task = asyncio.create_task(pool.run(request_scope, lambda: invoked.append(True), 1))
        await asyncio.sleep(0)
        assert pool.snapshot()["active"] == 1
        if close_kind == "ack":
            pool.gate.close()
        elif close_kind == "lease":
            pool.gate.lease_until = time.monotonic() - .01
        else:
            monkeypatch.setattr("xoc_mcp_lab.request_envelope.time.time", lambda: request_scope.verified.deadline + 1)
        executor.start()
        with pytest.raises(Exception):
            await task
        assert invoked == []
        assert pool.snapshot()["active"] == 0 and pool.snapshot()["permits"] == 4
        finished = pool.completed_metadata()[-1]
        assert finished["worker_started_at"] is None and finished["callable_finished_at"] is None
        assert finished["future_done_at"] <= finished["slot_released_at"]
        pool.shutdown(wait=True)
    asyncio.run(run())


def test_C21_submit_failure_returns_exactly_one_reservation(monkeypatch):
    async def run():
        pool = opened_pool()

        def fail(*args):
            raise RuntimeError("LAB_INJECTED_SUBMIT_FAILURE")

        monkeypatch.setattr(pool._executor, "submit", fail)
        try:
            with pytest.raises(RuntimeError):
                await pool.run(scope(), lambda: None, 1)
            assert pool.snapshot()["active"] == 0 and pool.snapshot()["permits"] == 4
            assert pool.snapshot()["reconciled"]
        finally:
            pool.shutdown(wait=True)
    asyncio.run(run())


def test_C23_blocked_observer_is_one_bounded_diagnostic_thread_not_hidden_worker():
    async def run():
        observed = []
        observer = BoundedObserver(observed.append, capacity=4)
        observer.block()
        pool = opened_pool(observer=observer)
        try:
            for i in range(16):
                assert await pool.run(scope(), lambda: 42, 1) == 42
            assert pool.snapshot()["active"] == 0 and pool.snapshot()["permits"] == 4
            assert pool.snapshot()["futures"] == 0 and pool.snapshot()["reconciled"]
            diagnostic = observer.snapshot()
            assert diagnostic["thread_alive"] and diagnostic["blocked"]
            assert diagnostic["backlog"] <= 4 and diagnostic["dropped"] > 0
            observer.unblock()
            await eventually(lambda: observer.snapshot()["backlog"] == 0 and not observer.snapshot()["busy"])
            assert observed
            assert all(event["future_done_at"] <= event["slot_released_at"] for event in observed)
        finally:
            observer.unblock()
            pool.shutdown(wait=True)
            observer.close()
    asyncio.run(run())


def test_C23_accounting_mismatch_closes_gate_without_zeroing_live_work():
    pool = opened_pool()
    try:
        pool._permits = 3  # Trusted deterministic fault, not a repair path.
        snapshot = pool.snapshot()
        assert not snapshot["reconciled"] and snapshot["gate_state"] == "CLOSED"
        assert snapshot["safe_reason"] == "accounting_mismatch"
        assert snapshot["permits"] == 3
    finally:
        pool.shutdown(wait=True)
