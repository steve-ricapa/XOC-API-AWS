from __future__ import annotations

from datetime import datetime

import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    plan_status: Mapped[str] = mapped_column(String(20), nullable=False, default="INACTIVE")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "plan_status": self.plan_status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="USER")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self, include_tenant: bool = False, tenant: Tenant | None = None) -> dict:
        data = {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "username": self.username,
            "email": self.email,
            "role": self.role,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_tenant and tenant is not None:
            data["tenant"] = tenant.to_dict()
        return data


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "actor_user_id": self.actor_user_id,
            "action": self.action,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "payload": self.payload,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TenantRuntimeSettings(Base):
    __tablename__ = "tenant_runtime_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    function_base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    function_route_sophia: Mapped[str] = mapped_column(String(255), nullable=False)
    function_route_sophia_history: Mapped[str] = mapped_column(String(255), nullable=False)
    function_route_sophia_delete: Mapped[str] = mapped_column(String(255), nullable=False)
    function_route_victor: Mapped[str] = mapped_column(String(255), nullable=False)
    speech_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    extra_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class Integration(Base):
    __tablename__ = "integrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    capabilities: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    keyvault_secret_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    credentials_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "provider": self.provider,
            "type": self.type,
            "capabilities": self.capabilities,
            "config": self.config,
            "keyvault_secret_id": self.keyvault_secret_id,
            "extra_json": self.extra_json,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    action_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    action_plan_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v0")
    approved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejected_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    execution_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    execution_logs: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    execution_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    capability_level: Mapped[str | None] = mapped_column(String(30), nullable=True)
    capability_policy_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decision_timeout_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    on_decision_timeout: Mapped[str | None] = mapped_column(String(30), nullable=True)

    def to_dict(self, include_creator: bool = False, creator: User | None = None) -> dict:
        data = {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "created_by_user_id": self.created_by_user_id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "action_plan": self.action_plan,
            "action_plan_version": self.action_plan_version,
            "approved_by_user_id": self.approved_by_user_id,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejected_by_user_id": self.rejected_by_user_id,
            "rejected_at": self.rejected_at.isoformat() if self.rejected_at else None,
            "execution_status": self.execution_status,
            "execution_logs": self.execution_logs,
            "execution_summary": self.execution_summary,
            "pending_decision": self.pending_decision,
            "capability_level": self.capability_level,
            "capability_policy_snapshot": self.capability_policy_snapshot,
            "decision_timeout_minutes": self.decision_timeout_minutes,
            "on_decision_timeout": self.on_decision_timeout,
        }
        if include_creator and creator is not None:
            data["creator"] = creator.to_dict()
        return data


class System(Base):
    __tablename__ = "systems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="online")
    health_score: Mapped[float | None] = mapped_column(nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    meta_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_check: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "integration_id": self.integration_id,
            "name": self.name,
            "type": self.type,
            "status": self.status,
            "health_score": self.health_score,
            "meta_info": self.meta_info,
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    integration_id: Mapped[int | None] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    meta_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "integration_id": self.integration_id,
            "external_id": self.external_id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "source": self.source,
            "status": self.status,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolved_by_user_id": self.resolved_by_user_id,
            "meta_info": self.meta_info,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Vulnerability(Base):
    __tablename__ = "vulnerabilities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    integration_id: Mapped[int | None] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"), nullable=True)
    cve_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    cvss_score: Mapped[float | None] = mapped_column(nullable=True)
    affected_systems: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    patch_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    patched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "integration_id": self.integration_id,
            "cve_id": self.cve_id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "cvss_score": self.cvss_score,
            "affected_systems": self.affected_systems,
            "status": self.status,
            "patch_status": self.patch_status,
            "patched_at": self.patched_at.isoformat() if self.patched_at else None,
            "meta_info": self.meta_info,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AgentApiKey(Base):
    __tablename__ = "agent_api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    integration_type: Mapped[str] = mapped_column(String(50), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "integration_type": self.integration_type,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }


class ScanSummary(Base):
    __tablename__ = "scan_summaries_soc"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    agent_api_key_id: Mapped[int | None] = mapped_column(ForeignKey("agent_api_keys.id", ondelete="SET NULL"), nullable=True)
    scan_id: Mapped[str] = mapped_column(String(255), nullable=False)
    scanner_type: Mapped[str] = mapped_column(String(50), nullable=False, default="openvas")
    summary_type: Mapped[str] = mapped_column(String(50), nullable=False, default="vulnerability")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="completed")
    critical_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    high_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    medium_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    info_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cvss_max: Mapped[float | None] = mapped_column(nullable=True, default=0.0)
    total_hosts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scan_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    meta_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    agent_api_key: Mapped[AgentApiKey | None] = relationship("AgentApiKey")

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "agent_api_key_id": self.agent_api_key_id,
            "scan_id": self.scan_id,
            "scanner_type": self.scanner_type,
            "summary_type": self.summary_type,
            "domain": "soc",
            "status": self.status,
            "critical_count": self.critical_count,
            "high_count": self.high_count,
            "medium_count": self.medium_count,
            "low_count": self.low_count,
            "info_count": self.info_count,
            "cvss_max": self.cvss_max,
            "total_hosts": self.total_hosts,
            "scan_name": self.scan_name,
            "scanned_at": self.scanned_at.isoformat() if self.scanned_at else None,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "meta_info": self.meta_info,
        }
        if self.agent_api_key:
            data["agent_name"] = self.agent_api_key.name
        return data


class ScanSummaryNoc(Base):
    __tablename__ = "scan_summaries_noc"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    agent_api_key_id: Mapped[int | None] = mapped_column(ForeignKey("agent_api_keys.id", ondelete="SET NULL"), nullable=True)
    scan_id: Mapped[str] = mapped_column(String(255), nullable=False)
    scanner_type: Mapped[str] = mapped_column(String(50), nullable=False, default="zabbix")
    summary_type: Mapped[str] = mapped_column(String(50), nullable=False, default="noc_health")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="completed")
    critical_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    high_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    medium_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    info_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cvss_max: Mapped[float | None] = mapped_column(nullable=True, default=0.0)
    total_hosts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scan_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    meta_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    agent_api_key: Mapped[AgentApiKey | None] = relationship("AgentApiKey")

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "agent_api_key_id": self.agent_api_key_id,
            "scan_id": self.scan_id,
            "scanner_type": self.scanner_type,
            "summary_type": self.summary_type,
            "domain": "noc",
            "status": self.status,
            "critical_count": self.critical_count,
            "high_count": self.high_count,
            "medium_count": self.medium_count,
            "low_count": self.low_count,
            "info_count": self.info_count,
            "cvss_max": self.cvss_max,
            "total_hosts": self.total_hosts,
            "scan_name": self.scan_name,
            "scanned_at": self.scanned_at.isoformat() if self.scanned_at else None,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "meta_info": self.meta_info,
        }
        if self.agent_api_key:
            data["agent_name"] = self.agent_api_key.name
        return data


class SnapshotArtifact(Base):
    __tablename__ = "snapshot_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    integration_id: Mapped[int | None] = mapped_column(ForeignKey("integrations.id", ondelete="SET NULL"), nullable=True)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    snapshot_type: Mapped[str] = mapped_column(String(100), nullable=False)
    domain: Mapped[str] = mapped_column(String(10), nullable=False, default="soc")
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="stored")
    scan_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scan_summary_soc_id: Mapped[int | None] = mapped_column(ForeignKey("scan_summaries_soc.id", ondelete="SET NULL"), nullable=True)
    scan_summary_noc_id: Mapped[int | None] = mapped_column(ForeignKey("scan_summaries_noc.id", ondelete="SET NULL"), nullable=True)
    s3_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False, default="application/json")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "integration_id": self.integration_id,
            "provider": self.provider,
            "snapshot_type": self.snapshot_type,
            "domain": self.domain,
            "source": self.source,
            "status": self.status,
            "scan_id": self.scan_id,
            "external_id": self.external_id,
            "scan_summary_soc_id": self.scan_summary_soc_id,
            "scan_summary_noc_id": self.scan_summary_noc_id,
            "s3_bucket": self.s3_bucket,
            "s3_key": self.s3_key,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
            "summary_json": self.summary_json,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class IngestIdempotencyRecord(Base):
    __tablename__ = "ingest_idempotency_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(71), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str] = mapped_column(String(10), nullable=False, default="soc")
    scan_summary_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scan_summary_soc_id: Mapped[int | None] = mapped_column(ForeignKey("scan_summaries_soc.id", ondelete="SET NULL"), nullable=True)
    scan_summary_noc_id: Mapped[int | None] = mapped_column(ForeignKey("scan_summaries_noc.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "idempotency_key": self.idempotency_key,
            "request_hash": self.request_hash,
            "domain": self.domain,
            "scan_summary_id": self.scan_summary_id,
            "scan_summary_soc_id": self.scan_summary_soc_id,
            "scan_summary_noc_id": self.scan_summary_noc_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }





class AgentInstance(Base):
    __tablename__ = "agent_instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False, default="SOPHIA")
    client_access_key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    client_access_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    speech_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "agent_type": self.agent_type,
            "status": self.status,
            "settings": self.settings,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    external_thread_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    purpose: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())

    user: Mapped[User] = relationship("User")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "external_thread_id": self.external_thread_id,
            "title": self.title,
            "purpose": self.purpose,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_activity_at": self.last_activity_at.isoformat() if self.last_activity_at else None,
        }


class LiveVoiceSession(Base):
    __tablename__ = "live_voice_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False, default="SOPHIA")
    agent_instance_id: Mapped[str | None] = mapped_column(ForeignKey("agent_instances.id", ondelete="CASCADE"), nullable=True)
    session_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_consumed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    messages: Mapped[list["LiveVoiceMessage"]] = relationship(
        "LiveVoiceMessage",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    def to_dict(self, include_messages: bool = False) -> dict:
        data = {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "agent_type": self.agent_type,
            "agent_instance_id": self.agent_instance_id,
            "session_name": self.session_name,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_seconds": self.duration_seconds,
            "tokens_consumed": self.tokens_consumed,
            "metrics": self.metrics,
            "metadata": self.metadata_json,
        }
        if include_messages:
            ordered = sorted(self.messages, key=lambda item: item.created_at or datetime.min)
            data["messages"] = [message.to_dict() for message in ordered]
        return data


class LiveVoiceMessage(Base):
    __tablename__ = "live_voice_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column("live_voice_session_id", ForeignKey("live_voice_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())

    session: Mapped[LiveVoiceSession] = relationship("LiveVoiceSession", back_populates="messages")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class IntegrationCapabilityTemplate(Base):
    __tablename__ = "integration_capability_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    capabilities: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider,
            "capabilities": self.capabilities,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class IntegrationCapabilityTemplateAssignment(Base):
    __tablename__ = "integration_capability_template_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("integration_capability_templates.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("template_id", "tenant_id", name="uq_template_tenant"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "template_id": self.template_id,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PendingIngestion(Base):
    __tablename__ = "pending_ingestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    upload_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    api_key_id: Mapped[int] = mapped_column(ForeignKey("agent_api_keys.id", ondelete="SET NULL"), nullable=True)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    scanner_type: Mapped[str] = mapped_column(String(50), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(71), nullable=True)
    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "upload_id": self.upload_id,
            "provider": self.provider,
            "scanner_type": self.scanner_type,
            "status": self.status,
            "s3_key": self.s3_key,
            "error_message": self.error_message,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ActivationKey(Base):
    __tablename__ = "activation_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    key_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    uses_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    used_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    used_tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "key_prefix": self.key_prefix,
            "key_type": self.key_type,
            "status": self.status,
            "max_uses": self.max_uses,
            "uses_count": self.uses_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by_user_id": self.created_by_user_id,
            "used_at": self.used_at.isoformat() if self.used_at else None,
            "used_by_user_id": self.used_by_user_id,
            "used_tenant_id": self.used_tenant_id,
        }





class FindingIndex(Base):
    __tablename__ = "finding_index"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_artifact_id: Mapped[int | None] = mapped_column(ForeignKey("snapshot_artifacts.id", ondelete="SET NULL"), nullable=True, index=True)
    scan_summary_soc_id: Mapped[int | None] = mapped_column(ForeignKey("scan_summaries_soc.id", ondelete="SET NULL"), nullable=True)
    scan_summary_noc_id: Mapped[int | None] = mapped_column(ForeignKey("scan_summaries_noc.id", ondelete="SET NULL"), nullable=True)
    scan_id: Mapped[str] = mapped_column(String(255), nullable=False)
    scanner_type: Mapped[str] = mapped_column(String(50), nullable=False)
    domain: Mapped[str] = mapped_column(String(10), nullable=False, default="soc")
    finding_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    cve: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    host: Mapped[str | None] = mapped_column(Text, nullable=True)
    port: Mapped[str | None] = mapped_column(String(50), nullable=True)
    protocol: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    service: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cvss: Mapped[float | None] = mapped_column(nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    solution: Mapped[str | None] = mapped_column(Text, nullable=True)
    impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    s3_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    detected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "snapshot_artifact_id": self.snapshot_artifact_id,
            "scan_summary_soc_id": self.scan_summary_soc_id,
            "scan_summary_noc_id": self.scan_summary_noc_id,
            "scan_id": self.scan_id,
            "scanner_type": self.scanner_type,
            "domain": self.domain,
            "finding_idx": self.finding_idx,
            "severity": self.severity,
            "name": self.name,
            "cve": self.cve,
            "host": self.host,
            "port": self.port,
            "protocol": self.protocol,
            "status": self.status,
            "event_type": self.event_type,
            "service": self.service,
            "cvss": self.cvss,
            "description": self.description,
            "solution": self.solution,
            "impact": self.impact,
            "s3_bucket": self.s3_bucket,
            "s3_key": self.s3_key,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
