from __future__ import annotations

import json
import os

import boto3

from src.reports.store import get_report, update_report_status, table
from src.shared.logging import logger

stepfunctions = boto3.client("stepfunctions")


def handler(event: dict, context) -> dict:
    for record in event.get("Records", []):
        try:
            body = json.loads(record.get("body", "{}"))
            detail = body.get("detail", body)
        except (json.JSONDecodeError, TypeError):
            logger.error("Invalid SQS message body")
            continue

        tenant_id = detail.get("tenant_id")
        report_id = detail.get("report_id")

        if not tenant_id or not report_id:
            logger.warning("Missing tenant_id or report_id in event detail")
            continue

        tenant_id = int(tenant_id)

        item = get_report(tenant_id, report_id)
        if not item:
            logger.warning("Report %s not found for tenant %s", report_id, tenant_id)
            continue

        if item.get("status") in ("PROCESSING", "COMPLETED"):
            logger.info("Report %s already %s, skipping", report_id, item["status"])
            continue

        update_report_status(tenant_id, report_id, "PROCESSING")

        state_machine_arn = os.environ.get("REPORT_WORKFLOW_STATE_MACHINE_ARN", "")
        if not state_machine_arn:
            logger.error("REPORT_WORKFLOW_STATE_MACHINE_ARN not configured")
            update_report_status(tenant_id, report_id, "FAILED", error_code="configuration_error", error_message="State machine ARN not configured")
            continue

        try:
            response = stepfunctions.start_execution(
                stateMachineArn=state_machine_arn,
                name=f"report-{report_id}",
                input=json.dumps({
                    "reportId": report_id,
                    "tenantId": tenant_id,
                    "reportType": detail.get("report_type", ""),
                    "request_hash": detail.get("request_hash", ""),
                }),
            )
            execution_arn = response.get("executionArn", "")
            if execution_arn:
                update_report_status(tenant_id, report_id, "PROCESSING", execution_arn=execution_arn)
            logger.info("Started Step Functions execution %s for report %s", execution_arn, report_id)
        except Exception as exc:
            logger.exception("Failed to start Step Functions for report %s", report_id)
            update_report_status(tenant_id, report_id, "FAILED", error_code="stepfunctions_error", error_message=str(exc))

    return {"statusCode": 200}
