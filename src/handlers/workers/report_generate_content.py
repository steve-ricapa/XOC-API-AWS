from __future__ import annotations

from src.reports.storage import download_artifact, upload_artifact
from src.shared.logging import logger


def handler(event: dict, context) -> dict:
    report_id = event.get("reportId")
    tenant_id = event.get("tenantId")
    collected_data_key = event.get("collectedDataKey")

    if not all([report_id, tenant_id, collected_data_key]):
        raise ValueError("reportId, tenantId, and collectedDataKey are required")

    tenant_id = int(tenant_id)

    collected_data = download_artifact(collected_data_key)

    generated_content = _generate_content(collected_data, event.get("reportType", ""))

    content_key = upload_artifact(tenant_id, report_id, "generated-content.json", generated_content)
    logger.info("Generated content uploaded to %s for report %s", content_key, report_id)

    return {
        **event,
        "generatedContentKey": content_key,
        "contentSummary": {
            "sections": len(generated_content.get("sections", [])),
            "totalFindings": len(generated_content.get("findings", [])),
        },
    }


def _generate_content(collected_data: dict, report_type: str) -> dict:
    sections = [
        {
            "id": "executive_summary",
            "title": "Resumen Ejecutivo",
            "content": collected_data.get("report", {}).get("executive_summary", ""),
        },
        {
            "id": "severity_analysis",
            "title": "Analisis de Severidades",
            "content": _build_severity_text(collected_data.get("severity_summary", {})),
        },
        {
            "id": "domain_analysis",
            "title": "Analisis por Dominio",
            "content": _build_domain_text(collected_data.get("domains", [])),
        },
        {
            "id": "findings_detail",
            "title": "Detalle de Hallazgos",
            "findings": collected_data.get("findings", []),
        },
        {
            "id": "actions_worked",
            "title": "Acciones Trabajadas",
            "actions": collected_data.get("actions_worked", []),
        },
        {
            "id": "security_news",
            "title": "Noticias de Seguridad",
            "news": collected_data.get("security_news", []),
        },
    ]

    return {
        "report_type": report_type,
        "sections": sections,
        "findings": collected_data.get("findings", []),
        "domains": collected_data.get("domains", []),
        "severity_summary": collected_data.get("severity_summary", {}),
        "actions_worked": collected_data.get("actions_worked", []),
    }


def _build_severity_text(severity_summary: dict) -> str:
    return (
        f"Critico: {severity_summary.get('critical', 0)} | "
        f"Alto: {severity_summary.get('high', 0)} | "
        f"Medio: {severity_summary.get('medium', 0)} | "
        f"Bajo: {severity_summary.get('low', 0)} | "
        f"Informativo: {severity_summary.get('informational', 0)}"
    )


def _build_domain_text(domains: list[dict]) -> str:
    parts = []
    for domain in domains:
        parts.append(f"{domain.get('name', '')}: {domain.get('summary', '')}")
    return "\n".join(parts)
