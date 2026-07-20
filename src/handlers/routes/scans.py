from __future__ import annotations

import re
import uuid as _uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import AgentApiKey, FindingIndex, PendingIngestion, ScanSummary, ScanSummaryNoc, SnapshotArtifact, Tenant, User
from src.shared.config import get_settings
from src.shared.context import effective_tenant_id_of, log_audit, require_tenant_read_access
from src.shared.dependencies import get_current_user, get_request_claims, get_request_tenant_context
from src.shared.errors import ForbiddenError, NotFoundError, ValidationError
from src.shared.integration_types import OFFICIAL_INTEGRATION_TYPES, normalize_integration_type
from src.shared.scans_demo_data import demo_latest_scans, demo_scan, demo_scan_findings, demo_scanner_analytics, demo_scans, demo_scans_summary
from src.shared.security_keys import verify_access_key
from src.shared.snapshots import fetch_snapshot_payload, generate_download_url, generate_upload_url


router = APIRouter(prefix="/scans", tags=["scans"])
findings_router = APIRouter(prefix="/findings", tags=["findings"])

ALLOWED_SCANNER_TYPES = OFFICIAL_INTEGRATION_TYPES
ALLOWED_SUMMARY_TYPES = ["vulnerability", "security_events", "noc_health", "availability", "network_discovery", "other"]
ALLOWED_DOMAINS = ["soc", "noc"]
MAX_META_SIZE_BYTES = 1024 * 1024
MAX_SCAN_ID_LENGTH = 255
MAX_IDEMPOTENCY_KEY_LENGTH = 71
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SOC_SCANNER_TYPES = {"openvas", "insightvm", "nessus", "qualys", "tenable", "rapid7", "wazuh"}
_NOC_SCANNER_TYPES = {"zabbix", "uptime_kuma"}
_SCANNER_DEFAULT_SUMMARY_TYPE = {
    "openvas": "vulnerability",
    "insightvm": "vulnerability",
    "nessus": "vulnerability",
    "qualys": "vulnerability",
    "tenable": "vulnerability",
    "rapid7": "vulnerability",
    "wazuh": "security_events",
    "zabbix": "noc_health",
    "uptime_kuma": "availability",
    "nmap": "network_discovery",
    "other": "other",
}
_SUMMARY_TYPE_DOMAIN_MAP = {
    "vulnerability": "soc",
    "security_events": "soc",
    "noc_health": "noc",
    "availability": "noc",
}


def _is_demo_tenant(current_user: User, session: Session) -> bool:
    tenant = session.get(Tenant, effective_tenant_id_of(current_user))
    plan_status = (getattr(tenant, "plan_status", "") or "").strip().upper()
    return plan_status == "DEMO"


def make_error_response(error_type: str, message: str, status_code: int) -> dict:
    raise ValidationError(message) if status_code == 400 else ForbiddenError(message)


def validate_integer(value, field_name: str, *, required: bool = True, min_val: int | None = None, max_val: int | None = None):
    if value is None:
        if required:
            return None, f"Field '{field_name}' is required"
        return None, None
    try:
        int_val = int(value)
    except (TypeError, ValueError):
        return None, f"Field '{field_name}' must be an integer"
    if min_val is not None and int_val < min_val:
        return None, f"Field '{field_name}' must be >= {min_val}"
    if max_val is not None and int_val > max_val:
        return None, f"Field '{field_name}' must be <= {max_val}"
    return int_val, None


def validate_string(value, field_name: str, *, required: bool = True, max_length: int | None = None, allowed_values: list[str] | None = None):
    if value is None or (isinstance(value, str) and value.strip() == ""):
        if required:
            return None, f"Field '{field_name}' is required"
        return None, None
    if not isinstance(value, str):
        return None, f"Field '{field_name}' must be a string"
    if max_length and len(value) > max_length:
        return None, f"Field '{field_name}' exceeds maximum length of {max_length}"
    if allowed_values and value not in allowed_values:
        return None, f"Field '{field_name}' must be one of: {', '.join(allowed_values)}"
    return value, None


def validate_iso8601(value, field_name: str):
    if value is None:
        return None, f"Field '{field_name}' is required"
    if not isinstance(value, str):
        return None, f"Field '{field_name}' must be an ISO 8601 string"
    try:
        parsed_dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed_dt.tzinfo is not None:
            parsed_dt = parsed_dt.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed_dt, None
    except Exception:
        return None, f"Field '{field_name}' is not a valid ISO 8601 timestamp"


def parse_analytics_range_args(start_date_raw: str | None, end_date_raw: str | None, days: int | None, *, default_days: int = 30, max_days: int = 90):
    if start_date_raw and end_date_raw:
        start_dt, start_err = validate_iso8601(start_date_raw, "start_date")
        if start_err:
            raise ValidationError(start_err)
        end_dt, end_err = validate_iso8601(end_date_raw, "end_date")
        if end_err:
            raise ValidationError(end_err)
        if start_dt > end_dt:
            raise ValidationError("Field 'start_date' must be <= 'end_date'")
        if (end_dt - start_dt) > timedelta(days=max_days):
            raise ValidationError(f"Requested range exceeds maximum of {max_days} days")
        return start_dt, end_dt
    requested_days = days or default_days
    if requested_days < 1:
        requested_days = default_days
    if requested_days > max_days:
        requested_days = max_days
    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=requested_days)
    return start_dt, end_dt


def validate_idempotency_key(value):
    normalized, err = validate_string(value, "idempotency_key", required=True, max_length=MAX_IDEMPOTENCY_KEY_LENGTH)
    if err:
        return None, err
    if not _IDEMPOTENCY_KEY_PATTERN.match(normalized):
        return None, "Field 'idempotency_key' must match format sha256:<64-lowercase-hex>"
    return normalized, None


def normalize_severity(value):
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if "critical" in normalized:
        return "critical"
    if "high" in normalized:
        return "high"
    if "medium" in normalized:
        return "medium"
    if "low" in normalized:
        return "low"
    if "info" in normalized or "log" in normalized or "information" in normalized:
        return "info"
    return normalized


def normalize_summary_type(value):
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized not in ALLOWED_SUMMARY_TYPES:
        return None
    return normalized


def normalize_domain(value, default: str = "soc"):
    normalized = str(value or "").strip().lower()
    if not normalized:
        return default
    if normalized not in ALLOWED_DOMAINS:
        return None
    return normalized


def _infer_domain_from_integration(integration_type: str | None):
    if integration_type in _SOC_SCANNER_TYPES:
        return "soc"
    if integration_type in _NOC_SCANNER_TYPES or integration_type == "nmap":
        return "noc"
    return None


def resolve_summary_type_and_domain(scanner_type: str, summary_type_raw: str | None, api_key_integration_type: str | None):
    summary_type = normalize_summary_type(summary_type_raw)
    if summary_type_raw and not summary_type:
        return None, None, f"Invalid summary_type. Allowed: {', '.join(ALLOWED_SUMMARY_TYPES)}"
    if not summary_type:
        summary_type = _SCANNER_DEFAULT_SUMMARY_TYPE.get(scanner_type)
    if not summary_type:
        return None, None, "summary_type is required for this scanner_type"
    domain = _SUMMARY_TYPE_DOMAIN_MAP.get(summary_type) or _infer_domain_from_integration(api_key_integration_type)
    if domain is None:
        return None, None, "Unable to infer domain from summary_type/api key integration"
    return summary_type, domain, None


def _summary_model_for_domain(domain: str):
    return ScanSummaryNoc if domain == "noc" else ScanSummary


def _resolve_requested_domain(scanner_type: str | None = None, domain_raw: str | None = None, default: str = "soc"):
    domain = normalize_domain(domain_raw, default=default)
    if domain_raw and not domain:
        return None
    inferred = _infer_domain_from_integration(scanner_type) if scanner_type else None
    return inferred or domain


def _get_snapshot_artifact_or_404(session: Session, tenant_id: int, artifact_id: int) -> SnapshotArtifact:
    artifact = session.scalar(select(SnapshotArtifact).where(SnapshotArtifact.id == artifact_id, SnapshotArtifact.tenant_id == tenant_id))
    if not artifact:
        raise NotFoundError("Snapshot artifact not found")
    return artifact


def _build_agent_info(session: Session, summary_model, tenant_id: int, scanner_type: str) -> dict | None:
    latest_scan = session.scalar(
        select(summary_model).where(
            summary_model.tenant_id == tenant_id,
            summary_model.scanner_type == scanner_type,
        ).order_by(summary_model.scanned_at.desc())
    )
    if not latest_scan or not latest_scan.agent_api_key_id:
        return None
    agent = session.get(AgentApiKey, latest_scan.agent_api_key_id)
    if not agent:
        return None
    return {
        "name": agent.name,
        "lastUsed": agent.last_used_at.isoformat() if agent.last_used_at else None,
    }


@router.post("/upload-url")
def request_upload_url(payload: dict, session: Session = Depends(get_db_session)) -> dict:
    if not payload or not isinstance(payload, dict):
        raise ValidationError("Request body must be a valid JSON object")

    tenant_id, err = validate_integer(payload.get("tenant_id") or payload.get("tenantId"), "tenant_id", min_val=1)
    if err:
        raise ValidationError(err)
    api_key, err = validate_string(payload.get("api_key") or payload.get("apiKey"), "api_key", required=True)
    if err:
        raise ValidationError(err)
    scanner_type_raw = payload.get("scanner_type") or payload.get("scannerType")
    if not scanner_type_raw:
        raise ValidationError("scanner_type is required")
    scanner_type = normalize_integration_type(scanner_type_raw)
    if not scanner_type:
        raise ValidationError(f"Invalid scanner_type. Allowed: {', '.join(ALLOWED_SCANNER_TYPES)}")
    idempotency_key = (payload.get("idempotency_key") or payload.get("idempotencyKey")) or None

    agent_api_keys = session.scalars(select(AgentApiKey).where(AgentApiKey.tenant_id == tenant_id, AgentApiKey.is_active.is_(True))).all()
    matched_key = None
    for candidate in agent_api_keys:
        if verify_access_key(api_key, candidate.api_key_hash):
            matched_key = candidate
            break
    if not matched_key:
        raise ForbiddenError("Invalid agent credentials")

    agent_integration_type = normalize_integration_type(matched_key.integration_type)
    if agent_integration_type != scanner_type:
        raise ForbiddenError(f"API key is for {matched_key.integration_type}, but scanner_type is {scanner_type}")

    upload_id = str(_uuid.uuid4())
    expires_at = datetime.utcnow() + timedelta(minutes=30)
    s3_key = f"pending/{tenant_id}/{upload_id}.json"

    pending = PendingIngestion(
        tenant_id=tenant_id,
        upload_id=upload_id,
        api_key_id=matched_key.id,
        provider=scanner_type,
        scanner_type=scanner_type,
        idempotency_key=idempotency_key,
        s3_key=s3_key,
        status="pending",
        expires_at=expires_at,
    )
    session.add(pending)
    session.commit()

    presigned_url = generate_upload_url(tenant_id=tenant_id, upload_id=upload_id, expires_in=1800)

    return {
        "upload_id": upload_id,
        "upload_url": presigned_url,
        "s3_key": s3_key,
        "expires_at": expires_at.isoformat(),
        "expires_in_seconds": 1800,
    }


@router.post("/ingest")
def ingest_scan(payload: dict, session: Session = Depends(get_db_session)) -> dict:
    if not payload or not isinstance(payload, dict):
        raise ValidationError("Request body must be a valid JSON object")

    tenant_id, err = validate_integer(payload.get("tenant_id") or payload.get("tenantId"), "tenant_id", min_val=1)
    if err:
        raise ValidationError(err)
    api_key, err = validate_string(payload.get("api_key") or payload.get("apiKey"), "api_key", required=True)
    if err:
        raise ValidationError(err)
    scanner_type_raw = payload.get("scanner_type") or payload.get("scannerType")
    if not scanner_type_raw:
        raise ValidationError("scanner_type is required")
    scanner_type = normalize_integration_type(scanner_type_raw)
    if not scanner_type:
        raise ValidationError(f"Invalid scanner_type. Allowed: {', '.join(ALLOWED_SCANNER_TYPES)}")
    idempotency_key = (payload.get("idempotency_key") or payload.get("idempotencyKey")) or None

    agent_api_keys = session.scalars(select(AgentApiKey).where(AgentApiKey.tenant_id == tenant_id, AgentApiKey.is_active.is_(True))).all()
    matched_key = None
    for candidate in agent_api_keys:
        if verify_access_key(api_key, candidate.api_key_hash):
            matched_key = candidate
            break
    if not matched_key:
        raise ForbiddenError("Invalid agent credentials")

    agent_integration_type = normalize_integration_type(matched_key.integration_type)
    if agent_integration_type != scanner_type:
        raise ForbiddenError(f"API key is for {matched_key.integration_type}, but scanner_type is {scanner_type}")

    upload_id = str(_uuid.uuid4())
    expires_at = datetime.utcnow() + timedelta(minutes=30)
    s3_key = f"pending/{tenant_id}/{upload_id}.json"

    pending = PendingIngestion(
        tenant_id=tenant_id,
        upload_id=upload_id,
        api_key_id=matched_key.id,
        provider=scanner_type,
        scanner_type=scanner_type,
        idempotency_key=idempotency_key,
        s3_key=s3_key,
        status="pending",
        expires_at=expires_at,
    )
    session.add(pending)
    session.commit()

    presigned_url = generate_upload_url(tenant_id=tenant_id, upload_id=upload_id, expires_in=1800)

    return {
        "upload_id": upload_id,
        "upload_url": presigned_url,
        "s3_key": s3_key,
        "expires_at": expires_at.isoformat(),
        "expires_in_seconds": 1800,
    }


@router.get("/snapshots")
def list_snapshot_artifacts(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    provider: str | None = None,
    snapshot_type: str | None = None,
    domain: str | None = None,
    scan_id: str | None = None,
    limit: int = 100,
) -> dict:
    resolved_domain = normalize_domain(domain, default="soc") if domain else None
    if domain and not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    query = select(SnapshotArtifact).where(SnapshotArtifact.tenant_id == effective_tenant_id_of(current_user))
    if provider:
        query = query.where(SnapshotArtifact.provider == provider)
    if snapshot_type:
        query = query.where(SnapshotArtifact.snapshot_type == snapshot_type)
    if resolved_domain:
        query = query.where(SnapshotArtifact.domain == resolved_domain)
    if scan_id:
        query = query.where(SnapshotArtifact.scan_id == scan_id)
    artifacts = session.scalars(query.order_by(SnapshotArtifact.created_at.desc()).limit(min(max(limit, 1), 200))).all()
    return {"snapshots": [artifact.to_dict() for artifact in artifacts], "count": len(artifacts)}


@router.get("/snapshots/{artifact_id}")
def get_snapshot_artifact(artifact_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    artifact = _get_snapshot_artifact_or_404(session, effective_tenant_id_of(current_user), artifact_id)
    return artifact.to_dict()


@router.get("/snapshots/{artifact_id}/payload")
def get_snapshot_payload(artifact_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    artifact = _get_snapshot_artifact_or_404(session, effective_tenant_id_of(current_user), artifact_id)
    return {
        "snapshot": artifact.to_dict(),
        "download_url": generate_download_url(s3_key=artifact.s3_key),
    }


@router.get("/snapshots/{artifact_id}/download-url")
def get_snapshot_download_url(artifact_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session), expires: int = 3600) -> dict:
    artifact = _get_snapshot_artifact_or_404(session, effective_tenant_id_of(current_user), artifact_id)
    return {
        "snapshot_id": artifact.id,
        "s3_key": artifact.s3_key,
        "download_url": generate_download_url(s3_key=artifact.s3_key, expires_in=expires),
        "expires_in_seconds": expires,
    }


@router.get("/agent/snapshots")
def agent_list_snapshot_artifacts(
    tenant_context: tuple[int, str] = Depends(get_request_tenant_context),
    session: Session = Depends(get_db_session),
    provider: str | None = None,
    snapshot_type: str | None = None,
    domain: str | None = None,
    scan_id: str | None = None,
    limit: int = 100,
) -> dict:
    tenant_id, actor = tenant_context
    if actor != "agent":
        raise ForbiddenError("Agent scope required")
    resolved_domain = normalize_domain(domain, default="soc") if domain else None
    if domain and not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    query = select(SnapshotArtifact).where(SnapshotArtifact.tenant_id == tenant_id)
    if provider:
        query = query.where(SnapshotArtifact.provider == provider)
    if snapshot_type:
        query = query.where(SnapshotArtifact.snapshot_type == snapshot_type)
    if resolved_domain:
        query = query.where(SnapshotArtifact.domain == resolved_domain)
    if scan_id:
        query = query.where(SnapshotArtifact.scan_id == scan_id)
    artifacts = session.scalars(query.order_by(SnapshotArtifact.created_at.desc()).limit(min(max(limit, 1), 200))).all()
    return {"snapshots": [artifact.to_dict() for artifact in artifacts], "count": len(artifacts)}


@router.get("/agent/snapshots/{artifact_id}")
def agent_get_snapshot_artifact(artifact_id: int, tenant_context: tuple[int, str] = Depends(get_request_tenant_context), session: Session = Depends(get_db_session)) -> dict:
    tenant_id, actor = tenant_context
    if actor != "agent":
        raise ForbiddenError("Agent scope required")
    artifact = _get_snapshot_artifact_or_404(session, tenant_id, artifact_id)
    return artifact.to_dict()


@router.get("/agent/snapshots/{artifact_id}/payload")
def agent_get_snapshot_payload(artifact_id: int, tenant_context: tuple[int, str] = Depends(get_request_tenant_context), session: Session = Depends(get_db_session)) -> dict:
    tenant_id, actor = tenant_context
    if actor != "agent":
        raise ForbiddenError("Agent scope required")
    artifact = _get_snapshot_artifact_or_404(session, tenant_id, artifact_id)
    return {
        "snapshot": artifact.to_dict(),
        "download_url": generate_download_url(s3_key=artifact.s3_key),
    }


@router.get("/agent/snapshots/{artifact_id}/download-url")
def agent_get_snapshot_download_url(artifact_id: int, tenant_context: tuple[int, str] = Depends(get_request_tenant_context), session: Session = Depends(get_db_session), expires: int = 3600) -> dict:
    tenant_id, actor = tenant_context
    if actor != "agent":
        raise ForbiddenError("Agent scope required")
    artifact = _get_snapshot_artifact_or_404(session, tenant_id, artifact_id)
    return {
        "snapshot_id": artifact.id,
        "s3_key": artifact.s3_key,
        "download_url": generate_download_url(s3_key=artifact.s3_key, expires_in=expires),
        "expires_in_seconds": expires,
    }


@router.get("")
def get_scans(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    scanner_type: str | None = None,
    status: str | None = None,
    days: int = 30,
    limit: int = 100,
    domain: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    scanner_type_normalized = normalize_integration_type(scanner_type) if scanner_type else None
    if scanner_type and not scanner_type_normalized:
        raise ValidationError(f"Invalid scanner_type. Allowed: {', '.join(ALLOWED_SCANNER_TYPES)}")
    if _is_demo_tenant(current_user, session):
        payload = demo_scans(scanner_type=scanner_type_normalized, days=days, limit=limit, tenant_id=effective_tenant_id_of(current_user))
        log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="SCAN_SUMMARIES_DEMO", payload={"count": payload.get("count", 0)})
        session.commit()
        return payload
    range_start, range_end = parse_analytics_range_args(start_date, end_date, days, default_days=days or 30, max_days=90)
    resolved_domain = _resolve_requested_domain(scanner_type_normalized, domain, default="soc")
    if not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    summary_model = _summary_model_for_domain(resolved_domain)
    query = select(summary_model).where(summary_model.tenant_id == effective_tenant_id_of(current_user), summary_model.scanned_at >= range_start, summary_model.scanned_at <= range_end)
    if scanner_type_normalized:
        query = query.where(summary_model.scanner_type == scanner_type_normalized)
    if status:
        query = query.where(summary_model.status == status)
    query = query.order_by(summary_model.scanned_at.desc()).limit(limit)
    scans = session.scalars(query).all()
    log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="SCAN_SUMMARIES", payload={"count": len(scans), "days": days, "start_date": range_start.isoformat(), "end_date": range_end.isoformat()})
    session.commit()
    return {"scans": [scan.to_dict() for scan in scans], "count": len(scans), "domain": resolved_domain, "period_days": days, "period": {"start_date": range_start.isoformat(), "end_date": range_end.isoformat()}}


@router.get("/latest")
def get_latest_scans(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    scanner_type: str | None = None,
    status: str | None = None,
    days: int = 30,
    domain: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    scanner_type_normalized = normalize_integration_type(scanner_type) if scanner_type else None
    if scanner_type and not scanner_type_normalized:
        raise ValidationError(f"Invalid scanner_type. Allowed: {', '.join(ALLOWED_SCANNER_TYPES)}")
    if _is_demo_tenant(current_user, session):
        payload = demo_latest_scans(scanner_type=scanner_type_normalized, days=days, tenant_id=effective_tenant_id_of(current_user))
        log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="SCAN_LATEST_DEMO", payload={"count": payload.get("count", 0)})
        session.commit()
        return payload
    range_start, range_end = parse_analytics_range_args(start_date, end_date, days, default_days=days or 30, max_days=90)
    resolved_domain = _resolve_requested_domain(scanner_type_normalized, domain, default="soc")
    if not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    summary_model = _summary_model_for_domain(resolved_domain)
    query = select(summary_model).where(summary_model.tenant_id == effective_tenant_id_of(current_user), summary_model.scanned_at >= range_start, summary_model.scanned_at <= range_end)
    if scanner_type_normalized:
        query = query.where(summary_model.scanner_type == scanner_type_normalized)
    if status:
        query = query.where(summary_model.status == status)
    all_scans = session.scalars(query.order_by(summary_model.scanned_at.desc())).all()
    latest_by_target = {}
    for scan in all_scans:
        target_key = None
        meta = scan.meta_info
        if isinstance(meta, dict):
            target_key = meta.get("taskId")
        if not target_key:
            target_key = scan.scan_name or scan.scan_id
        if target_key not in latest_by_target or scan.scanned_at > latest_by_target[target_key].scanned_at:
            latest_by_target[target_key] = scan
    latest_scans = list(latest_by_target.values())
    latest_scans.sort(key=lambda s: s.scanned_at, reverse=True)
    total_critical = sum(s.critical_count for s in latest_scans)
    total_high = sum(s.high_count for s in latest_scans)
    total_medium = sum(s.medium_count for s in latest_scans)
    total_low = sum(s.low_count for s in latest_scans)
    total_info = sum(s.info_count for s in latest_scans)
    total_hosts = sum(s.total_hosts or 0 for s in latest_scans)
    log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="SCAN_LATEST", payload={"count": len(latest_scans), "days": days, "start_date": range_start.isoformat(), "end_date": range_end.isoformat()})
    session.commit()
    return {
        "scans": [scan.to_dict() for scan in latest_scans],
        "count": len(latest_scans),
        "domain": resolved_domain,
        "period_days": days,
        "period": {"start_date": range_start.isoformat(), "end_date": range_end.isoformat()},
        "current_state_totals": {"critical": total_critical, "high": total_high, "medium": total_medium, "low": total_low, "info": total_info, "total_vulnerabilities": total_critical + total_high + total_medium + total_low + total_info, "total_hosts": total_hosts},
        "description": "Latest scan per unique target (taskId or scanName)",
    }


@router.get("/summary")
def get_scans_summary(current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session), days: int = 30) -> dict:
    if _is_demo_tenant(current_user, session):
        payload = demo_scans_summary(days, effective_tenant_id_of(current_user))
        log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="SCAN_DASHBOARD_DEMO", payload={"days": payload.get("period_days")})
        session.commit()
        return payload
    time_threshold = datetime.utcnow() - timedelta(days=days)
    total_scans = 0
    total_critical = total_high = total_medium = total_low = total_info = 0
    by_scanner_counts = {}
    latest_scan_candidates = []
    for summary_model in (ScanSummary, ScanSummaryNoc):
        total_scans += session.query(summary_model).filter(summary_model.tenant_id == effective_tenant_id_of(current_user), summary_model.scanned_at >= time_threshold).count()
        totals = session.query(
            func.sum(summary_model.critical_count).label("critical"),
            func.sum(summary_model.high_count).label("high"),
            func.sum(summary_model.medium_count).label("medium"),
            func.sum(summary_model.low_count).label("low"),
            func.sum(summary_model.info_count).label("info"),
        ).filter(summary_model.tenant_id == effective_tenant_id_of(current_user), summary_model.scanned_at >= time_threshold, summary_model.status == "completed").first()
        total_critical += int(totals.critical or 0) if totals else 0
        total_high += int(totals.high or 0) if totals else 0
        total_medium += int(totals.medium or 0) if totals else 0
        total_low += int(totals.low or 0) if totals else 0
        total_info += int(totals.info or 0) if totals else 0
        by_scanner_rows = session.query(summary_model.scanner_type, func.count(summary_model.id).label("count")).filter(summary_model.tenant_id == effective_tenant_id_of(current_user), summary_model.scanned_at >= time_threshold).group_by(summary_model.scanner_type).all()
        for row in by_scanner_rows:
            by_scanner_counts[row.scanner_type] = by_scanner_counts.get(row.scanner_type, 0) + int(row.count or 0)
        latest_scan_candidates.extend(session.query(summary_model).filter(summary_model.tenant_id == effective_tenant_id_of(current_user)).order_by(summary_model.scanned_at.desc()).limit(5).all())
    latest_scan_candidates.sort(key=lambda scan: scan.scanned_at or datetime.min, reverse=True)
    latest_scans = latest_scan_candidates[:5]
    trend_data = []
    for i in range(7):
        day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        day_critical = day_high = day_medium = day_low = day_info = 0
        for summary_model in (ScanSummary, ScanSummaryNoc):
            day_totals = session.query(
                func.sum(summary_model.critical_count).label("critical"),
                func.sum(summary_model.high_count).label("high"),
                func.sum(summary_model.medium_count).label("medium"),
                func.sum(summary_model.low_count).label("low"),
                func.sum(summary_model.info_count).label("info"),
            ).filter(summary_model.tenant_id == effective_tenant_id_of(current_user), summary_model.scanned_at >= day_start, summary_model.scanned_at < day_end, summary_model.status == "completed").first()
            day_critical += int(day_totals.critical or 0) if day_totals else 0
            day_high += int(day_totals.high or 0) if day_totals else 0
            day_medium += int(day_totals.medium or 0) if day_totals else 0
            day_low += int(day_totals.low or 0) if day_totals else 0
            day_info += int(day_totals.info or 0) if day_totals else 0
        trend_data.append({"date": day_start.strftime("%Y-%m-%d"), "critical": day_critical, "high": day_high, "medium": day_medium, "low": day_low, "info": day_info})
    log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="SCAN_DASHBOARD", payload={"days": days})
    session.commit()
    return {"period_days": days, "total_scans": total_scans, "vulnerability_totals": {"critical": total_critical, "high": total_high, "medium": total_medium, "low": total_low, "info": total_info}, "by_scanner": by_scanner_counts, "latest_scans": [s.to_dict() for s in latest_scans], "trend_7_days": list(reversed(trend_data)), "timestamp": datetime.utcnow().isoformat()}


@router.get("/agent/summaries")
def agent_get_scan_summaries(
    tenant_context: tuple[int, str] = Depends(get_request_tenant_context),
    session: Session = Depends(get_db_session),
    scanner_type: str | None = None,
    status: str | None = None,
    days: int = 30,
    limit: int = 100,
    domain: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    tenant_id, actor = tenant_context
    if actor != "agent":
        raise ForbiddenError("Agent scope required")
    scanner_type_normalized = normalize_integration_type(scanner_type) if scanner_type else None
    if scanner_type and not scanner_type_normalized:
        raise ValidationError(f"Invalid scanner_type. Allowed: {', '.join(ALLOWED_SCANNER_TYPES)}")
    range_start, range_end = parse_analytics_range_args(start_date, end_date, days, default_days=days or 30, max_days=90)
    resolved_domain = normalize_domain(domain, default="soc")
    if not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    summary_model = _summary_model_for_domain(resolved_domain)
    query = select(summary_model).where(summary_model.tenant_id == tenant_id, summary_model.scanned_at >= range_start, summary_model.scanned_at <= range_end)
    if scanner_type_normalized:
        query = query.where(summary_model.scanner_type == scanner_type_normalized)
    if status:
        query = query.where(summary_model.status == status)
    scans = session.scalars(query.order_by(summary_model.scanned_at.desc()).limit(min(max(limit, 1), 500))).all()
    return {"scans": [s.to_dict() for s in scans], "count": len(scans), "domain": resolved_domain, "period_days": days, "period": {"start_date": range_start.isoformat(), "end_date": range_end.isoformat()}}


@router.get("/agent/summaries/{scan_summary_id}")
def agent_get_scan_summary(scan_summary_id: int, tenant_context: tuple[int, str] = Depends(get_request_tenant_context), session: Session = Depends(get_db_session), domain: str | None = None) -> dict:
    tenant_id, actor = tenant_context
    if actor != "agent":
        raise ForbiddenError("Agent scope required")
    resolved_domain = normalize_domain(domain, default="soc")
    if not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    summary_model = _summary_model_for_domain(resolved_domain)
    scan = session.scalar(select(summary_model).where(summary_model.id == scan_summary_id, summary_model.tenant_id == tenant_id))
    if not scan:
        raise NotFoundError("Scan summary not found")
    return scan.to_dict()


@router.get("/agent/summaries/{scan_summary_id}/findings")
def agent_get_scan_summary_findings(
    scan_summary_id: int,
    tenant_context: tuple[int, str] = Depends(get_request_tenant_context),
    session: Session = Depends(get_db_session),
    domain: str | None = None,
    severity: str | None = None,
    cve: str | None = None,
    host: str | None = None,
    limit: int = 200,
) -> dict:
    tenant_id, actor = tenant_context
    if actor != "agent":
        raise ForbiddenError("Agent scope required")
    resolved_domain = normalize_domain(domain, default="soc")
    if not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    summary_model = _summary_model_for_domain(resolved_domain)
    scan = session.scalar(select(summary_model).where(summary_model.id == scan_summary_id, summary_model.tenant_id == tenant_id))
    if not scan:
        raise NotFoundError("Scan summary not found")
    filter_col = FindingIndex.scan_summary_noc_id if resolved_domain == "noc" else FindingIndex.scan_summary_soc_id
    query = select(FindingIndex).where(filter_col == scan_summary_id)
    if severity:
        query = query.where(FindingIndex.severity.ilike(f"%{severity}%"))
    if cve:
        col = FindingIndex.event_type if resolved_domain == "noc" else FindingIndex.cve
        query = query.where(col.ilike(f"%{cve}%"))
    if host:
        query = query.where(FindingIndex.host.ilike(f"%{host}%"))
    findings = session.scalars(query.limit(min(max(limit, 1), 1000))).all()
    payload = [finding.to_dict() for finding in findings]
    return {"findings": payload, "count": len(payload), "scan_summary_id": scan_summary_id, "domain": resolved_domain}


@router.get("/agent/findings")
def agent_get_findings(
    tenant_context: tuple[int, str] = Depends(get_request_tenant_context),
    session: Session = Depends(get_db_session),
    domain: str | None = None,
    scanner_type: str | None = None,
    severity: str | None = None,
    cve: str | None = None,
    host: str | None = None,
    days: int = 30,
    limit: int = 200,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    tenant_id, actor = tenant_context
    if actor != "agent":
        raise ForbiddenError("Agent scope required")
    resolved_domain = normalize_domain(domain, default="soc")
    if not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    scanner_type_normalized = normalize_integration_type(scanner_type) if scanner_type else None
    if scanner_type and not scanner_type_normalized:
        raise ValidationError(f"Invalid scanner_type. Allowed: {', '.join(ALLOWED_SCANNER_TYPES)}")
    range_start, range_end = parse_analytics_range_args(start_date, end_date, days, default_days=days or 30, max_days=90)
    join_col = FindingIndex.scan_summary_noc_id if resolved_domain == "noc" else FindingIndex.scan_summary_soc_id
    summary_model = ScanSummaryNoc if resolved_domain == "noc" else ScanSummary
    query = session.query(FindingIndex, summary_model).join(summary_model, join_col == summary_model.id).filter(summary_model.tenant_id == tenant_id, summary_model.scanned_at >= range_start, summary_model.scanned_at <= range_end)
    if scanner_type_normalized:
        query = query.filter(summary_model.scanner_type == scanner_type_normalized)
    if severity:
        query = query.filter(FindingIndex.severity.ilike(f"%{severity}%"))
    cve_col = FindingIndex.event_type if resolved_domain == "noc" else FindingIndex.cve
    if cve:
        query = query.filter(cve_col.ilike(f"%{cve}%"))
    if host:
        query = query.filter(FindingIndex.host.ilike(f"%{host}%"))
    rows = query.order_by(summary_model.scanned_at.desc()).limit(min(max(limit, 1), 1000)).all()
    result = []
    for finding, summary in rows:
        payload = finding.to_dict()
        payload["scan_summary"] = {"id": summary.id, "scanner_type": summary.scanner_type, "scan_id": summary.scan_id, "scan_name": summary.scan_name, "scanned_at": summary.scanned_at.isoformat() if summary.scanned_at else None}
        result.append(payload)
    return {"findings": result, "count": len(result), "domain": resolved_domain, "period_days": days, "period": {"start_date": range_start.isoformat(), "end_date": range_end.isoformat()}}


@router.get("/{scanner_type}/analytics")
def get_scanner_analytics(
    scanner_type: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 30,
) -> dict:
    scanner_type_normalized = normalize_integration_type(scanner_type)
    if not scanner_type_normalized:
        raise ValidationError(f"Invalid scanner_type. Allowed: {', '.join(ALLOWED_SCANNER_TYPES)}")
    if _is_demo_tenant(current_user, session):
        payload = demo_scanner_analytics(scanner_type_normalized)
        log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="SCAN_ANALYTICS_DEMO", payload={"scanner_type": scanner_type_normalized})
        session.commit()
        return payload
    range_start, range_end = parse_analytics_range_args(start_date, end_date, days, default_days=30, max_days=90)
    domain = _infer_domain_from_integration(scanner_type_normalized) or "soc"
    summary_model = ScanSummaryNoc if domain == "noc" else ScanSummary
    join_col = FindingIndex.scan_summary_noc_id if domain == "noc" else FindingIndex.scan_summary_soc_id
    summaries = session.scalars(select(summary_model).where(summary_model.tenant_id == effective_tenant_id_of(current_user), summary_model.scanner_type == scanner_type_normalized, summary_model.scanned_at >= range_start, summary_model.scanned_at <= range_end)).all()
    top_items = []
    cve_col = FindingIndex.event_type if domain == "noc" else FindingIndex.cve
    rows = session.query(cve_col, FindingIndex.severity, func.count(func.distinct(FindingIndex.host)).label("host_count"), func.max(FindingIndex.cvss).label("cvss_score")).join(summary_model, join_col == summary_model.id).filter(summary_model.tenant_id == effective_tenant_id_of(current_user), summary_model.scanner_type == scanner_type_normalized, summary_model.scanned_at >= range_start, summary_model.scanned_at <= range_end, cve_col.is_not(None), cve_col != "").group_by(cve_col, FindingIndex.severity).all()
    for row in rows:
        impact = (row.host_count or 0) * (row.cvss_score or 0)
        top_items.append({"cve_id": getattr(row, cve_col.key), "severity": row.severity, "hosts_affected": row.host_count, "cvss_score": row.cvss_score, "impact_score": impact})
    top_items.sort(key=lambda item: item["impact_score"], reverse=True)
    trend = []
    range_start_day = range_start.replace(hour=0, minute=0, second=0, microsecond=0)
    range_end_day = range_end.replace(hour=0, minute=0, second=0, microsecond=0)
    total_days = max(1, (range_end_day - range_start_day).days + 1)
    for i in range(total_days):
        day_start = range_start_day + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        counts = session.query(func.sum(summary_model.critical_count).label("critical"), func.sum(summary_model.high_count).label("high"), func.sum(summary_model.medium_count).label("medium"), func.sum(summary_model.low_count).label("low"), func.sum(summary_model.info_count).label("info")).filter(summary_model.tenant_id == effective_tenant_id_of(current_user), summary_model.scanner_type == scanner_type_normalized, summary_model.scanned_at >= day_start, summary_model.scanned_at < day_end, summary_model.status == "completed").first()
        trend.append({"date": day_start.strftime("%Y-%m-%d"), "critical": int(counts.critical or 0), "high": int(counts.high or 0), "medium": int(counts.medium or 0), "low": int(counts.low or 0), "info": int(counts.info or 0)})
    total_hosts = sum(summary.total_hosts or 0 for summary in summaries)
    most_critical_host_row = session.query(
        FindingIndex.host,
        func.count(FindingIndex.id).label("critical_count"),
    ).join(summary_model, join_col == summary_model.id).filter(
        summary_model.tenant_id == effective_tenant_id_of(current_user),
        summary_model.scanner_type == scanner_type_normalized,
        summary_model.scanned_at >= range_start,
        summary_model.scanned_at <= range_end,
        FindingIndex.severity.ilike("%critical%"),
    ).group_by(FindingIndex.host).order_by(func.count(FindingIndex.id).desc()).first()
    recent_findings = []
    rows = session.query(FindingIndex, summary_model).join(summary_model, join_col == summary_model.id).filter(summary_model.tenant_id == effective_tenant_id_of(current_user), summary_model.scanner_type == scanner_type_normalized, summary_model.scanned_at >= range_start, summary_model.scanned_at <= range_end).order_by(case((FindingIndex.severity.ilike("%critical%"), 1), (FindingIndex.severity.ilike("%high%"), 2), (FindingIndex.severity.ilike("%medium%"), 3), (FindingIndex.severity.ilike("%low%"), 4), else_=5), summary_model.scanned_at.desc()).limit(20).all()
    for finding, summary in rows:
        recent_findings.append({"cve": finding.event_type if domain == "noc" else finding.cve, "name": finding.name, "host": finding.host, "severity": finding.severity, "cvss": finding.cvss, "detectedAt": summary.scanned_at.isoformat() if summary.scanned_at else None})
    return {"success": True, "domain": domain, "scanner_type": scanner_type_normalized, "period": {"start_date": range_start.isoformat(), "end_date": range_end.isoformat(), "days": max(1, (range_end - range_start).days + 1)}, "topCVEs": top_items[:10], "trend_7_days": trend, "hostDistribution": {"totalUniqueHosts": total_hosts, "avgVulnerabilitiesPerHost": round((sum((summary.critical_count + summary.high_count + summary.medium_count + summary.low_count + summary.info_count) for summary in summaries) / total_hosts), 2) if total_hosts else 0, "mostCriticalHost": {"host": most_critical_host_row.host if most_critical_host_row else None, "criticalCount": int(most_critical_host_row.critical_count or 0) if most_critical_host_row else 0}}, "recentFindings": recent_findings, "agentInfo": _build_agent_info(session, summary_model, effective_tenant_id_of(current_user), scanner_type_normalized)}


@router.get("/{scan_summary_id}")
def get_scan(scan_summary_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session), domain: str | None = None) -> dict:
    resolved_domain = normalize_domain(domain, default="soc")
    if not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    if _is_demo_tenant(current_user, session):
        scan = demo_scan(scan_summary_id, effective_tenant_id_of(current_user))
        if not scan:
            raise NotFoundError("Scan summary not found")
        return scan
    summary_model = _summary_model_for_domain(resolved_domain)
    scan = session.get(summary_model, scan_summary_id)
    if not scan or scan.tenant_id != effective_tenant_id_of(current_user):
        raise NotFoundError("Scan summary not found")
    return scan.to_dict()


@router.get("/{scan_summary_id}/findings")
def get_scan_findings(scan_summary_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session), domain: str | None = None) -> dict:
    resolved_domain = normalize_domain(domain, default="soc")
    if not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    if _is_demo_tenant(current_user, session):
        return demo_scan_findings(scan_summary_id)
    summary_model = _summary_model_for_domain(resolved_domain)
    scan = session.scalar(select(summary_model).where(summary_model.id == scan_summary_id, summary_model.tenant_id == effective_tenant_id_of(current_user)))
    if not scan:
        raise NotFoundError("Scan summary not found")
    if resolved_domain == "noc":
        findings = session.scalars(select(FindingIndex).where(FindingIndex.scan_summary_noc_id == scan_summary_id)).all()
    else:
        findings = session.scalars(select(FindingIndex).where(FindingIndex.scan_summary_soc_id == scan_summary_id)).all()
    payload = [finding.to_dict() for finding in findings]
    return {"findings": payload, "count": len(payload), "domain": resolved_domain}


@findings_router.get("/{finding_id}")
def get_finding_detail(finding_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    finding = session.scalar(
        select(FindingIndex).where(
            FindingIndex.id == finding_id,
            FindingIndex.tenant_id == effective_tenant_id_of(current_user),
        )
    )
    if not finding:
        raise NotFoundError("Finding not found")
    return finding.to_dict()
