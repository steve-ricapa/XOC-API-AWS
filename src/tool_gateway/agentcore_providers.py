"""Bounded DynamoDB readers for the private AgentCore target.

Uses existing keys/serializers, not local fixture tables. Never installs global
monkeypatches or calls writers. No partial count/list on a budget failure.
"""
import os
import time

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from src.tool_gateway.executor import _allowed_arguments, _bounded_limit


class ReadOnlyProviders:
    def __init__(self, deadline_ms):
        self.deadline_ms = deadline_ms
        self.tables = {}

    def check(self):
        if time.time() * 1000 + 500 >= self.deadline_ms:
            raise TimeoutError("provider_deadline")

    def table(self, variable):
        self.check()
        if variable not in self.tables:
            name = os.environ[variable]
            if not name or os.getenv("REPORTS_STORE_MODE") == "local":
                raise ValueError("real_store_required")
            resource = boto3.resource("dynamodb", config=Config(connect_timeout=0.15, read_timeout=0.3,
                                        retries={"total_max_attempts": 1}))
            self.tables[variable] = resource.Table(name)
        self.check()  # resource/credential initialization cannot extend validity
        return self.tables[variable]

    def pages(self, table, **query):
        for _ in range(20):
            self.check()
            page = table.query(**query)
            self.check()
            yield page
            key = page.get("LastEvaluatedKey")
            if not key:
                return
            query["ExclusiveStartKey"] = key
        raise TimeoutError("provider_page_limit")

    def tickets_list(self, context, arguments):
        values = _allowed_arguments(arguments, {"status", "limit"})
        limit = _bounded_limit(values.get("limit"), default=50, maximum=200)
        from src.shared import tickets_store as store
        status = store.normalize_status(values.get("status"))
        query = {"KeyConditionExpression": Key("pk").eq(store.tenant_pk(context.effective_tenant_id)), "Limit": 200}
        if status:
            query.update(IndexName="StatusIndex", ScanIndexForward=False,
                         KeyConditionExpression=Key("gsi1pk").eq(store.status_index_pk(context.effective_tenant_id, status)))
        rows = []
        for page in self.pages(self.table("TICKETS_TABLE_NAME"), **query):
            rows.extend(page.get("Items", []))
        rows = [store.serialize_ticket(row) for row in store.sort_tickets_desc(rows)[:limit]]
        return {"tickets": rows, "count": len(rows)}

    def tickets_get(self, context, arguments):
        values = _allowed_arguments(arguments, {"ticket_id"})
        ticket_id = values.get("ticket_id")
        if not isinstance(ticket_id, str) or not 1 <= len(ticket_id.strip()) <= 256:
            raise ValueError("invalid_ticket_id")
        from src.shared import tickets_store as store
        row = self.table("TICKETS_TABLE_NAME").get_item(
            Key=store.ticket_key(context.effective_tenant_id, ticket_id.strip())).get("Item")
        self.check()
        if not row or row.get("pk") != store.tenant_pk(context.effective_tenant_id):
            raise ValueError("not_found")
        return {"ticket": store.serialize_ticket(row)}

    def documents_list(self, context, arguments):
        values = _allowed_arguments(arguments, {"status", "limit"})
        limit = _bounded_limit(values.get("limit"), default=50, maximum=200)
        # Verify configuration BEFORE importing a store with a legacy local mode.
        table = self.table("REPORTS_TABLE_NAME")
        from src.reports import store
        status = values.get("status")
        if status is not None and (not isinstance(status, str) or status not in store.DOCUMENT_STATUSES):
            raise ValueError("invalid_status")
        query = {"KeyConditionExpression": Key("pk").eq(store.tenant_pk(context.effective_tenant_id)),
                 "Limit": limit, "ScanIndexForward": False}
        if status:
            query.update(IndexName="StatusIndex", KeyConditionExpression=Key("gsi2pk").eq(
                store.status_index_pk(context.effective_tenant_id, status)))
        rows = []
        for page in self.pages(table, **query):
            rows.extend(page.get("Items", []))
            if len(rows) >= limit:
                break
        rows = [store.serialize_report(row) for row in rows[:limit]]
        return {"documents": rows, "count": len(rows)}

    def unread_count(self, context, arguments):
        _allowed_arguments(arguments, set())
        from src.shared.user_notification_inbox import _status_partition_key, STATUS_UNREAD
        total = 0
        for page in self.pages(self.table("USER_NOTIFICATION_INBOX_TABLE_NAME"),
                IndexName="UserStatusCreatedAtIndex", Select="COUNT",
                KeyConditionExpression=Key("GSI2PK").eq(_status_partition_key(
                    context.effective_tenant_id, context.user_id, STATUS_UNREAD))):
            total += int(page.get("Count", 0))
        return {"count": total}

    def handlers(self):
        return {"tickets.list": self.tickets_list, "tickets.get": self.tickets_get,
                "documents.list": self.documents_list, "notifications.unread_count": self.unread_count}
