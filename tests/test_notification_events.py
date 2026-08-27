"""Unit tests for the Phase 3 EventBridge -> SQS notification pipeline."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from src.handlers.workers import notification_events
from src.notifications import events


def _report_event() -> dict:
    return {
        "version": 1,
        "eventId": "evt-report-001",
        "eventType": "report.generated",
        "tenantId": "8",
        "audienceType": "SELF",
        "recipientUserId": "18",
        "title": "Reporte generado",
        "body": "Tu reporte ya está disponible.",
        "deepLink": "xoc://reports/report-001",
        "priority": "normal",
        "resourceType": "report",
        "resourceId": "report-001",
        "dedupeKey": "report.generated:8:report-001",
        "metadata": {"reportId": "report-001"},
    }


class NotificationEventsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_application_id = os.environ.get("END_USER_MESSAGING_APPLICATION_ID")
        self.previous_max = os.environ.get("NOTIFICATION_MAX_DEVICES_PER_EVENT")
        self.previous_batch = os.environ.get("NOTIFICATION_SEND_BATCH_SIZE")
        os.environ["END_USER_MESSAGING_APPLICATION_ID"] = "app-id"
        os.environ["NOTIFICATION_MAX_DEVICES_PER_EVENT"] = "500"
        os.environ["NOTIFICATION_SEND_BATCH_SIZE"] = "100"

    def tearDown(self) -> None:
        for key, value in {
            "END_USER_MESSAGING_APPLICATION_ID": self.previous_application_id,
            "NOTIFICATION_MAX_DEVICES_PER_EVENT": self.previous_max,
            "NOTIFICATION_SEND_BATCH_SIZE": self.previous_batch,
        }.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_invalid_event_is_permanent_validation_error(self) -> None:
        event = _report_event()
        event.pop("tenantId")
        with self.assertRaises(events.NotificationEventValidationError):
            events.normalize_notification_event(event)

    def test_report_generated_builder_uses_self_when_recipient_exists(self) -> None:
        event = events.build_notification_event_for_report_generated(
            tenant_id="8",
            report_id="report-001",
            recipient_user_id="18",
            report_type="minority_report",
            report_title="Reporte semanal",
        )
        self.assertEqual("SELF", event["audienceType"])
        self.assertEqual("18", event["recipientUserId"])
        self.assertEqual("report.generated:8:report-001", event["dedupeKey"])
        self.assertEqual(
            "xoc://sophia-docs?documentId=report-001&action=download-docx",
            event["deepLink"],
        )
        self.assertEqual("minority_report", event["metadata"]["reportType"])
        self.assertEqual("Reporte semanal", event["metadata"]["reportTitle"])
        self.assertTrue(event["metadata"]["downloadReady"])

    def test_critical_vulnerability_builder_uses_tenant_all(self) -> None:
        event = events.build_notification_event_for_critical_vulnerability(tenant_id="8", finding_id="finding-001")
        self.assertEqual("TENANT_ALL", event["audienceType"])
        self.assertEqual("critical", event["priority"])

    def test_ticket_approval_builder_targets_only_the_creator(self) -> None:
        event = events.build_notification_event_for_ticket_status(
            tenant_id="8",
            ticket_id="123e4567-e89b-42d3-a456-426614174000",
            recipient_user_id="18",
            status="PREAPROBADO",
            attempt_count=2,
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual("SELF", event["audienceType"])
        self.assertEqual("18", event["recipientUserId"])
        self.assertEqual("ticket.approval_required", event["eventType"])
        self.assertEqual(
            "xoc://ticket/123e4567-e89b-42d3-a456-426614174000",
            event["deepLink"],
        )
        self.assertEqual(
            "ticket.approval_required:8:123e4567-e89b-42d3-a456-426614174000:attempt:2",
            event["dedupeKey"],
        )
        self.assertEqual(2, event["metadata"]["attemptCount"])

    def test_duplicate_event_is_ignored_without_sending(self) -> None:
        with patch.object(notification_events, "claim_notification_event", return_value=False), patch.object(
            notification_events, "_resolve_notification_audience"
        ) as resolve_audience, patch.object(notification_events, "_send_push_to_registered_device") as send_push:
            result = notification_events.process_notification_event(_report_event(), queue_message_id="message-1")

        self.assertEqual("duplicate_ignored", result["status"])
        resolve_audience.assert_not_called()
        send_push.assert_not_called()

    def test_worker_sends_self_and_updates_campaign_summary(self) -> None:
        device = {
            "deviceId": "ios-1",
            "userId": "18",
            "platform": "ios",
            "pushProvider": "apns",
            "apnsEnvironment": "production",
            "status": "ACTIVE",
            "notificationsEnabled": True,
        }
        with patch.object(notification_events, "claim_notification_event", return_value=True), patch.object(
            notification_events, "_resolve_notification_audience", return_value=[device]
        ) as resolve_audience, patch.object(notification_events, "create_notification_campaign") as create_campaign, patch.object(
            notification_events, "update_notification_campaign_result"
        ) as update_campaign, patch.object(
            notification_events, "complete_notification_event"
        ) as complete_event, patch.object(
            notification_events, "_send_push_to_registered_device",
            return_value={"deliveryStatus": "SUCCESSFUL", "invalidToken": False},
        ) as send_push:
            result = notification_events.process_notification_event(_report_event(), queue_message_id="message-1")

        self.assertEqual("completed", result["status"])
        resolve_audience.assert_called_once_with("8", "18", "SELF", max_devices=501)
        send_push.assert_called_once()
        campaign = create_campaign.call_args.args[0]
        self.assertEqual("eventbridge", campaign["triggerSource"])
        self.assertEqual("evt-report-001", campaign["sourceEventId"])
        self.assertEqual("message-1", campaign["queueMessageId"])
        self.assertEqual("COMPLETED", update_campaign.call_args.args[2]["status"])
        complete_event.assert_called_once()

    def test_worker_does_not_send_when_audience_has_no_active_devices(self) -> None:
        with patch.object(notification_events, "claim_notification_event", return_value=True), patch.object(
            notification_events, "_resolve_notification_audience", return_value=[]
        ), patch.object(notification_events, "create_notification_campaign"), patch.object(
            notification_events, "update_notification_campaign_result"
        ) as update_campaign, patch.object(notification_events, "complete_notification_event"), patch.object(
            notification_events, "_send_push_to_registered_device"
        ) as send_push:
            result = notification_events.process_notification_event(_report_event())

        self.assertEqual("no_devices", result["status"])
        send_push.assert_not_called()
        self.assertEqual("NO_DEVICES", update_campaign.call_args.args[2]["status"])

    def test_permanent_schema_error_is_acknowledged_by_sqs_worker(self) -> None:
        result = notification_events.handler(
            {"Records": [{"messageId": "m-1", "body": "{not-json"}]}, MagicMock()
        )
        self.assertEqual([], result["batchItemFailures"])

    def test_retryable_worker_error_returns_partial_batch_failure(self) -> None:
        with patch.object(notification_events, "process_notification_event", side_effect=RuntimeError("dynamodb unavailable")):
            result = notification_events.handler(
                {"Records": [{"messageId": "m-2", "body": '{"detail": {}}'}]}, MagicMock()
            )
        self.assertEqual([{"itemIdentifier": "m-2"}], result["batchItemFailures"])


if __name__ == "__main__":
    unittest.main()
