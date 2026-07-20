from __future__ import annotations

import json
import os

import boto3

from src.reports.store import get_document_job, update_document_status
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
        document_id = detail.get("document_id")

        if not tenant_id or not document_id:
            logger.warning("Missing tenant_id or document_id in event detail")
            continue

        tenant_id = int(tenant_id)

        item = get_document_job(tenant_id, document_id)
        if not item:
            logger.warning("Document %s not found for tenant %s", document_id, tenant_id)
            continue

        if item.get("status") in ("PROCESSING", "COMPLETED"):
            logger.info("Document %s already %s, skipping", document_id, item["status"])
            continue

        update_document_status(tenant_id, document_id, "PROCESSING")

        state_machine_arn = os.environ.get("REPORT_WORKFLOW_STATE_MACHINE_ARN", "")
        if not state_machine_arn:
            logger.error("REPORT_WORKFLOW_STATE_MACHINE_ARN not configured")
            update_document_status(tenant_id, document_id, "FAILED", error_code="configuration_error", error_message="State machine ARN not configured")
            continue

        try:
            response = stepfunctions.start_execution(
                stateMachineArn=state_machine_arn,
                name=f"document-{document_id}",
                input=json.dumps({
                    "documentId": document_id,
                    "tenantId": tenant_id,
                    "documentType": detail.get("document_type", ""),
                    "request_hash": detail.get("request_hash", ""),
                }),
            )
            execution_arn = response.get("executionArn", "")
            if execution_arn:
                update_document_status(tenant_id, document_id, "PROCESSING", execution_arn=execution_arn)
            logger.info("Started Step Functions execution %s for document %s", execution_arn, document_id)
        except Exception as exc:
            logger.exception("Failed to start Step Functions for document %s", document_id)
            update_document_status(tenant_id, document_id, "FAILED", error_code="stepfunctions_error", error_message=str(exc))

    return {"statusCode": 200}
