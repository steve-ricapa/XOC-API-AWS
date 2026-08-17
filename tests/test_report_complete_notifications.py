"""Tests for publishing report.generated after a DOCX is ready."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from src.handlers.workers import report_complete


def _ready_event() -> dict:
    return {
        "documentId": "report-001",
        "tenantId": 8,
        "documentType": "minority_report",
        "s3Bucket": "reports-bucket",
        "s3Key": "prod/reports/8/report-001/generated.docx",
        "s3VersionId": "version-1",
        "sizeBytes": 1024,
    }


class ReportCompleteNotificationTests(unittest.TestCase):
    @patch.object(report_complete, "generate_download_url", return_value="https://download.example")
    @patch.object(report_complete, "publish_notification_requested")
    @patch.object(
        report_complete,
        "update_document_status",
        return_value={"document_type": "minority_report", "status": "COMPLETED"},
    )
    def test_ready_docx_publishes_tenant_notification(self, update_status, publish_event, _download_url) -> None:
        result = report_complete.handler(_ready_event(), None)

        self.assertEqual("COMPLETED", result["status"])
        update_status.assert_called_once()
        event = publish_event.call_args.args[0]
        self.assertEqual("report.generated", event["eventType"])
        self.assertEqual("8", event["tenantId"])
        self.assertEqual("TENANT_ALL", event["audienceType"])
        self.assertEqual("report-001", event["resourceId"])
        self.assertEqual("report.generated:8:report-001", event["dedupeKey"])
        self.assertEqual(
            "xoc://sophia-docs?documentId=report-001&action=download-docx",
            event["deepLink"],
        )
        self.assertEqual("minority_report", event["metadata"]["reportType"])
        self.assertTrue(event["metadata"]["downloadReady"])

    @patch.object(report_complete, "generate_download_url", return_value="https://download.example")
    @patch.object(report_complete, "publish_notification_requested", side_effect=RuntimeError("event bridge unavailable"))
    @patch.object(
        report_complete,
        "update_document_status",
        return_value={"document_type": "minority_report", "status": "COMPLETED"},
    )
    def test_publish_failure_does_not_fail_completed_report(self, update_status, publish_event, _download_url) -> None:
        result = report_complete.handler(_ready_event(), None)

        self.assertEqual("COMPLETED", result["status"])
        update_status.assert_called_once()
        publish_event.assert_called_once()

    @patch.object(report_complete, "publish_notification_requested")
    @patch.object(report_complete, "update_document_status")
    def test_failed_report_never_publishes_notification(self, update_status, publish_event) -> None:
        result = report_complete.handler(
            {"documentId": "report-001", "tenantId": 8, "status": "FAILED", "error": {}},
            None,
        )

        self.assertEqual("FAILED", result["status"])
        publish_event.assert_not_called()
        self.assertEqual("FAILED", update_status.call_args.args[2])

    @patch.object(report_complete, "generate_download_url", return_value=None)
    @patch.object(report_complete, "publish_notification_requested")
    @patch.object(
        report_complete,
        "update_document_status",
        return_value={"document_type": "minority_report", "status": "COMPLETED"},
    )
    def test_completed_report_without_docx_location_does_not_publish(
        self, update_status, publish_event, _download_url
    ) -> None:
        event = _ready_event()
        event.pop("s3Bucket")
        event.pop("s3Key")

        result = report_complete.handler(event, None)

        self.assertEqual("COMPLETED", result["status"])
        update_status.assert_called_once()
        publish_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
