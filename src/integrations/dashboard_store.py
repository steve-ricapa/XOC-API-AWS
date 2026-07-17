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

_SUPPORTED_PROVIDERS = {"openvas", "insightvm", "nessus", "wazuh", "zabbix", "uptime_kuma"}
_VULN_PROVIDERS = {"openvas", "insightvm", "nessus"}
_NOC_PROVIDERS = {"zabbix", "uptime_kuma"}


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
            "detectedAt": summary.scanned_at.isoformat() if summary.scanned_at else None,
        }
        for finding, summary in rows
    ]


def _build_provider_scans(session: Session, tenant_id: int, provider: str, range_start: datetime, range_end: datetime, limit: int = 10) -> list[dict]:
    scans = _recent_soc_scans(session, tenant_id, provider, limit=limit)
    return [
        {
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
            "total_hosts": scan.total_hosts,
            "cvss_max": scan.cvss_max,
        }
        for scan in scans
    ]


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
    status = _base_status(session, tenant_id, provider, provider)
    latest_scan = _latest_soc_scan(session, tenant_id, provider)
    recent_scans = _recent_soc_scans(session, tenant_id, provider, limit=30)
    latest_counts = _scan_counts(latest_scan) if latest_scan else {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    completed = sum(1 for s in recent_scans if (s.status or "").lower() == "completed")
    running = sum(1 for s in recent_scans if (s.status or "").lower() == "running")
    unique_hosts = int(latest_scan.total_hosts or 0) if latest_scan else 0
    trend = _build_vuln_trend(session, tenant_id, provider, start, end)
    top_cves = _build_top_cves(session, tenant_id, provider, start, end)
    recent_findings = _build_recent_findings_for_provider(session, tenant_id, provider, start, end)
    scan_rows = _build_provider_scans(session, tenant_id, provider, start, end)
    agent_info = _build_agent_info(session, tenant_id, provider, "soc")

    return {
        "provider": provider,
        "configured": status["configured"],
        "active": status["active"],
        "has_data": latest_scan is not None,
        "last_sync": latest_scan.scanned_at.isoformat() if latest_scan else None,
        "range": {"preset": range_info["preset"], "from": range_info["from"], "to": range_info["to"]},
        "summary": latest_counts,
        "kpis": {
            "hosts_scanned": unique_hosts,
            "scans_completed": completed,
            "scans_running": running,
            "total_scans": len(recent_scans),
            "cvss_max": float(latest_scan.cvss_max or 0) if latest_scan else 0,
        },
        "charts": {
            "trend": trend,
            "top_cves": top_cves,
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
        return {"provider": "wazuh", "configured": False, "message": "Wazuh integration not configured for this company"}

    meta = latest_scan.meta_info if latest_scan and isinstance(latest_scan.meta_info, dict) else {}
    agent_meta = meta.get("agents") if isinstance(meta.get("agents"), dict) else {}
    recent = [finding.to_dict() for finding in _recent_findings(session, latest_scan.id)] if latest_scan else []
    trend = _build_vuln_trend(session, tenant_id, "wazuh", start, end)
    top_alerts = _build_top_alerts(session, tenant_id, "wazuh", start, end)
    agent_info = _build_agent_info(session, tenant_id, "wazuh", "soc")

    return {
        "provider": "wazuh",
        "configured": True,
        "active": status["active"],
        "has_data": latest_scan is not None,
        "last_sync": latest_scan.scanned_at.isoformat() if latest_scan else None,
        "range": {"preset": range_info["preset"], "from": range_info["from"], "to": range_info["to"]},
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
        },
        "charts": {
            "trend": trend,
            "top_alerts": top_alerts,
        },
        "tables": {
            "recent_findings": recent,
        },
        "agentInfo": agent_info,
    }


def _build_zabbix_dashboard(session: Session, tenant_id: int, range_info: dict) -> dict:
    status = _base_status(session, tenant_id, "zabbix", "zabbix")
    latest_scan = _latest_noc_scan(session, tenant_id, "zabbix")
    if not status["configured"]:
        return {"provider": "zabbix", "configured": False, "message": "Zabbix integration not configured for this company"}

    meta = latest_scan.meta_info if latest_scan and isinstance(latest_scan.meta_info, dict) else {}
    metrics = meta.get("metrics") if isinstance(meta.get("metrics"), dict) else {}
    hosts = meta.get("hosts") if isinstance(meta.get("hosts"), list) else []
    recent_alerts = [event.to_dict() for event in _recent_noc_events(session, latest_scan.id)] if latest_scan else []
    range_start = range_info["start"]
    range_end = range_info["end"]
    trend = _build_noc_trend(session, tenant_id, "zabbix", range_start, range_end)
    scan_rows = _build_noc_scans(session, tenant_id, "zabbix")
    agent_info = _build_agent_info(session, tenant_id, "zabbix", "noc")

    return {
        "provider": "zabbix",
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
            "avg_cpu": float(metrics.get("avg_cpu", meta.get("avg_cpu", 0.0))) if latest_scan else 0.0,
            "avg_ram": float(metrics.get("avg_ram", meta.get("avg_ram", 0.0))) if latest_scan else 0.0,
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
        return {"provider": "uptime_kuma", "configured": False, "message": "Uptime Kuma integration not configured for this company"}

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
    agent_info = _build_agent_info(session, tenant_id, "uptime_kuma", "noc")

    return {
        "provider": "uptime_kuma",
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
        },
        "charts": {
            "trend": trend,
        },
        "tables": {
            "recent_scans": scan_rows,
        },
        "agentInfo": agent_info,
    }


def build_home_dashboard(session: Session, tenant_id: int) -> dict:
    integrations_block = build_dashboard_summary(session, tenant_id)
    range_info = _parse_range("30d")
    start = range_info["start"]
    end = range_info["end"]

    all_top_cves = []
    for prov in _VULN_PROVIDERS:
        all_top_cves.extend(_build_top_cves(session, tenant_id, prov, start, end))
    all_top_cves.sort(key=lambda x: x["impact_score"], reverse=True)
    top_threats = all_top_cves[:10]

    tickets_count = session.query(func.count(Ticket.id)).filter(
        Ticket.tenant_id == tenant_id,
        Ticket.status != "resolved",
    ).scalar() or 0

    return {
        "integrations": integrations_block,
        "summary": integrations_block.get("summary", {}),
        "top_threats": top_threats,
        "tickets": {
            "open_count": tickets_count,
        },
        "generated_at": datetime.utcnow().isoformat(),
    }


def build_provider_dashboard(session: Session, tenant_id: int, provider: str, preset: str | None = None, from_date: str | None = None, to_date: str | None = None) -> dict:
    if provider not in _SUPPORTED_PROVIDERS:
        return {"error": f"Unsupported provider: {provider}", "supported": list(_SUPPORTED_PROVIDERS)}

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
