from __future__ import annotations

from typing import Any


REPORT_TYPES = frozenset({
    "minority_report",
    "vulnerability_report",
    "compliance_report",
    "executive_summary",
})


REQUIRED_REPORT_FIELDS = frozenset({"report_type"})

OPTIONAL_REPORT_FIELDS = frozenset({
    "filters",
    "idempotency_key",
})


VALID_FILTER_KEYS = frozenset({
    "date_from",
    "date_to",
    "severity",
    "domain",
    "status",
})


def validate_report_request(payload: dict) -> dict:
    errors: list[str] = []

    if not isinstance(payload, dict):
        return {"valid": False, "errors": ["Request body must be a JSON object"]}

    for field in REQUIRED_REPORT_FIELDS:
        if field not in payload or not payload[field]:
            errors.append(f"{field} is required")

    report_type = payload.get("report_type", "")
    if report_type and report_type not in REPORT_TYPES:
        errors.append(f"report_type must be one of: {', '.join(sorted(REPORT_TYPES))}")

    filters = payload.get("filters")
    if filters is not None:
        if not isinstance(filters, dict):
            errors.append("filters must be a JSON object")
        else:
            for key in filters:
                if key not in VALID_FILTER_KEYS:
                    errors.append(f"Unknown filter key: {key}")

    unknown_keys = set(payload.keys()) - REQUIRED_REPORT_FIELDS - OPTIONAL_REPORT_FIELDS
    if unknown_keys:
        for key in sorted(unknown_keys):
            errors.append(f"Unknown field: {key}")

    return {"valid": len(errors) == 0, "errors": errors}


def build_report_response(item: dict) -> dict:
    response = {
        "reportId": item.get("report_id"),
        "status": item.get("status"),
        "reportType": item.get("report_type"),
        "createdAt": item.get("created_at"),
        "updatedAt": item.get("updated_at"),
    }

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
