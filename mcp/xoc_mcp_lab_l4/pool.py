"""Four admitted synchronous jobs; timeout never releases a running thread's slot."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from .scope import bind_scope, optional_scope, worker_context


class BusyError(RuntimeError):
    def __init__(self):
        super().__init__("LAB_WORKER_CAPACITY_EXCEEDED")


class BoundedPool:
    def __init__(self, workers=4, observer=None):
        if workers != 4:
            raise ValueError("LAB_FOUR_SYNC_WORKERS_REQUIRED")
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="xoc-l4")
        self._slots = threading.BoundedSemaphore(4)
        self._lock = threading.Lock()
        self._active = self._max_active = self._submitted = self._completed = 0
        self._closed = False
        self._observer = observer

    async def run(self, scope, function, timeout_seconds):
        if timeout_seconds <= 0:
            raise TimeoutError("LAB_REQUEST_TIMEOUT")
        with self._lock:
            if self._closed or not self._slots.acquire(blocking=False):
                raise BusyError()
            self._active += 1
            self._submitted += 1
            self._max_active = max(self._max_active, self._active)
        context = worker_context()

        def invoke():
            worker_started_ns = time.perf_counter_ns()
            callable_finished_ns = worker_started_ns
            try:
                with bind_scope(scope):
                    try:
                        return function()
                    finally:
                        callable_finished_ns = time.perf_counter_ns()
            finally:
                with self._lock:
                    self._active -= 1
                    self._completed += 1
                self._slots.release()
                worker_finished_ns = time.perf_counter_ns()
                if self._observer is not None:
                    try:
                        self._observer(dict(event="worker_finished", **scope.safe_summary(),
                            worker_context_cleared=optional_scope() is None, pool=self.snapshot(),
                            worker_started_ns=worker_started_ns, worker_finished_ns=worker_finished_ns,
                            worker_elapsed_ns=worker_finished_ns-worker_started_ns,
                            worker_cleanup_ns=worker_finished_ns-callable_finished_ns))
                    except Exception:
                        # Diagnostics can never alter the canonical business result.
                        pass

        try:
            future = self._executor.submit(context.run, invoke)
        except BaseException:
            with self._lock:
                self._active -= 1
                self._submitted -= 1
            self._slots.release()
            raise
        wrapped = asyncio.wrap_future(future)
        # No pool registry retains futures/scopes. Consume a late exception even if
        # the awaiting HTTP request has timed out or disconnected.
        wrapped.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        try:
            return await asyncio.wait_for(asyncio.shield(wrapped), timeout=timeout_seconds)
        except TimeoutError:
            raise TimeoutError("LAB_REQUEST_TIMEOUT") from None

    def snapshot(self):
        with self._lock:
            return dict(active=self._active, max_active=self._max_active,
                        submitted=self._submitted, completed=self._completed)

    def shutdown(self, *, wait=False):
        """Does not kill threads. Truly noncooperative jobs require process teardown."""
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=False)
