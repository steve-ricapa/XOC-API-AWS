from __future__ import annotations

from src.reports.storage import download_artifact, upload_artifact
from src.shared.logging import logger


def handler(event: dict, context) -> dict:
    document_id = event.get("documentId")
    tenant_id = event.get("tenantId")
    collected_data_key = event.get("collectedDataKey")
    document_type = event.get("documentType", "")

    if not all([document_id, tenant_id, collected_data_key]):
        raise ValueError("documentId, tenantId, and collectedDataKey are required")

    tenant_id = int(tenant_id)

    collected_data = download_artifact(collected_data_key)

    generated_content = _generate_content(collected_data, document_type)

    content_key = upload_artifact(tenant_id, document_id, document_type, "generated-content.json", generated_content)
    logger.info("Generated content uploaded to %s for document %s", content_key, document_id)

    return {
        **event,
        "generatedContentKey": content_key,
        "contentSummary": {
            "sections": len(generated_content.get("sections", [])),
            "totalFindings": len(generated_content.get("findings", [])),
        },
    }


def _generate_content(collected_data: dict, document_type: str) -> dict:
    document = collected_data.get("document", {})
    sections = _build_sections(document_type, collected_data, document)

    return {
        "document_type": document_type,
        "document": document,
        "sections": sections,
        "findings": collected_data.get("findings", []),
        "domains": collected_data.get("domains", []),
        "severity_summary": collected_data.get("severity_summary", {}),
        "actions_worked": collected_data.get("actions_worked", []),
        "support_entries": collected_data.get("actions_worked", []),
        "security_news": collected_data.get("security_news", []),
    }


def _build_sections(document_type: str, collected_data: dict, document: dict) -> list[dict]:
    shared = [
        {
            "id": "executive_summary",
            "title": "Resumen Ejecutivo",
            "content": document.get("executive_summary", ""),
        },
        {
            "id": "findings_detail",
            "title": "Detalle de Hallazgos",
            "findings": collected_data.get("findings", []),
        },
    ]
    if document_type == "small_report":
        return [
            shared[0],
            {
                "id": "results",
                "title": "Resultados",
                "content": document.get("results", ""),
            },
            shared[1],
        ]
    if document_type == "informe_soporte":
        return [
            shared[0],
            {
                "id": "support_actions",
                "title": "Acciones de Soporte",
                "actions": collected_data.get("actions_worked", []),
            },
            {
                "id": "status_overview",
                "title": "Estado General",
                "content": document.get("results", ""),
            },
        ]
    return [
        shared[0],
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
        shared[1],
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
