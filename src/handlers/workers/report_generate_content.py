from __future__ import annotations

import json
import os
from pathlib import Path

from src.reports.minority_foundry import _builder_structured_snapshot, generate_minority_payload
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
        client_name = str(tenant.get("name") or "Cliente")
        period = str(document.get("period") or "Periodo no especificado")
        analyst_text = str(collected_data.get("analyst_text") or "")
        structured_data = collected_data.get("structured_data") or {}
        reference_markdown = _load_minority_reference()
        plan = None
        use_foundry, budget = _should_use_foundry(structured_data)
        logger.info(
            "Minority content routing for document %s: use_foundry=%s compact_chars=%s domains=%s sample_findings=%s",
            document.get("id"),
            use_foundry,
            budget["compact_chars"],
            budget["domains"],
            budget["sample_findings"],
        )
        try:
            if not use_foundry:
                raise RuntimeError(
                    f"Minority payload exceeded budget (chars={budget['compact_chars']}, domains={budget['domains']}, sample_findings={budget['sample_findings']})"
                )
            payload = generate_minority_payload(
                client_name=client_name,
                period=period,
                analyst_text=analyst_text,
                structured_data=structured_data,
                reference_markdown=reference_markdown,
            )
        except Exception as exc:
            logger.warning("Minority Foundry generation skipped/failed; using deterministic backend fallback: %s", exc)
            payload = _build_backend_minority_payload(collected_data)
        structured = collected_data.get("structured_data") or {}
        metrics = structured.get("aggregated_metrics") or {}
        payload.setdefault("document_code", collected_data.get("document_code") or structured.get("document_code") or document.get("id"))
        payload["report_variant"] = collected_data.get("report_variant") or structured.get("report_variant") or "client"
        payload["template_variant"] = structured.get("template_variant") or (
            "report for admin client" if payload["report_variant"] == "client_admin" else "report for client"
        )
        # La IA redacta texto, pero los valores usados en tablas y figuras se
        # preservan exactamente como fueron recolectados de la BD del tenant.
        payload["client_name"] = str(tenant.get("name") or payload.get("client_name") or "Cliente")
        payload["period"] = str(document.get("period") or payload.get("period") or "Periodo no especificado")
        payload["prepared_by"] = "TXDXSECURE"
        payload["tools"] = structured.get("tools") or []
        payload["data_base"] = metrics.get("data_base") or payload.get("data_base") or ""
        payload["vulnerability_comparison"] = metrics.get("vulnerability_comparison") or {
            "summary": "No hay datos comparativos disponibles para el período evaluado.",
            "severity_rows": [],
        }
        payload["histogram_summary"] = metrics.get("histogram_summary") or payload.get("histogram_summary") or ""
        payload["security_domains"] = structured.get("security_domains") or []
        payload["pending_findings"] = metrics.get("pending_findings") or structured.get("pending_findings") or []
        payload["coverage_summary"] = metrics.get("coverage_summary") or structured.get("coverage_summary") or ""
        payload["coverage_rows"] = metrics.get("coverage_rows") or structured.get("coverage_rows") or []
        payload["priority_focuses"] = metrics.get("priority_focuses") or structured.get("priority_focuses") or []
        payload["operational_considerations"] = metrics.get("operational_considerations") or structured.get("operational_considerations") or []
        payload["chart_data"] = collected_data.get("chart_data") or structured.get("chart_evidence") or {}
        # Estas secciones son evidencia ingresada/controlada por backend. No se
        # dejan a interpretación del modelo para evitar duplicación de texto o
        # noticias inventadas.
        module_actions = structured.get("weekly_actions") or []
        if payload["report_variant"] == "client_admin" and module_actions:
            payload["weekly_actions"] = module_actions
            payload["results_obtained"] = (
                f"Se consolidó la información de {len(module_actions)} módulo(s) de reporte. "
                "Los resultados se presentan como conclusiones operativas derivadas de las actividades registradas, "
                "sin repetir el detalle de cada acción."
            )
        if payload["report_variant"] == "client_admin":
            results = metrics.get("results_obtained") or []
            payload["results_obtained"] = (
                f"Resultados del período: {' '.join(str(item) for item in results)} "
                "Estas conclusiones se derivan de hallazgos, tickets y escaneos reales, "
                "sin repetir el detalle de las acciones registradas en los módulos."
            )
        else:
            payload["weekly_actions"] = structured.get("weekly_actions") or []
        manual_security_news = structured.get("manual_security_news") or []
        if payload["report_variant"] == "client_admin" and manual_security_news:
            payload["security_news"] = manual_security_news
        payload["limitations"] = metrics.get("limitations") or payload.get("limitations") or []
        normalized_findings = _build_minority_findings(payload)
        return {
            "document_type": document_type,
            "document": document,
            "minority_payload": payload,
            "minority_plan": plan,
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


def _should_use_foundry(structured_data: dict) -> tuple[bool, dict[str, int]]:
    compact = _builder_structured_snapshot(structured_data or {})
    compact_chars = len(json.dumps(compact, ensure_ascii=False))
    domains = len(compact.get("security_domains") or [])
    sample_findings = sum(len(domain.get("findings") or []) for domain in (compact.get("security_domains") or []))
    max_chars = int(os.environ.get("MINORITY_MAX_COMPACT_CHARS", "14000"))
    max_domains = int(os.environ.get("MINORITY_MAX_DOMAINS_FOR_FOUNDRY", "6"))
    max_sample_findings = int(os.environ.get("MINORITY_MAX_SAMPLE_FINDINGS", "18"))
    decision = compact_chars <= max_chars and domains <= max_domains and sample_findings <= max_sample_findings
    return decision, {
        "compact_chars": compact_chars,
        "domains": domains,
        "sample_findings": sample_findings,
    }


def _build_backend_minority_payload(collected_data: dict) -> dict:
    structured = collected_data.get("structured_data") or {}
    metrics = structured.get("aggregated_metrics") or {}
    tenant = collected_data.get("tenant") or {}
    document = collected_data.get("document") or {}
    tools = structured.get("tools") or []
    security_domains = structured.get("security_domains") or []
    coverage_summary = metrics.get("coverage_summary") or structured.get("coverage_summary") or ""
    data_base = metrics.get("data_base") or ""
    period = str(document.get("period") or structured.get("period") or "Estado actual")
    client_name = str(tenant.get("name") or structured.get("client_name") or "Cliente")
    top_domain_names = ", ".join(str(domain.get("name") or "") for domain in security_domains[:3] if str(domain.get("name") or "").strip())
    executive_summary = (
        f"El presente Minority Report resume el estado actual del servicio para {client_name}. "
        f"La cobertura considera {len(tools)} integraciones activas y {len(security_domains)} dominios con evidencia utilizable. "
        f"La última evidencia disponible se consolidó bajo el contexto '{period}'. "
        f"Los dominios con mayor visibilidad en el estado actual son: {top_domain_names or 'sin dominios destacados en la evidencia actual'}."
    )
    results_obtained = metrics.get("results_obtained") or [
        f"Integraciones activas consideradas: {len(tools)}.",
        f"Dominios con evidencia disponible: {len(security_domains)}.",
    ]
    return {
        "title": f"Minority Report - {client_name}",
        "client_name": client_name,
        "prepared_by": "TXDXSECURE",
        "period": period,
        "service_name": str(document.get("service") or "Servicio de Monitoreo XOC"),
        "tools": tools,
        "data_base": data_base,
        "coverage_summary": coverage_summary,
        "coverage_rows": metrics.get("coverage_rows") or structured.get("coverage_rows") or [],
        "executive_summary": executive_summary,
        "vulnerability_comparison": metrics.get("vulnerability_comparison") or {"summary": "No se dispone de referencia comparativa adicional.", "severity_rows": []},
        "histogram_summary": metrics.get("histogram_summary") or "No se dispone de histograma adicional para la evidencia actual.",
        "priority_focuses": metrics.get("priority_focuses") or [],
        "operational_considerations": metrics.get("operational_considerations") or [],
        "results_and_next_actions": " ".join(str(item) for item in (results_obtained or [])),
        "results_obtained": " ".join(str(item) for item in (results_obtained or [])),
        "next_actions": metrics.get("pending_findings") or [],
        "requirements": [],
        "security_domains": security_domains,
        "weekly_actions": structured.get("weekly_actions") or [],
        "reinforced_security": "La continuidad del servicio y la cobertura por integración se mantienen según la última evidencia disponible.",
        "pending_findings": metrics.get("pending_findings") or structured.get("pending_findings") or [],
        "security_news": structured.get("manual_security_news") or [],
        "limitations": metrics.get("limitations") or [],
        "image_citations": [],
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
