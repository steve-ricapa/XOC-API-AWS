"""Invocable local fixture worker; SDK client/server, not AWS Lambda Invoke.

One instance represents ONE request; no retry and no durable replay store. A new
instance with the identical signed request is another READ_ONLY invocation.
"""
from datetime import timedelta
import asyncio
import json
from pathlib import Path
import sys
import threading
import time
import anyio
from mcp import ClientSession, StdioServerParameters, types
from mcp.client import stdio
from .client import PROTOCOL
from .isolation import LAB_ROOT, backend_path, clean_environment, laboratory_python
from .request_envelope import EnvelopeError, canonical, verify_envelope
from .transport import OUTPUT_LIMIT, wire_bytes
from .verified_server import ENVELOPE_ENV
from .budget import TotalBudget, ProcessOwner

_SLOTS = threading.BoundedSemaphore(4)


class BoundedReceive:
    """Validate the complete SDK message before it reaches ClientSession.

SDK v1 parses a line before yielding it. This is not a pre-allocation defense
against hostile arbitrary servers; this worker launches only our fixed child.
"""
    def __init__(self, stream):
        self.stream = stream

    async def receive(self):
        message = await self.stream.receive()
        if isinstance(message, Exception) or len(wire_bytes(message.message)) > OUTPUT_LIMIT:
            raise ValueError("LAB_INVALID_WIRE_RESULT")
        return message

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.stream.aclose()

    async def aclose(self):
        await self.stream.aclose()


def validate_result(result, verified):
    payload = result.structuredContent
    if (type(payload) is not dict or set(payload) != {
            "request_id", "tool_name", "status", "code", "data", "audit_id"}
            or payload["request_id"] != verified.request_id
            or payload["tool_name"] != verified.tool_name
            or payload["status"] not in ("allowed", "denied", "error", "needs_approval")
            or result.isError != (payload["status"] != "allowed")
            or len(result.content) != 1 or result.content[0].type != "text"
            or json.loads(result.content[0].text) != payload
            or type(payload["code"]) is not str or not 1 <= len(payload["code"]) <= 128
            or type(payload["audit_id"]) is not str or not 1 <= len(payload["audit_id"]) <= 128):
        raise ValueError("LAB_INVALID_TOOL_RESULT")
    if payload["status"] != "allowed" and payload["data"] is not None:
        raise ValueError("LAB_INVALID_TOOL_RESULT")
    if payload["status"] == "allowed" and type(payload["data"]) is not dict:
        raise ValueError("LAB_INVALID_TOOL_RESULT")
    # The fixed child applies the canonical backend projection. This boundary
    # checks correlation/envelope, not a second copy of the business projection.
    if "LAB_CANARY" in result.content[0].text:
        raise ValueError("LAB_INVALID_TOOL_RESULT")
    return payload


class QueryWorker:
    def __init__(self):
        self._claimed = False
        self._lock = threading.Lock()
        self.measurements = []
        self.lifecycle = []

    async def run(self, envelope, backend=None, *, timeout_seconds=5, errlog=sys.stderr):
        # Monotonic elapsed timeline; ends AFTER the SDK context reaps the child.
        # No request data, envelope, token or result is recorded in measurements.
        start = time.monotonic()
        marks = {"entry": 0.0}
        def mark(name): marks.setdefault(name, time.monotonic() - start)
        try:
            return await self._run(envelope, backend, timeout_seconds=timeout_seconds, errlog=errlog, mark=mark, start=start)
        finally:
            mark("finished")
            self.measurements.append(marks)

    async def _run(self, envelope, backend, *, timeout_seconds, errlog, mark, start):
        with self._lock:
            if self._claimed:
                return {"status": "error", "code": "lab_call_limit"}
            self._claimed = True
        try:
            budget = TotalBudget(start, timeout_seconds)
        except ValueError:
            return {"status": "error", "code": "lab_invalid_deadline"}
        try:
            # Verify before checking backend paths, launching SDK or importing providers.
            serialized = canonical(envelope) if type(envelope) is dict else envelope
            mark("serialized")
            verified = verify_envelope(serialized)
            mark("verified")
            budget.bind(verified)
        except (EnvelopeError, TypeError, ValueError, RecursionError):
            return {"status": "error", "code": "lab_invalid_envelope"}
        if not _SLOTS.acquire(blocking=False):
            return {"status": "error", "code": "lab_busy"}
        owner = None
        task = None
        deadline_rejected = False
        try:
            mark("admitted")
            budget.require_margin(TotalBudget.SPAWN_MARGIN)
            backend = backend_path(backend)
            mark("artifact_verified")
            python = laboratory_python()
            mark("interpreter_verified")
            # Override ALL SDK default inheritance; no user home/PATH or app config.
            env = {name: "" for name in stdio.DEFAULT_INHERITED_ENV_VARS}
            env.update(clean_environment())
            env[ENVELOPE_ENV] = serialized
            params = StdioServerParameters(command=str(python),
                args=["-B", "-m", "xoc_mcp_lab.verified_server", "--lab", "--backend", str(backend)],
                env=env, cwd=str(LAB_ROOT))
            mark("prepared")
            budget.require_margin(TotalBudget.SPAWN_MARGIN)
            owner = ProcessOwner(budget, mark)
            async def operation(read, write):
                budget.require_margin(TotalBudget.PHASE_MARGIN)
                async with ClientSession(BoundedReceive(read), write,
                        read_timeout_seconds=timedelta(seconds=max(0.001, budget.work_deadline-time.monotonic()))) as session:
                    info = await session.initialize()
                    mark("initialized")
                    if info.protocolVersion != PROTOCOL:
                        raise ValueError("LAB_PROTOCOL_MISMATCH")
                    budget.require_margin(TotalBudget.PHASE_MARGIN)
                    if owner.cancelled: raise TimeoutError("LAB_CANCELLED")
                    result = await session.call_tool(verified.tool_name, json.loads(verified.arguments_json))
                    mark("tool_result")
                    payload = validate_result(result, verified)
                    response = {"status": "completed", "request_id": verified.request_id, "result": payload}
                    canonical(response)  # account for the local response serialization cost
                    mark("validated")
                    return response
            task = asyncio.create_task(owner.exchange(params, operation, errlog), name="xoc-l3-exchange")
            owner.task = task
            try:
                return await asyncio.wait_for(asyncio.shield(task), max(0, budget.deadline-time.monotonic()))
            except asyncio.CancelledError:
                await owner.drain(task)
                raise
            except TimeoutError:
                await owner.drain(task)
                raise
        except Exception as exc:
            deadline_rejected = isinstance(exc, TimeoutError)
            # Includes SDK exception groups, child EOF, timeout, projection and protocol errors.
            return {"status": "error", "code": "lab_worker_unavailable", "request_id": verified.request_id}
        finally:
            reaped = owner is None or owner.process is None or owner.process.returncode is not None
            finished = task is None or task.done()
            self.lifecycle.append(dict(total_limit_seconds=budget.deadline-start,
                cleanup_reserve_seconds=budget.reserve, overrun=time.monotonic() > budget.deadline,
                reaped=reaped, exchange_finished=finished,
                supervisor_finished=owner is None or owner.guard is None or owner.guard.done(),
                cancelled=owner is not None and owner.cancelled,
                timeout=deadline_rejected or (owner is not None and owner.timed_out),
                actions=owner.actions if owner else []))
            # Fail closed on an unobserved kernel-level cleanup failure: do not
            # return the admission slot while a child/task might still be alive.
            if reaped and finished:
                _SLOTS.release()
