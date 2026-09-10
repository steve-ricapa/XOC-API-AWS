"""Offline assessment synchronization tests; Dynamo races use moto[dynamodb]."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("TICKETS_TABLE_NAME", "ticket-sync-test")

import boto3
import requests
from botocore.exceptions import ClientError

from src.handlers.workers import assess_ticket_automation as worker
from src.notifications import tickets as notifications
from src.notifications.events import build_notification_event_for_ticket_status
from src.shared import ticket_automation_sync as sync
from src.shared import tickets_store as store

try:
    from moto import mock_aws
except ImportError:
    mock_aws = None


class AssessmentContractTests(unittest.TestCase):
    def setUp(self):
        self.event = {"ticketId": "t1", "tenantId": 7, "subject": "Read only", "description": "Check availability", "phase": "assessment"}
        self.endpoint = patch.object(worker, "_resolve_victor_endpoint", return_value=("https://example.invalid", "/assess", "global"))
        self.endpoint.start()
        self.addCleanup(self.endpoint.stop)
        token = patch.object(worker, "_build_service_token", return_value="test-service-token")
        token.start()
        self.addCleanup(token.stop)

    def expected(self, can_resolve, source="global"):
        return {"canResolve": can_resolve, "ticketId": "t1", "tenantId": 7, "subject": "Read only", "description": "Check availability", "victorSource": source}

    def test_negative_assessment_keeps_exact_step_function_contract(self):
        response = MagicMock()
        response.json.return_value = {"can_resolve": False, "reason": "raw private provider content"}
        with patch.object(worker.requests, "post", return_value=response), patch.object(worker, "sync_unresolvable_assessment") as persist:
            self.assertEqual(self.expected(False), worker.handler(self.event, None))
        persist.assert_called_once_with(7, "t1", "CANNOT_RESOLVE")

    def test_positive_assessment_is_untouched_for_both_provider_keys(self):
        for key in ("can_resolve", "canResolve"):
            with self.subTest(key=key):
                response = MagicMock()
                response.json.return_value = {key: True}
                with patch.object(worker.requests, "post", return_value=response), patch.object(worker, "sync_unresolvable_assessment") as persist:
                    self.assertEqual(self.expected(True), worker.handler(self.event, None))
                persist.assert_not_called()

    def test_technical_errors_keep_false_result_but_have_distinct_safe_reason(self):
        forbidden = requests.Response()
        forbidden.status_code = 403
        unauthorized = requests.Response()
        unauthorized.status_code = 401
        errors = [
            (requests.exceptions.Timeout("private body"), "VICTOR_TIMEOUT"),
            (requests.exceptions.HTTPError(response=forbidden), "VICTOR_ACCESS_DENIED"),
            (requests.exceptions.HTTPError(response=unauthorized), "VICTOR_ACCESS_DENIED"),
            (requests.exceptions.ConnectionError("private body"), "VICTOR_UNAVAILABLE"),
        ]
        for error, code in errors:
            with self.subTest(code=code), patch.object(worker.requests, "post", side_effect=error), patch.object(worker, "sync_unresolvable_assessment") as persist:
                self.assertEqual(self.expected(False), worker.handler(self.event, None))
                persist.assert_called_once_with(7, "t1", code)

    def test_unconfigured_assessment_only_not_planning(self):
        with patch.object(worker, "_resolve_victor_endpoint", return_value=(None, "/assess", "fallback")), patch.object(worker, "sync_unresolvable_assessment") as persist:
            self.assertEqual(self.expected(False, "fallback"), worker.handler(self.event, None))
            persist.assert_called_once_with(7, "t1", "VICTOR_NOT_CONFIGURED")
            persist.reset_mock()
            worker.handler({**self.event, "phase": "plan"}, None)
            persist.assert_not_called()

    def test_persistence_failure_does_not_fail_or_change_workflow_decision(self):
        response = MagicMock()
        response.json.return_value = {"canResolve": False}
        with patch.object(worker.requests, "post", return_value=response), patch.object(store, "table") as table, patch.object(sync, "publish_ticket_status_notification") as push:
            table.update_item.side_effect = RuntimeError("secret database detail")
            with self.assertLogs(sync.logger, level="WARNING") as logs:
                self.assertEqual(self.expected(False), worker.handler(self.event, None))
            self.assertNotIn("secret database detail", "".join(logs.output))
            push.assert_not_called()

    def test_planning_and_execution_errors_remain_errors_without_sync(self):
        for phase in ("plan", "execute"):
            with self.subTest(phase=phase), patch.object(worker.requests, "post", side_effect=requests.exceptions.Timeout()), patch.object(worker, "sync_unresolvable_assessment") as persist:
                with self.assertRaises(worker.ValidationError):
                    worker.handler({**self.event, "phase": phase}, None)
                persist.assert_not_called()

    def test_push_event_uses_existing_type_self_and_safe_deep_link(self):
        event = build_notification_event_for_ticket_status(tenant_id=7, ticket_id="t1", recipient_user_id=32, status="DERIVED")
        self.assertEqual("ticket.derived", event["eventType"])
        self.assertEqual("SELF", event["audienceType"])
        self.assertEqual("32", event["recipientUserId"])
        self.assertEqual("xoc://ticket/t1", event["deepLink"])
        self.assertEqual("ticket.derived:7:t1:status", event["dedupeKey"])
        self.assertNotIn("expir", event["body"])

    def test_push_failure_cannot_undo_persisted_result(self):
        with patch.object(store, "table"), patch.object(sync, "publish_ticket_status_notification", side_effect=RuntimeError("private")):
            self.assertTrue(sync.sync_unresolvable_assessment(7, "t1", "CANNOT_RESOLVE"))

    def test_aws_conditional_conflict_never_publishes(self):
        with patch.object(store, "table") as table, patch.object(sync, "publish_ticket_status_notification") as push:
            table.update_item.side_effect = ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            self.assertFalse(sync.sync_unresolvable_assessment(7, "t1", "CANNOT_RESOLVE"))
            push.assert_not_called()


@unittest.skipUnless(mock_aws, "Install moto[dynamodb] to run offline DynamoDB condition/race tests")
class DynamoAssessmentSyncTests(unittest.TestCase):
    def setUp(self):
        self.aws = mock_aws()
        self.aws.start()
        self.addCleanup(self.aws.stop)
        self.table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="ticket-sync-races", KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}], BillingMode="PAY_PER_REQUEST",
        )
        table_patch = patch.object(store, "table", self.table)
        table_patch.start()
        self.addCleanup(table_patch.stop)
        publisher = patch.object(notifications, "publish_notification_requested")
        self.publish = publisher.start()
        self.addCleanup(publisher.stop)
        self.ticket_id, self.initial = store.build_new_ticket_item({"subject": "test"}, 7, 32)
        self.table.put_item(Item=self.initial)
        self.arn = "arn:aws:states:us-east-1:123456789012:execution:automation:test"

    def item(self, tenant=7):
        return self.table.get_item(Key=store.ticket_key(tenant, self.ticket_id), ConsistentRead=True).get("Item")

    def finish(self, reason="CANNOT_RESOLVE", tenant=7):
        return sync.sync_unresolvable_assessment(tenant, self.ticket_id, reason)

    def test_finish_after_start_updates_ticket_index_and_notifies_creator_once(self):
        sync.record_automation_started(7, self.ticket_id, self.arn)
        self.assertEqual("STARTED", self.item()["execution_status"])
        self.assertTrue(self.finish())
        result = self.item()
        self.assertEqual("DERIVED", result["status"])
        self.assertEqual("CANNOT_RESOLVE", result["execution_status"])
        self.assertEqual("TICKET#7#STATUS#DERIVED", result["gsi1pk"])
        self.assertIsNone(result["executed_at"])
        for key in ("gsi1sk", "gsi2pk", "gsi2sk", "gsi3pk", "gsi3sk", "created_by_user_id", "created_at", "action_plan", "pending_decision"):
            self.assertEqual(self.initial[key], result[key])
        self.assertFalse(self.finish())
        self.publish.assert_called_once()
        event = self.publish.call_args.args[0]
        self.assertEqual("32", event["recipientUserId"])
        self.assertEqual("7", event["tenantId"])
        self.assertEqual("SELF", event["audienceType"])
        self.assertIn("automation_assessment", store.serialize_ticket(result))

    def test_finish_before_start_preserves_terminal_result_but_attaches_arn(self):
        self.assertTrue(self.finish())
        before = self.item()
        sync.record_automation_started(7, self.ticket_id, self.arn)
        after = self.item()
        self.assertEqual(self.arn, after.pop("execution_arn"))
        before.pop("execution_arn")
        self.assertEqual(before, after)

    def test_finish_between_start_writes_cannot_be_reverted(self):
        real_update = self.table.update_item
        def interleaved(**kwargs):
            result = real_update(**kwargs)
            if kwargs["UpdateExpression"] == "SET execution_arn = :arn":
                self.assertTrue(self.finish())
            return result
        with patch.object(self.table, "update_item", side_effect=interleaved):
            sync.record_automation_started(7, self.ticket_id, self.arn)
        self.assertEqual("CANNOT_RESOLVE", self.item()["execution_status"])

    def test_no_overwrite_of_advanced_status_plan_approval_or_run(self):
        protected = [{"status": status} for status in ("PREAPROBADO", "APROBADO", "EN_EJECUCION", "RESUELTO", "FALLIDO", "RECHAZADO", "DERIVADO", "DERIVED")]
        protected += [{"execution_status": "RUNNING"}, {"action_plan": {"steps": ["test"]}}, {"action_plans": [{"plan_id": "p1"}]}, {"pending_decision": {"requested_at": "test"}}]
        for changes in protected:
            with self.subTest(changes=changes):
                item = {**self.initial, **changes}
                self.table.put_item(Item=item)
                self.assertFalse(self.finish())
                sync.record_automation_started(7, self.ticket_id, self.arn)
                result = self.item()
                self.assertEqual(item["status"], result["status"])
                self.assertEqual(item["execution_status"], result["execution_status"])
                self.assertNotIn("automation_assessment", result)
        self.publish.assert_not_called()

    def test_missing_or_different_tenant_cannot_create_or_update_ticket(self):
        self.assertFalse(self.finish(tenant=8))
        sync.record_automation_started(8, self.ticket_id, self.arn)
        self.assertIsNone(self.item(8))
        self.assertEqual(self.initial, self.item())
        self.table.delete_item(Key=store.ticket_key(7, self.ticket_id))
        self.assertFalse(self.finish())
        sync.record_automation_started(7, self.ticket_id, self.arn)
        self.assertIsNone(self.item())
        self.publish.assert_not_called()

    def test_old_item_with_absent_null_fields_is_supported(self):
        legacy = {k: v for k, v in self.initial.items() if v is not None}
        self.table.put_item(Item=legacy)
        sync.record_automation_started(7, self.ticket_id, self.arn)
        self.assertTrue(self.finish())
        self.assertEqual("CANNOT_RESOLVE", self.item()["execution_status"])

    def test_wrong_stored_tenant_is_rejected_even_when_key_matches(self):
        self.table.put_item(Item={**self.initial, "tenant_id": 8})
        self.assertFalse(self.finish())
        self.assertEqual("PENDING", self.item()["status"])

    def test_existing_other_execution_arn_is_not_replaced(self):
        self.table.put_item(Item={**self.initial, "execution_arn": "another-run"})
        sync.record_automation_started(7, self.ticket_id, self.arn)
        self.assertEqual("another-run", self.item()["execution_arn"])
        self.assertIsNone(self.item()["execution_status"])

    def test_technical_assessment_failure_is_not_a_business_denial(self):
        self.assertTrue(self.finish("VICTOR_TIMEOUT"))
        item = self.item()
        self.assertEqual("ASSESSMENT_FAILED", item["execution_status"])
        self.assertEqual("VICTOR_TIMEOUT", item["automation_assessment"]["reason_code"])
        self.assertIsNone(item["executed_at"])


class StartAutomationContractTests(unittest.TestCase):
    def test_start_execution_contract_is_unchanged_even_if_sync_fails(self):
        from src.handlers.workers import start_automation
        with patch.object(start_automation, "get_tenant_ticket_or_none", return_value={"description": "readonly"}), patch.object(start_automation, "stepfunctions") as sf, patch.object(start_automation, "record_automation_started", side_effect=RuntimeError("offline")), patch.dict(os.environ, {"AUTOMATION_WORKFLOW_ARN": "existing-state-machine"}):
            sf.start_execution.return_value = {"executionArn": "existing-execution"}
            result = start_automation.handler({"ticketId": "t1", "tenantId": 7, "subject": "test", "eventType": "ticket.created"}, None)
        self.assertEqual({"status": "started", "executionArn": "existing-execution"}, result)
        args = sf.start_execution.call_args.kwargs
        self.assertEqual("ticket-t1", args["name"])
        self.assertEqual("existing-state-machine", args["stateMachineArn"])
        self.assertEqual({"input": {"ticketId": "t1", "tenantId": 7, "subject": "test", "description": "readonly"}}, json.loads(args["input"]))


if __name__ == "__main__":
    unittest.main()
