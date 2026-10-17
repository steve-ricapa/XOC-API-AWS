"""LOCAL total budget and selective supervision of the SDK's OWN subprocess.

The pinned SDK exposes no public process handle in stdio_client. A context-local
capture delegates its existing factory unchanged and records the resulting handle.
No SDK file, framing, JSON-RPC implementation or process creation code is copied.
Private API coupling is intentional, pinned and tested; not production approval.
"""
import asyncio
from contextvars import ContextVar
import math
import time
import anyio
from mcp.client import stdio

_OWNER = ContextVar("xoc_lab_process_owner", default=None)
_SDK_CREATE = stdio._create_platform_compatible_process


async def _capture(**kwargs):
    owner = _OWNER.get()
    if owner is not None:
        owner.mark("spawn_started")
    process = await _SDK_CREATE(**kwargs)
    if owner is not None:
        owner.attach(process)
    return process


# One transparent dispatcher, not a per-request global patch. Other SDK clients
# without this context delegate unchanged. Concurrent requests have separate owners.
stdio._create_platform_compatible_process = _capture


class TotalBudget:
    MAX_TOTAL = 5.0
    MAX_RESERVE = 0.65
    PHASE_MARGIN = 0.02
    SPAWN_MARGIN = 0.05

    def __init__(self, entered, requested):
        if type(requested) not in (float, int) or not math.isfinite(requested) or not 0 < requested <= self.MAX_TOTAL:
            raise ValueError("LAB_INVALID_DEADLINE")
        self.entered = entered
        self.deadline = entered + requested
        self.reserve = min(self.MAX_RESERVE, requested / 2)

    def bind(self, verified):
        # verify_envelope already proves signed deadline <= exp; do not decode
        # unverified claims again. Conservative wall->monotonic mapping, never extend.
        wall, mono = time.time(), time.monotonic()
        self.deadline = min(self.deadline, mono + max(0, verified.deadline - wall))
        self.reserve = min(self.reserve, max(0, self.deadline - self.entered) / 2)

    @property
    def work_deadline(self): return self.deadline - self.reserve

    def require_margin(self, margin):
        if time.monotonic() + margin >= self.work_deadline:
            raise TimeoutError("LAB_INSUFFICIENT_BUDGET")


class ProcessOwner:
    def __init__(self, budget, mark):
        self.budget, self.mark = budget, mark
        self.process = None
        self.ready = asyncio.Event()
        self.closing = asyncio.Event()
        self.protocol_scope = None
        self.cancelled = False
        self.guard = None
        self.actions = []
        self.cleanup_errors = False
        self.phase = "starting"
        self.task = None
        self.timed_out = False

    def attach(self, process):
        if self.process is not None:
            raise RuntimeError("LAB_SECOND_PROCESS_FORBIDDEN")
        self.process = process
        self.mark("spawn_returned")
        self.ready.set()
        if self.cancelled or time.monotonic() >= self.budget.work_deadline:
            self.closing.set()

    def cancel(self):
        self.cancelled = True
        self.closing.set()
        if self.protocol_scope is not None:
            self.protocol_scope.cancel()
        elif self.phase == "starting" and self.task is not None:
            self.task.cancel()

    async def _wait(self, seconds):
        if self.process is None: return True
        if self.process.returncode is not None:
            await self.process.wait()
            return True
        try:
            await asyncio.wait_for(self.process.wait(), timeout=max(0, seconds))
            return True
        except TimeoutError:
            return False

    def force(self, kill=False):
        if self.process is None or self.process.returncode is not None: return
        try:
            (self.process.kill if kill else self.process.terminate)()
            self.actions.append("kill" if kill else "terminate")
        except ProcessLookupError:
            pass
        except Exception:
            self.cleanup_errors = True

    async def supervise(self):
        # asyncio task is owned/joined by this invocation, not a persistent worker.
        try:
            await asyncio.wait_for(self.ready.wait(), max(0, self.budget.work_deadline-time.monotonic()))
        except TimeoutError:
            return
        try:
            await asyncio.wait_for(self.closing.wait(), max(0, self.budget.work_deadline-time.monotonic()))
        except TimeoutError:
            self.timed_out = True
            self.cancel()
        self.mark("cleanup_started")
        remaining = max(0, self.budget.deadline-time.monotonic())
        if self.process.stdin is not None:
            try:
                with anyio.move_on_after(min(0.02, remaining)):
                    await self.process.stdin.aclose()
            except Exception:
                self.cleanup_errors = True
        # Grace, TERM and KILL all consume the SAME remaining total budget.
        if await self._wait(min(0.45, max(0, self.budget.deadline-time.monotonic()) * .7)): return
        self.force()
        if await self._wait(min(0.10, max(0, self.budget.deadline-time.monotonic()) * .5)): return
        self.force(kill=True)
        if not await self._wait(max(0, self.budget.deadline-time.monotonic())):
            self.cleanup_errors = True

    async def drain(self, task):
        self.cancel()
        # Shield only a bounded cleanup wait; cancellation never becomes an
        # unlimited shield around the original business exchange.
        with anyio.move_on_after(max(0, self.budget.deadline-time.monotonic()), shield=True):
            try:
                await asyncio.shield(task)
            except Exception:
                pass  # exchange errors are not a reason to abandon its reaping
        if not task.done():
            self.force(kill=True)
            # Emergency OS-reap observation, NOT added business/grace budget.
            # Any use of this interval is measured as an overrun, never success.
            with anyio.move_on_after(0.25, shield=True):
                try:
                    await asyncio.shield(task)
                except Exception:
                    pass
        if not task.done():
            task.cancel()
            raise RuntimeError("LAB_CLEANUP_INCOMPLETE")

    async def exchange(self, params, operation, errlog):
        token = _OWNER.set(self)
        self.guard = asyncio.create_task(self.supervise(), name="xoc-l3-process-guard")
        try:
            self.budget.require_margin(TotalBudget.SPAWN_MARGIN)
            if self.cancelled: raise TimeoutError("LAB_CANCELLED_BEFORE_SPAWN")
            async with stdio.stdio_client(params, errlog=errlog) as streams:
                self.phase = "protocol"
                self.mark("transport_open")
                try:
                    with anyio.fail_after(max(0, self.budget.work_deadline-time.monotonic())) as scope:
                        self.protocol_scope = scope
                        if self.cancelled: scope.cancel()
                        result = await operation(*streams)
                finally:
                    self.phase = "cleanup"
                    self.protocol_scope = None
                    self.mark("cleanup_started")
                    self.closing.set()
            self.mark("reaped")
            if self.cancelled or self.cleanup_errors or self.process is None or self.process.returncode != 0:
                raise RuntimeError("LAB_EXCHANGE_INTERRUPTED")
            if time.monotonic() >= self.budget.deadline:
                raise TimeoutError("LAB_TOTAL_BUDGET_EXCEEDED")
            return result
        finally:
            self.closing.set()
            if self.process is None:
                self.guard.cancel()
            try:
                await self.guard
            except asyncio.CancelledError:
                pass
            if self.phase == "starting" and self.process is not None:
                # A cancelled factory wrapper may have returned the OS handle
                # to capture, but never entered the SDK's process context.
                with anyio.move_on_after(max(0, self.budget.deadline-time.monotonic()), shield=True):
                    await self.process.aclose()
            _OWNER.reset(token)
