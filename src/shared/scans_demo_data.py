from datetime import datetime, timedelta


DEMO_PROVIDERS = {
    "wazuh": {"base_id": 1100, "scan_name": "Wazuh Security Sweep"},
    "openvas": {"base_id": 1200, "scan_name": "OpenVAS Weekly Scan"},
    "insightvm": {"base_id": 1250, "scan_name": "InsightVM Rapid7"},
    "rapid7": {"base_id": 1300, "scan_name": "Rapid7 InsightVM"},
    "zabbix": {"base_id": 1400, "scan_name": "Zabbix Health Check"},
    "uptime_kuma": {"base_id": 1450, "scan_name": "Uptime Kuma Monitor"},
    "nessus": {"base_id": 1500, "scan_name": "Nessus Vulnerability Scan"},
    "tenable": {"base_id": 1600, "scan_name": "Tenable Security Audit"},
    "qualys": {"base_id": 1700, "scan_name": "Qualys Guard Review"},
    "nmap": {"base_id": 1800, "scan_name": "Nmap Discovery"},
    "other": {"base_id": 1900, "scan_name": "Security Assessment"},
}


def _base_time():
    return datetime.utcnow().replace(minute=0, second=0, microsecond=0)


def _severity_counts(provider: str, index: int) -> dict:
    base = {
        "wazuh": (3, 9, 14, 22, 30),
        "openvas": (4, 12, 18, 26, 35),
        "insightvm": (2, 7, 12, 18, 24),
        "rapid7": (2, 7, 12, 18, 24),
        "zabbix": (1, 4, 8, 12, 16),
        "uptime_kuma": (0, 1, 3, 8, 20),
        "nessus": (5, 11, 16, 24, 32),
        "tenable": (3, 8, 13, 20, 28),
        "qualys": (2, 6, 10, 15, 22),
        "nmap": (0, 2, 4, 6, 8),
        "other": (1, 3, 6, 10, 14),
    }
    critical, high, medium, low, info = base.get(provider, base["other"])
    delta = index % 3
    return {
        "critical_count": max(0, critical - delta),
        "high_count": max(0, high + delta),
        "medium_count": max(0, medium + delta),
        "low_count": max(0, low + delta),
        "info_count": max(0, info + delta),
    }


def _build_scan(provider: str, index: int, company_id: int | None) -> dict:
    config = DEMO_PROVIDERS.get(provider, DEMO_PROVIDERS["other"])
    base_time = _base_time() - timedelta(hours=index * 6)
    counts = _severity_counts(provider, index)
    scan_id = f"{provider}-demo-{index + 1}"
    total_hosts = 18 + (index * 3)
    cvss_max = 9.6 if counts["critical_count"] > 0 else 7.8

    return {
        "id": config["base_id"] + index,
        "company_id": company_id or 0,
        "agent_api_key_id": None,
        "scan_id": scan_id,
        "scanner_type": provider,
        "status": "completed",
        "critical_count": counts["critical_count"],
        "high_count": counts["high_count"],
        "medium_count": counts["medium_count"],
        "low_count": counts["low_count"],
        "info_count": counts["info_count"],
        "cvss_max": cvss_max,
        "total_hosts": total_hosts,
        "scan_name": config["scan_name"],
        "scanned_at": base_time.isoformat(),
        "received_at": (base_time + timedelta(minutes=10)).isoformat(),
        "meta_info": {"taskId": f"{provider}-task-{index + 1}", "demo": True},
    }


def demo_scans(scanner_type: str | None, days: int, limit: int, company_id: int | None) -> dict:
    providers = [scanner_type] if scanner_type else list(DEMO_PROVIDERS.keys())
    scans = []
    for provider in providers:
        if provider not in DEMO_PROVIDERS:
            continue
        for idx in range(3):
            scans.append(_build_scan(provider, idx, company_id))
    scans.sort(key=lambda item: item.get("scanned_at") or "", reverse=True)
    scans = scans[:limit]
    return {"scans": scans, "count": len(scans), "period_days": days}


def demo_latest_scans(scanner_type: str | None, days: int, company_id: int | None) -> dict:
    data = demo_scans(scanner_type, days, limit=50, company_id=company_id)
    scans = data["scans"]
    totals = {
        "critical": sum(item.get("critical_count", 0) for item in scans),
        "high": sum(item.get("high_count", 0) for item in scans),
        "medium": sum(item.get("medium_count", 0) for item in scans),
        "low": sum(item.get("low_count", 0) for item in scans),
        "info": sum(item.get("info_count", 0) for item in scans),
    }
    total_hosts = sum(item.get("total_hosts") or 0 for item in scans)
    return {
        "scans": scans,
        "count": len(scans),
        "period_days": days,
        "current_state_totals": {**totals, "total_vulnerabilities": sum(totals.values()), "total_hosts": total_hosts},
        "description": "Latest scan per unique target (demo data)",
    }


def demo_scan(scan_summary_id: int, company_id: int | None) -> dict | None:
    for provider in DEMO_PROVIDERS:
        for idx in range(3):
            scan = _build_scan(provider, idx, company_id)
            if scan["id"] == scan_summary_id:
                return scan
    return None


def demo_scan_findings(scan_summary_id: int) -> dict:
    findings = []
    for idx in range(6):
        findings.append(
            {
                "id": scan_summary_id * 100 + idx,
                "scan_summary_id": scan_summary_id,
                "scan_id": f"demo-{scan_summary_id}",
                "name": f"Demo finding {idx + 1}",
                "severity": "high" if idx < 2 else "medium",
                "cvss": 7.5 if idx < 2 else 5.2,
                "cve": f"CVE-2025-{1200 + idx}",
                "oid": None,
                "host": f"192.168.1.{20 + idx}",
                "port": "443",
                "protocol": "tcp",
                "description": "Demo finding for presentation purposes.",
                "solution": "Apply latest patches and verify configuration.",
            }
        )
    return {"findings": findings, "count": len(findings)}


def demo_scanner_analytics(scanner_type: str) -> dict:
    top_cves = [
        {"cve_id": "CVE-2025-1201", "severity": "critical", "hosts_affected": 6, "cvss_score": 9.8, "impact_score": 58.8},
        {"cve_id": "CVE-2025-1207", "severity": "high", "hosts_affected": 9, "cvss_score": 8.2, "impact_score": 73.8},
    ]
    trend_7_days = []
    for idx in range(6, -1, -1):
        day = _base_time() - timedelta(days=idx)
        trend_7_days.append({"date": day.strftime("%Y-%m-%d"), "critical": max(0, 3 - idx % 2), "high": 6 + (idx % 3), "medium": 10 + (idx % 4), "low": 14 + (idx % 5), "info": 18 + (idx % 6)})
    host_distribution = {"totalUniqueHosts": 42, "avgVulnerabilitiesPerHost": 5.2, "mostCriticalHost": {"host": "192.168.1.25", "criticalCount": 4}}
    recent_findings = [{"cve": "CVE-2025-1209", "name": "Demo remote code execution", "host": "192.168.1.41", "severity": "critical", "cvss": 9.6, "detectedAt": _base_time().isoformat()}]
    return {"success": True, "scanner_type": scanner_type, "topCVEs": top_cves, "trend_7_days": trend_7_days, "hostDistribution": host_distribution, "recentFindings": recent_findings, "agentInfo": {"name": f"{scanner_type.upper()}-DEMO", "lastUsed": _base_time().isoformat()}}


def demo_scans_summary(days: int, company_id: int | None) -> dict:
    data = demo_scans(None, days, limit=20, company_id=company_id)
    scans = data["scans"]
    totals = {
        "critical": sum(item.get("critical_count", 0) for item in scans),
        "high": sum(item.get("high_count", 0) for item in scans),
        "medium": sum(item.get("medium_count", 0) for item in scans),
        "low": sum(item.get("low_count", 0) for item in scans),
        "info": sum(item.get("info_count", 0) for item in scans),
    }
    by_scanner = {}
    for scan in scans:
        provider = scan.get("scanner_type")
        if provider:
            by_scanner[provider] = by_scanner.get(provider, 0) + 1
    trend_7_days = []
    for idx in range(7):
        day = _base_time() - timedelta(days=idx)
        trend_7_days.append({"date": day.strftime("%Y-%m-%d"), "critical": max(0, 2 - idx % 2), "high": 5 + (idx % 3), "medium": 9 + (idx % 4), "low": 12 + (idx % 5), "info": 15 + (idx % 6)})
    return {"period_days": days, "total_scans": len(scans), "vulnerability_totals": totals, "by_scanner": by_scanner, "latest_scans": scans[:5], "trend_7_days": list(reversed(trend_7_days)), "timestamp": _base_time().isoformat()}
