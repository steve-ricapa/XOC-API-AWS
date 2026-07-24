from __future__ import annotations

from src.reports.store import update_document_status
from src.reports.storage import generate_download_url
from src.shared.logging import logger


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

    extra = {
        "s3_bucket": s3_bucket,
        "s3_key": s3_key,
    }
    if s3_version_id:
        extra["s3_version_id"] = s3_version_id
    if size_bytes is not None:
        extra["size_bytes"] = int(size_bytes)

    update_document_status(tenant_id, document_id, "COMPLETED", **extra)

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
