from __future__ import annotations

from typing import Any


DOCUMENT_TYPES = frozenset({
    "minority_report",
    "small_report",
    "informe_soporte",
})

DOCUMENT_VARIANTS = frozenset({
    "client",
    "admin_xoc",
})


REQUIRED_DOCUMENT_FIELDS = frozenset({"document_type"})

OPTIONAL_DOCUMENT_FIELDS = frozenset({
    "filters",
    "idempotency_key",
    "parameters",
    "variant",
})


VALID_FILTER_KEYS = frozenset({
    "date_from",
    "date_to",
    "severity",
    "domain",
    "status",
})


def validate_document_request(payload: dict) -> dict:
    errors: list[str] = []

    if not isinstance(payload, dict):
        return {"valid": False, "errors": ["Request body must be a JSON object"]}

    for field in REQUIRED_DOCUMENT_FIELDS:
        if field not in payload or not payload[field]:
            errors.append(f"{field} is required")

    document_type = payload.get("document_type", "")
    if document_type and document_type not in DOCUMENT_TYPES:
        errors.append(f"document_type must be one of: {', '.join(sorted(DOCUMENT_TYPES))}")

    variant = str(payload.get("variant") or "client").strip().lower()
    if variant and variant not in DOCUMENT_VARIANTS:
        errors.append(f"variant must be one of: {', '.join(sorted(DOCUMENT_VARIANTS))}")

    filters = payload.get("filters")
    if filters is not None:
        if not isinstance(filters, dict):
            errors.append("filters must be a JSON object")
        else:
            for key in filters:
                if key not in VALID_FILTER_KEYS:
                    errors.append(f"Unknown filter key: {key}")

    unknown_keys = set(payload.keys()) - REQUIRED_DOCUMENT_FIELDS - OPTIONAL_DOCUMENT_FIELDS
    if unknown_keys:
        for key in sorted(unknown_keys):
            errors.append(f"Unknown field: {key}")

    return {"valid": len(errors) == 0, "errors": errors}


def build_document_response(item: dict) -> dict:
    response = {
        "documentId": item.get("document_id") or item.get("report_id"),
        "status": item.get("status"),
        "documentType": item.get("document_type") or item.get("report_type"),
        "variant": item.get("variant") or "client",
        "createdAt": item.get("created_at"),
        "updatedAt": item.get("updated_at"),
    }

    if item.get("size_bytes") is not None:
        response["sizeBytes"] = item.get("size_bytes")

    if item.get("created_by_user_id") is not None:
        response["createdByUserId"] = item.get("created_by_user_id")

    if item.get("completed_at"):
        response["completedAt"] = item["completed_at"]

    if item.get("started_at"):
        response["startedAt"] = item["started_at"]

    if item.get("status") == "FAILED":
        response["error"] = {
            "code": item.get("error_code") or "unknown_error",
            "message": item.get("error_message") or "An unknown error occurred",
        }

    if item.get("download_url"):
        response["downloadUrl"] = item["download_url"]

    return response


def build_document_preview_response(item: dict, generated_content: dict | None = None) -> dict:
    response = build_document_response(item)

    if response.get("status") == "FAILED":
        response["previewStatus"] = "failed"
        return response

    if response.get("status") != "COMPLETED":
        response["previewStatus"] = "not_ready"
        return response

    if not generated_content:
        response["previewStatus"] = "unavailable"
        return response

    document = generated_content.get("document") or {}
    response["previewStatus"] = "ready"
    response["preview"] = {
        "title": document.get("title"),
        "service": document.get("service"),
        "period": document.get("period"),
        "preparedBy": document.get("prepared_by"),
        "sections": generated_content.get("sections") or [],
        "findings": generated_content.get("findings") or [],
        "domains": generated_content.get("domains") or [],
        "severitySummary": generated_content.get("severity_summary") or {},
        "actionsWorked": generated_content.get("actions_worked") or generated_content.get("support_entries") or [],
        "securityNews": generated_content.get("security_news") or [],
    }
    return response


validate_report_request = validate_document_request
build_report_response = build_document_response
