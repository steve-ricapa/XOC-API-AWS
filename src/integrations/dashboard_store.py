from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from src.integrations.summary_store import (
    _base_status,
    _latest_noc_scan,
    _latest_soc_scan,
    _pick_integration,
    _recent_findings,
    _recent_noc_events,
    _recent_noc_scans,
    _recent_soc_scans,
    _scan_counts,
    build_dashboard_summary,
    build_uptime_kuma_summary,
    build_vulnerability_summary,
    build_wazuh_summary,
    build_zabbix_summary,
)
from src.persistence.models import AgentApiKey, FindingIndex, ScanSummary, ScanSummaryNoc, Ticket
from src.shared.errors import NotFoundError
from src.shared.tenant_preferences import (
    get_tenant_preferences,
    get_visible_integration_providers,
    hide_unconfigured_providers,
)

_SUPPORTED_PROVIDERS = {"openvas", "insightvm", "nessus", "tenable", "wazuh", "zabbix", "uptime_kuma"}
_VULN_PROVIDERS = {"openvas", "insightvm", "nessus", "tenable"}
_NOC_PROVIDERS = {"zabbix", "uptime_kuma"}

_HOME_PROVIDER_META = {
    "openvas": {"label": "OpenVAS Scans", "navigation_slug": "openvas"},
    "insightvm": {"label": "InsightVM / Rapid7", "navigation_slug": "insightvm"},
    "nessus": {"label": "Nessus Scans", "navigation_slug": "nessus"},
    "tenable": {"label": "Tenable Scans", "navigation_slug": "tenable"},
    "wazuh": {"label": "Wazuh SIEM", "navigation_slug": "wazuh"},
    "zabbix": {"label": "Zabbix Monitor", "navigation_slug": "zabbix"},
    "uptime_kuma": {"label": "Uptime Kuma", "navigation_slug": "uptime"},
}

_PROVIDER_DOMAINS = {
    "openvas": "vulnerability",
    "insightvm": "vulnerability",
    "nessus": "vulnerability",
    "tenable": "vulnerability",
    "wazuh": "soc",
    "zabbix": "noc",
    "uptime_kuma": "noc",
}

_PREFERENCE_PROVIDER_ALIASES = {
    "tenable": "nessus",
}

_PROVIDER_ALIASES = {
    "uptime": "uptime_kuma",
    "rapid7": "insightvm",
}


def _canonical_provider(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(normalized, normalized)


def _provider_meta(provider: str) -> dict:
    meta = _HOME_PROVIDER_META.get(provider, {"label": provider.replace("_", " ").title(), "navigation_slug": provider})
    return {
        "provider": provider,
        "label": meta["label"],
        "navigation_slug": meta["navigation_slug"],
        "domain": _PROVIDER_DOMAINS.get(provider, "other"),
    }


def _provider_visibility_key(provider: str) -> str:
    return _PREFERENCE_PROVIDER_ALIASES.get(provider, provider)


def _empty_provider_dashboard(provider: str, message: str) -> dict:
    meta = _provider_meta(provider)
    return {
        **meta,
        "configured": False,
        "active": False,
        "has_data": False,
        "message": message,
        "range": None,
        "summary": {},
        "kpis": {},
        "charts": {},
        "tables": {},
        "agentInfo": None,
    }


def _parse_range(preset: str | None = None, from_date: str | None = None, to_date: str | None = None, default_days: int = 30, max_days: int = 90) -> dict:
    now = datetime.utcnow()
    if preset == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
    elif preset == "yesterday":
        yesterday = now - timedelta(days=1)
        start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        end = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif preset == "7d":
        start = now - timedelta(days=7)
        end = now
    elif preset == "custom" and from_date and to_date:
        try:
            start = datetime.fromisoformat(from_date.replace("Z", "+00:00"))
            if start.tzinfo:
                start = start.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            start = now - timedelta(days=default_days)
        try:
            end = datetime.fromisoformat(to_date.replace("Z", "+00:00"))
            if end.tzinfo:
                end = end.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            end = now
        if start > end:
            start, end = end, start
        if (end - start) > timedelta(days=max_days):
            end = start + timedelta(days=max_days)
    else:
        days = default_days if default_days else 30
        start = now - timedelta(days=min(days, max_days))
        end = now

    return {
        "preset": preset or f"{default_days}d",
        "from": start.isoformat(),
        "to": end.isoformat(),
        "start": start,
        "end": end,
    }


def _build_vuln_trend(session: Session, tenant_id: int, provider: str, range_start: datetime, range_end: datetime) -> list[dict]:
    range_start_day = range_start.replace(hour=0, minute=0, second=0, microsecond=0)
    range_end_day = range_end.replace(hour=0, minute=0, second=0, microsecond=0)
    total_days = max(1, (range_end_day - range_start_day).days + 1)
    trend = []
    for i in range(total_days):
        day_start = range_start_day + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        row = session.query(
            func.sum(ScanSummary.critical_count).label("critical"),
            func.sum(ScanSummary.high_count).label("high"),
            func.sum(ScanSummary.medium_count).label("medium"),
            func.sum(ScanSummary.low_count).label("low"),
            func.sum(ScanSummary.info_count).label("info"),
        ).filter(
            ScanSummary.tenant_id == tenant_id,
            ScanSummary.scanner_type == provider,
            ScanSummary.scanned_at >= day_start,
            ScanSummary.scanned_at < day_end,
            ScanSummary.status == "completed",
        ).first()
        trend.append({
            "date": day_start.strftime("%Y-%m-%d"),
            "critical": int(row.critical or 0) if row else 0,
            "high": int(row.high or 0) if row else 0,
            "medium": int(row.medium or 0) if row else 0,
            "low": int(row.low or 0) if row else 0,
            "info": int(row.info or 0) if row else 0,
        })
    return trend


def _build_noc_trend(session: Session, tenant_id: int, provider: str, range_start: datetime, range_end: datetime) -> list[dict]:
    range_start_day = range_start.replace(hour=0, minute=0, second=0, microsecond=0)
    range_end_day = range_end.replace(hour=0, minute=0, second=0, microsecond=0)
    total_days = max(1, (range_end_day - range_start_day).days + 1)
    trend = []
    for i in range(total_days):
        day_start = range_start_day + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        row = session.query(
            func.sum(ScanSummaryNoc.critical_count).label("critical"),
            func.sum(ScanSummaryNoc.high_count).label("high"),
            func.sum(ScanSummaryNoc.medium_count).label("medium"),
            func.sum(ScanSummaryNoc.low_count).label("low"),
            func.sum(ScanSummaryNoc.info_count).label("info"),
        ).filter(
            ScanSummaryNoc.tenant_id == tenant_id,
            ScanSummaryNoc.scanner_type == provider,
            ScanSummaryNoc.scanned_at >= day_start,
            ScanSummaryNoc.scanned_at < day_end,
            ScanSummaryNoc.status == "completed",
        ).first()
        trend.append({
            "date": day_start.strftime("%Y-%m-%d"),
            "critical": int(row.critical or 0) if row else 0,
            "high": int(row.high or 0) if row else 0,
            "medium": int(row.medium or 0) if row else 0,
            "low": int(row.low or 0) if row else 0,
            "info": int(row.info or 0) if row else 0,
        })
    return trend


def _build_top_cves(session: Session, tenant_id: int, provider: str, range_start: datetime, range_end: datetime) -> list[dict]:
    summaries = session.scalars(
        select(ScanSummary).where(
            ScanSummary.tenant_id == tenant_id,
            ScanSummary.scanner_type == provider,
            ScanSummary.scanned_at >= range_start,
            ScanSummary.scanned_at <= range_end,
        )
    ).all()
    if not summaries:
        return []
    summary_ids = [s.id for s in summaries]
    rows = session.query(
        FindingIndex.cve,
        FindingIndex.severity,
        func.count(func.distinct(FindingIndex.host)).label("host_count"),
        func.max(FindingIndex.cvss).label("cvss_score"),
    ).filter(
        FindingIndex.scan_summary_soc_id.in_(summary_ids),
        FindingIndex.cve.is_not(None),
        FindingIndex.cve != "",
    ).group_by(FindingIndex.cve, FindingIndex.severity).all()
    top = []
    for row in rows:
        impact = (row.host_count or 0) * (row.cvss_score or 0)
        top.append({
            "cve_id": row.cve,
            "severity": row.severity,
            "hosts_affected": row.host_count,
            "cvss_score": row.cvss_score,
            "impact_score": impact,
        })
    top.sort(key=lambda x: x["impact_score"], reverse=True)
    return top[:10]


def _build_host_exposure(session: Session, tenant_id: int, provider: str, range_start: datetime, range_end: datetime, limit: int = 10) -> list[dict]:
    summaries = session.scalars(
        select(ScanSummary).where(
            ScanSummary.tenant_id == tenant_id,
            ScanSummary.scanner_type == provider,
            ScanSummary.scanned_at >= range_start,
            ScanSummary.scanned_at <= range_end,
        )
    ).all()
    if not summaries:
        return []

    summary_ids = [s.id for s in summaries]
    rows = session.query(
        FindingIndex.host,
        FindingIndex.severity,
        func.count(FindingIndex.id).label("finding_count"),
        func.max(FindingIndex.cvss).label("max_cvss"),
        func.max(ScanSummary.scanned_at).label("last_seen"),
    ).join(
        ScanSummary, FindingIndex.scan_summary_soc_id == ScanSummary.id
    ).filter(
        FindingIndex.scan_summary_soc_id.in_(summary_ids),
        FindingIndex.host.is_not(None),
        FindingIndex.host != "",
    ).group_by(
        FindingIndex.host,
        FindingIndex.severity,
    ).all()

    by_host: dict[str, dict] = {}
    for row in rows:
        host = str(row.host)
        item = by_host.setdefault(host, {
            "host": host,
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "info": 0,
            "total_findings": 0,
            "max_cvss": 0.0,
            "last_seen": None,
        })
        severity = str(row.severity or "info").strip().lower()
        severity_key = severity if severity in {"critical", "high", "medium", "low", "info"} else "info"
        count = int(row.finding_count or 0)
        item[severity_key] += count
        item["total_findings"] += count
        item["max_cvss"] = max(float(item["max_cvss"] or 0.0), float(row.max_cvss or 0.0))
        last_seen = row.last_seen.isoformat() if row.last_seen else None
        if last_seen and (item["last_seen"] is None or last_seen > item["last_seen"]):
            item["last_seen"] = last_seen

    result = list(by_host.values())
    result.sort(
        key=lambda item: (
            -int(item["critical"]),
            -int(item["high"]),
            -float(item["max_cvss"]),
            -int(item["total_findings"]),
            item["host"],
        )
    )
    return result[:limit]


def _build_recent_noc_findings_for_provider(session: Session, tenant_id: int, provider: str, range_start: datetime, range_end: datetime, limit: int = 20) -> list[dict]:
    summaries = session.scalars(
        select(ScanSummaryNoc).where(
            ScanSummaryNoc.tenant_id == tenant_id,
            ScanSummaryNoc.scanner_type == provider,
            ScanSummaryNoc.scanned_at >= range_start,
            ScanSummaryNoc.scanned_at <= range_end,
        ).order_by(ScanSummaryNoc.scanned_at.desc()).limit(10)
    ).all()
    if not summaries:
        return []

    summary_ids = [s.id for s in summaries]
    rows = session.query(FindingIndex, ScanSummaryNoc).join(
        ScanSummaryNoc, FindingIndex.scan_summary_noc_id == ScanSummaryNoc.id
    ).filter(
        FindingIndex.scan_summary_noc_id.in_(summary_ids),
    ).order_by(
        case(
            (FindingIndex.severity.ilike("%critical%"), 1),
            (FindingIndex.severity.ilike("%high%"), 2),
            (FindingIndex.severity.ilike("%medium%"), 3),
            (FindingIndex.severity.ilike("%low%"), 4),
            else_=5,
        ),
        ScanSummaryNoc.scanned_at.desc(),
    ).limit(limit).all()
    return [
        {
            "id": finding.id,
            "cve": finding.event_type or finding.cve,
            "name": finding.name,
            "host": finding.host,
            "severity": finding.severity,
            "cvss": finding.cvss,
            "domain": finding.domain,
            "scan_id": finding.scan_id,
            "scan_summary_soc_id": finding.scan_summary_soc_id,
            "scan_summary_noc_id": finding.scan_summary_noc_id,
            "detectedAt": summary.scanned_at.isoformat() if summary.scanned_at else None,
        }
        for finding, summary in rows
    ]


def _build_top_alerts(session: Session, tenant_id: int, provider: str, range_start: datetime, range_end: datetime) -> list[dict]:
    summaries = session.scalars(
        select(ScanSummary).where(
            ScanSummary.tenant_id == tenant_id,
            ScanSummary.scanner_type == provider,
            ScanSummary.scanned_at >= range_start,
            ScanSummary.scanned_at <= range_end,
        )
    ).all()
    if not summaries:
        return []
    summary_ids = [s.id for s in summaries]
    rows = session.query(
        FindingIndex.name,
        FindingIndex.severity,
        FindingIndex.host,
        func.count(FindingIndex.id).label("occurrences"),
    ).filter(
        FindingIndex.scan_summary_soc_id.in_(summary_ids),
    ).group_by(FindingIndex.name, FindingIndex.severity, FindingIndex.host).order_by(
        func.count(FindingIndex.id).desc()
    ).limit(10).all()
    return [
        {
            "name": row.name,
            "severity": row.severity,
            "host": row.host,
            "occurrences": row.occurrences,
        }
        for row in rows
    ]


def _build_scan_cut_trend(session: Session, tenant_id: int, provider: str, range_start: datetime, range_end: datetime, limit: int = 20) -> list[dict]:
    scans = list(
        session.scalars(
            select(ScanSummary).where(
                ScanSummary.tenant_id == tenant_id,
                ScanSummary.scanner_type == provider,
                ScanSummary.scanned_at >= range_start,
                ScanSummary.scanned_at <= range_end,
            ).order_by(ScanSummary.scanned_at.asc()).limit(limit)
        )
    )
    return [
        {
            "scan_summary_id": scan.id,
            "scan_id": scan.scan_id,
            "scan_name": scan.scan_name,
            "display_label": scan.scan_name or (scan.scanned_at.isoformat() if scan.scanned_at else f"Cut {scan.id}"),
            "status": scan.status,
            "scanned_at": scan.scanned_at.isoformat() if scan.scanned_at else None,
            "critical": int(scan.critical_count or 0),
            "high": int(scan.high_count or 0),
            "medium": int(scan.medium_count or 0),
            "low": int(scan.low_count or 0),
            "info": int(scan.info_count or 0),
            "total": int((scan.critical_count or 0) + (scan.high_count or 0) + (scan.medium_count or 0) + (scan.low_count or 0) + (scan.info_count or 0)),
        }
        for scan in scans
    ]


def _build_latest_soc_snapshot(session: Session, latest_scan: ScanSummary | None) -> dict | None:
    if not latest_scan:
        return None

    findings = list(
        session.scalars(
            select(FindingIndex)
            .where(FindingIndex.scan_summary_soc_id == latest_scan.id)
            .order_by(FindingIndex.created_at.desc())
        )
    )

    top_rules_counter: dict[str, int] = {}
    top_agents_counter: dict[str, int] = {}
    for finding in findings:
        rule_name = str(finding.name or "").strip()
        host_name = str(finding.host or "").strip()
        if rule_name:
            top_rules_counter[rule_name] = top_rules_counter.get(rule_name, 0) + 1
        if host_name:
            top_agents_counter[host_name] = top_agents_counter.get(host_name, 0) + 1

    top_rules = [
        {"name": name, "count": count}
        for name, count in sorted(top_rules_counter.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]
    top_agents = [
        {"name": name, "count": count}
        for name, count in sorted(top_agents_counter.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]

    meta = latest_scan.meta_info if isinstance(latest_scan.meta_info, dict) else {}
    return {
        "scan_summary_id": latest_scan.id,
        "scan_id": latest_scan.scan_id,
        "scan_name": latest_scan.scan_name,
        "status": latest_scan.status,
        "scanned_at": latest_scan.scanned_at.isoformat() if latest_scan.scanned_at else None,
        "severity_totals": {
            "critical": int(latest_scan.critical_count or 0),
            "high": int(latest_scan.high_count or 0),
            "medium": int(latest_scan.medium_count or 0),
            "low": int(latest_scan.low_count or 0),
            "info": int(latest_scan.info_count or 0),
            "total": int((latest_scan.critical_count or 0) + (latest_scan.high_count or 0) + (latest_scan.medium_count or 0) + (latest_scan.low_count or 0) + (latest_scan.info_count or 0)),
        },
        "total_hosts": int(latest_scan.total_hosts or 0),
        "cvss_max": float(latest_scan.cvss_max or 0),
        "top_rules": top_rules,
        "top_agents": top_agents,
        "manager_status": meta.get("manager_status"),
    }


def _build_scan_rule_and_agent_highlights(session: Session, scan_summary_id: int, limit: int = 5) -> tuple[list[dict], list[dict]]:
    findings = list(
        session.scalars(
            select(FindingIndex)
            .where(FindingIndex.scan_summary_soc_id == scan_summary_id)
            .order_by(FindingIndex.created_at.desc())
        )
    )

    top_rules_counter: dict[str, int] = {}
    top_agents_counter: dict[str, int] = {}
    for finding in findings:
        rule_name = str(finding.name or "").strip()
        host_name = str(finding.host or "").strip()
        if rule_name:
            top_rules_counter[rule_name] = top_rules_counter.get(rule_name, 0) + 1
        if host_name:
            top_agents_counter[host_name] = top_agents_counter.get(host_name, 0) + 1

    top_rules = [
        {"name": name, "count": count}
        for name, count in sorted(top_rules_counter.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]
    top_agents = [
        {"name": name, "count": count}
        for name, count in sorted(top_agents_counter.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]
    return top_rules, top_agents


def _build_recent_findings_for_provider(session: Session, tenant_id: int, provider: str, range_start: datetime, range_end: datetime, limit: int = 20) -> list[dict]:
    summaries = session.scalars(
        select(ScanSummary).where(
            ScanSummary.tenant_id == tenant_id,
            ScanSummary.scanner_type == provider,
            ScanSummary.scanned_at >= range_start,
            ScanSummary.scanned_at <= range_end,
        ).order_by(ScanSummary.scanned_at.desc()).limit(10)
    ).all()
    if not summaries:
        return []
    summary_ids = [s.id for s in summaries]
    rows = session.query(FindingIndex, ScanSummary).join(
        ScanSummary, FindingIndex.scan_summary_soc_id == ScanSummary.id
    ).filter(
        FindingIndex.scan_summary_soc_id.in_(summary_ids),
    ).order_by(
        case(
            (FindingIndex.severity.ilike("%critical%"), 1),
            (FindingIndex.severity.ilike("%high%"), 2),
            (FindingIndex.severity.ilike("%medium%"), 3),
            (FindingIndex.severity.ilike("%low%"), 4),
            else_=5,
        ),
        ScanSummary.scanned_at.desc(),
    ).limit(limit).all()
    return [
        {
            "id": finding.id,
            "cve": finding.cve,
            "name": finding.name,
            "host": finding.host,
            "severity": finding.severity,
            "cvss": finding.cvss,
            "domain": finding.domain,
            "scan_id": finding.scan_id,
            "scan_summary_soc_id": finding.scan_summary_soc_id,
            "scan_summary_noc_id": finding.scan_summary_noc_id,
            "scan_summary_id": finding.scan_summary_soc_id,
            "detected_at": summary.scanned_at.isoformat() if summary.scanned_at else None,
            "detectedAt": summary.scanned_at.isoformat() if summary.scanned_at else None,
        }
        for finding, summary in rows
    ]


def _build_provider_scans(session: Session, tenant_id: int, provider: str, range_start: datetime, range_end: datetime, limit: int = 10) -> list[dict]:
    scans = _recent_soc_scans(session, tenant_id, provider, limit=limit)
    result = []
    for scan in scans:
        meta = scan.meta_info if isinstance(scan.meta_info, dict) else {}
        top_rules, top_agents = _build_scan_rule_and_agent_highlights(session, scan.id)
        result.append({
            "id": scan.id,
            "scan_id": scan.scan_id,
            "scan_name": scan.scan_name,
            "status": scan.status,
            "scanned_at": scan.scanned_at.isoformat() if scan.scanned_at else None,
            "critical_count": scan.critical_count,
            "high_count": scan.high_count,
            "medium_count": scan.medium_count,
            "low_count": scan.low_count,
            "info_count": scan.info_count,
            "total_events": int((scan.critical_count or 0) + (scan.high_count or 0) + (scan.medium_count or 0) + (scan.low_count or 0) + (scan.info_count or 0)),
            "total_hosts": scan.total_hosts,
            "cvss_max": scan.cvss_max,
            "send_reason": meta.get("send_reason"),
            "snapshot_mode": meta.get("snapshot_mode"),
            "top_rules": top_rules,
            "top_agents": top_agents,
        })
    return result


def _build_noc_scans(session: Session, tenant_id: int, provider: str, limit: int = 10) -> list[dict]:
    scans = _recent_noc_scans(session, tenant_id, provider, limit=limit)
    return [
        {
            "id": scan.id,
            "scan_id": scan.scan_id,
            "status": scan.status,
            "scanned_at": scan.scanned_at.isoformat() if scan.scanned_at else None,
            "critical_count": scan.critical_count,
            "high_count": scan.high_count,
            "medium_count": scan.medium_count,
            "low_count": scan.low_count,
            "info_count": scan.info_count,
            "total_hosts": scan.total_hosts,
        }
        for scan in scans
    ]


def _build_agent_info(session: Session, tenant_id: int, provider: str, domain: str) -> dict | None:
    if domain == "noc":
        latest = _latest_noc_scan(session, tenant_id, provider)
    else:
        latest = _latest_soc_scan(session, tenant_id, provider)
    if not latest or not latest.agent_api_key_id:
        return None
    agent = session.get(AgentApiKey, latest.agent_api_key_id)
    if not agent:
        return None
    return {
        "name": agent.name,
        "lastUsed": agent.last_used_at.isoformat() if agent.last_used_at else None,
    }


def _build_vuln_provider_dashboard(session: Session, tenant_id: int, provider: str, range_info: dict) -> dict:
    start = range_info["start"]
    end = range_info["end"]
    scanner = provider
    meta = _provider_meta(provider)
    status = _base_status(session, tenant_id, provider, scanner)
    if not status["configured"]:
        return _empty_provider_dashboard(provider, f"{meta['label']} integration not configured for this company")

    latest_scan = _latest_soc_scan(session, tenant_id, scanner)
    recent_scans = _recent_soc_scans(session, tenant_id, scanner, limit=30)
    latest_counts = _scan_counts(latest_scan) if latest_scan else {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    completed = sum(1 for s in recent_scans if (s.status or "").lower() == "completed")
    running = sum(1 for s in recent_scans if (s.status or "").lower() == "running")
    unique_hosts = int(latest_scan.total_hosts or 0) if latest_scan else 0
    trend = _build_vuln_trend(session, tenant_id, scanner, start, end)
    top_cves = _build_top_cves(session, tenant_id, scanner, start, end)
    host_exposure = _build_host_exposure(session, tenant_id, scanner, start, end)
    recent_findings = _build_recent_findings_for_provider(session, tenant_id, scanner, start, end)
    scan_rows = _build_provider_scans(session, tenant_id, scanner, start, end)
    agent_info = _build_agent_info(session, tenant_id, provider, "soc")
    total_findings = sum(int(latest_counts.get(key, 0) or 0) for key in ("critical", "high", "medium", "low", "info"))

    return {
        **meta,
        "configured": status["configured"],
        "active": status["active"],
        "has_data": latest_scan is not None,
        "last_sync": latest_scan.scanned_at.isoformat() if latest_scan else None,
        "range": {"preset": range_info["preset"], "from": range_info["from"], "to": range_info["to"]},
        "summary": latest_counts,
        "kpis": {
            "total_findings": total_findings,
            "critical": int(latest_counts.get("critical", 0) or 0),
            "high": int(latest_counts.get("high", 0) or 0),
            "hosts_scanned": unique_hosts,
            "scans_completed": completed,
            "scans_running": running,
            "total_scans": len(recent_scans),
            "cvss_max": float(latest_scan.cvss_max or 0) if latest_scan else 0,
        },
        "charts": {
            "trend": trend,
            "top_cves": top_cves,
            "host_exposure": host_exposure,
        },
        "tables": {
            "recent_findings": recent_findings,
            "recent_scans": scan_rows,
        },
        "agentInfo": agent_info,
    }


def _build_wazuh_dashboard(session: Session, tenant_id: int, range_info: dict) -> dict:
    start = range_info["start"]
    end = range_info["end"]
    status = _base_status(session, tenant_id, "wazuh", "wazuh")
    latest_scan = _latest_soc_scan(session, tenant_id, "wazuh")
    if not status["configured"]:
        return _empty_provider_dashboard("wazuh", "Wazuh integration not configured for this company")

    provider_meta = _provider_meta("wazuh")
    meta = latest_scan.meta_info if latest_scan and isinstance(latest_scan.meta_info, dict) else {}
    agent_meta = meta.get("agents") if isinstance(meta.get("agents"), dict) else {}
    recent = [finding.to_dict() for finding in _recent_findings(session, latest_scan.id)] if latest_scan else []
    trend = _build_scan_cut_trend(session, tenant_id, "wazuh", start, end)
    recent_scans = _build_provider_scans(session, tenant_id, "wazuh", start, end)
    agent_info = _build_agent_info(session, tenant_id, "wazuh", "soc")
    snapshot = _build_latest_soc_snapshot(session, latest_scan)
    total_alerts = sum(_scan_counts(latest_scan).values()) if latest_scan else 0
    active_agents = int(agent_meta.get("active", latest_scan.total_hosts if latest_scan else 0)) if latest_scan else 0

    return {
        **provider_meta,
        "configured": True,
        "active": status["active"],
        "has_data": latest_scan is not None,
        "last_sync": latest_scan.scanned_at.isoformat() if latest_scan else None,
        "range": {"preset": range_info["preset"], "from": range_info["from"], "to": range_info["to"]},
        "snapshot": snapshot,
        "summary": {
            "alerts": {
                "total": sum(_scan_counts(latest_scan).values()) if latest_scan else 0,
                **(_scan_counts(latest_scan) if latest_scan else {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}),
            },
            "agents": {
                "total": int(agent_meta.get("total", latest_scan.total_hosts if latest_scan else 0)) if latest_scan else 0,
                "active": int(agent_meta.get("active", latest_scan.total_hosts if latest_scan else 0)) if latest_scan else 0,
                "disconnected": int(agent_meta.get("disconnected", 0)),
                "never_connected": int(agent_meta.get("never_connected", 0)),
            },
        },
        "kpis": {
            "manager_status": meta.get("manager_status", "healthy" if latest_scan else "unknown"),
            "alerts_active": total_alerts,
            "agents_active": active_agents,
        },
        "charts": {
            "trend": trend,
        },
        "tables": {
            "recent_findings": recent,
            "recent_scans": recent_scans,
        },
        "agentInfo": agent_info,
    }


def _build_zabbix_dashboard(session: Session, tenant_id: int, range_info: dict) -> dict:
    status = _base_status(session, tenant_id, "zabbix", "zabbix")
    latest_scan = _latest_noc_scan(session, tenant_id, "zabbix")
    if not status["configured"]:
        return _empty_provider_dashboard("zabbix", "Zabbix integration not configured for this company")

    provider_meta = _provider_meta("zabbix")
    scan_meta = latest_scan.meta_info if latest_scan and isinstance(latest_scan.meta_info, dict) else {}
    metrics = scan_meta.get("metrics") if isinstance(scan_meta.get("metrics"), dict) else {}
    hosts = scan_meta.get("hosts") if isinstance(scan_meta.get("hosts"), list) else []
    recent_alerts = [event.to_dict() for event in _recent_noc_events(session, latest_scan.id)] if latest_scan else []
    range_start = range_info["start"]
    range_end = range_info["end"]
    trend = _build_noc_trend(session, tenant_id, "zabbix", range_start, range_end)
    scan_rows = _build_noc_scans(session, tenant_id, "zabbix")
    agent_info = _build_agent_info(session, tenant_id, "zabbix", "noc")

    return {
        **provider_meta,
        "configured": True,
        "active": status["active"],
        "has_data": latest_scan is not None,
        "last_sync": latest_scan.scanned_at.isoformat() if latest_scan else None,
        "range": {"preset": range_info["preset"], "from": range_info["from"], "to": range_info["to"]},
        "summary": {
            "alerts": sum(_scan_counts(latest_scan).values()) if latest_scan else 0,
            "hosts_monitored": int(latest_scan.total_hosts or 0) if latest_scan else 0,
        },
        "kpis": {
            "alerts_active": sum(_scan_counts(latest_scan).values()) if latest_scan else 0,
            "hosts_monitored": int(latest_scan.total_hosts or 0) if latest_scan else 0,
            "avg_cpu": float(metrics.get("avg_cpu", scan_meta.get("avg_cpu", 0.0))) if latest_scan else 0.0,
            "avg_ram": float(metrics.get("avg_ram", scan_meta.get("avg_ram", 0.0))) if latest_scan else 0.0,
        },
        "charts": {
            "trend": trend,
        },
        "tables": {
            "recent_alerts": recent_alerts,
            "hosts": hosts,
            "recent_scans": scan_rows,
        },
        "agentInfo": agent_info,
    }


def _build_uptime_kuma_dashboard(session: Session, tenant_id: int, range_info: dict) -> dict:
    status = _base_status(session, tenant_id, "uptime_kuma", "uptime_kuma")
    latest_scan = _latest_noc_scan(session, tenant_id, "uptime_kuma")
    if not status["configured"]:
        return _empty_provider_dashboard("uptime_kuma", "Uptime Kuma integration not configured for this company")

    provider_meta = _provider_meta("uptime_kuma")
    meta = latest_scan.meta_info if latest_scan and isinstance(latest_scan.meta_info, dict) else {}
    services = meta.get("services") if isinstance(meta.get("services"), dict) else {}
    total = int(services.get("total", latest_scan.total_hosts if latest_scan else 0)) if latest_scan else 0
    up_services = int(services.get("up", max(0, total - int(services.get("down", 0))))) if latest_scan else 0
    down = int(services.get("down", 0)) if latest_scan else 0
    pending = int(services.get("pending", 0)) if latest_scan else 0
    uptime = float(meta.get("uptime_percentage", services.get("uptime_percentage", 0.0))) if latest_scan else 0.0
    range_start = range_info["start"]
    range_end = range_info["end"]
    trend = _build_noc_trend(session, tenant_id, "uptime_kuma", range_start, range_end)
    scan_rows = _build_noc_scans(session, tenant_id, "uptime_kuma")
    recent_events = _build_recent_noc_findings_for_provider(session, tenant_id, "uptime_kuma", range_start, range_end)
    agent_info = _build_agent_info(session, tenant_id, "uptime_kuma", "noc")

    return {
        **provider_meta,
        "configured": True,
        "active": status["active"],
        "has_data": latest_scan is not None,
        "last_sync": latest_scan.scanned_at.isoformat() if latest_scan else None,
        "range": {"preset": range_info["preset"], "from": range_info["from"], "to": range_info["to"]},
        "summary": {
            "services": {"total": total, "up": up_services, "down": down, "pending": pending},
            "uptime_percentage": uptime,
        },
        "kpis": {
            "status": "healthy" if down == 0 else "degraded",
            "services_monitored": total,
            "services_down": down,
            "services_up": up_services,
            "uptime_percentage": uptime,
        },
        "charts": {
            "trend": trend,
        },
        "tables": {
            "recent_events": recent_events,
            "recent_scans": scan_rows,
        },
        "agentInfo": agent_info,
    }


def _integration_status_text(*, configured: bool, active: bool, has_data: bool) -> str:
    if not configured:
        return "Pendiente de configurar"
    if active and has_data:
        return "Activo y sincronizado"
    if active:
        return "Activo sin datos"
    return "Configurado sin agente activo"


def _build_integration_summary_slots(provider: str, item: dict) -> list[dict]:
    if provider in _VULN_PROVIDERS:
        vulnerabilities = item.get("vulnerabilities") or item.get("summary") or {}
        total_vulns = sum(int(vulnerabilities.get(key, 0) or 0) for key in ("critical", "high", "medium", "low", "info"))
        scans_total = int((item.get("scans") or {}).get("total", 0) or 0)
        return [
            {"label": "Escaneos", "value": scans_total},
            {"label": "Vulns", "value": total_vulns},
            {"label": "Criticas", "value": int(vulnerabilities.get("critical", 0) or 0), "danger": True},
        ]

    if provider == "wazuh":
        alerts = item.get("alerts") or {}
        critical = int(alerts.get("critical", 0) or 0)
        high = int(alerts.get("high", 0) or 0)
        medium = int(alerts.get("medium", 0) or 0)
        low = int(alerts.get("low", 0) or 0)
        agents = item.get("agents") or {}
        return [
            {"label": "Agentes", "value": int(agents.get("active", 0) or 0)},
            {"label": "Alertas", "value": critical + high + medium + low},
            {"label": "Crit + High", "value": critical + high, "danger": True},
        ]

    if provider == "zabbix":
        return [
            {"label": "Hosts", "value": int(item.get("hosts_monitored", 0) or 0)},
            {"label": "Alertas", "value": int(item.get("alerts", 0) or 0), "danger": True},
            {"label": "Online", "value": int(item.get("hosts_monitored", 0) or 0)},
        ]

    if provider == "uptime_kuma":
        services = item.get("services") or {}
        return [
            {"label": "Monitores", "value": int(services.get("total", 0) or 0)},
            {"label": "Online", "value": int(services.get("up", 0) or 0)},
            {"label": "Caidos", "value": int(services.get("down", 0) or 0), "danger": True},
        ]

    return []


def _build_home_integration_status(integrations_block: dict, preferences: dict | None = None) -> list[dict]:
    preferences = preferences or {}
    visible_providers = set(get_visible_integration_providers(preferences)) or set(_HOME_PROVIDER_META.keys())
    hide_unconfigured = hide_unconfigured_providers(preferences)
    result = []
    for provider in ("openvas", "insightvm", "nessus", "wazuh", "zabbix", "uptime_kuma"):
        if provider not in visible_providers:
            continue
        item = integrations_block.get(provider) or {}
        meta = _HOME_PROVIDER_META[provider]
        configured = bool(item.get("configured"))
        active = bool(item.get("active"))
        has_data = bool(item.get("has_data"))
        if hide_unconfigured and not configured:
            continue
        result.append(
            {
                "provider": provider,
                "label": meta["label"],
                "navigation_slug": meta["navigation_slug"],
                "configured": configured,
                "active": active,
                "has_data": has_data,
                "last_sync": item.get("last_sync"),
                "agent_name": item.get("agent_name") or (item.get("agentInfo") or {}).get("name"),
                "status_text": _integration_status_text(configured=configured, active=active, has_data=has_data),
                "summary_slots": _build_integration_summary_slots(provider, item),
            }
        )
    return result


def build_home_dashboard(session: Session, tenant_id: int) -> dict:
    preferences = get_tenant_preferences(session, tenant_id)
    integrations_block = build_dashboard_summary(session, tenant_id, preferences)
    ticket_status_rows = session.query(
        Ticket.status,
        func.count(Ticket.id),
    ).filter(
        Ticket.tenant_id == tenant_id,
    ).group_by(Ticket.status).all()

    ticket_status_counts = {
        str(status or "UNKNOWN"): int(count or 0)
        for status, count in ticket_status_rows
    }
    total_tickets = sum(ticket_status_counts.values())
    manual_pending = sum(
        count for status, count in ticket_status_counts.items()
        if status not in {"EXECUTED", "RESUELTO"}
    )
    automated_completed = session.query(func.count(Ticket.id)).filter(
        Ticket.tenant_id == tenant_id,
        (Ticket.status == "EXECUTED") | (Ticket.execution_status == "COMPLETED"),
    ).scalar() or 0

    return {
        "preferences": preferences,
        "integration_status": _build_home_integration_status(integrations_block, preferences),
        "summary": integrations_block.get("summary", {}),
        "ticket_counts": {
            "total": int(total_tickets),
            "by_status": ticket_status_counts,
            "manual_pending": int(manual_pending),
            "automated_completed": int(automated_completed),
        },
        "generated_at": datetime.utcnow().isoformat(),
    }


def build_provider_dashboard(session: Session, tenant_id: int, provider: str, preset: str | None = None, from_date: str | None = None, to_date: str | None = None) -> dict:
    provider = _canonical_provider(provider)
    if provider not in _SUPPORTED_PROVIDERS:
        return {"error": f"Unsupported provider: {provider}", "supported": list(_SUPPORTED_PROVIDERS)}

    preferences = get_tenant_preferences(session, tenant_id)
    visible_providers = set(get_visible_integration_providers(preferences)) or set(_SUPPORTED_PROVIDERS)
    if _provider_visibility_key(provider) not in visible_providers and provider not in visible_providers:
        raise NotFoundError("Provider dashboard not enabled for this tenant")

    range_info = _parse_range(preset, from_date, to_date)

    if provider in _VULN_PROVIDERS:
        return _build_vuln_provider_dashboard(session, tenant_id, provider, range_info)
    elif provider == "wazuh":
        return _build_wazuh_dashboard(session, tenant_id, range_info)
    elif provider == "zabbix":
        return _build_zabbix_dashboard(session, tenant_id, range_info)
    elif provider == "uptime_kuma":
        return _build_uptime_kuma_dashboard(session, tenant_id, range_info)

    return {"error": "Unsupported provider"}
