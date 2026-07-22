from __future__ import annotations

import argparse
import os
import random
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.persistence.db import session_scope
from src.persistence.models import (
    AgentApiKey,
    AgentInstance,
    Alert,
    FindingIndex,
    Integration,
    LiveVoiceMessage,
    LiveVoiceSession,
    ScanSummary,
    ScanSummaryNoc,
    System,
    Tenant,
    Ticket,
    User,
    Vulnerability,
)
from src.reports.store import create_document_job, delete_tenant_document_jobs, table as documents_table, update_document_status
from src.shared.encryption import encrypt_agent_key, encrypt_credentials
from src.shared.security_keys import generate_access_key, hash_access_key
from src.shared.tickets_store import (
    build_new_ticket_item,
    build_secondary_index_fields as build_ticket_secondary_index_fields,
    delete_tenant_tickets,
    table as tickets_table,
)


SOC_PROVIDERS = ["wazuh", "nessus", "openvas", "insightvm"]
NOC_PROVIDERS = ["zabbix", "uptime_kuma"]
ALL_PROVIDERS = SOC_PROVIDERS + NOC_PROVIDERS
DEMO_DOCUMENT_TYPES = ["minority_report", "small_report", "informe_soporte"]
DEMO_VOICE_TRANSCRIPTS = [
    [
        ("USER", "Hola SOPHIA, necesito un resumen del estado actual de seguridad."),
        ("ASSISTANT", "Actualmente el tenant demo presenta hallazgos criticos controlados y una tendencia estable en superficie de riesgo."),
    ],
    [
        ("USER", "Quiero saber si hay activos degradados en el entorno NOC."),
        ("ASSISTANT", "Se observan activos degradados simulados en monitoreo, pero la disponibilidad general permanece dentro del umbral esperado."),
    ],
    [
        ("USER", "Genera una explicacion simple para direccion sobre vulnerabilidades prioritarias."),
        ("ASSISTANT", "Las vulnerabilidades demo priorizadas muestran exposicion controlada y sirven para explicar criticidad, impacto y plan de remediacion."),
    ],
]


def _now() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def _credential_payload(provider: str) -> dict:
    if provider == "nessus":
        return {"url": "https://demo.local", "access_key": "demo-access", "secret_key": "demo-secret"}
    if provider == "uptime_kuma":
        return {"url": "https://demo.local"}
    return {"url": "https://demo.local", "username": "demo", "password": "demo"}


def _agent_name(provider: str) -> str:
    return {
        "wazuh": "Wazuh SIEM Agent",
        "zabbix": "Zabbix Monitor Agent",
        "nessus": "Nessus Scanner Agent",
        "openvas": "OpenVAS Scanner Agent",
        "insightvm": "InsightVM Rapid7 Agent",
        "uptime_kuma": "Uptime Kuma Agent",
    }[provider]


def _get_or_create_admin_user(session: Session, tenant_id: int) -> User:
    admin_user = session.scalar(select(User).where(User.tenant_id == tenant_id).order_by(User.id.asc()))
    if admin_user:
        return admin_user

    username = f"xoc_demo_admin_{tenant_id}"
    email = f"xoc.demo.{tenant_id}@xoc.local"
    admin_user = User(
        tenant_id=tenant_id,
        username=username,
        email=email,
        role="ADMIN",
    )
    admin_user.set_password("DemoTemp123!")
    session.add(admin_user)
    session.flush()
    return admin_user


def _cleanup_tenant_demo_data(session: Session, tenant_id: int) -> None:
    soc_ids = list(session.scalars(select(ScanSummary.id).where(ScanSummary.tenant_id == tenant_id, ScanSummary.scanner_type.in_(SOC_PROVIDERS))))
    noc_ids = list(session.scalars(select(ScanSummaryNoc.id).where(ScanSummaryNoc.tenant_id == tenant_id, ScanSummaryNoc.scanner_type.in_(NOC_PROVIDERS))))
    if soc_ids:
        session.execute(delete(FindingIndex).where(FindingIndex.scan_summary_soc_id.in_(soc_ids)))
    if noc_ids:
        session.execute(delete(FindingIndex).where(FindingIndex.scan_summary_noc_id.in_(noc_ids)))
    session.execute(delete(ScanSummary).where(ScanSummary.tenant_id == tenant_id, ScanSummary.scanner_type.in_(SOC_PROVIDERS)))
    session.execute(delete(ScanSummaryNoc).where(ScanSummaryNoc.tenant_id == tenant_id, ScanSummaryNoc.scanner_type.in_(NOC_PROVIDERS)))

    integration_ids = list(session.scalars(select(Integration.id).where(Integration.tenant_id == tenant_id, Integration.provider.in_(ALL_PROVIDERS))))
    if integration_ids:
        session.execute(delete(System).where(System.tenant_id == tenant_id, System.integration_id.in_(integration_ids)))
        session.execute(delete(Alert).where(Alert.tenant_id == tenant_id, Alert.integration_id.in_(integration_ids)))
        session.execute(delete(Vulnerability).where(Vulnerability.tenant_id == tenant_id, Vulnerability.integration_id.in_(integration_ids)))
    session.execute(delete(Ticket).where(Ticket.tenant_id == tenant_id, Ticket.subject.like("Demo %")))
    voice_session_ids = list(session.scalars(select(LiveVoiceSession.id).where(LiveVoiceSession.tenant_id == tenant_id)))
    if voice_session_ids:
        session.execute(delete(LiveVoiceMessage).where(LiveVoiceMessage.session_id.in_(voice_session_ids)))
    session.execute(delete(LiveVoiceSession).where(LiveVoiceSession.tenant_id == tenant_id))
    session.execute(delete(AgentApiKey).where(AgentApiKey.tenant_id == tenant_id, AgentApiKey.integration_type.in_(ALL_PROVIDERS)))
    session.execute(delete(Integration).where(Integration.tenant_id == tenant_id, Integration.provider.in_(ALL_PROVIDERS)))
    session.flush()
    delete_tenant_tickets(tenant_id)
    delete_tenant_document_jobs(tenant_id)


def _cleanup_operational_demo_data(session: Session, tenant_id: int) -> None:
    voice_session_ids = list(session.scalars(select(LiveVoiceSession.id).where(LiveVoiceSession.tenant_id == tenant_id)))
    if voice_session_ids:
        session.execute(delete(LiveVoiceMessage).where(LiveVoiceMessage.session_id.in_(voice_session_ids)))
    session.execute(delete(LiveVoiceSession).where(LiveVoiceSession.tenant_id == tenant_id))
    session.flush()
    delete_tenant_tickets(tenant_id)
    delete_tenant_document_jobs(tenant_id)


def _create_integrations(session: Session, tenant_id: int) -> dict[str, Integration]:
    integrations: dict[str, Integration] = {}
    for provider in ALL_PROVIDERS:
        credentials_encrypted = None
        if os.environ.get("AGENT_KEY_ENCRYPTION_KEY"):
            credentials_encrypted = encrypt_credentials(_credential_payload(provider))
        integration = Integration(
            tenant_id=tenant_id,
            provider=provider,
            type=provider,
            credentials_encrypted=credentials_encrypted,
            extra_json={"seeded": True, "provider": provider},
        )
        session.add(integration)
        session.flush()
        integrations[provider] = integration
    return integrations


def _create_agent_keys(session: Session, tenant_id: int) -> dict[str, AgentApiKey]:
    agent_keys: dict[str, AgentApiKey] = {}
    for index, provider in enumerate(ALL_PROVIDERS, start=1):
        access_key = generate_access_key(40)
        encrypted = encrypt_agent_key(access_key) if os.environ.get("AGENT_KEY_ENCRYPTION_KEY") else None
        agent_key = AgentApiKey(
            tenant_id=tenant_id,
            name=_agent_name(provider),
            integration_type=provider,
            api_key_hash=hash_access_key(access_key),
            api_key_encrypted=encrypted,
            is_active=True,
            last_used_at=_now() - timedelta(minutes=index * 7),
        )
        session.add(agent_key)
        session.flush()
        agent_keys[provider] = agent_key
    return agent_keys


def _seed_soc_scans(session: Session, tenant_id: int, provider: str, agent_key: AgentApiKey, rng: random.Random) -> None:
    base_time = _now() - timedelta(days=30)
    for day in range(12):
        scanned_at = base_time + timedelta(days=day * 2, hours=rng.randint(0, 20))
        critical = rng.randint(1, 5)
        high = rng.randint(4, 10)
        medium = rng.randint(8, 18)
        low = rng.randint(6, 16)
        info = rng.randint(2, 10)
        total_hosts = rng.randint(8, 28)
        scan = ScanSummary(
            tenant_id=tenant_id,
            agent_api_key_id=agent_key.id,
            scan_id=f"{provider}-scan-{day + 1:03d}",
            scanner_type=provider,
            summary_type="security_events" if provider == "wazuh" else "vulnerability",
            status="completed",
            critical_count=critical,
            high_count=high,
            medium_count=medium,
            low_count=low,
            info_count=info,
            cvss_max=round(rng.uniform(8.8, 10.0), 1),
            total_hosts=total_hosts,
            scan_name=f"{provider.upper()} Demo Scan {day + 1}",
            scanned_at=scanned_at,
            meta_info={
                "seeded": True,
                "agent_name": agent_key.name,
                "source": provider,
            },
        )
        session.add(scan)
        session.flush()

        findings_to_create = rng.randint(10, 24)
        for finding_idx in range(findings_to_create):
            severity_bucket = rng.choices(
                ["critical", "high", "medium", "low", "info"],
                weights=[2, 4, 6, 4, 2],
                k=1,
            )[0]
            host = f"{provider}-host-{rng.randint(1, total_hosts):02d}.corp.local"
            finding = FindingIndex(
                tenant_id=tenant_id,
                scan_summary_soc_id=scan.id,
                scan_id=scan.scan_id,
                scanner_type=provider,
                domain="soc",
                finding_idx=finding_idx,
                severity=severity_bucket,
                name=f"{provider.upper()} Finding {finding_idx + 1}",
                cvss=round(rng.uniform(3.4, 9.9), 1),
                cve=f"CVE-2026-{rng.randint(1000, 9999)}",
                host=host,
                port=str(rng.choice([22, 80, 443, 8080, 3306, 5432])),
                protocol=rng.choice(["tcp", "udp"]),
                description=f"Synthetic {provider} finding for demo dashboards.",
                solution="Apply the recommended patch or mitigation.",
                impact="Potential compromise or service degradation.",
                s3_bucket="demo-bucket",
                s3_key=f"demo/{provider}/{scan.scan_id}.json",
            )
            session.add(finding)


def _seed_noc_scans(session: Session, tenant_id: int, provider: str, agent_key: AgentApiKey, rng: random.Random) -> None:
    base_time = _now() - timedelta(days=30)
    for day in range(12):
        scanned_at = base_time + timedelta(days=day * 2, hours=rng.randint(0, 20))
        total_hosts = rng.randint(12, 42)
        if provider == "zabbix":
            critical = rng.randint(0, 2)
            high = rng.randint(1, 4)
            medium = rng.randint(3, 8)
            low = rng.randint(2, 6)
            info = rng.randint(1, 4)
            meta_info = {
                "seeded": True,
                "metrics": {"avg_cpu": round(rng.uniform(35, 78), 2), "avg_ram": round(rng.uniform(42, 84), 2)},
                "hosts": [
                    {"name": f"zabbix-node-{idx:02d}", "status": rng.choice(["online", "degraded", "online"])}
                    for idx in range(1, min(total_hosts, 12) + 1)
                ],
                "manager_status": "healthy",
            }
        else:
            critical = 0
            high = rng.randint(0, 2)
            medium = rng.randint(1, 4)
            low = rng.randint(1, 4)
            info = rng.randint(0, 2)
            down = rng.randint(0, 3)
            up = max(total_hosts - down, 0)
            pending = rng.randint(0, 1)
            uptime_pct = round((up / total_hosts) * 100, 2) if total_hosts else 100.0
            meta_info = {
                "seeded": True,
                "services": {"total": total_hosts, "up": up, "down": down, "pending": pending},
                "uptime_percentage": uptime_pct,
                "down_monitors": [f"monitor-{idx:02d}" for idx in range(1, down + 1)],
                "avg_response_time_ms": rng.randint(82, 210),
            }
        scan = ScanSummaryNoc(
            tenant_id=tenant_id,
            agent_api_key_id=agent_key.id,
            scan_id=f"{provider}-scan-{day + 1:03d}",
            scanner_type=provider,
            summary_type="noc_health" if provider == "zabbix" else "availability",
            status="completed",
            critical_count=critical,
            high_count=high,
            medium_count=medium,
            low_count=low,
            info_count=info,
            cvss_max=0.0,
            total_hosts=total_hosts,
            scan_name=f"{provider.upper()} Demo Snapshot {day + 1}",
            scanned_at=scanned_at,
            meta_info=meta_info,
        )
        session.add(scan)
        session.flush()

        events_to_create = rng.randint(4, 10)
        for finding_idx in range(events_to_create):
            severity_bucket = rng.choices(["critical", "high", "medium", "low", "info"], weights=[1, 2, 4, 3, 1], k=1)[0]
            finding = FindingIndex(
                tenant_id=tenant_id,
                scan_summary_noc_id=scan.id,
                scan_id=scan.scan_id,
                scanner_type=provider,
                domain="noc",
                finding_idx=finding_idx,
                severity=severity_bucket,
                name=f"{provider.upper()} Event {finding_idx + 1}",
                event_type="availability" if provider == "uptime_kuma" else "performance_alert",
                status=rng.choice(["active", "resolved", "active"]),
                host=f"{provider}-node-{rng.randint(1, total_hosts):02d}.corp.local",
                service=rng.choice(["http", "database", "ping", "cpu", "memory"]),
                description=f"Synthetic {provider} event for demo dashboards.",
                impact="Service degradation detected.",
                s3_bucket="demo-bucket",
                s3_key=f"demo/{provider}/{scan.scan_id}.json",
            )
            session.add(finding)


def _seed_systems_alerts_vulns_tickets(session: Session, tenant: Tenant, admin_user: User, integrations: dict[str, Integration], rng: random.Random) -> None:
    for provider, integration in integrations.items():
        total = 4 if provider in {"zabbix", "uptime_kuma"} else 3
        for idx in range(1, total + 1):
            session.add(
                System(
                    tenant_id=tenant.id,
                    integration_id=integration.id,
                    name=f"{provider}-system-{idx:02d}",
                    type="server",
                    status=rng.choice(["online", "online", "degraded", "offline"]),
                    health_score=round(rng.uniform(63, 99), 2),
                    meta_info={"seeded": True, "provider": provider},
                )
            )

    for idx in range(1, 13):
        provider = rng.choice(["wazuh", "zabbix", "uptime_kuma"])
        session.add(
            Alert(
                tenant_id=tenant.id,
                integration_id=integrations[provider].id,
                external_id=f"alert-{provider}-{idx:03d}",
                title=f"Demo {provider} alert {idx}",
                description="Synthetic alert generated for dashboard demos.",
                severity=rng.choice(["critical", "high", "medium", "low"]),
                source=provider,
                status=rng.choice(["active", "active", "resolved"]),
                created_at=_now() - timedelta(hours=rng.randint(1, 240)),
                meta_info={"seeded": True},
            )
        )

    vuln_providers = ["nessus", "openvas", "insightvm"]
    for idx in range(1, 19):
        provider = rng.choice(vuln_providers)
        session.add(
            Vulnerability(
                tenant_id=tenant.id,
                integration_id=integrations[provider].id,
                cve_id=f"CVE-2026-{2000 + idx}",
                title=f"Demo vulnerability {idx}",
                description="Synthetic vulnerability for analytics and dashboards.",
                severity=rng.choice(["critical", "high", "medium", "low"]),
                cvss_score=round(rng.uniform(4.1, 9.9), 1),
                affected_systems=[f"{provider}-system-{rng.randint(1,3):02d}"],
                status=rng.choice(["open", "open", "patched"]),
                patch_status=rng.choice(["pending", "scheduled", "patched"]),
                created_at=_now() - timedelta(days=rng.randint(1, 30)),
                meta_info={"seeded": True},
            )
        )

    for idx in range(1, 9):
        session.add(
            Ticket(
                tenant_id=tenant.id,
                created_by_user_id=admin_user.id,
                subject=f"Demo ticket {idx}",
                description="Synthetic ticket for dashboard and workflow demos.",
                status=rng.choice(["PENDING", "APPROVED", "REJECTED"]),
                action_plan={"steps": ["validate", "contain", "report"]},
                action_plan_version="v1",
                created_at=_now() - timedelta(days=rng.randint(1, 20)),
                execution_status=rng.choice([None, "QUEUED", "COMPLETED"]),
            )
            )


def _seed_live_voice_sessions(session: Session, tenant_id: int, rng: random.Random) -> None:
    agent_instance_id = session.scalar(
        select(AgentInstance.id).where(
            AgentInstance.tenant_id == tenant_id,
            AgentInstance.agent_type == "SOPHIA",
            AgentInstance.status == "ACTIVE",
        ).order_by(AgentInstance.created_at.desc())
    )
    base_time = _now()
    for idx, transcript in enumerate(DEMO_VOICE_TRANSCRIPTS, start=1):
        started_at = base_time - timedelta(hours=idx * 7)
        duration_seconds = 240 + (idx * 75)
        voice_session = LiveVoiceSession(
            tenant_id=tenant_id,
            agent_type="SOPHIA",
            agent_instance_id=agent_instance_id,
            session_name=f"Demo Voice Session {idx}",
            status="ENDED",
            created_at=started_at,
            ended_at=started_at + timedelta(seconds=duration_seconds),
            duration_seconds=duration_seconds,
            tokens_consumed=900 + (idx * 120),
            metrics={"seeded": True, "turns": len(transcript)},
            metadata_json={"seeded": True, "channel": "voice_demo"},
        )
        session.add(voice_session)
        session.flush()
        for msg_idx, (role, content) in enumerate(transcript, start=1):
            session.add(
                LiveVoiceMessage(
                    session_id=voice_session.id,
                    role=role,
                    content=content,
                    created_at=started_at + timedelta(seconds=msg_idx * 45),
                )
            )


def _seed_dynamo_tickets(tenant_id: int, admin_user: User, rng: random.Random) -> None:
    ticket_statuses = [
        "PENDING",
        "DERIVED",
        "PREAPROBADO",
        "APROBADO",
        "EN_EJECUCION",
        "RESUELTO",
    ]
    for idx, status in enumerate(ticket_statuses, start=1):
        payload = {
            "subject": f"Demo ticket {idx}",
            "description": f"Synthetic operational demo ticket {idx} for tenant showcase.",
            "status": status,
        }
        ticket_id, item = build_new_ticket_item(payload, tenant_id, admin_user.id)
        created_at = (_now() - timedelta(hours=idx * 5)).isoformat()
        item["created_at"] = created_at
        item["updated_at"] = created_at
        item.update(build_ticket_secondary_index_fields(tenant_id, ticket_id, status, created_at))
        item["execution_status"] = rng.choice([None, "QUEUED", "COMPLETED"])
        if status == "PREAPROBADO":
            item["action_plan"] = {
                "summary": "Demo remediation plan",
                "steps": [
                    {"id": "step-1", "tool": "ticket_get", "description": "Review ticket context", "parameters": {}},
                    {"id": "step-2", "tool": "ticket_patch", "description": "Apply remediation state", "parameters": {"status": "EN_EJECUCION"}},
                ],
            }
            item["action_plan_version"] = "v1"
            item["pending_decision"] = {
                "options": [
                    {"option_id": "approve-demo", "label": "Approve remediation"},
                    {"option_id": "reject-demo", "label": "Escalate manually"},
                ]
            }
        tickets_table.put_item(Item=item)


def _seed_document_jobs(tenant_id: int, admin_user: User) -> None:
    seeded = [
        (DEMO_DOCUMENT_TYPES[0], "PENDING"),
        (DEMO_DOCUMENT_TYPES[1], "PROCESSING"),
        (DEMO_DOCUMENT_TYPES[2], "FAILED"),
    ]
    for idx, (document_type, status) in enumerate(seeded, start=1):
        document_id, item = create_document_job(
            tenant_id=tenant_id,
            document_type=document_type,
            created_by_user_id=admin_user.id,
            filters={"severity": "critical"},
            parameters={"seeded": True, "window": idx},
            request_payload={"document_type": document_type},
            request_hash=f"demo-{tenant_id}-{document_type}-{idx}",
        )
        created_at = (_now() - timedelta(hours=idx * 9)).isoformat()
        item["created_at"] = created_at
        item["updated_at"] = created_at
        documents_table.put_item(Item=item)
        if status == "PROCESSING":
            update_document_status(tenant_id, document_id, "PROCESSING")
        elif status == "FAILED":
            update_document_status(
                tenant_id,
                document_id,
                "FAILED",
                error_code="demo_seed",
                error_message="Synthetic demo failure for showcase purposes",
            )


def seed_tenant(tenant_id: int, random_seed: int = 20260620) -> None:
    rng = random.Random(random_seed)
    with session_scope() as session:
        tenant = session.get(Tenant, tenant_id)
        if not tenant:
            raise ValueError(f"Tenant {tenant_id} not found")
        admin_user = _get_or_create_admin_user(session, tenant_id)

        _cleanup_tenant_demo_data(session, tenant_id)
        integrations = _create_integrations(session, tenant_id)
        agent_keys = _create_agent_keys(session, tenant_id)

        for provider in SOC_PROVIDERS:
            _seed_soc_scans(session, tenant_id, provider, agent_keys[provider], rng)
        for provider in NOC_PROVIDERS:
            _seed_noc_scans(session, tenant_id, provider, agent_keys[provider], rng)

        _seed_systems_alerts_vulns_tickets(session, tenant, admin_user, integrations, rng)
        _seed_live_voice_sessions(session, tenant_id, rng)
        _seed_dynamo_tickets(tenant_id, admin_user, rng)
        _seed_document_jobs(tenant_id, admin_user)

        print(f"Seed completed for tenant_id={tenant_id}")
        print(f"Integrations created: {len(integrations)}")
        print(f"Agent keys created: {len(agent_keys)}")


def seed_operational_tenant_data(tenant_id: int, random_seed: int = 20260620) -> None:
    rng = random.Random(random_seed)
    with session_scope() as session:
        tenant = session.get(Tenant, tenant_id)
        if not tenant:
            raise ValueError(f"Tenant {tenant_id} not found")
        admin_user = _get_or_create_admin_user(session, tenant_id)
        _cleanup_operational_demo_data(session, tenant_id)
        _seed_live_voice_sessions(session, tenant_id, rng)
        _seed_dynamo_tickets(tenant_id, admin_user, rng)
        _seed_document_jobs(tenant_id, admin_user)
        print(f"Operational demo seed completed for tenant_id={tenant_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo tenant data for dashboards and summaries")
    parser.add_argument("--tenant-id", type=int, required=True, dest="tenant_id")
    parser.add_argument("--seed", type=int, default=20260620)
    args = parser.parse_args()
    seed_tenant(args.tenant_id, args.seed)


if __name__ == "__main__":
    main()
