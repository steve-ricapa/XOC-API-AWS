"""L4 request ownership. No identity cache, session authority or replay ledger."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
import threading
import time
from typing import Callable

from xoc_mcp_lab.request_envelope import VerifiedContext
from xoc_mcp_lab.wiring import Wiring


class ScopeError(RuntimeError):
    def __init__(self):
        super().__init__("LAB_REQUEST_SCOPE_REQUIRED")


@dataclass(frozen=True)
class FaultPlan:
    """Trusted harness callback only; never populated from tool arguments."""

    first_read: Callable[["RequestScope"], None] | None = field(default=None, repr=False)


@dataclass
class _Observation:
    adapter: object | None = field(default=None, repr=False)
    provider_read_count: int = 0
    provider_first_ns: int | None = None
    provider_last_ns: int | None = None
    network_attempts: int = 0
    secret_file_attempts: int = 0
    audit_records: list = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_CURRENT: ContextVar[RequestScope | None] = ContextVar("xoc_l4_request_scope", default=None)


@dataclass(frozen=True)
class RequestScope:
    """Frozen authority, with separate mutable request-owned fixture observations."""

    verified: VerifiedContext = field(repr=False)
    faults: FaultPlan | None = field(default=None, repr=False)
    wiring: Wiring = field(default_factory=Wiring, init=False, repr=False)
    provider_entered: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _observation: _Observation = field(default_factory=_Observation, init=False, repr=False)

    def __post_init__(self):
        if type(self.verified) is not VerifiedContext:
            raise ScopeError()
        self.verified.require_valid()
        if self.faults is not None and not isinstance(self.faults, FaultPlan):
            raise ScopeError()

    @property
    def adapter(self):
        if current_scope() is not self:
            raise ScopeError()
        from .wiring import require_persistent_wiring
        require_persistent_wiring()
        with self._observation.lock:
            if self._observation.adapter is None:
                from xoc_mcp_lab.adapter import Adapter
                self._observation.adapter = Adapter.from_verified(self.verified, self.wiring)
            return self._observation.adapter

    def _provider_read(self):
        """Called after the canonical fake read counter/budget check, before lookup."""
        observation = self._observation
        with observation.lock:
            first = observation.provider_read_count == 0
            observation.provider_read_count += 1
            if first:
                observation.provider_first_ns = time.perf_counter_ns()
        self.provider_entered.set()
        if first and self.faults is not None and self.faults.first_read is not None:
            self.faults.first_read(self)

    def _provider_finished(self):
        with self._observation.lock:
            self._observation.provider_last_ns = time.perf_counter_ns()

    def safe_summary(self):
        """Finite counters and canonical audit fields, never assertions/results/args."""
        observation = self._observation
        with observation.lock:
            adapter = observation.adapter
            return dict(
                request_id=self.verified.request_id,
                handler_calls=adapter.handler_calls if adapter is not None else 0,
                executor_calls=adapter.executor_calls if adapter is not None else 0,
                reads=self.wiring.reads,
                writes=self.wiring.write_attempts,
                write_attempts=self.wiring.write_attempts,
                provider_attempts=self.wiring.provider_attempts,
                network_attempts=observation.network_attempts,
                secret_file_attempts=observation.secret_file_attempts,
                provider_entered=self.provider_entered.is_set(),
                provider_first_ns=observation.provider_first_ns,
                provider_last_ns=observation.provider_last_ns,
                provider_read_count=observation.provider_read_count,
                audit_records=[dict(record) for record in observation.audit_records],
            )


def current_scope():
    scope = _CURRENT.get()
    if scope is None:
        raise ScopeError()
    return scope


def optional_scope():
    """For process-start guards only; business/provider entry uses current_scope."""
    return _CURRENT.get()


def worker_context():
    """Copy other request context, but give worker binding a genuinely empty base."""
    context = copy_context()
    context.run(_CURRENT.set, None)
    return context


@contextmanager
def bind_scope(scope):
    if not isinstance(scope, RequestScope):
        raise ScopeError()
    token = _CURRENT.set(scope)
    try:
        yield scope
    finally:
        _CURRENT.reset(token)
