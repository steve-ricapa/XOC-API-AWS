from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from src.persistence.db import session_scope
from src.persistence.models import AgentApiKey, FindingIndex, Integration, ScanSummary, ScanSummaryNoc, Tenant, Ticket
from src.reports.store import get_document_job_or_404
from src.reports.storage import upload_artifact
from src.shared.logging import logger


def handler(event: dict, context) -> dict:
    document_id = event.get("documentId")
    tenant_id = event.get("tenantId")
    document_type = event.get("documentType", "")

    if not document_id or not tenant_id:
        raise ValueError("documentId and tenantId are required")

    tenant_id = int(tenant_id)

    item = get_document_job_or_404(tenant_id, document_id)
    filters = item.get("filters") or {}
    parameters = item.get("parameters") or {}

    collected = _collect_from_sources(tenant_id, document_id, document_type, filters, parameters)

    artifact_key = upload_artifact(tenant_id, document_id, document_type, "collected-data.json", collected)
    logger.info("Collected data uploaded to %s for document %s", artifact_key, document_id)

    return {
        "documentId": document_id,
        "tenantId": tenant_id,
        "documentType": document_type,
        "collectedDataKey": artifact_key,
        "tenantName": collected.get("tenant", {}).get("name", f"Tenant-{tenant_id}"),
        "severitySummary": collected.get("severity_summary", {}),
        "findings": collected.get("findings", []),
        "domains": collected.get("domains", []),
    }


def _collect_from_sources(tenant_id: int, document_id: str, document_type: str, filters: dict, parameters: dict) -> dict:
    if document_type == "minority_report":
        return _build_real_minority_context(tenant_id, document_id, filters, parameters)
    return _build_minimal_document_context(tenant_id, document_id, document_type, filters, parameters)


PROVIDER_LABELS = {
    "wazuh": "Wazuh SIEM",
    "nessus": "Nessus",
    "openvas": "OpenVAS",
    "insightvm": "InsightVM / Rapid7",
    "zabbix": "Zabbix",
    "uptime_kuma": "Uptime Kuma",
}


def _build_real_minority_context(tenant_id: int, document_id: str, filters: dict, parameters: dict) -> dict:
    with session_scope() as session:
        tenant = session.get(Tenant, tenant_id)
        if not tenant:
            raise ValueError(f"Tenant {tenant_id} not found")

        date_from, date_to, period = _resolve_period(filters)
        previous_from = date_from - timedelta(days=7)
        previous_to = date_from - timedelta(microseconds=1)
        findings = session.scalars(
            select(FindingIndex).where(
                FindingIndex.tenant_id == tenant_id,
                FindingIndex.created_at >= date_from,
                FindingIndex.created_at <= date_to,
            ).order_by(FindingIndex.created_at.desc()).limit(150)
        ).all()
        previous_findings = session.scalars(
            select(FindingIndex).where(
                FindingIndex.tenant_id == tenant_id,
                FindingIndex.created_at >= previous_from,
                FindingIndex.created_at <= previous_to,
            ).order_by(FindingIndex.created_at.desc()).limit(150)
        ).all()
        integrations = session.scalars(select(Integration).where(Integration.tenant_id == tenant_id)).all()
        agent_keys = session.scalars(
            select(AgentApiKey).where(AgentApiKey.tenant_id == tenant_id, AgentApiKey.is_active == True)
        ).all()
        tickets = session.scalars(
            select(Ticket).where(
                Ticket.tenant_id == tenant_id,
                Ticket.created_at >= date_from,
                Ticket.created_at <= date_to,
            ).order_by(Ticket.created_at.desc()).limit(40)
        ).all()

        severity_summary = _build_real_severity_summary(findings)
        previous_summary = _build_real_severity_summary(previous_findings)
        scan_snapshot = _build_scan_snapshot(session, tenant_id, date_from, date_to, previous_from, previous_to)
        if not any(severity_summary.values()) and any((scan_snapshot.get("current_scan_severity_summary") or {}).values()):
            severity_summary = scan_snapshot["current_scan_severity_summary"]
        if not any(previous_summary.values()) and any((scan_snapshot.get("previous_scan_severity_summary") or {}).values()):
            previous_summary = scan_snapshot["previous_scan_severity_summary"]
        tools = _build_real_tools(integrations, agent_keys)
        security_domains = _build_security_domains(findings)
        weekly_actions = _build_weekly_actions(tickets)
        admin_module_actions = _build_admin_module_actions(parameters)
        if admin_module_actions:
            weekly_actions = admin_module_actions
        pending_findings = _build_pending_findings(findings)
        analyst_text = _build_analyst_text(parameters)
        manual_security_news = _build_manual_security_news(parameters)
        report_variant = _normalize_report_variant(parameters.get("report_variant"))
        document_code = _build_document_code(tenant_id, document_id, tenant.name, date_to, report_variant)
        template_rules = _template_rules(report_variant)
        chart_data = {
            "client_name": tenant.name,
            "period": period,
            "previous_severity_summary": previous_summary,
            "current_severity_summary": severity_summary,
        }

        return {
            "tenant": {
                "id": str(tenant_id),
                "name": tenant.name,
            },
            "document": {
                "id": document_id,
                "title": "Minority Report - XOC",
                "service": "Servicio de Monitoreo Proactivo XOC",
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "period": period,
                "prepared_by": "TXDXSECURE",
                "executive_summary": "",
                "results": "",
            },
            "parameters": parameters,
            "analyst_text": analyst_text,
            "structured_data": {
                "source": "xoc-api-aws-real-db",
                "tenant_id": tenant_id,
                "tenant_name": tenant.name,
                "client_name": tenant.name,
                "period": period,
                "document_code": document_code,
                "report_variant": report_variant,
                "template_variant": "report for admin client" if report_variant == "client_admin" else "report for client",
                "admin_reference": parameters.get("admin_reference") or "",
                "section_instructions": parameters.get("section_instructions") or {},
                "template_rules": template_rules,
                "severity_summary": severity_summary,
                "previous_severity_summary": previous_summary,
                "chart_evidence": chart_data,
                "tools": tools,
                "aggregated_metrics": {
                    "data_base": _build_data_base_text(findings, previous_findings, tickets, scan_snapshot),
                    "vulnerability_comparison": _build_vulnerability_comparison_rows(previous_summary, severity_summary),
                    "histogram_summary": _build_histogram_summary(previous_summary, severity_summary),
                    "results_obtained": _build_results_obtained(tickets, findings, scan_snapshot),
                    "pending_findings": pending_findings,
                    "security_domains": security_domains,
                    "weekly_actions": weekly_actions,
                },
                "security_domains": security_domains,
                "weekly_actions": weekly_actions,
                "manual_security_news": manual_security_news,
                "pending_findings": pending_findings,
                "top_findings": [_finding_to_minority_row(finding) for finding in findings[:20]],
                "previous_top_findings": [_finding_to_minority_row(finding) for finding in previous_findings[:20]],
                "scan_snapshot": scan_snapshot,
                "ticket_snapshot": [
                    {
                        "subject": ticket.subject,
                        "status": ticket.status,
                        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
                    }
                    for ticket in tickets
                ],
            },
            "tools": tools,
            "severity_summary": severity_summary,
            "previous_severity_summary": previous_summary,
            "findings": [_finding_to_legacy_row(finding) for finding in findings[:50]],
            "domains": security_domains,
            "actions_worked": weekly_actions,
            "security_news": manual_security_news,
            "chart_data": chart_data,
            "document_code": document_code,
            "report_variant": report_variant,
        }


def _resolve_period(filters: dict) -> tuple[datetime, datetime, str]:
    now = datetime.now(timezone.utc)
    date_from_raw = filters.get("date_from")
    date_to_raw = filters.get("date_to")
    if date_from_raw and date_to_raw:
        start = _parse_date_boundary(str(date_from_raw), end_of_day=False)
        end = _parse_date_boundary(str(date_to_raw), end_of_day=True)
        return start, end, f"Del {start.date().isoformat()} al {end.date().isoformat()}"
    end = now
    start_date = (now - timedelta(days=6)).date()
    start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
    return start, end, f"Del {start.date().isoformat()} al {end.date().isoformat()}"


def _parse_date_boundary(value: str, *, end_of_day: bool) -> datetime:
    parsed = datetime.fromisoformat(value[:10])
    if end_of_day:
        return datetime(parsed.year, parsed.month, parsed.day, 23, 59, 59, 999999, tzinfo=timezone.utc)
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)


def _build_real_severity_summary(findings: list[FindingIndex]) -> dict:
    summary = {"critical": 0, "high": 0, "medium": 0, "low": 0, "informational": 0}
    for finding in findings:
        severity = (finding.severity or "").strip().lower()
        if "critical" in severity or "crit" in severity:
            summary["critical"] += 1
        elif "high" in severity or "alto" in severity:
            summary["high"] += 1
        elif "medium" in severity or "medio" in severity:
            summary["medium"] += 1
        elif "low" in severity or "bajo" in severity:
            summary["low"] += 1
        else:
            summary["informational"] += 1
    return summary


def _build_previous_severity_summary(session, tenant_id: int, current_start: datetime) -> dict:
    previous_start = current_start - timedelta(days=30)
    previous_findings = session.scalars(
        select(FindingIndex).where(
            FindingIndex.tenant_id == tenant_id,
            FindingIndex.created_at >= previous_start,
            FindingIndex.created_at < current_start,
        )
    ).all()
    return _build_real_severity_summary(previous_findings)


def _normalize_report_variant(value: object) -> str:
    normalized = str(value or "client").strip().lower().replace("-", "_")
    if normalized in {"admin", "client_admin", "admin_client", "report_for_admin_client"}:
        return "client_admin"
    return "client"


def _template_rules(report_variant: str) -> dict:
    base = [
        "Datos generales",
        "Resumen ejecutivo del dominio",
        "Seguridad por Dominio",
    ]
    if report_variant == "client_admin":
        base.extend([
            "Reporte de acciones trabajadas durante la semana",
            "Resultados obtenidos",
            "Noticias de seguridad",
        ])
    return {
        "allowed_top_level_sections": base,
        "required_tables": {
            "security_domain_findings": ["ID", "Vulnerabilidades", "Host Afectados", "Severidad"],
            "severity_values": ["BAJO", "MEDIO", "ALTO"],
        },
        "required_figures": [
            {"label": "Figura 1", "section": "2.1 Análisis Comparativo de Vulnerabilidades Semanales"},
            {"label": "Figura 2", "section": "2.1 Análisis Comparativo de Vulnerabilidades Semanales"},
            {"label": "Figura 3", "section": "2.2 Histograma de la seguridad"},
        ],
    }


def _safe_code(value: str) -> str:
    import re

    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value.upper()).strip("_")
    return normalized[:60] or "CLIENTE"


def _build_document_code(tenant_id: int, document_id: str, tenant_name: str, end: datetime, report_variant: str) -> str:
    suffix = "ADMIN" if report_variant == "client_admin" else "CLIENT"
    client = _safe_code(tenant_name)
    short_id = str(document_id).split("-", 1)[0].upper()
    return f"TXDX-WR-{end:%y%m}-{tenant_id:05d}-{client}_MINORITY_REPORT_{suffix}_{end:%d_%B}_{short_id}"


def _severity_rows(summary: dict) -> list[dict]:
    labels = [
        ("Crítica", "critical"),
        ("Alta", "high"),
        ("Media", "medium"),
        ("Baja", "low"),
        ("Informativa", "informational"),
    ]
    return [{"severity": label, "value": int(summary.get(key, 0) or 0)} for label, key in labels]


def _build_vulnerability_comparison_rows(previous_summary: dict, current_summary: dict) -> dict:
    rows = []
    for label, key in [
        ("Crítica", "critical"),
        ("Alta", "high"),
        ("Media", "medium"),
        ("Baja", "low"),
        ("Informativa", "informational"),
    ]:
        rows.append({
            "severity": label,
            "previous": str(int(previous_summary.get(key, 0) or 0)),
            "current": str(int(current_summary.get(key, 0) or 0)),
        })
    return {
        "summary": "Comparativo construido con la semana anterior y la semana actual usando hallazgos reales registrados en la plataforma.",
        "severity_rows": rows,
    }


def _build_histogram_summary(previous_summary: dict, current_summary: dict) -> str:
    previous_total = sum(int(value or 0) for value in previous_summary.values())
    current_total = sum(int(value or 0) for value in current_summary.values())
    delta = current_total - previous_total
    return (
        "El histograma de seguridad compara el volumen de hallazgos por severidad entre la semana anterior "
        f"({previous_total}) y la semana actual ({current_total}), con una variación neta de {delta:+d}."
    )


def _build_data_base_text(findings: list[FindingIndex], previous_findings: list[FindingIndex], tickets: list[Ticket], scan_snapshot: dict) -> str:
    return (
        f"Se consolidaron {len(findings)} hallazgos del periodo actual, {len(previous_findings)} hallazgos de la semana anterior "
        f"y {len(tickets)} tickets operativos asociados al tenant. "
        f"Snapshots SOC evaluados: {scan_snapshot.get('current_soc_scans', 0)}; snapshots NOC evaluados: {scan_snapshot.get('current_noc_scans', 0)}."
    )


def _build_results_obtained(tickets: list[Ticket], findings: list[FindingIndex], scan_snapshot: dict) -> list[str]:
    closed_statuses = {"RESUELTO", "RESOLVED", "COMPLETED", "EXECUTED", "APROBADO"}
    closed = [ticket for ticket in tickets if (ticket.status or "").upper() in closed_statuses]
    return [
        f"Tickets cerrados o ejecutados durante el periodo: {len(closed)}.",
        f"Hallazgos técnicos identificados en el periodo: {len(findings)}.",
        f"Escaneos/snapshots considerados en el periodo: {scan_snapshot.get('current_total_scans', 0)}.",
    ]


def _build_scan_snapshot(session, tenant_id: int, start: datetime, end: datetime, previous_start: datetime, previous_end: datetime) -> dict:
    current_soc = session.scalars(
        select(ScanSummary).where(ScanSummary.tenant_id == tenant_id, ScanSummary.scanned_at >= start, ScanSummary.scanned_at <= end)
    ).all()
    current_noc = session.scalars(
        select(ScanSummaryNoc).where(ScanSummaryNoc.tenant_id == tenant_id, ScanSummaryNoc.scanned_at >= start, ScanSummaryNoc.scanned_at <= end)
    ).all()
    previous_soc = session.scalars(
        select(ScanSummary).where(ScanSummary.tenant_id == tenant_id, ScanSummary.scanned_at >= previous_start, ScanSummary.scanned_at <= previous_end)
    ).all()
    previous_noc = session.scalars(
        select(ScanSummaryNoc).where(ScanSummaryNoc.tenant_id == tenant_id, ScanSummaryNoc.scanned_at >= previous_start, ScanSummaryNoc.scanned_at <= previous_end)
    ).all()

    def totals(rows: list) -> dict:
        return {
            "critical": sum(int(getattr(row, "critical_count", 0) or 0) for row in rows),
            "high": sum(int(getattr(row, "high_count", 0) or 0) for row in rows),
            "medium": sum(int(getattr(row, "medium_count", 0) or 0) for row in rows),
            "low": sum(int(getattr(row, "low_count", 0) or 0) for row in rows),
            "informational": sum(int(getattr(row, "info_count", 0) or 0) for row in rows),
        }

    return {
        "current_soc_scans": len(current_soc),
        "current_noc_scans": len(current_noc),
        "previous_soc_scans": len(previous_soc),
        "previous_noc_scans": len(previous_noc),
        "current_total_scans": len(current_soc) + len(current_noc),
        "previous_total_scans": len(previous_soc) + len(previous_noc),
        "current_scan_severity_summary": totals(current_soc + current_noc),
        "previous_scan_severity_summary": totals(previous_soc + previous_noc),
    }


def _build_real_tools(integrations: list[Integration], agent_keys: list[AgentApiKey]) -> list[dict]:
    names = {integration.provider for integration in integrations}
    names.update(agent_key.integration_type for agent_key in agent_keys)
    tools = []
    for provider in sorted(names):
        tools.append({
            "name": PROVIDER_LABELS.get(provider, provider.upper()),
            "description": f"Integracion activa para {PROVIDER_LABELS.get(provider, provider)}.",
        })
    return tools


def _group_findings_by_provider(findings: list[FindingIndex]) -> dict[str, list[FindingIndex]]:
    grouped: dict[str, list[FindingIndex]] = {}
    for finding in findings:
        grouped.setdefault(finding.scanner_type or "other", []).append(finding)
    return grouped


def _build_security_domains(findings: list[FindingIndex]) -> list[dict]:
    grouped = _group_findings_by_provider(findings)
    domains = []
    for provider in sorted(grouped):
        items = grouped[provider]
        domains.append({
            "name": PROVIDER_LABELS.get(provider, provider.upper()),
            "summary": f"Se identificaron {len(items)} hallazgos asociados a {PROVIDER_LABELS.get(provider, provider)} en el periodo evaluado.",
            "findings": [_finding_to_minority_row(item) for item in items[:8]],
        })
    return domains


def _finding_to_minority_row(finding: FindingIndex) -> dict:
    return {
        "id": str(finding.id),
        "vulnerability": finding.name or finding.cve or finding.event_type or "Hallazgo sin titulo",
        "affected_hosts": finding.host or "N/D",
        "severity": finding.severity or "Informativa",
    }


def _finding_to_legacy_row(finding: FindingIndex) -> dict:
    return {
        "id": str(finding.id),
        "domain": PROVIDER_LABELS.get(finding.scanner_type or "", finding.domain or "Dominio"),
        "title": finding.name or finding.cve or finding.event_type or "Hallazgo",
        "affected_hosts": finding.host or "N/D",
        "severity": finding.severity or "Informativa",
        "description": finding.description or "",
        "recommendation": finding.solution or "",
    }


def _build_weekly_actions(tickets: list[Ticket]) -> list[str]:
    actions = []
    for ticket in tickets[:10]:
        actions.append(f"{ticket.subject} ({ticket.status})")
    return actions


def _build_pending_findings(findings: list[FindingIndex]) -> list[str]:
    pending = []
    for finding in findings[:12]:
        pending.append(f"{finding.scanner_type}: {(finding.name or finding.cve or finding.event_type or 'Hallazgo')} ({finding.severity})")
    return pending


def _build_analyst_text(parameters: dict) -> str:
    modules = parameters.get("modules") or {}
    parts = []
    if isinstance(modules, dict):
        for module in modules.values():
            if not isinstance(module, dict):
                continue
            content = str(module.get("content") or "").strip()
            software = module.get("software") or []
            if content:
                parts.append(content)
            if software:
                parts.append(f"Software relacionado: {', '.join(str(item) for item in software)}")
    return "\n\n".join(parts)


def _build_admin_module_actions(parameters: dict) -> list[str]:
    """Convierte los módulos escritos por el admin en acciones trazables.

    El resultado se usa como evidencia controlada por backend, no como una
    inferencia del modelo. Así la sección de acciones conserva módulo,
    actividad y software asociado.
    """
    modules = parameters.get("modules") or {}
    if not isinstance(modules, dict):
        return []
    actions: list[str] = []
    for module_id, module in modules.items():
        if not isinstance(module, dict) or not module.get("enabled", True):
            continue
        content = str(module.get("content") or "").strip()
        software = [str(item).strip() for item in (module.get("software") or []) if str(item).strip()]
        if not content:
            continue
        title = str(module.get("title") or module_id).strip()
        software_text = ", ".join(software) if software else "Sin software asociado"
        actions.append(f"Módulo: {title}. Actividad realizada: {content}. Software asociado: {software_text}.")
    return actions


def _build_manual_security_news(parameters: dict) -> list[dict]:
    raw = str(parameters.get("security_news") or "").strip()
    if not raw:
        return []
    return [{
        "title": "Noticias de seguridad proporcionadas por el administrador",
        "date": "",
        "source": "Administrador del tenant",
        "links": [],
        "summary": raw,
        "recommendation": "Revisar la información proporcionada y aplicar las acciones que correspondan al entorno del cliente.",
    }]


def _build_minimal_document_context(tenant_id: int, document_id: str, document_type: str, filters: dict, parameters: dict) -> dict:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    period = _build_period_string(filters)

    tenant_name = f"Tenant-{tenant_id}"
    title, service, executive_summary, results = _document_copy(document_type)

    return {
        "tenant": {
            "id": str(tenant_id),
            "name": tenant_name,
        },
        "document": {
            "id": document_id,
            "title": title,
            "service": service,
            "generated_at": generated_at,
            "period": period,
            "prepared_by": "TXDXSECURE",
            "executive_summary": executive_summary,
            "results": results,
        },
        "parameters": parameters,
        "tools": ["MonEvents", "MonVulE", "MonVulC", "MonApps", "MonNet", "MonInfra"],
        "severity_summary": {
            "critical": 2,
            "high": 7,
            "medium": 33,
            "low": 8,
            "informational": 147,
        },
        "findings": _build_demo_findings(),
        "domains": _build_demo_domains(),
        "actions_worked": [
            "Validacion de exposicion de servicios administrativos en activos publicos.",
            "Revision de vulnerabilidades altas asociadas a servidores criticos.",
            "Afinamiento de reglas y excepciones en controles perimetrales.",
            "Consolidacion de hallazgos recurrentes para priorizacion operativa.",
        ],
        "security_news": _build_demo_news(),
    }


def _document_copy(document_type: str) -> tuple[str, str, str, str]:
    if document_type == "small_report":
        return (
            "Small Report - XOC",
            "Servicio de Generacion Documental XOC",
            "Documento resumido con los hallazgos y acciones mas relevantes del periodo evaluado.",
            "Se consolidaron hallazgos prioritarios y un resumen operativo de seguimiento.",
        )
    if document_type == "informe_soporte":
        return (
            "Informe de Soporte - XOC",
            "Servicio de Soporte Operativo XOC",
            "Documento orientado a registrar actividades de soporte, atenciones ejecutadas y estado del servicio.",
            "Se documentaron acciones de soporte, estado de atenciones y puntos de seguimiento para continuidad operativa.",
        )
    return (
        "Minority Report - XOC",
        "Servicio de Monitoreo Proactivo XOC",
        "Durante la semana evaluada se mantuvo el monitoreo proactivo sobre superficies publicas, plataformas criticas e infraestructura priorizada.",
        "Se obtuvo visibilidad consolidada de exposiciones criticas y altas, se priorizaron actividades de mitigacion y se mantuvo evidencia estructurada para seguimiento semanal.",
    )


def _build_period_string(filters: dict) -> str:
    date_from = filters.get("date_from", "")
    date_to = filters.get("date_to", "")
    if date_from and date_to:
        return f"Del {date_from} al {date_to}"
    return "Ultima semana evaluada"


def _build_demo_findings() -> list[dict]:
    return [
        {"id": "WEB-001", "domain": "Dominio de Web Externo", "title": "Versiones desactualizadas en portal publico", "affected_hosts": "portal.jockeysalud.example", "severity": "Alto", "description": "Componentes web con versiones expuestas.", "recommendation": "Actualizar componentes."},
        {"id": "IP-014", "domain": "Dominio de IPs Públicas", "title": "Servicio administrativo expuesto a internet", "affected_hosts": "181.10.10.25", "severity": "Critico", "description": "Superficie administrativa accesible desde internet.", "recommendation": "Restringir acceso por VPN."},
        {"id": "FW-009", "domain": "Dominio de FW", "title": "Politicas con reglas amplias", "affected_hosts": "fw-core-01", "severity": "Medio", "description": "Reglas con origen/destino amplios.", "recommendation": "Aplicar minimo privilegio."},
        {"id": "SRV-021", "domain": "Dominio Infraestructura de Computo - Servers", "title": "Parches pendientes en servidores Windows", "affected_hosts": "srv-app-01, srv-db-02", "severity": "Alto", "description": "Parches de seguridad pendientes.", "recommendation": "Programar ventana de mantenimiento."},
        {"id": "SW-005", "domain": "Dominio Infraestructura de Red - Switches", "title": "SNMP con configuracion heredada", "affected_hosts": "sw-dist-03", "severity": "Bajo", "description": "Parametros heredados de monitoreo.", "recommendation": "Migrar a configuracion segura."},
    ]


def _build_demo_domains() -> list[dict]:
    return [
        {"name": "Dominio de Web Externo", "summary": "Hallazgos asociados a exposicion de versiones y cabeceras de seguridad.", "findings": ["WEB-001"]},
        {"name": "Dominio de IPs Públicas", "summary": "Servicios con exposicion publica que requieren restriccion.", "findings": ["IP-014"]},
        {"name": "Dominio de FW", "summary": "Afinamiento de reglas y validacion de reglas temporales.", "findings": ["FW-009"]},
        {"name": "Dominio Infraestructura de Computo - Servers", "summary": "Gestion de parches y controles de endurecimiento.", "findings": ["SRV-021"]},
        {"name": "Dominio Infraestructura de Red - Switches", "summary": "Configuraciones heredadas y estandarizacion pendiente.", "findings": ["SW-005"]},
        {"name": "Dominio Infraestructura de Red - WIFI", "summary": "No se observaron incidentes criticos.", "findings": []},
        {"name": "Dominio Infraestructura de Computo - Desktops", "summary": "Oportunidades de mejora en higiene de endpoints.", "findings": []},
        {"name": "Dominio Infraestructura OT/IoT", "summary": "Ampliar inventario y establecer linea base de monitoreo.", "findings": []},
    ]


def _build_demo_news() -> list[dict]:
    return [
        {"title": "Nueva campana de phishing dirigida a sector salud", "date": "2026-06-24", "source": "XOC Threat Intel", "summary": "Campanas con archivos adjuntos y robo de credenciales.", "links": ["https://example.com/news/phishing-health"]},
        {"title": "Actualizacion critica para plataforma perimetral", "date": "2026-06-22", "source": "Vendor Advisory", "summary": "Actualizacion para corregir vulnerabilidades explotables.", "links": ["https://example.com/news/perimeter-advisory"]},
    ]
