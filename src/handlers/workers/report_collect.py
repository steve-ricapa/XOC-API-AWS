from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from src.integrations.summary_store import _latest_noc_scan, _latest_soc_scan
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
    "tenable": "Nessus",
    "openvas": "OpenVAS",
    "insightvm": "InsightVM / Rapid7",
    "rapid7": "InsightVM / Rapid7",
    "zabbix": "Zabbix",
    "uptime_kuma": "Uptime Kuma",
}

PROVIDER_ORDER = [
    "wazuh",
    "openvas",
    "nessus",
    "insightvm",
    "zabbix",
    "uptime_kuma",
]

SOC_PROVIDERS = {"wazuh", "openvas", "nessus", "insightvm"}
NOC_PROVIDERS = {"zabbix", "uptime_kuma"}

SEVERITY_PRIORITY = {
    "critical": 0,
    "critica": 0,
    "crítica": 0,
    "high": 1,
    "alta": 1,
    "medium": 2,
    "media": 2,
    "low": 3,
    "baja": 3,
    "informational": 4,
    "informativa": 4,
    "info": 4,
}


def _build_real_minority_context(tenant_id: int, document_id: str, filters: dict, parameters: dict) -> dict:
    with session_scope() as session:
        tenant = session.get(Tenant, tenant_id)
        if not tenant:
            raise ValueError(f"Tenant {tenant_id} not found")

        integrations = session.scalars(select(Integration).where(Integration.tenant_id == tenant_id)).all()
        agent_keys = session.scalars(
            select(AgentApiKey).where(AgentApiKey.tenant_id == tenant_id, AgentApiKey.is_active == True)
        ).all()
        tools = _build_real_tools(integrations, agent_keys)
        tickets = session.scalars(
            select(Ticket).where(Ticket.tenant_id == tenant_id).order_by(Ticket.created_at.desc()).limit(40)
        ).all()
        security_domains = _build_security_domains(
            session,
            tenant_id,
            integrations,
            agent_keys,
            tickets,
        )
        findings = _flatten_domain_findings(security_domains)
        previous_findings = _flatten_previous_domain_findings(security_domains)
        severity_summary = _sum_domain_severity_summaries(security_domains, "current_severity_summary")
        previous_summary = _sum_domain_severity_summaries(security_domains, "previous_severity_summary")
        scan_snapshot = _build_latest_snapshot_overview(security_domains)
        period = _build_current_state_period(security_domains)
        weekly_actions = _build_weekly_actions(tickets)
        admin_module_actions = _build_admin_module_actions(parameters)
        if admin_module_actions:
            weekly_actions = admin_module_actions
        pending_findings = _build_pending_findings_from_rows(findings)
        integrations_overview = _build_integrations_overview(tools, security_domains)
        coverage_rows = _build_coverage_rows(tools, security_domains)
        coverage_summary = _build_coverage_summary(tools, security_domains, scan_snapshot)
        priority_focuses = _build_priority_focuses(security_domains, tickets, pending_findings)
        operational_considerations = _build_operational_considerations(tools, security_domains, scan_snapshot, tickets)
        client_limitations = _build_client_limitations(security_domains, scan_snapshot, tickets)
        analyst_text = _build_analyst_text(parameters)
        manual_security_news = _build_manual_security_news(parameters)
        report_variant = _normalize_report_variant(parameters.get("report_variant"))
        document_code = _build_document_code(
            tenant_id,
            document_id,
            tenant.name,
            _current_state_reference_datetime(security_domains) or datetime.now(timezone.utc),
            report_variant,
        )
        template_rules = _template_rules(report_variant)
        chart_data = {
            "client_name": tenant.name,
            "period": period,
            "previous_severity_summary": previous_summary,
            "current_severity_summary": severity_summary,
            "coverage_rows": coverage_rows,
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
                "integrations_overview": integrations_overview,
                "aggregated_metrics": {
                    "data_base": _build_data_base_text(findings, previous_findings, tickets, scan_snapshot, tools, security_domains),
                    "vulnerability_comparison": _build_vulnerability_comparison_rows(previous_summary, severity_summary),
                    "histogram_summary": _build_histogram_summary(previous_summary, severity_summary, tools, security_domains),
                    "results_obtained": _build_results_obtained(tickets, findings, scan_snapshot, tools, security_domains),
                    "coverage_summary": coverage_summary,
                    "coverage_rows": coverage_rows,
                    "priority_focuses": priority_focuses,
                    "operational_considerations": operational_considerations,
                    "limitations": client_limitations,
                    "pending_findings": pending_findings,
                    "security_domains": security_domains,
                    "weekly_actions": weekly_actions,
                },
                "security_domains": security_domains,
                "coverage_rows": coverage_rows,
                "coverage_summary": coverage_summary,
                "priority_focuses": priority_focuses,
                "operational_considerations": operational_considerations,
                "weekly_actions": weekly_actions,
                "manual_security_news": manual_security_news,
                "pending_findings": pending_findings,
                "top_findings": [_row_to_minority_row(finding) for finding in _sort_finding_rows(findings)[:40]],
                "previous_top_findings": [_row_to_minority_row(finding) for finding in _sort_finding_rows(previous_findings)[:40]],
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
            "findings": _sort_finding_rows(findings)[:80],
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
        "summary": "Comparativo construido entre la referencia anterior disponible y el estado actual de los hallazgos consolidados por integración.",
        "severity_rows": rows,
    }


def _build_histogram_summary(previous_summary: dict, current_summary: dict, tools: list[dict], security_domains: list[dict]) -> str:
    previous_total = sum(int(value or 0) for value in previous_summary.values())
    current_total = sum(int(value or 0) for value in current_summary.values())
    delta = current_total - previous_total
    active_domains = [str(domain.get("name") or "") for domain in security_domains if int(domain.get("current_findings_total") or 0) > 0]
    return (
        "El histograma de seguridad compara el volumen de hallazgos por severidad entre la semana anterior "
        f"({previous_total}) y la semana actual ({current_total}), con una variación neta de {delta:+d}. "
        f"El servicio se mantuvo activo sobre {len(tools)} integraciones; aportaron evidencia indexada en esta ventana: "
        f"{', '.join(active_domains) if active_domains else 'ninguna'}."
    )


def _build_data_base_text(
    findings: list[FindingIndex],
    previous_findings: list[FindingIndex],
    tickets: list[Ticket],
    scan_snapshot: dict,
    tools: list[dict],
    security_domains: list[dict],
) -> str:
    active_with_findings = sum(1 for domain in security_domains if int(domain.get("current_findings_total") or 0) > 0)
    return (
        f"Se consolidaron {len(findings)} hallazgos del estado actual y {len(previous_findings)} hallazgos en la referencia anterior disponible, "
        f"con {len(tickets)} tickets operativos asociados al tenant. "
        f"Hay {len(tools)} integraciones activas en cobertura y {active_with_findings} dominios con hallazgos indexados en el estado actual. "
        f"Integraciones con snapshot SOC utilizable: {scan_snapshot.get('current_soc_scans', 0)}; con snapshot NOC utilizable: {scan_snapshot.get('current_noc_scans', 0)}."
    )


def _build_results_obtained(
    tickets: list[Ticket],
    findings: list[FindingIndex],
    scan_snapshot: dict,
    tools: list[dict],
    security_domains: list[dict],
) -> list[str]:
    closed_statuses = {"RESUELTO", "RESOLVED", "COMPLETED", "EXECUTED", "APROBADO"}
    closed = [ticket for ticket in tickets if (ticket.status or "").upper() in closed_statuses]
    active_with_findings = [str(domain.get("name") or "") for domain in security_domains if int(domain.get("current_findings_total") or 0) > 0]
    covered_only = [str(tool.get("name") or "") for tool in tools if str(tool.get("name") or "") not in set(active_with_findings)]
    return [
        f"Tickets cerrados o ejecutados durante el periodo: {len(closed)}.",
        f"Hallazgos técnicos identificados en el periodo: {len(findings)}.",
        f"Escaneos/snapshots considerados en el periodo: {scan_snapshot.get('current_total_scans', 0)}.",
        f"Integraciones con hallazgos indexados: {', '.join(active_with_findings) if active_with_findings else 'ninguna'}.",
        f"Integraciones activas sin hallazgos indexados en esta ventana: {', '.join(covered_only) if covered_only else 'ninguna'}.",
    ]


def _sum_domain_severity_summaries(domains: list[dict], key: str) -> dict[str, int]:
    summary = {"critical": 0, "high": 0, "medium": 0, "low": 0, "informational": 0}
    for domain in domains:
        values = domain.get(key) or {}
        for severity in summary:
            summary[severity] += int(values.get(severity, 0) or 0)
    return summary


def _flatten_domain_findings(domains: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for domain in domains:
        name = str(domain.get("name") or "")
        for finding in domain.get("findings") or []:
            rows.append({
                "id": finding.get("id"),
                "domain": name,
                "title": finding.get("vulnerability"),
                "affected_hosts": finding.get("affected_hosts"),
                "severity": finding.get("severity"),
                "provider": domain.get("provider"),
                "description": None,
                "recommendation": None,
            })
    return rows


def _flatten_previous_domain_findings(domains: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for domain in domains:
        name = str(domain.get("name") or "")
        for finding in domain.get("previous_findings") or []:
            rows.append({
                "id": finding.get("id"),
                "domain": name,
                "title": finding.get("vulnerability"),
                "affected_hosts": finding.get("affected_hosts"),
                "severity": finding.get("severity"),
                "provider": domain.get("provider"),
            })
    return rows


def _sort_finding_rows(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (
            _severity_rank(row.get("severity")),
            str(row.get("id") or ""),
        ),
    )


def _row_to_minority_row(row: dict) -> dict:
    return {
        "id": str(row.get("id") or ""),
        "vulnerability": str(row.get("title") or "Hallazgo sin titulo"),
        "affected_hosts": str(row.get("affected_hosts") or "N/D"),
        "severity": str(row.get("severity") or "Informativa"),
    }


def _current_state_reference_datetime(domains: list[dict]) -> datetime | None:
    latest_values = [
        str((domain.get("snapshot") or {}).get("scanned_at") or "")
        for domain in domains
        if (domain.get("snapshot") or {}).get("scanned_at")
    ]
    if not latest_values:
        return None
    latest = max(latest_values)
    return datetime.fromisoformat(latest.replace("Z", "+00:00"))


def _build_current_state_period(domains: list[dict]) -> str:
    snapshot_dates = [
        str((domain.get("snapshot") or {}).get("scanned_at") or "")
        for domain in domains
        if (domain.get("snapshot") or {}).get("scanned_at")
    ]
    if not snapshot_dates:
        return "Estado actual sin snapshots disponibles"
    latest = max(snapshot_dates)
    return f"Estado actual con última evidencia al {latest[:10]}"


def _build_latest_snapshot_overview(domains: list[dict]) -> dict:
    current_soc = 0
    current_noc = 0
    current_total = 0
    previous_total = 0
    current_summary = {"critical": 0, "high": 0, "medium": 0, "low": 0, "informational": 0}
    previous_summary = {"critical": 0, "high": 0, "medium": 0, "low": 0, "informational": 0}
    for domain in domains:
        snapshot = domain.get("snapshot") or {}
        previous_snapshot = domain.get("previous_snapshot") or {}
        if snapshot.get("available"):
            current_total += 1
            if snapshot.get("domain") == "noc":
                current_noc += 1
            else:
                current_soc += 1
        if previous_snapshot.get("available"):
            previous_total += 1
        for severity in current_summary:
            current_summary[severity] += int((snapshot.get("counts") or {}).get(severity, 0) or 0)
            previous_summary[severity] += int((previous_snapshot.get("counts") or {}).get(severity, 0) or 0)
    return {
        "current_soc_scans": current_soc,
        "current_noc_scans": current_noc,
        "previous_soc_scans": previous_total,
        "previous_noc_scans": 0,
        "current_total_scans": current_total,
        "previous_total_scans": previous_total,
        "current_scan_severity_summary": current_summary,
        "previous_scan_severity_summary": previous_summary,
    }


def _normalize_provider(value: object) -> str:
    normalized = str(value or "other").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "rapid7": "insightvm",
        "insight_vm": "insightvm",
        "tenable": "nessus",
        "uptime": "uptime_kuma",
    }
    return aliases.get(normalized, normalized)


def _provider_sort_key(provider: str) -> tuple[int, str]:
    normalized = _normalize_provider(provider)
    try:
        return (PROVIDER_ORDER.index(normalized), PROVIDER_LABELS.get(normalized, normalized))
    except ValueError:
        return (len(PROVIDER_ORDER), PROVIDER_LABELS.get(normalized, normalized))


def _provider_layer(provider: str) -> str:
    normalized = _normalize_provider(provider)
    if normalized in NOC_PROVIDERS:
        return "NOC"
    return "SOC"


def _severity_rank(value: object) -> int:
    normalized = str(value or "").strip().lower()
    return SEVERITY_PRIORITY.get(normalized, 99)


def _sort_findings(findings: list[FindingIndex]) -> list[FindingIndex]:
    return sorted(
        findings,
        key=lambda finding: (
            _severity_rank(finding.severity),
            -(finding.created_at.timestamp() if finding.created_at else 0),
            str(finding.id),
        ),
    )


def _build_integration_provider_names(integrations: list[Integration], agent_keys: list[AgentApiKey]) -> list[str]:
    names = {_normalize_provider(integration.provider) for integration in integrations}
    names.update(_normalize_provider(agent_key.integration_type) for agent_key in agent_keys)
    names.discard("other")
    return sorted(names, key=_provider_sort_key)


def _build_real_tools(integrations: list[Integration], agent_keys: list[AgentApiKey]) -> list[dict]:
    tools = []
    for provider in _build_integration_provider_names(integrations, agent_keys):
        tools.append({
            "name": PROVIDER_LABELS.get(provider, provider.upper()),
            "description": f"Integracion activa para {PROVIDER_LABELS.get(provider, provider)}.",
        })
    return tools


def _group_findings_by_provider(findings: list[FindingIndex]) -> dict[str, list[FindingIndex]]:
    grouped: dict[str, list[FindingIndex]] = {}
    for finding in findings:
        grouped.setdefault(_normalize_provider(finding.scanner_type), []).append(finding)
    return grouped


def _load_snapshot_findings(session, provider: str, scan: ScanSummary | ScanSummaryNoc | None) -> list[FindingIndex]:
    if scan is None:
        return []
    if provider in {"zabbix", "uptime_kuma"}:
        stmt = select(FindingIndex).where(FindingIndex.scan_summary_noc_id == scan.id)
    else:
        stmt = select(FindingIndex).where(FindingIndex.scan_summary_soc_id == scan.id)
    return list(session.scalars(stmt.order_by(FindingIndex.created_at.desc()).limit(200)))


def _latest_provider_scan(session, tenant_id: int, provider: str) -> ScanSummary | ScanSummaryNoc | None:
    if provider in {"zabbix", "uptime_kuma"}:
        return _latest_noc_scan(session, tenant_id, provider)
    return _latest_soc_scan(session, tenant_id, provider)


def _previous_provider_scan(
    session,
    tenant_id: int,
    provider: str,
    before: datetime | None,
) -> ScanSummary | ScanSummaryNoc | None:
    if before is None:
        return None
    if provider in {"zabbix", "uptime_kuma"}:
        return session.scalar(
            select(ScanSummaryNoc)
            .where(
                ScanSummaryNoc.tenant_id == tenant_id,
                ScanSummaryNoc.scanner_type == provider,
                ScanSummaryNoc.scanned_at < before,
            )
            .order_by(ScanSummaryNoc.scanned_at.desc())
        )
    return session.scalar(
        select(ScanSummary)
        .where(
            ScanSummary.tenant_id == tenant_id,
            ScanSummary.scanner_type == provider,
            ScanSummary.scanned_at < before,
        )
        .order_by(ScanSummary.scanned_at.desc())
    )


def _scan_counts_dict(scan: ScanSummary | ScanSummaryNoc | None) -> dict[str, int]:
    if scan is None:
        return {"critical": 0, "high": 0, "medium": 0, "low": 0, "informational": 0}
    return {
        "critical": int(getattr(scan, "critical_count", 0) or 0),
        "high": int(getattr(scan, "high_count", 0) or 0),
        "medium": int(getattr(scan, "medium_count", 0) or 0),
        "low": int(getattr(scan, "low_count", 0) or 0),
        "informational": int(getattr(scan, "info_count", 0) or 0),
    }


def _total_from_scan_counts(scan: ScanSummary | ScanSummaryNoc | None) -> int:
    counts = _scan_counts_dict(scan)
    return sum(counts.values())


def _build_snapshot_summary(provider: str, scan: ScanSummary | ScanSummaryNoc | None, findings_count: int) -> dict[str, Any]:
    if scan is None:
        return {
            "available": False,
            "domain": "noc" if provider in {"zabbix", "uptime_kuma"} else "soc",
            "scanned_at": None,
            "status": None,
            "scan_id": None,
            "summary_type": None,
            "counts": {"critical": 0, "high": 0, "medium": 0, "low": 0, "informational": 0},
            "total_hosts": 0,
            "findings_indexed": findings_count,
        }
    return {
        "available": True,
        "domain": "noc" if provider in {"zabbix", "uptime_kuma"} else "soc",
        "scanned_at": scan.scanned_at.isoformat() if getattr(scan, "scanned_at", None) else None,
        "status": getattr(scan, "status", None),
        "scan_id": getattr(scan, "scan_id", None),
        "summary_type": getattr(scan, "summary_type", None),
        "counts": _scan_counts_dict(scan),
        "total_hosts": int(getattr(scan, "total_hosts", 0) or 0),
        "findings_indexed": findings_count,
    }


def _snapshot_compact_row(scan: ScanSummary | ScanSummaryNoc | None) -> dict[str, Any]:
    if scan is None:
        return {
            "available": False,
            "scanned_at": None,
            "counts": {"critical": 0, "high": 0, "medium": 0, "low": 0, "informational": 0},
            "scan_id": None,
            "status": None,
        }
    return {
        "available": True,
        "scanned_at": scan.scanned_at.isoformat() if getattr(scan, "scanned_at", None) else None,
        "counts": _scan_counts_dict(scan),
        "scan_id": getattr(scan, "scan_id", None),
        "status": getattr(scan, "status", None),
    }


def _build_severity_summary_for_findings(findings: list[FindingIndex]) -> dict[str, int]:
    return _build_real_severity_summary(findings)


def _sample_findings_for_domain(findings: list[FindingIndex], limit: int = 12) -> list[dict]:
    return [_finding_to_minority_row(item) for item in _sort_findings(findings)[:limit]]


def _build_domain_summary(
    provider: str,
    current_items: list[FindingIndex],
    previous_items: list[FindingIndex],
    tickets: list[Ticket],
    total_integrations: int,
    latest_scan: ScanSummary | ScanSummaryNoc | None,
) -> str:
    label = PROVIDER_LABELS.get(provider, provider.upper())
    current_total = len(current_items) or _total_from_scan_counts(latest_scan)
    previous_total = len(previous_items)
    delta = current_total - previous_total
    current_severity = _build_severity_summary_for_findings(current_items) if current_items else _scan_counts_dict(latest_scan)
    tickets_total = len(tickets)
    latest_scan_at = latest_scan.scanned_at.date().isoformat() if latest_scan and getattr(latest_scan, "scanned_at", None) else None
    if current_total:
        return (
            f"{label} aporta {current_total} hallazgos en el periodo evaluado "
            f"({delta:+d} frente a la ventana previa con {previous_total}). "
            f"Distribución actual: críticas {current_severity.get('critical', 0)}, altas {current_severity.get('high', 0)}, "
            f"medias {current_severity.get('medium', 0)}, bajas {current_severity.get('low', 0)} e informativas {current_severity.get('informational', 0)}. "
            f"Último snapshot disponible: {latest_scan_at or 'sin fecha registrada'}. "
            f"El tenant mantiene {total_integrations} integraciones activas en cobertura actual, "
            f"con {tickets_total} tickets operativos asociados para seguimiento."
        )
    if latest_scan is not None:
        return (
            f"{label} se mantiene como integración activa del servicio y su último snapshot disponible corresponde a {latest_scan_at or 'una fecha no registrada'}, "
            "pero ese snapshot no dejó hallazgos indexados consumibles en el reporte. "
            f"En la ventana previa se observaron {previous_total} hallazgos asociados. "
            "Se recomienda validar su última ejecución para mantener continuidad operativa."
        )
    return (
        f"{label} se mantiene como integración activa del servicio, pero no registra snapshot utilizable ni hallazgos indexados en el periodo evaluado. "
        f"En la ventana previa se observaron {previous_total} hallazgos asociados. "
        "Se recomienda usar esta integración como cobertura operativa complementaria y validar su estado operativo actual."
    )


def _build_security_domains(
    session,
    tenant_id: int,
    integrations: list[Integration],
    agent_keys: list[AgentApiKey],
    tickets: list[Ticket],
) -> list[dict]:
    active_providers = _build_integration_provider_names(integrations, agent_keys)
    provider_names = sorted(active_providers, key=_provider_sort_key)
    domains = []
    total_integrations = len(active_providers)
    for provider in provider_names:
        latest_scan = _latest_provider_scan(session, tenant_id, provider)
        snapshot_items = _load_snapshot_findings(session, provider, latest_scan)
        previous_scan = _previous_provider_scan(session, tenant_id, provider, getattr(latest_scan, "scanned_at", None))
        previous_items = _load_snapshot_findings(session, provider, previous_scan)
        items = snapshot_items
        current_severity = _build_severity_summary_for_findings(items) if items else _scan_counts_dict(latest_scan)
        previous_severity = _build_severity_summary_for_findings(previous_items) if previous_items else _scan_counts_dict(previous_scan)
        current_total = len(items) or _total_from_scan_counts(latest_scan)
        previous_total = len(previous_items) or _total_from_scan_counts(previous_scan)
        domains.append({
            "name": PROVIDER_LABELS.get(provider, provider.upper()),
            "provider": provider,
            "layer": _provider_layer(provider),
            "is_active": provider in active_providers,
            "current_findings_total": current_total,
            "previous_findings_total": previous_total,
            "current_severity_summary": current_severity,
            "previous_severity_summary": previous_severity,
            "snapshot": _build_snapshot_summary(provider, latest_scan, len(items)),
            "previous_snapshot": _snapshot_compact_row(previous_scan),
            "summary": _build_domain_summary(provider, items, previous_items, tickets, total_integrations, latest_scan),
            "findings": _sample_findings_for_domain(items, limit=12),
            "previous_findings": _sample_findings_for_domain(previous_items, limit=8),
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
    for finding in _sort_findings(findings)[:20]:
        pending.append(
            f"{_normalize_provider(finding.scanner_type)}: {(finding.name or finding.cve or finding.event_type or 'Hallazgo')} ({finding.severity})"
        )
    return pending


def _build_pending_findings_from_rows(findings: list[dict]) -> list[str]:
    pending = []
    for finding in _sort_finding_rows(findings)[:20]:
        pending.append(
            f"{_normalize_provider(finding.get('provider'))}: {(finding.get('title') or 'Hallazgo')} ({finding.get('severity')})"
        )
    return pending


def _build_coverage_rows(tools: list[dict], security_domains: list[dict]) -> list[dict[str, Any]]:
    domain_by_name = {str(domain.get("name") or ""): domain for domain in security_domains}
    rows: list[dict[str, Any]] = []
    for tool in tools:
        name = str(tool.get("name") or "")
        domain = domain_by_name.get(name, {})
        snapshot = domain.get("snapshot") or {}
        current_total = int(domain.get("current_findings_total") or 0)
        status = "Con evidencia reciente" if current_total > 0 else (
            "Sin hallazgos observables" if snapshot.get("available") else "Sin snapshot reciente"
        )
        rows.append(
            {
                "integration": name,
                "layer": _provider_layer(domain.get("provider") or name),
                "last_evidence_at": snapshot.get("scanned_at") or "No disponible",
                "current_findings_total": current_total,
                "status": status,
            }
        )
    return rows


def _build_coverage_summary(tools: list[dict], security_domains: list[dict], scan_snapshot: dict) -> str:
    rows = _build_coverage_rows(tools, security_domains)
    with_evidence = [row["integration"] for row in rows if row["current_findings_total"] > 0]
    without_findings = [row["integration"] for row in rows if row["current_findings_total"] == 0 and row["status"] == "Sin hallazgos observables"]
    without_snapshot = [row["integration"] for row in rows if row["status"] == "Sin snapshot reciente"]
    return (
        f"La cobertura del servicio abarca {len(rows)} integraciones activas ({sum(1 for row in rows if row['layer']=='SOC')} SOC y {sum(1 for row in rows if row['layer']=='NOC')} NOC). "
        f"Con evidencia reciente en la ventana o último snapshot utilizable: {', '.join(with_evidence) if with_evidence else 'ninguna'}. "
        f"Activas sin hallazgos observables en la última evidencia: {', '.join(without_findings) if without_findings else 'ninguna'}. "
        f"Sin snapshot reciente utilizable: {', '.join(without_snapshot) if without_snapshot else 'ninguna'}. "
        f"Snapshots SOC/NOC dentro de la ventana actual: {scan_snapshot.get('current_total_scans', 0)}."
    )


def _build_priority_focuses(security_domains: list[dict], tickets: list[Ticket], pending_findings: list[str]) -> list[str]:
    focuses: list[str] = []
    ranked = sorted(
        security_domains,
        key=lambda domain: (
            -int((domain.get("current_severity_summary") or {}).get("critical", 0) or 0),
            -int((domain.get("current_severity_summary") or {}).get("high", 0) or 0),
            -int(domain.get("current_findings_total") or 0),
        ),
    )
    for domain in ranked[:3]:
        sev = domain.get("current_severity_summary") or {}
        if int(domain.get("current_findings_total") or 0) <= 0:
            continue
        focuses.append(
            f"Priorizar {domain.get('name')} por concentración de hallazgos: críticas {sev.get('critical', 0)}, altas {sev.get('high', 0)} y total {domain.get('current_findings_total', 0)}."
        )
    if pending_findings:
        focuses.append(f"Mantener seguimiento sobre {len(pending_findings)} hallazgos pendientes priorizados para remediación.")
    if not tickets:
        focuses.append("No se registran tickets operativos en la ventana; validar si las acciones de remediación están siendo trazadas fuera del flujo de tickets.")
    return focuses[:5]


def _build_operational_considerations(tools: list[dict], security_domains: list[dict], scan_snapshot: dict, tickets: list[Ticket]) -> list[str]:
    considerations: list[str] = []
    if int(scan_snapshot.get("current_total_scans", 0) or 0) == 0:
        considerations.append("No se registraron scans o snapshots SOC/NOC dentro de la ventana actual; parte del análisis se apoya en la última evidencia disponible por integración.")
    dormant = [domain.get("name") for domain in security_domains if int(domain.get("current_findings_total") or 0) == 0]
    if dormant:
        considerations.append(f"Las integraciones {', '.join(str(name) for name in dormant)} se mantienen activas, pero sin hallazgos observables en la última evidencia disponible.")
    if not tickets:
        considerations.append("No se identificaron tickets operativos asociados al período, por lo que la trazabilidad de remediaciones es limitada.")
    if len(tools) > len([domain for domain in security_domains if int(domain.get("current_findings_total") or 0) > 0]):
        considerations.append("La cobertura del servicio es más amplia que la evidencia con hallazgos del período; esto debe leerse como diferencia entre monitoreo activo y actividad observable.")
    return considerations[:5]


def _build_client_limitations(security_domains: list[dict], scan_snapshot: dict, tickets: list[Ticket]) -> list[str]:
    limitations: list[str] = []
    if int(scan_snapshot.get("current_total_scans", 0) or 0) == 0:
        limitations.append("No se registraron snapshots SOC/NOC dentro de la ventana evaluada; parte de la comparación se apoya en la última evidencia disponible por integración.")
    if not tickets:
        limitations.append("No se identificaron tickets operativos asociados al período, lo que limita la validación de remediaciones ejecutadas.")
    zero_domains = [domain.get("name") for domain in security_domains if int(domain.get("current_findings_total") or 0) == 0]
    if zero_domains:
        limitations.append(f"Algunas integraciones activas no generaron hallazgos observables en la última evidencia disponible ({', '.join(str(name) for name in zero_domains)}).")
    limitations.append("Las tablas por dominio presentan una muestra priorizada de hallazgos y no el universo completo de eventos del período.")
    return limitations[:5]


def _build_integrations_overview(tools: list[dict], security_domains: list[dict]) -> list[dict]:
    domain_by_name = {str(domain.get("name") or ""): domain for domain in security_domains}
    overview = []
    for tool in tools:
        name = str(tool.get("name") or "")
        domain = domain_by_name.get(name, {})
        overview.append(
            {
                "name": name,
                "description": tool.get("description") or "",
                "current_findings_total": int(domain.get("current_findings_total") or 0),
                "previous_findings_total": int(domain.get("previous_findings_total") or 0),
                "is_active": bool(domain.get("is_active", True)),
            }
        )
    return overview


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
