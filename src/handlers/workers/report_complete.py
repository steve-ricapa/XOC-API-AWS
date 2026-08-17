from __future__ import annotations

from src.notifications.events import (
    build_notification_event_for_report_generated,
    publish_notification_requested,
)
from src.reports.store import update_document_status
from src.reports.storage import generate_download_url
from src.shared.logging import logger


def _publish_report_generated_notification(
    *, tenant_id: int, document_id: str, document_type: str | None
) -> None:
    """Publish a ready-document notification without affecting report completion."""
    try:
        notification_event = build_notification_event_for_report_generated(
            tenant_id=tenant_id,
            report_id=document_id,
            report_type=document_type,
        )
        publish_notification_requested(notification_event)
        logger.info(
            "report_generated_notification_published",
            extra={
                "event": "report_generated_notification_published",
                "tenantId": tenant_id,
                "reportId": document_id,
                "eventId": notification_event["eventId"],
            },
        )
    except Exception as exc:
        # Notification delivery is asynchronous and must not undo a generated
        # report or prevent the authenticated download path from working.
        logger.warning(
            "report_generated_notification_publish_failed",
            extra={
                "event": "report_generated_notification_publish_failed",
                "tenantId": tenant_id,
                "reportId": document_id,
                "errorType": type(exc).__name__,
                "safeMessage": "Notification event publication failed",
            },
        )


def handler(event: dict, context) -> dict:
    document_id = event.get("documentId")
    tenant_id = event.get("tenantId")

    if not document_id or not tenant_id:
        raise ValueError("documentId and tenantId are required")

    tenant_id = int(tenant_id)
    status = event.get("status", "COMPLETED")
    error_info = event.get("error", {})

    if status == "FAILED":
        error_code = error_info.get("Error", "unknown_error")
        error_message = error_info.get("Cause", "Unknown error during report generation")
        update_document_status(
            tenant_id,
            document_id,
            "FAILED",
            error_code=error_code,
            error_message=str(error_message)[:2000],
        )
        logger.warning("Document %s failed: %s", document_id, error_message)
        return {
            "documentId": document_id,
            "tenantId": tenant_id,
            "status": "FAILED",
        }

    s3_key = event.get("s3Key")
    s3_bucket = event.get("s3Bucket")
    s3_version_id = event.get("s3VersionId", "")
    size_bytes = event.get("sizeBytes")
    preview_s3_key = event.get("previewS3Key")
    preview_s3_bucket = event.get("previewS3Bucket")
    preview_s3_version_id = event.get("previewS3VersionId", "")
    preview_size_bytes = event.get("previewSizeBytes")
    preview_format = event.get("previewFormat")

    extra = {
        "s3_bucket": s3_bucket,
        "s3_key": s3_key,
    }
    if event.get("localPath"):
        extra["local_path"] = event.get("localPath")
    if s3_version_id:
        extra["s3_version_id"] = s3_version_id
    if size_bytes is not None:
        extra["size_bytes"] = int(size_bytes)
    if preview_s3_bucket:
        extra["preview_s3_bucket"] = preview_s3_bucket
    if preview_s3_key:
        extra["preview_s3_key"] = preview_s3_key
    if preview_s3_version_id:
        extra["preview_s3_version_id"] = preview_s3_version_id
    if preview_size_bytes is not None:
        extra["preview_size_bytes"] = int(preview_size_bytes)
    if preview_format:
        extra["preview_format"] = preview_format

    completed_document = update_document_status(tenant_id, document_id, "COMPLETED", **extra)

    # This worker receives s3Key/s3Bucket only after report_generate_docx has
    # validated and uploaded the DOCX. Failed or intermediate reports never
    # reach this publication point.
    if s3_key and s3_bucket:
        _publish_report_generated_notification(
            tenant_id=tenant_id,
            document_id=str(document_id),
            document_type=str(
                completed_document.get("document_type") or event.get("documentType") or ""
            ) or None,
        )
    else:
        logger.warning(
            "report_generated_notification_skipped",
            extra={
                "event": "report_generated_notification_skipped",
                "tenantId": tenant_id,
                "reportId": document_id,
                "reason": "docx_storage_not_ready",
            },
        )

    download_url = generate_download_url(
        s3_key,
        bucket_name=s3_bucket,
        document_type=event.get("documentType"),
    ) if s3_key else None
    logger.info("Document %s completed. Download URL generated.", document_id)

    return {
        "documentId": document_id,
        "tenantId": tenant_id,
        "status": "COMPLETED",
        "downloadUrl": download_url,
    }
