from __future__ import annotations

from pathlib import Path

from src.reports.minority_foundry import generate_minority_payload
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
    if document_type == "minority_report":
        tenant = collected_data.get("tenant", {})
        document = collected_data.get("document", {})
        payload = generate_minority_payload(
            client_name=str(tenant.get("name") or "Cliente"),
            period=str(document.get("period") or "Periodo no especificado"),
            analyst_text=str(collected_data.get("analyst_text") or ""),
            structured_data=collected_data.get("structured_data") or {},
            reference_markdown=_load_minority_reference(),
        )
        payload.setdefault("document_code", document.get("id"))
        normalized_findings = _build_minority_findings(payload)
        return {
            "document_type": document_type,
            "document": document,
            "minority_payload": payload,
            "sections": _build_minority_sections(payload),
            "findings": normalized_findings,
            "domains": payload.get("security_domains", []),
            "severity_summary": collected_data.get("severity_summary", {}),
            "actions_worked": payload.get("weekly_actions", []),
            "security_news": payload.get("security_news", []),
        }

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


def _load_minority_reference() -> str:
    reference_path = Path(__file__).resolve().parents[2] / "reports" / "minority_reference.md"
    if not reference_path.exists():
        return ""
    return reference_path.read_text(encoding="utf-8")


def _build_minority_sections(payload: dict) -> list[dict]:
    return [
        {
            "id": "executive_summary",
            "title": "Resumen Ejecutivo",
            "content": payload.get("executive_summary", ""),
        },
        {
            "id": "severity_analysis",
            "title": "Analisis de Severidades",
            "content": (payload.get("vulnerability_comparison") or {}).get("summary", ""),
        },
        {
            "id": "findings_detail",
            "title": "Detalle de Hallazgos",
            "findings": _build_minority_findings(payload),
        },
    ]


def _build_minority_findings(payload: dict) -> list[dict]:
    findings = []
    for domain in payload.get("security_domains") or []:
        domain_name = domain.get("name", "")
        for finding in domain.get("findings") or []:
            findings.append(
                {
                    "id": finding.get("id"),
                    "title": finding.get("vulnerability"),
                    "affected_hosts": finding.get("affected_hosts"),
                    "severity": finding.get("severity"),
                    "domain": domain_name,
                }
            )
    return findings
