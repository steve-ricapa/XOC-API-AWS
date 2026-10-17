"""Install fake provider boundaries once, dispatch every operation by ContextVar."""
from __future__ import annotations

from contextlib import ExitStack
import importlib
import logging
import threading
from unittest.mock import patch

from xoc_mcp_lab.isolation import Isolation, configure_logs
from xoc_mcp_lab.wiring import MemoryTable
from .scope import ScopeError, current_scope, optional_scope


_LIFETIME_LOCK = threading.Lock()
_ACTIVE = False
_TABLE_NAMES = frozenset({"LAB_TICKETS", "LAB_DOCUMENTS", "LAB_INBOX"})


def require_persistent_wiring():
    if not _ACTIVE:
        raise RuntimeError("LAB_PERSISTENT_WIRING_REQUIRED")


class _RequestTable(MemoryTable):
    def __init__(self, name, scope):
        super().__init__(name, scope.wiring)
        self.scope = scope

    def _read(self):
        super()._read()
        self.scope._provider_read()


class ScopedTable:
    """Stable import-time facade, never capturing the startup or another request."""

    def __init__(self, name):
        self.name = name

    def _invoke(self, operation, **kwargs):
        require_persistent_wiring()
        scope = current_scope()
        table = _RequestTable(self.name, scope)
        try:
            return getattr(table, operation)(**kwargs)
        finally:
            scope._provider_finished()

    def get_item(self, **kwargs):
        return self._invoke("get_item", **kwargs)

    def query(self, **kwargs):
        return self._invoke("query", **kwargs)

    def __getattr__(self, name):
        require_persistent_wiring()
        current_scope().wiring.write_attempts += 1
        raise RuntimeError("LAB_WRITE_OR_UNKNOWN_OPERATION_BLOCKED")


class _AuditObserver(logging.Handler):
    def emit(self, record):
        scope = optional_scope()
        if scope is None or record.name != "src.tool_gateway.audit":
            return
        values = record.args
        if not isinstance(values, tuple) or len(values) != 11:
            return
        keys = ("audit_id", "request_id", "tool_name", "effective_tenant_id", "user_id",
                "role", "source", "decision", "code", "risk_level", "access_level")
        safe = dict(zip(keys, values))
        with scope._observation.lock:
            # One business attempt normally produces one record. Bound diagnostics too.
            if len(scope._observation.audit_records) < 4:
                scope._observation.audit_records.append(safe)


class PersistentWiring:
    """One non-nestable process lifetime; no policy, executor or helper replacements."""

    def __init__(self):
        self.stack = ExitStack()
        self._entered = False

    def __enter__(self):
        global _ACTIVE
        if not _LIFETIME_LOCK.acquire(blocking=False):
            raise RuntimeError("LAB_PERSISTENT_WIRING_ALREADY_ACTIVE")
        try:
            if self._entered:
                raise RuntimeError("LAB_PERSISTENT_WIRING_ALREADY_USED")
            self._entered = True
            import boto3
            import boto3.session
            import botocore.session
            import botocore.endpoint
            import sqlalchemy

            def forbidden(*args, **kwargs):
                scope = optional_scope()
                if scope is not None:
                    scope.wiring.provider_attempts += 1
                raise RuntimeError("LAB_REAL_PROVIDER_BLOCKED")

            def resource(service, *args, **kwargs):
                if service != "dynamodb" or args or kwargs:
                    return forbidden()
                return self

            for target, attribute, replacement in (
                (boto3, "resource", resource),
                (boto3, "client", forbidden),
                (boto3.session.Session, "resource", lambda _, *a, **kw: resource(*a, **kw)),
                (boto3.session.Session, "client", forbidden),
                (botocore.session.Session, "get_credentials", forbidden),
                (botocore.endpoint.Endpoint, "_send", forbidden),
                (sqlalchemy, "create_engine", forbidden),
            ):
                self.stack.enter_context(patch.object(target, attribute, replacement))
            tickets = importlib.import_module("src.shared.tickets_store")
            documents = importlib.import_module("src.reports.store")
            importlib.import_module("src.shared.user_notification_inbox")
            self.stack.enter_context(patch.object(tickets, "table", self.Table("LAB_TICKETS")))
            self.stack.enter_context(patch.object(documents, "table", self.Table("LAB_DOCUMENTS")))
            configure_logs()
            logger = logging.getLogger("src.tool_gateway.audit")
            observer = _AuditObserver()
            logger.addHandler(observer)
            self.stack.callback(logger.removeHandler, observer)
            _ACTIVE = True
            return self
        except BaseException:
            self.stack.close()
            _LIFETIME_LOCK.release()
            raise

    def Table(self, name):
        if name not in _TABLE_NAMES:
            scope = optional_scope()
            if scope is not None:
                scope.wiring.provider_attempts += 1
            raise RuntimeError("LAB_UNKNOWN_TABLE_BLOCKED")
        return ScopedTable(name)

    def __exit__(self, *exc):
        global _ACTIVE
        _ACTIVE = False
        try:
            return self.stack.__exit__(*exc)
        finally:
            _LIFETIME_LOCK.release()


class ScopedIsolation(Isolation):
    """Canonical guards installed once, their mutable counts resolved per request."""

    def __init__(self):
        self._counter_lock = threading.Lock()
        self._totals = {"network_attempts": 0, "secret_file_attempts": 0}
        super().__init__()

    def _get(self, name):
        scope = optional_scope()
        if scope is not None:
            return getattr(scope._observation, name)
        with self._counter_lock:
            return self._totals[name]

    def _set(self, name, value):
        scope = optional_scope()
        with self._counter_lock:
            if scope is None:
                self._totals[name] = value
            else:
                previous = getattr(scope._observation, name)
                setattr(scope._observation, name, value)
                self._totals[name] += value - previous

    network_attempts = property(lambda self: self._get("network_attempts"),
                                lambda self, value: self._set("network_attempts", value))
    secret_file_attempts = property(lambda self: self._get("secret_file_attempts"),
                                   lambda self, value: self._set("secret_file_attempts", value))
