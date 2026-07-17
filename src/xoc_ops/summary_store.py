from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.persistence.models import AgentApiKey, AgentInstance, Alert, Integration, ScanSummary, ScanSummaryNoc, Tenant, Ticket, Vulnerability


RUNNING_TICKET_STATUSES = {"RUNNING", "EN_EJECUCION", "PENDIENTE_EJECUCION"}
OPEN_TICKET_STATUSES = {"PENDING", "PREAPROBADO", "APROBADO", "RECHAZADO", "RUNNING", "EN_EJECUCION", "PENDIENTE_EJECUCION"}


def _integration_key(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"uptime", "uptime-kuma", "uptimekuma"}:
        return "uptime_kuma"
    return normalized


def _build_status(*, plan_status: str, critical_incidents: int, degraded_integrations: int, operational_health_score: int, integrations_count: int, agents_count: int) -> str:
    normalized_plan = (plan_status or "").strip().upper()
    if normalized_plan == "INACTIVE" or (integrations_count == 0 and agents_count == 0):
        return "inactive"
    if critical_incidents > 0 or operational_health_score < 50:
        return "critical"
    if degraded_integrations > 0 or operational_health_score < 80:
        return "warning"
    return "healthy"


def _health_score(*, critical_incidents: int, degraded_integrations: int, pending_tickets: int, operations_running: int, plan_status: str) -> int:
    normalized_plan = (plan_status or "").strip().upper()
    if normalized_plan == "INACTIVE":
        return 15
    score = 100
    score -= min(50, critical_incidents * 20)
    score -= min(25, degraded_integrations * 10)
    score -= min(15, pending_tickets * 2)
    score -= min(10, operations_running * 2)
    return max(0, min(100, score))


def _build_last_relevant_event(
    *,
    critical_alert_title: str | None,
    pending_ticket_subject: str | None,
    latest_soc_scanner: str | None,
    latest_noc_scanner: str | None,
) -> str:
    if critical_alert_title:
        return f"Alerta critica activa: {critical_alert_title}"
    if pending_ticket_subject:
        return f"Ticket pendiente: {pending_ticket_subject}"
    if latest_soc_scanner:
        return f"Ultimo corte SOC completado en {latest_soc_scanner}"
    if latest_noc_scanner:
        return f"Ultimo corte NOC completado en {latest_noc_scanner}"
    return "Sin eventos recientes"


def _group_count(session: Session, stmt, key_index: int = 0, value_index: int = 1) -> dict[int, int]:
    rows = session.execute(stmt).all()
    return {int(row[key_index]): int(row[value_index] or 0) for row in rows}


def list_xoc_clients(session: Session) -> list[dict]:
    tenants = list(session.scalars(select(Tenant).order_by(Tenant.name.asc())))
    if not tenants:
        return []

    tenant_ids = [tenant.id for tenant in tenants]

    integrations_rows = session.execute(
        select(Integration.tenant_id, Integration.provider).where(Integration.tenant_id.in_(tenant_ids))
    ).all()
    integrations_by_tenant: dict[int, list[str]] = {tenant_id: [] for tenant_id in tenant_ids}
    for tenant_id, provider in integrations_rows:
        integrations_by_tenant[int(tenant_id)].append(_integration_key(provider))

    active_agent_rows = session.execute(
        select(AgentApiKey.tenant_id, AgentApiKey.integration_type)
        .where(AgentApiKey.tenant_id.in_(tenant_ids), AgentApiKey.is_active.is_(True))
    ).all()
    active_agents_by_tenant: dict[int, set[str]] = {tenant_id: set() for tenant_id in tenant_ids}
    for tenant_id, integration_type in active_agent_rows:
        active_agents_by_tenant[int(tenant_id)].add(_integration_key(integration_type))

    integrations_count = {tenant_id: len(providers) for tenant_id, providers in integrations_by_tenant.items()}
    agents_count = {tenant_id: len(providers) for tenant_id, providers in active_agents_by_tenant.items()}
    degraded_integrations = {
        tenant_id: sum(1 for provider in providers if provider not in active_agents_by_tenant.get(tenant_id, set()))
        for tenant_id, providers in integrations_by_tenant.items()
    }

    open_tickets = _group_count(
        session,
        select(Ticket.tenant_id, func.count(Ticket.id)).where(Ticket.tenant_id.in_(tenant_ids), Ticket.status.in_(OPEN_TICKET_STATUSES)).group_by(Ticket.tenant_id),
    )
    pending_tickets = _group_count(
        session,
        select(Ticket.tenant_id, func.count(Ticket.id)).where(Ticket.tenant_id.in_(tenant_ids), Ticket.status == "PENDING").group_by(Ticket.tenant_id),
    )
    operations_running = _group_count(
        session,
        select(Ticket.tenant_id, func.count(Ticket.id)).where(Ticket.tenant_id.in_(tenant_ids), Ticket.status.in_(RUNNING_TICKET_STATUSES)).group_by(Ticket.tenant_id),
    )
    critical_alerts = _group_count(
        session,
        select(Alert.tenant_id, func.count(Alert.id)).where(Alert.tenant_id.in_(tenant_ids), Alert.status == "active", Alert.severity == "critical").group_by(Alert.tenant_id),
    )
    critical_vulns = _group_count(
        session,
        select(Vulnerability.tenant_id, func.count(Vulnerability.id)).where(Vulnerability.tenant_id.in_(tenant_ids), Vulnerability.status == "open", Vulnerability.severity == "critical").group_by(Vulnerability.tenant_id),
    )
    agent_instances = _group_count(
        session,
        select(AgentInstance.tenant_id, func.count(AgentInstance.id)).where(AgentInstance.tenant_id.in_(tenant_ids), AgentInstance.status != "DISABLED").group_by(AgentInstance.tenant_id),
    )

    latest_critical_alert_rows = session.execute(
        select(Alert.tenant_id, Alert.title, Alert.created_at)
        .where(Alert.tenant_id.in_(tenant_ids), Alert.status == "active", Alert.severity == "critical")
        .order_by(Alert.tenant_id.asc(), Alert.created_at.desc())
    ).all()
    latest_critical_alert_by_tenant: dict[int, str] = {}
    for tenant_id, title, _ in latest_critical_alert_rows:
        latest_critical_alert_by_tenant.setdefault(int(tenant_id), title)

    latest_pending_ticket_rows = session.execute(
        select(Ticket.tenant_id, Ticket.subject, Ticket.created_at)
        .where(Ticket.tenant_id.in_(tenant_ids), Ticket.status == "PENDING")
        .order_by(Ticket.tenant_id.asc(), Ticket.created_at.desc())
    ).all()
    latest_pending_ticket_by_tenant: dict[int, str] = {}
    for tenant_id, subject, _ in latest_pending_ticket_rows:
        latest_pending_ticket_by_tenant.setdefault(int(tenant_id), subject)

    latest_soc_scan_rows = session.execute(
        select(ScanSummary.tenant_id, ScanSummary.scanner_type, ScanSummary.scanned_at)
        .where(ScanSummary.tenant_id.in_(tenant_ids))
        .order_by(ScanSummary.tenant_id.asc(), ScanSummary.scanned_at.desc())
    ).all()
    latest_soc_scan_by_tenant: dict[int, str] = {}
    for tenant_id, scanner_type, _ in latest_soc_scan_rows:
        latest_soc_scan_by_tenant.setdefault(int(tenant_id), scanner_type)

    latest_noc_scan_rows = session.execute(
        select(ScanSummaryNoc.tenant_id, ScanSummaryNoc.scanner_type, ScanSummaryNoc.scanned_at)
        .where(ScanSummaryNoc.tenant_id.in_(tenant_ids))
        .order_by(ScanSummaryNoc.tenant_id.asc(), ScanSummaryNoc.scanned_at.desc())
    ).all()
    latest_noc_scan_by_tenant: dict[int, str] = {}
    for tenant_id, scanner_type, _ in latest_noc_scan_rows:
        latest_noc_scan_by_tenant.setdefault(int(tenant_id), scanner_type)

    results: list[dict] = []
    for tenant in tenants:
        tenant_id = int(tenant.id)
        critical_incidents = int(critical_alerts.get(tenant_id, 0)) + int(critical_vulns.get(tenant_id, 0))
        pending = int(pending_tickets.get(tenant_id, 0))
        running = int(operations_running.get(tenant_id, 0))
        degraded = int(degraded_integrations.get(tenant_id, 0))
        score = _health_score(
            critical_incidents=critical_incidents,
            degraded_integrations=degraded,
            pending_tickets=pending,
            operations_running=running,
            plan_status=tenant.plan_status,
        )
        status = _build_status(
            plan_status=tenant.plan_status,
            critical_incidents=critical_incidents,
            degraded_integrations=degraded,
            operational_health_score=score,
            integrations_count=int(integrations_count.get(tenant_id, 0)),
            agents_count=int(agents_count.get(tenant_id, 0)),
        )
        results.append(
            {
                "id": str(tenant_id),
                "name": tenant.name,
                "status": status,
                "planStatus": tenant.plan_status,
                "integrationsCount": int(integrations_count.get(tenant_id, 0)),
                "agentsCount": int(agents_count.get(tenant_id, 0)),
                "openTickets": int(open_tickets.get(tenant_id, 0)),
                "pendingTickets": pending,
                "criticalIncidents": critical_incidents,
                "operationsRunning": running,
                "degradedIntegrations": degraded,
                "slaAtRisk": bool(critical_incidents > 0 or degraded > 0 or pending >= 5),
                "operationalHealthScore": score,
                "lastRelevantEvent": _build_last_relevant_event(
                    critical_alert_title=latest_critical_alert_by_tenant.get(tenant_id),
                    pending_ticket_subject=latest_pending_ticket_by_tenant.get(tenant_id),
                    latest_soc_scanner=latest_soc_scan_by_tenant.get(tenant_id),
                    latest_noc_scanner=latest_noc_scan_by_tenant.get(tenant_id),
                ),
            }
        )
    return results


def get_xoc_clients_kpis(session: Session) -> dict:
    clients = list_xoc_clients(session)
    return {
        "totalClients": len(clients),
        "activeClients": len([client for client in clients if client["status"] != "inactive"]),
        "clientsWithCriticalIncidents": len([client for client in clients if int(client["criticalIncidents"]) > 0 or client["status"] == "critical"]),
        "operationsRunning": sum(int(client["operationsRunning"]) for client in clients),
        "degradedIntegrations": sum(int(client["degradedIntegrations"]) for client in clients),
        "pendingTickets": sum(int(client["pendingTickets"]) for client in clients),
        "slaAtRisk": len([client for client in clients if bool(client["slaAtRisk"])]),
    }


def get_xoc_client_by_tenant_id(session: Session, tenant_id: int) -> dict | None:
    for client in list_xoc_clients(session):
        if int(client["id"]) == int(tenant_id):
            return client
    return None
