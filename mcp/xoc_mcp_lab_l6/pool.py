"""Four real reservations, absolute admission leases, and nonblocking diagnostics."""
from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
import math
import threading
import time

from xoc_mcp_lab_l4.pool import BusyError
from xoc_mcp_lab_l4.scope import bind_scope, optional_scope, worker_context


_PRIVATE_SCOPE = ContextVar("xoc_l6_private_probe", default=None)


class PrivateProbeCapability:
    """An object reference, never a serializable HTTP/JSON control credential."""
    def __reduce__(self):
        raise TypeError("LAB_PRIVATE_CAPABILITY_NOT_SERIALIZABLE")


class BoundedObserver:
    """One accounted daemon and a fixed ring; producers never call a sink.

    Blocking/failing I/O belongs to this diagnostic-only thread, not a pool
    unit, heartbeat or request. Drops and the thread's occupancy remain visible.
    No event contains a live scope, future, request or business result.
    """
    def __init__(self, sink=None, *, capacity=128):
        self.sink = sink
        self.capacity = capacity
        self._queue = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._unblocked = threading.Event()
        self._unblocked.set()
        self._stop = False
        self.dropped = self.errors = self.completed = 0
        self.busy = False
        self.last_observation_finished_at = None
        self._thread = threading.Thread(target=self._run, name="xoc-l6-diagnostics", daemon=True)
        self._thread.start()

    def __call__(self, event):
        if self._stop or not self._lock.acquire(blocking=False):
            self.dropped += 1
            return
        try:
            if len(self._queue) >= self.capacity:
                self.dropped += 1
                return
            self._queue.append(event)
        finally:
            self._lock.release()
        self._wake.set()

    def _run(self):
        try:
            while True:
                self._wake.wait(0.1)
                with self._lock:
                    event = self._queue.popleft() if self._queue else None
                    if not self._queue:
                        self._wake.clear()
                if event is None:
                    if self._stop:
                        break
                    continue
                self.busy = True
                self._unblocked.wait()
                try:
                    if self.sink is not None:
                        self.sink(event)
                except Exception:
                    self.errors += 1
                finally:
                    self.last_observation_finished_at = time.monotonic()
                    self.completed += 1
                    self.busy = False
        finally:
            close = getattr(self.sink, "close", None)
            if close is not None:
                close()

    def block(self):
        self._unblocked.clear()

    def unblock(self):
        self._unblocked.set()

    def snapshot(self):
        return dict(backlog=len(self._queue), capacity=self.capacity, dropped=self.dropped,
                    errors=self.errors, completed=self.completed, busy=self.busy,
                    blocked=not self._unblocked.is_set(), thread_alive=self._thread.is_alive(),
                    last_observation_finished_at=self.last_observation_finished_at)

    def close(self, timeout=0.1):
        self._stop = True
        self._wake.set()
        self._thread.join(timeout)


class AdmissionGate:
    """The pool and gate share this lock, including reserve/submit/start checks."""
    def __init__(self, generation, lock):
        self.generation = generation
        self.lock = lock
        self.state = "CLOSED"
        self.lease_until = 0.0
        self.last_sequence = 0
        self.opened_once = False
        self.safe_reason = "starting"
        self.closed_at = None

    def renew(self, generation, sequence, lease_until):
        with self.lock:
            now = time.monotonic()
            if (generation != self.generation or type(sequence) is not int or sequence <= self.last_sequence
                    or not isinstance(lease_until, (int, float)) or not math.isfinite(lease_until)
                    or lease_until <= now or lease_until > now + 1.05):
                return False
            self.last_sequence = sequence
            self.lease_until = lease_until
            # A renewal NEVER reopens a CLOSED gate.
            return True

    def open(self, generation, sequence, lease_until, *, ready):
        with self.lock:
            if (not ready or self.opened_once or self.safe_reason != "starting"
                    or not self.renew(generation, sequence, lease_until)):
                return False
            self.opened_once = True
            self.state, self.safe_reason = "OPEN", "ready"
            return True

    def close(self, reason="control_close"):
        with self.lock:
            if self.state != "CLOSED" or self.closed_at is None:
                self.closed_at = time.monotonic()
            self.state, self.safe_reason = "CLOSED", reason

    def permits(self, private=False):
        with self.lock:
            if private and not self.opened_once and self.safe_reason == "starting":
                return True
            if self.state == "OPEN" and time.monotonic() >= self.lease_until:
                self.close("lease_expired")
            return self.state == "OPEN"


class BoundedPool:
    def __init__(self, workers=4, observer=None, *, generation=1, capability=None):
        if workers != 4:
            raise ValueError("LAB_FOUR_SYNC_WORKERS_REQUIRED")
        self._lock = threading.RLock()
        self.gate = AdmissionGate(generation, self._lock)
        self.generation = generation
        self.capability = capability if capability is not None else PrivateProbeCapability()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="xoc-l6")
        self._slots = threading.BoundedSemaphore(4)
        self._permits = 4
        self._jobs = {}
        self._history = deque(maxlen=16)
        self._submitted = self._completed = self._max_active = self._next_id = 0
        self._closed = False
        self._owns_observer = observer is not None and not isinstance(observer, BoundedObserver)
        self._observer = BoundedObserver(observer) if self._owns_observer else observer

    def is_private(self):
        return _PRIVATE_SCOPE.get() is self.capability

    def _reconciled(self):
        # This semaphore is exclusively owned by this class; its CPython 3.11
        # value is read while holding the same lock as every acquire/release.
        return (self._permits == self._slots._value and self._permits + len(self._jobs) == 4
                and self._submitted - self._completed == len(self._jobs)
                and all(job["future"] is not None for job in self._jobs.values()))

    def _metadata(self, job):
        values = dict(job["metadata"])
        observation = job["scope"]._observation
        with observation.lock:
            values.update(provider_entered_at=(observation.provider_first_ns / 1e9
                                              if observation.provider_first_ns is not None else None),
                          provider_finished_at=(observation.provider_last_ns / 1e9
                                               if observation.provider_last_ns is not None else None),
                          provider_read_count=observation.provider_read_count)
        return values

    async def run(self, scope, function, timeout_seconds):
        if timeout_seconds <= 0:
            raise TimeoutError("LAB_REQUEST_TIMEOUT")
        private = self.is_private()
        context = worker_context()
        with self._lock:
            scope.verified.require_valid()
            if self._closed or not self.gate.permits(private) or not self._slots.acquire(blocking=False):
                raise BusyError()
            self._permits -= 1
            self._submitted += 1
            self._next_id += 1
            admission_id = self._next_id
            metadata = dict(generation=self.generation, internal_admission_id=admission_id,
                admitted_at=time.monotonic(), worker_started_at=None, callable_finished_at=None,
                context_reset_at=None, future_done_at=None, slot_released_at=None,
                awaiter_abandoned_at=None)
            job = dict(metadata=metadata, scope=scope, future=None)
            self._jobs[admission_id] = job
            self._max_active = max(self._max_active, len(self._jobs))

            def invoke():
                with self._lock:
                    if not self.gate.permits(private):
                        raise BusyError()
                    scope.verified.require_valid()
                    metadata["worker_started_at"] = time.monotonic()
                try:
                    with bind_scope(scope):
                        try:
                            return function()
                        finally:
                            metadata["callable_finished_at"] = time.monotonic()
                finally:
                    metadata["context_reset_at"] = time.monotonic()
                    job["context_cleared"] = optional_scope() is None

            try:
                future = self._executor.submit(context.run, invoke)
                job["future"] = future
                future.add_done_callback(lambda done: self._finish(admission_id, done))
            except BaseException:
                del self._jobs[admission_id]
                self._completed += 1
                self._permits += 1
                self._slots.release()
                if self._observer is not None:
                    self._observer(dict(event="reservation_rejected", generation=self.generation,
                        internal_admission_id=admission_id, safe_reason="submit_failed",
                        admitted_at=metadata["admitted_at"], slot_released_at=time.monotonic()))
                raise

        wrapped = asyncio.wrap_future(future)
        wrapped.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        try:
            return await asyncio.wait_for(asyncio.shield(wrapped), timeout=timeout_seconds)
        except (TimeoutError, asyncio.CancelledError) as error:
            with self._lock:
                if admission_id in self._jobs:
                    metadata["awaiter_abandoned_at"] = time.monotonic()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise TimeoutError("LAB_REQUEST_TIMEOUT") from None

    def _finish(self, admission_id, future):
        # Concurrent Future.done() is true before any permission is returned.
        with self._lock:
            job = self._jobs[admission_id]
            metadata = job["metadata"]
            metadata["future_done_at"] = time.monotonic()
            if not future.done():
                self.gate.close("accounting_mismatch")
                return
            summary = job["scope"].safe_summary()
            finished = self._metadata(job)
            self._slots.release()
            self._permits += 1
            self._completed += 1
            metadata["slot_released_at"] = finished["slot_released_at"] = time.monotonic()
            del self._jobs[admission_id]
            self._history.append(finished)
            snapshot = self.snapshot()
        if self._observer is not None:
            began = metadata["worker_started_at"]
            ended = metadata["context_reset_at"]
            self._observer(dict(summary | finished,
                event="worker_finished" if began is not None else "reservation_rejected",
                worker_context_cleared=job.get("context_cleared", True),
                worker_started_ns=int(began * 1e9) if began is not None else None,
                worker_finished_ns=int(ended * 1e9) if ended is not None else None,
                worker_elapsed_ns=int((ended-began)*1e9) if ended is not None else None,
                worker_cleanup_ns=int((ended-metadata["callable_finished_at"])*1e9) if ended is not None else None,
                pool=snapshot))

    def snapshot(self):
        with self._lock:
            self.gate.permits()
            reconciled = self._reconciled()
            if not reconciled:
                self.gate.close("accounting_mismatch")
            jobs = [self._metadata(job) for job in self._jobs.values()]
            return dict(active=len(jobs), max_active=self._max_active, submitted=self._submitted,
                completed=self._completed, permits=self._permits,
                futures=sum(not job["future"].done() for job in self._jobs.values()),
                reconciled=reconciled, generation=self.generation, gate_state=self.gate.state,
                lease_until=self.gate.lease_until, safe_reason=self.gate.safe_reason,
                jobs=jobs, abandoned=sum(job["awaiter_abandoned_at"] is not None for job in jobs))

    def completed_metadata(self):
        with self._lock:
            return list(self._history)

    def diagnostic_identity(self, scope):
        """Fixture correlation for local audit evidence, NEVER sent on IPC."""
        with self._lock:
            for job in self._jobs.values():
                if job["scope"] is scope:
                    return dict(internal_admission_id=job["metadata"]["internal_admission_id"],
                                admitted_at=job["metadata"]["admitted_at"],
                                fixture_actor_user_id=scope.verified.user_id,
                                fixture_effective_tenant_id=scope.verified.effective_tenant_id)
        return {}

    def shutdown(self, *, wait=False):
        with self._lock:
            self._closed = True
            self.gate.close("shutdown")
        self._executor.shutdown(wait=wait, cancel_futures=False)
        if self._owns_observer:
            self._observer.close()
