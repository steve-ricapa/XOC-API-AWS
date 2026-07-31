from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.integrations.summary_store import _latest_noc_scan, _latest_soc_scan
from src.persistence.models import FindingIndex, ScanSummary, ScanSummaryNoc
from src.shared.tenant_preferences import get_tenant_preferences, get_visible_prevention_providers


_VULNERABILITY_PROVIDERS = ("openvas", "nessus", "insightvm")
_SECURITY_EVENT_PROVIDERS = ("wazuh",)
_MONITORING_PROVIDERS = ("zabbix", "uptime_kuma")
_ALL_PROVIDERS = _VULNERABILITY_PROVIDERS + _SECURITY_EVENT_PROVIDERS + _MONITORING_PROVIDERS
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4, "informational": 4}
_PROVIDER_LABELS = {
    "openvas": "OpenVAS",
    "nessus": "Nessus",
    "insightvm": "InsightVM / Rapid7",
    "wazuh": "Wazuh",
    "zabbix": "Zabbix",
    "uptime_kuma": "Uptime Kuma",
}


def _normalize_severity(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return "info"
    if "critical" in normalized:
        return "critical"
    if "high" in normalized:
        return "high"
    if "medium" in normalized:
        return "medium"
    if "low" in normalized:
        return "low"
    if normalized in {"info", "informational"}:
        return "info"
    return "info"


def _normalize_status(provider: str, status: str | None) -> str:
    normalized = str(status or "").strip().lower()
    if normalized:
        return normalized
    if provider in _VULNERABILITY_PROVIDERS:
        return "open"
    return "active"


def _normalize_snapshot_mode(value: str | None) -> str:
    normalized = str(value or "latest").strip().lower()
    return normalized if normalized == "latest" else "latest"


def _latest_provider_scans(session: Session, tenant_id: int, visible_providers: set[str]) -> dict[str, ScanSummary | ScanSummaryNoc]:
    scans: dict[str, ScanSummary | ScanSummaryNoc] = {}
    for provider in _ALL_PROVIDERS:
        if provider not in visible_providers:
            continue
        if provider in _MONITORING_PROVIDERS:
            latest = _latest_noc_scan(session, tenant_id, provider)
        else:
            latest = _latest_soc_scan(session, tenant_id, provider)
        if latest:
            scans[provider] = latest
    return scans


def _provider_bucket(provider: str) -> str:
    if provider in _VULNERABILITY_PROVIDERS:
        return "vulnerabilities"
    if provider in _SECURITY_EVENT_PROVIDERS:
        return "security_events"
    return "monitoring"


def _entity_type(provider: str) -> str:
    if provider in _VULNERABILITY_PROVIDERS:
        return "finding"
    if provider in _SECURITY_EVENT_PROVIDERS:
        return "security_event"
    return "monitoring_issue"


def _scan_detail_endpoint(summary_id: int, domain: str) -> str:
    if domain == "noc":
        return f"/scans/{summary_id}?domain=noc"
    return f"/scans/{summary_id}"


def _load_findings_for_scan(session: Session, provider: str, scan) -> list[FindingIndex]:
    if provider in _MONITORING_PROVIDERS:
        stmt = select(FindingIndex).where(FindingIndex.scan_summary_noc_id == scan.id)
    else:
        stmt = select(FindingIndex).where(FindingIndex.scan_summary_soc_id == scan.id)
    return list(session.scalars(stmt.order_by(FindingIndex.created_at.desc())))


def _map_issue_item(provider: str, scan, finding: FindingIndex) -> dict:
    severity = _normalize_severity(finding.severity)
    domain = "noc" if provider in _MONITORING_PROVIDERS else "soc"
    summary_id = finding.scan_summary_noc_id if domain == "noc" else finding.scan_summary_soc_id
    title = str(finding.name or finding.event_type or finding.cve or f"{_PROVIDER_LABELS[provider]} issue").strip()
    host_or_service = str(finding.host or finding.service or "").strip()
    subtitle_parts = [part for part in [finding.cve, host_or_service] if part]

    detected_at = finding.detected_at or scan.scanned_at or finding.created_at
    detected_at_iso = detected_at.isoformat() if detected_at else None
    snapshot_at_iso = scan.scanned_at.isoformat() if getattr(scan, "scanned_at", None) else None

    return {
        "id": str(finding.id),
        "entity_type": _entity_type(provider),
        "provider": provider,
        "provider_label": _PROVIDER_LABELS[provider],
        "bucket": _provider_bucket(provider),
        "domain": domain,
        "severity": severity,
        "title": title,
        "subtitle": " | ".join(subtitle_parts) if subtitle_parts else None,
        "host": finding.host,
        "service": finding.service,
        "cve": finding.cve or finding.event_type,
        "cvss": finding.cvss,
        "status": _normalize_status(provider, finding.status),
        "description": finding.description,
        "solution": finding.solution,
        "impact": finding.impact,
        "detected_at": detected_at_iso,
        "snapshot_at": snapshot_at_iso,
        "scan_summary_id": str(summary_id) if summary_id is not None else None,
        "scan_summary_soc_id": str(finding.scan_summary_soc_id) if finding.scan_summary_soc_id is not None else None,
        "scan_summary_noc_id": str(finding.scan_summary_noc_id) if finding.scan_summary_noc_id is not None else None,
        "scan_id": finding.scan_id,
        "detail_type": "finding",
        "detail_endpoint": f"/findings/{finding.id}",
        "scan_detail_endpoint": _scan_detail_endpoint(int(summary_id), domain) if summary_id is not None else None,
    }


def _collect_latest_issue_items(session: Session, tenant_id: int) -> list[dict]:
    preferences = get_tenant_preferences(session, tenant_id)
    visible_providers = set(get_visible_prevention_providers(preferences)) or set(_ALL_PROVIDERS)
    scans = _latest_provider_scans(session, tenant_id, visible_providers)
    items: list[dict] = []
    for provider, scan in scans.items():
        for finding in _load_findings_for_scan(session, provider, scan):
            items.append(_map_issue_item(provider, scan, finding))
    items.sort(
        key=lambda item: (
            _SEVERITY_ORDER.get(item["severity"], 99),
            item.get("snapshot_at") or "",
            item.get("provider") or "",
            item.get("title") or "",
        ),
        reverse=False,
    )
    items.sort(key=lambda item: item.get("snapshot_at") or "", reverse=True)
    return items


def _apply_filters(items: list[dict], *, search: str | None = None, severity: str | None = None, provider: str | None = None, status: str | None = None, host: str | None = None, service: str | None = None) -> list[dict]:
    severity = _normalize_severity(severity) if severity else None
    provider = str(provider or "").strip().lower() or None
    status = str(status or "").strip().lower() or None
    host = str(host or "").strip().lower() or None
    service = str(service or "").strip().lower() or None
    search = str(search or "").strip().lower() or None

    result = []
    for item in items:
        if severity and item.get("severity") != severity:
            continue
        if provider and item.get("provider") != provider:
            continue
        if status and str(item.get("status") or "").lower() != status:
            continue
        if host and host not in str(item.get("host") or "").lower():
            continue
        if service and service not in str(item.get("service") or "").lower():
            continue
        if search:
            haystack = " ".join(
                str(item.get(key) or "")
                for key in ("title", "subtitle", "host", "service", "cve", "provider_label", "severity")
            ).lower()
            if search not in haystack:
                continue
        result.append(item)
    return result


def get_preventions_issues_overview(session: Session, tenant_id: int) -> dict:
    items = _collect_latest_issue_items(session, tenant_id)
    by_severity = {severity: 0 for severity in ("critical", "high", "medium", "low", "info")}
    by_provider: dict[str, int] = {}
    latest_snapshot_times: dict[str, str | None] = {}
    for item in items:
        severity = item.get("severity") or "info"
        by_severity[severity] = by_severity.get(severity, 0) + 1
        provider = str(item.get("provider") or "other")
        by_provider[provider] = by_provider.get(provider, 0) + 1
        latest_snapshot_times.setdefault(provider, item.get("snapshot_at"))

    vulnerabilities = [item for item in items if item["bucket"] == "vulnerabilities"]
    security_events = [item for item in items if item["bucket"] == "security_events"]
    monitoring = [item for item in items if item["bucket"] == "monitoring"]

    providers = [
        {
            "provider": provider,
            "label": _PROVIDER_LABELS.get(provider, provider),
            "count": count,
            "last_snapshot_at": latest_snapshot_times.get(provider),
        }
        for provider, count in sorted(by_provider.items(), key=lambda item: (-item[1], item[0]))
    ]
    statuses = sorted({str(item.get("status") or "active") for item in items})

    return {
        "snapshot_mode": "latest",
        "counts": {
            "all": len(items),
            "vulnerabilities": len(vulnerabilities),
            "security_events": len(security_events),
            "monitoring": len(monitoring),
        },
        "by_severity": by_severity,
        "by_provider": by_provider,
        "providers": providers,
        "available_filters": {
            "severities": [severity for severity, count in by_severity.items() if count > 0],
            "providers": [provider["provider"] for provider in providers],
            "statuses": statuses,
            "tabs": ["all", "vulnerabilities", "security-events", "monitoring"],
        },
        "generated_at": datetime.utcnow().isoformat(),
    }


def list_preventions_issues(session: Session, tenant_id: int, *, tab: str = "all", search: str | None = None, severity: str | None = None, provider: str | None = None, status: str | None = None, host: str | None = None, service: str | None = None, limit: int = 100, offset: int = 0, snapshot_mode: str | None = None) -> dict:
    snapshot_mode = _normalize_snapshot_mode(snapshot_mode)
    items = _collect_latest_issue_items(session, tenant_id)
    if tab == "vulnerabilities":
        items = [item for item in items if item["bucket"] == "vulnerabilities"]
    elif tab == "security-events":
        items = [item for item in items if item["bucket"] == "security_events"]
    elif tab == "monitoring":
        items = [item for item in items if item["bucket"] == "monitoring"]

    filtered = _apply_filters(items, search=search, severity=severity, provider=provider, status=status, host=host, service=service)
    total = len(filtered)
    page = filtered[offset: offset + max(1, min(limit, 500))]
    return {
        "tab": tab,
        "snapshot_mode": snapshot_mode,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": page,
    }
