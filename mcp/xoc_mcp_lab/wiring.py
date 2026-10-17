"""Only provider boundary is fake. XOC helpers, schemas, policy and executor are original."""
from __future__ import annotations
from contextlib import ExitStack
from copy import deepcopy
import importlib
import logging
import sys
import time
from unittest.mock import patch
from .fixtures import dataset
from .isolation import configure_logs

class MemoryTable:
    def __init__(self, name, owner):
        self.name, self.owner = name, owner
    def _read(self):
        self.owner.reads += 1
        if self.owner.reads - self.owner.read_start > 40 or time.monotonic() > self.owner.deadline:
            raise RuntimeError("LAB_READ_BUDGET_EXCEEDED")
        if self.owner.fail_reads: raise RuntimeError("LAB_CANARY_NEVER_PUBLIC fake provider failure")
    def get_item(self, *, Key, **kwargs):
        self._read()
        if self.name != "LAB_TICKETS" or set(Key) != {"pk", "sk"} or kwargs:
            raise RuntimeError("Unsupported fixture get contract")
        self.owner.queries.append((self.name, "get", deepcopy(Key)))
        row = next((r for r in self.owner.rows[self.name] if all(r.get(k)==v for k,v in Key.items())), None)
        return {"Item":deepcopy(row)} if row else {}
    def query(self, **kwargs):
        self._read()
        if set(kwargs) - {"KeyConditionExpression","IndexName","ScanIndexForward","Select","Limit","ExclusiveStartKey"}:
            raise RuntimeError("Unsupported fixture query")
        expr = kwargs["KeyConditionExpression"].get_expression()
        if expr["operator"] != "=": raise RuntimeError("Unsupported fixture condition")
        attr, value = expr["values"]
        field = attr.name
        expected = {
            "LAB_TICKETS": {None:"pk", "StatusIndex":"gsi1pk"},
            "LAB_DOCUMENTS": {None:"pk", "StatusIndex":"gsi2pk"},
            "LAB_INBOX": {"UserStatusCreatedAtIndex":"GSI2PK"}}
        if expected[self.name].get(kwargs.get("IndexName")) != field:
            raise RuntimeError("Unexpected fixture index/key")
        self.owner.queries.append((self.name, "query", {field:value}))
        rows = [r for r in self.owner.rows[self.name] if r.get(field)==value]
        sort = {"LAB_TICKETS":"sk", "LAB_DOCUMENTS":"sk", "LAB_INBOX":"SK"}[self.name]
        rows.sort(key=lambda r:r[sort], reverse=kwargs.get("ScanIndexForward") is False)
        offset = 0
        start = kwargs.get("ExclusiveStartKey")
        if start:
            offset = next(i+1 for i,r in enumerate(rows) if r[sort]==start[sort])
        page = rows[offset:offset+min(kwargs.get("Limit",200),2)]  # force real pagination
        response = {"Count":len(page)}
        if kwargs.get("Select") != "COUNT": response["Items"] = deepcopy(page)
        if offset+len(page)<len(rows): response["LastEvaluatedKey"] = {sort:page[-1][sort]}
        return response
    def __getattr__(self, name):
        self.owner.write_attempts += 1
        raise RuntimeError("LAB_WRITE_OR_UNKNOWN_OPERATION_BLOCKED")

class Wiring:
    def __init__(self):
        self.rows = dataset()
        self.reads = self.read_start = self.write_attempts = self.provider_attempts = 0
        self.queries = []
        self.deadline = float("inf")
        self.fail_reads = False
        self.stack = ExitStack()
    def start_call(self):
        self.read_start = self.reads
        self.deadline = time.monotonic()+5
    def __enter__(self):
        import boto3
        import boto3.session
        import botocore.session
        import botocore.endpoint
        import sqlalchemy
        def forbidden(*args, **kwargs):
            self.provider_attempts += 1
            raise RuntimeError("LAB_REAL_PROVIDER_BLOCKED")
        def resource(service, *args, **kwargs):
            if service != "dynamodb" or args or kwargs: return forbidden()
            return self
        self.stack.enter_context(patch.object(boto3,"resource",resource))
        self.stack.enter_context(patch.object(boto3,"client",forbidden))
        self.stack.enter_context(patch.object(boto3.session.Session,"resource",lambda _,*a,**kw: resource(*a,**kw)))
        self.stack.enter_context(patch.object(boto3.session.Session,"client",forbidden))
        self.stack.enter_context(patch.object(botocore.session.Session,"get_credentials",forbidden))
        self.stack.enter_context(patch.object(botocore.endpoint.Endpoint,"_send",forbidden))
        self.stack.enter_context(patch.object(sqlalchemy,"create_engine",forbidden))
        self.prior_modules = set(sys.modules)
        try:
            self.tickets = importlib.import_module("src.shared.tickets_store")
            self.documents = importlib.import_module("src.reports.store")
            self.inbox = importlib.import_module("src.shared.user_notification_inbox")
            # A second fixture in the same test process must never retain previous tables.
            self.stack.enter_context(patch.object(self.tickets, "table", self.Table("LAB_TICKETS")))
            self.stack.enter_context(patch.object(self.documents, "table", self.Table("LAB_DOCUMENTS")))
            configure_logs()
            return self
        except BaseException:
            self.stack.close()
            raise
    def Table(self, name):
        if name not in self.rows:
            self.provider_attempts += 1
            raise RuntimeError("LAB_UNKNOWN_TABLE_BLOCKED")
        return MemoryTable(name,self)
    def __exit__(self,*exc):
        self.stack.__exit__(*exc)
        # Restore import-time tables too: don't leave a fake reachable after a test.
        for name in set(sys.modules)-self.prior_modules:
            if name in {"src.shared.tickets_store", "src.reports.store", "src.shared.user_notification_inbox"}:
                sys.modules.pop(name,None)
