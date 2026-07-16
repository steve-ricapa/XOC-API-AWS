from __future__ import annotations

from src.reports.storage import download_artifact
from src.shared.logging import logger


def handler(event: dict, context) -> dict:
    generated_content_key = event.get("generatedContentKey")

    if not generated_content_key:
        raise ValueError("generatedContentKey is required")

    generated_content = download_artifact(generated_content_key)

    validation = _validate_content(generated_content)
    if not validation["valid"]:
        logger.warning("Report content validation failed: %s", validation["errors"])
        raise ValueError(f"Content validation failed: {'; '.join(validation['errors'])}")

    logger.info("Report content validated successfully")
    return {**event, "validated": True}


def _validate_content(content: dict) -> dict:
    errors: list[str] = []

    sections = content.get("sections", [])
    if not sections:
        errors.append("No sections found in generated content")

    section_ids = {s.get("id") for s in sections if isinstance(s, dict)}
    required = {"executive_summary", "severity_analysis", "findings_detail"}
    missing = required - section_ids
    if missing:
        errors.append(f"Missing required sections: {', '.join(sorted(missing))}")

    severity = content.get("severity_summary", {})
    if not isinstance(severity, dict):
        errors.append("severity_summary must be a dict")
    elif not any(severity.values()):
        errors.append("severity_summary is empty")

    findings = content.get("findings", [])
    if not isinstance(findings, list):
        errors.append("findings must be a list")

    for finding in findings:
        if not finding.get("id") or not finding.get("title"):
            errors.append("Each finding must have id and title")
            break

    return {"valid": len(errors) == 0, "errors": errors}
