from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.persistence.models import AgentApiKey, FindingIndex, Integration, ScanSummary, ScanSummaryNoc


def _pick_integration(session: Session, tenant_id: int, provider: str) -> Integration | None:
    return session.scalar(select(Integration).where(Integration.tenant_id == tenant_id, Integration.provider == provider))


def _pick_agent_key(session: Session, tenant_id: int, integration_type: str) -> AgentApiKey | None:
    return session.scalar(
        select(AgentApiKey)
        .where(
            AgentApiKey.tenant_id == tenant_id,
            AgentApiKey.integration_type == integration_type,
            AgentApiKey.is_active == True,
        )
        .order_by(AgentApiKey.last_used_at.desc().nullslast(), AgentApiKey.created_at.desc())
    )


def _latest_soc_scan(session: Session, tenant_id: int, scanner_type: str) -> ScanSummary | None:
    return session.scalar(
        select(ScanSummary)
        .where(ScanSummary.tenant_id == tenant_id, ScanSummary.scanner_type == scanner_type)
        .order_by(ScanSummary.scanned_at.desc())
    )


def _latest_noc_scan(session: Session, tenant_id: int, scanner_type: str) -> ScanSummaryNoc | None:
    return session.scalar(
        select(ScanSummaryNoc)
        .where(ScanSummaryNoc.tenant_id == tenant_id, ScanSummaryNoc.scanner_type == scanner_type)
        .order_by(ScanSummaryNoc.scanned_at.desc())
    )


def _recent_soc_scans(session: Session, tenant_id: int, scanner_type: str, limit: int = 10) -> list[ScanSummary]:
    return list(
        session.scalars(
            select(ScanSummary)
            .where(ScanSummary.tenant_id == tenant_id, ScanSummary.scanner_type == scanner_type)
            .order_by(ScanSummary.scanned_at.desc())
            .limit(limit)
        )
    )


def _recent_noc_scans(session: Session, tenant_id: int, scanner_type: str, limit: int = 10) -> list[ScanSummaryNoc]:
    return list(
        session.scalars(
            select(ScanSummaryNoc)
            .where(ScanSummaryNoc.tenant_id == tenant_id, ScanSummaryNoc.scanner_type == scanner_type)
            .order_by(ScanSummaryNoc.scanned_at.desc())
            .limit(limit)
        )
    )


def _recent_findings(session: Session, scan_summary_id: int, limit: int = 5) -> list[FindingIndex]:
    return list(
        session.scalars(
            select(FindingIndex)
            .where(FindingIndex.scan_summary_soc_id == scan_summary_id)
            .order_by(FindingIndex.created_at.desc())
            .limit(limit)
        )
    )


def _recent_noc_events(session: Session, scan_summary_noc_id: int, limit: int = 5) -> list[FindingIndex]:
    return list(
        session.scalars(
            select(FindingIndex)
            .where(FindingIndex.scan_summary_noc_id == scan_summary_noc_id)
            .order_by(FindingIndex.created_at.desc())
            .limit(limit)
        )
    )


def _scan_counts(scan) -> dict:
    return {
        "critical": int(scan.critical_count or 0),
        "high": int(scan.high_count or 0),
        "medium": int(scan.medium_count or 0),
        "low": int(scan.low_count or 0),
        "info": int(scan.info_count or 0),
    }


def _base_status(session: Session, tenant_id: int, provider: str, integration_type: str) -> dict:
    integration = _pick_integration(session, tenant_id, provider)
    agent_key = _pick_agent_key(session, tenant_id, integration_type)
    return {
        "integration": integration,
        "agent_key": agent_key,
        "configured": integration is not None,
        "active": integration is not None and agent_key is not None,
    }


def build_wazuh_summary(session: Session, tenant_id: int) -> dict:
    status = _base_status(session, tenant_id, "wazuh", "wazuh")
    latest_scan = _latest_soc_scan(session, tenant_id, "wazuh")
    if not status["configured"]:
        return {"configured": False, "message": "Wazuh integration not configured for this company"}

    meta = latest_scan.meta_info if latest_scan and isinstance(latest_scan.meta_info, dict) else {}
    agent_meta = meta.get("agents") if isinstance(meta.get("agents"), dict) else {}
    recent = []
    if latest_scan:
        recent = [finding.to_dict() for finding in _recent_findings(session, latest_scan.id)]

    return {
        "configured": True,
        "active": status["active"],
        "has_data": latest_scan is not None,
        "last_sync": latest_scan.scanned_at.isoformat() if latest_scan else None,
        "alerts": {"total": sum(_scan_counts(latest_scan).values()) if latest_scan else 0, **(_scan_counts(latest_scan) if latest_scan else {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}), "recent": recent},
        "agents": {
            "total": int(agent_meta.get("total", latest_scan.total_hosts if latest_scan and latest_scan.total_hosts else 0)) if latest_scan else 0,
            "active": int(agent_meta.get("active", latest_scan.total_hosts if latest_scan and latest_scan.total_hosts else 0)) if latest_scan else 0,
            "disconnected": int(agent_meta.get("disconnected", 0)),
            "never_connected": int(agent_meta.get("never_connected", 0)),
        },
        "manager_status": meta.get("manager_status", "healthy" if latest_scan else "unknown"),
        "agent_name": status["agent_key"].name if status["agent_key"] else None,
    }


def build_zabbix_summary(session: Session, tenant_id: int) -> dict:
    status = _base_status(session, tenant_id, "zabbix", "zabbix")
    latest_scan = _latest_noc_scan(session, tenant_id, "zabbix")
    if not status["configured"]:
        return {"configured": False, "message": "Zabbix integration not configured for this company"}

    meta = latest_scan.meta_info if latest_scan and isinstance(latest_scan.meta_info, dict) else {}
    metrics = meta.get("metrics") if isinstance(meta.get("metrics"), dict) else {}
    recent_alerts = []
    if latest_scan:
        recent_alerts = [event.to_dict() for event in _recent_noc_events(session, latest_scan.id)]

    return {
        "configured": True,
        "active": status["active"],
        "has_data": latest_scan is not None,
        "last_sync": latest_scan.scanned_at.isoformat() if latest_scan else None,
        "alerts": sum(_scan_counts(latest_scan).values()) if latest_scan else 0,
        "hosts_monitored": int(latest_scan.total_hosts or 0) if latest_scan else 0,
        "avg_cpu": float(metrics.get("avg_cpu", meta.get("avg_cpu", 0.0))) if latest_scan else 0.0,
        "avg_ram": float(metrics.get("avg_ram", meta.get("avg_ram", 0.0))) if latest_scan else 0.0,
        "recent_alerts": recent_alerts,
        "agent_name": status["agent_key"].name if status["agent_key"] else None,
    }


def build_zabbix_detailed_metrics(session: Session, tenant_id: int) -> dict:
    status = _base_status(session, tenant_id, "zabbix", "zabbix")
    latest_scan = _latest_noc_scan(session, tenant_id, "zabbix")
    if not status["configured"]:
        return {"configured": False, "message": "Zabbix integration not configured for this company"}
    meta = latest_scan.meta_info if latest_scan and isinstance(latest_scan.meta_info, dict) else {}
    metrics = meta.get("metrics") if isinstance(meta.get("metrics"), dict) else {}
    hosts = meta.get("hosts") if isinstance(meta.get("hosts"), list) else []
    return {"configured": True, "metrics": {"avg_cpu": float(metrics.get("avg_cpu", meta.get("avg_cpu", 0.0))) if latest_scan else 0.0, "avg_ram": float(metrics.get("avg_ram", meta.get("avg_ram", 0.0))) if latest_scan else 0.0}, "hosts": hosts}


def build_vulnerability_summary(session: Session, tenant_id: int, provider: str, scanner_type: str | None = None) -> dict:
    scanner = scanner_type or provider
    status = _base_status(session, tenant_id, provider, scanner)
    recent_scans = _recent_soc_scans(session, tenant_id, scanner, limit=30)
    latest_scan = recent_scans[0] if recent_scans else None
    if not status["configured"]:
        return {"configured": False, "message": f"{provider.title()} integration not configured for this company"}

    latest_counts = _scan_counts(latest_scan) if latest_scan else {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    recent_scan_payload = [
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
        for scan in recent_scans[:10]
    ]
    latest_findings = [finding.to_dict() for finding in _recent_findings(session, latest_scan.id)] if latest_scan else []
    completed = sum(1 for scan in recent_scans if (scan.status or "").lower() == "completed")
    running = sum(1 for scan in recent_scans if (scan.status or "").lower() == "running")
    unique_hosts = len({finding.get("host") for finding in latest_findings if isinstance(finding, dict) and finding.get("host")}) or int(latest_scan.total_hosts or 0) if latest_scan else 0

    return {
        "configured": True,
        "active": status["active"],
        "has_data": latest_scan is not None,
        "last_sync": latest_scan.scanned_at.isoformat() if latest_scan else None,
        "scans": {"total": len(recent_scans), "completed": completed, "running": running, "scans": recent_scan_payload},
        "vulnerabilities": latest_counts,
        "recent_scans": recent_scan_payload,
        "hosts_scanned": unique_hosts,
        "recent_findings": latest_findings,
        "agent_name": status["agent_key"].name if status["agent_key"] else None,
    }


def build_uptime_kuma_summary(session: Session, tenant_id: int) -> dict:
    status = _base_status(session, tenant_id, "uptime_kuma", "uptime_kuma")
    latest_scan = _latest_noc_scan(session, tenant_id, "uptime_kuma")
    if not status["configured"]:
        return {"configured": False, "message": "Uptime Kuma integration not configured for this company"}

    meta = latest_scan.meta_info if latest_scan and isinstance(latest_scan.meta_info, dict) else {}
    services = meta.get("services") if isinstance(meta.get("services"), dict) else {}
    total = int(services.get("total", latest_scan.total_hosts if latest_scan and latest_scan.total_hosts else 0)) if latest_scan else 0
    up = int(services.get("up", max(0, total - int(services.get("down", 0))))) if latest_scan else 0
    down = int(services.get("down", 0)) if latest_scan else 0
    pending = int(services.get("pending", 0)) if latest_scan else 0
    uptime = float(meta.get("uptime_percentage", services.get("uptime_percentage", 0.0))) if latest_scan else 0.0

    return {
        "configured": True,
        "active": status["active"],
        "has_data": latest_scan is not None,
        "last_sync": latest_scan.scanned_at.isoformat() if latest_scan else None,
        "services": {"total": total, "up": up, "down": down, "pending": pending},
        "uptime_percentage": uptime,
        "status": "healthy" if down == 0 else "degraded",
        "agent_name": status["agent_key"].name if status["agent_key"] else None,
    }


def build_dashboard_summary(session: Session, tenant_id: int) -> dict:
    zabbix = build_zabbix_summary(session, tenant_id)
    wazuh = build_wazuh_summary(session, tenant_id)
    nessus = build_vulnerability_summary(session, tenant_id, "nessus")
    openvas = build_vulnerability_summary(session, tenant_id, "openvas")
    insightvm = build_vulnerability_summary(session, tenant_id, "insightvm")
    uptime_kuma = build_uptime_kuma_summary(session, tenant_id)

    configured_count = sum(
        1
        for item in [zabbix, wazuh, nessus, openvas, insightvm, uptime_kuma]
        if item.get("configured") and item.get("active")
    )
    total_alerts = int(zabbix.get("alerts", 0) or 0) + int((wazuh.get("alerts") or {}).get("total", 0) or 0)
    critical_vulnerabilities = sum(
        int((item.get("vulnerabilities") or {}).get("critical", 0) or 0)
        for item in [nessus, openvas, insightvm]
    )
    services_down = int((uptime_kuma.get("services") or {}).get("down", 0) or 0)

    return {
        "zabbix": zabbix,
        "wazuh": wazuh,
        "nessus": nessus,
        "openvas": openvas,
        "insightvm": insightvm,
        "uptime_kuma": uptime_kuma,
        "summary": {
            "total_integrations_configured": configured_count,
            "total_alerts": total_alerts,
            "critical_vulnerabilities": critical_vulnerabilities,
            "services_down": services_down,
            "generated_at": datetime.utcnow().isoformat(),
        },
    }
