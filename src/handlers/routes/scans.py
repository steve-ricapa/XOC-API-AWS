from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.persistence.db import get_db_session
from src.persistence.models import AgentApiKey, Company, IngestIdempotencyRecord, Integration, ScanFinding, ScanNocEvent, ScanSummary, ScanSummaryNoc, SnapshotArtifact, User
from src.shared.config import get_settings
from src.shared.context import log_audit
from src.shared.dependencies import get_current_user, get_request_claims, get_request_company_context
from src.shared.errors import ForbiddenError, NotFoundError, ValidationError
from src.shared.integration_types import OFFICIAL_INTEGRATION_TYPES, normalize_integration_type
from src.shared.scans_demo_data import demo_latest_scans, demo_scan, demo_scan_findings, demo_scanner_analytics, demo_scans, demo_scans_summary
from src.shared.security_keys import verify_access_key
from src.shared.snapshots import SnapshotArtifactInput, fetch_snapshot_payload, store_snapshot_artifact


router = APIRouter(prefix="/scans", tags=["scans"])

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


def _is_demo_company(current_user: User, session: Session) -> bool:
    company = getattr(current_user, "company", None)
    if not company:
        company = session.get(Company, current_user.company_id)
    plan_status = (getattr(company, "plan_status", "") or "").strip().upper()
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


def _findings_model_for_domain(domain: str):
    return ScanNocEvent if domain == "noc" else ScanFinding


def _resolve_requested_domain(scanner_type: str | None = None, domain_raw: str | None = None, default: str = "soc"):
    domain = normalize_domain(domain_raw, default=default)
    if domain_raw and not domain:
        return None
    inferred = _infer_domain_from_integration(scanner_type) if scanner_type else None
    return inferred or domain


def build_request_hash(company_id, scan_id, scanner_type, summary_type, domain, scanned_at_str, findings_data, summary_data):
    payload = {
        "company_id": company_id,
        "scan_id": scan_id,
        "scanner_type": scanner_type,
        "summary_type": summary_type,
        "domain": domain,
        "scanned_at": scanned_at_str,
        "status": summary_data.get("status"),
        "results": summary_data.get("results"),
        "cvss_max": summary_data.get("cvss_max"),
        "total_hosts": summary_data.get("total_hosts"),
        "scan_name": summary_data.get("scan_name") or summary_data.get("target"),
        "meta": summary_data.get("meta"),
        "findings": findings_data,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_optional_iso8601(value, field_name: str):
    if value is None or value == "":
        return None, None
    return validate_iso8601(value, field_name)


def _get_snapshot_artifact_or_404(session: Session, company_id: int, artifact_id: int) -> SnapshotArtifact:
    artifact = session.scalar(select(SnapshotArtifact).where(SnapshotArtifact.id == artifact_id, SnapshotArtifact.company_id == company_id))
    if not artifact:
        raise NotFoundError("Snapshot artifact not found")
    return artifact


def _resolve_snapshot_integration_id(session: Session, company_id: int, provider: str) -> int | None:
    integration = session.scalar(
        select(Integration).where(
            Integration.company_id == company_id,
            (Integration.type == provider) | (Integration.provider == provider),
        )
    )
    return integration.id if integration else None


def _build_agent_info(session: Session, summary_model, company_id: int, scanner_type: str) -> dict | None:
    latest_scan = session.scalar(
        select(summary_model).where(
            summary_model.company_id == company_id,
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


def _persist_snapshot_artifact(
    *,
    session: Session,
    payload: dict,
    company_id: int,
    scanner_type: str,
    summary_type: str,
    domain: str,
    scan_id: str,
    scan_summary,
):
    bucket_name = get_settings().snapshots_bucket_name
    if not bucket_name:
        return None

    existing_artifact = session.scalar(
        select(SnapshotArtifact).where(
            SnapshotArtifact.company_id == company_id,
            SnapshotArtifact.scan_id == scan_id,
            SnapshotArtifact.provider == scanner_type,
            SnapshotArtifact.snapshot_type == summary_type,
            SnapshotArtifact.domain == domain,
        )
    )

    return store_snapshot_artifact(
        session=session,
        payload=payload,
        existing_artifact=existing_artifact,
        artifact_input=SnapshotArtifactInput(
            company_id=company_id,
            integration_id=_resolve_snapshot_integration_id(session, company_id, scanner_type),
            provider=scanner_type,
            snapshot_type=summary_type,
            domain=domain,
            source="scan_ingest",
            status="stored",
            scan_id=scan_id,
            scan_summary_soc_id=scan_summary.id if domain != "noc" else None,
            scan_summary_noc_id=scan_summary.id if domain == "noc" else None,
            captured_at=scan_summary.scanned_at,
            received_at=datetime.utcnow(),
            summary_json={
                "scanner_type": scanner_type,
                "summary_type": summary_type,
                "domain": domain,
                "findings_count": len(payload.get("findings") or []),
                "scan_summary": payload.get("scan_summary"),
            },
        ),
    )


@router.post("/ingest")
def ingest_scan(payload: dict, session: Session = Depends(get_db_session)) -> dict:
    if not payload or not isinstance(payload, dict):
        raise ValidationError("Request body must be a valid JSON object")

    company_id, err = validate_integer(payload.get("company_id") or payload.get("companyId"), "company_id", min_val=1)
    if err:
        raise ValidationError(err)
    api_key, err = validate_string(payload.get("api_key") or payload.get("apiKey"), "api_key", required=True)
    if err:
        raise ValidationError(err)
    idempotency_key, err = validate_idempotency_key(payload.get("idempotency_key") or payload.get("idempotencyKey"))
    if err:
        raise ValidationError(err)
    scan_id, err = validate_string(payload.get("scan_id") or payload.get("scanId"), "scan_id", required=True, max_length=MAX_SCAN_ID_LENGTH)
    if err:
        raise ValidationError(err)

    scan_summary_data = payload.get("scan_summary")
    if not isinstance(scan_summary_data, dict):
        raise ValidationError("Field 'scan_summary' must be an object")

    scanner_type_raw = scan_summary_data.get("scanner_type") or payload.get("scannerType") or payload.get("scanner_type")
    if not scanner_type_raw:
        raise ValidationError("scanner_type is required")
    scanner_type = normalize_integration_type(scanner_type_raw)
    if not scanner_type:
        raise ValidationError(f"Invalid scanner_type. Allowed: {', '.join(ALLOWED_SCANNER_TYPES)}")

    findings_data = payload.get("findings", []) or []
    if not isinstance(findings_data, list):
        raise ValidationError("Field 'findings' must be an array")

    validated_findings = []
    for index, finding_raw in enumerate(findings_data):
        if not isinstance(finding_raw, dict):
            raise ValidationError(f"findings[{index}] must be an object")
        name, err = validate_string(finding_raw.get("name"), f"findings[{index}].name", required=True, max_length=500)
        if err:
            raise ValidationError(err)
        severity_value, err = validate_string(finding_raw.get("severity") or finding_raw.get("severity_level"), f"findings[{index}].severity", required=True, max_length=50)
        if err:
            raise ValidationError(err)
        normalized_severity = normalize_severity(severity_value)
        if not normalized_severity:
            raise ValidationError(f"findings[{index}].severity is invalid")
        normalized_finding = dict(finding_raw)
        normalized_finding["name"] = name
        normalized_finding["severity"] = normalized_severity
        validated_findings.append(normalized_finding)

    scanned_at_str = scan_summary_data.get("scanned_at") or payload.get("scannedAt")
    scanned_at, err = validate_iso8601(scanned_at_str, "scanned_at")
    if err:
        raise ValidationError(err)

    agent_api_keys = session.scalars(select(AgentApiKey).where(AgentApiKey.company_id == company_id, AgentApiKey.is_active.is_(True))).all()
    agent_api_key = None
    for candidate in agent_api_keys:
        if verify_access_key(api_key, candidate.api_key_hash):
            agent_api_key = candidate
            break
    if not agent_api_key:
        raise ForbiddenError("Invalid agent credentials")

    agent_integration_type = normalize_integration_type(agent_api_key.integration_type)
    if agent_integration_type != scanner_type:
        raise ForbiddenError(f"API key is for {agent_api_key.integration_type}, but payload scanner_type is {scanner_type}")

    summary_type, domain, err = resolve_summary_type_and_domain(
        scanner_type=scanner_type,
        summary_type_raw=scan_summary_data.get("summary_type") or scan_summary_data.get("summaryType") or payload.get("summary_type") or payload.get("summaryType"),
        api_key_integration_type=agent_integration_type,
    )
    if err:
        raise ValidationError(err)

    request_hash = build_request_hash(company_id, scan_id, scanner_type, summary_type, domain, scanned_at_str, validated_findings, scan_summary_data)

    idempotency_record = None
    insert_attempts = 0
    while idempotency_record is None:
        insert_attempts += 1
        try:
            idempotency_record = IngestIdempotencyRecord(company_id=company_id, idempotency_key=idempotency_key, request_hash=request_hash, domain=domain)
            session.add(idempotency_record)
            session.flush()
        except IntegrityError:
            session.rollback()
            existing_record = session.scalar(select(IngestIdempotencyRecord).where(IngestIdempotencyRecord.company_id == company_id, IngestIdempotencyRecord.idempotency_key == idempotency_key))
            if existing_record is None:
                if insert_attempts < 3:
                    continue
                raise ValidationError("Unable to resolve idempotency state")
            if existing_record.request_hash != request_hash:
                return {"success": False, "error": "IDEMPOTENCY_CONFLICT", "message": "idempotency_key already used with different payload"}
            replay_summary = None
            replay_domain = (existing_record.domain or "soc").strip().lower()
            if replay_domain == "noc" and existing_record.scan_summary_noc_id:
                replay_summary = session.scalar(select(ScanSummaryNoc).where(ScanSummaryNoc.id == existing_record.scan_summary_noc_id, ScanSummaryNoc.company_id == company_id))
            elif existing_record.scan_summary_soc_id:
                replay_summary = session.scalar(select(ScanSummary).where(ScanSummary.id == existing_record.scan_summary_soc_id, ScanSummary.company_id == company_id))
            return {
                "success": True,
                "message": "Idempotent replay: request already processed",
                "ingest_status": "idempotent_replay",
                "domain": replay_domain,
                "data": replay_summary.to_dict() if replay_summary else None,
            }

    agent_api_key.last_used_at = datetime.utcnow()
    summary_model = _summary_model_for_domain(domain)
    existing_summary = session.scalar(select(summary_model).where(summary_model.company_id == company_id, summary_model.scan_id == scan_id))
    if existing_summary:
        scan_summary = existing_summary
    else:
        scan_summary = summary_model(company_id=company_id, scan_id=scan_id, scanned_at=scanned_at)
        session.add(scan_summary)

    scan_summary.scanner_type = scanner_type
    scan_summary.summary_type = summary_type
    scan_summary.status = scan_summary_data.get("status", payload.get("status", "completed"))
    scan_summary.scanned_at = scanned_at
    scan_summary.agent_api_key_id = agent_api_key.id

    results_raw = scan_summary_data.get("results") or payload.get("results", {})
    critical = int(results_raw.get("critical", 0))
    high = int(results_raw.get("high", 0))
    medium = int(results_raw.get("medium", 0))
    low = int(results_raw.get("low", 0))
    info = int(results_raw.get("info", 0))

    recalculated = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for finding in validated_findings:
        sev = finding.get("severity")
        if sev in recalculated:
            recalculated[sev] += 1
    critical = critical or recalculated["critical"]
    high = high or recalculated["high"]
    medium = medium or recalculated["medium"]
    low = low or recalculated["low"]
    info = info or recalculated["info"]

    scan_summary.critical_count = critical
    scan_summary.high_count = high
    scan_summary.medium_count = medium
    scan_summary.low_count = low
    scan_summary.info_count = info
    scan_summary.cvss_max = float(scan_summary_data.get("cvss_max") or payload.get("cvssMax", 0.0))
    scan_summary.total_hosts = scan_summary_data.get("total_hosts") or payload.get("totalHosts")
    scan_summary.scan_name = scan_summary_data.get("scan_name") or scan_summary_data.get("target") or payload.get("scanName")
    scan_summary.meta_info = scan_summary_data.get("meta") or payload.get("meta")
    scan_summary.received_at = datetime.utcnow()
    session.flush()

    if domain == "noc":
        session.query(ScanNocEvent).filter_by(scan_summary_noc_id=scan_summary.id).delete()
        for finding_data in validated_findings:
            started_at, start_err = _parse_optional_iso8601(finding_data.get("started_at") or finding_data.get("start_time"), "findings[].started_at")
            if start_err:
                raise ValidationError(start_err)
            ended_at, end_err = _parse_optional_iso8601(finding_data.get("ended_at") or finding_data.get("end_time"), "findings[].ended_at")
            if end_err:
                raise ValidationError(end_err)
            session.add(
                ScanNocEvent(
                    scan_summary_noc_id=scan_summary.id,
                    scan_id=scan_id,
                    name=finding_data.get("name"),
                    severity=finding_data.get("severity"),
                    event_type=finding_data.get("event_type") or finding_data.get("type") or finding_data.get("category"),
                    status=finding_data.get("status"),
                    source=finding_data.get("source"),
                    host=finding_data.get("host"),
                    service=finding_data.get("service"),
                    description=finding_data.get("description"),
                    impact=finding_data.get("impact"),
                    started_at=started_at,
                    ended_at=ended_at,
                    meta_info=finding_data.get("meta") if isinstance(finding_data.get("meta"), dict) else None,
                )
            )
        idempotency_record.scan_summary_noc_id = scan_summary.id
    else:
        session.query(ScanFinding).filter_by(scan_summary_id=scan_summary.id).delete()
        for finding_data in validated_findings:
            session.add(
                ScanFinding(
                    scan_summary_id=scan_summary.id,
                    scan_id=scan_id,
                    name=finding_data.get("name"),
                    severity=finding_data.get("severity"),
                    cvss=finding_data.get("cvss"),
                    cve=finding_data.get("cve"),
                    oid=finding_data.get("oid"),
                    host=finding_data.get("host"),
                    port=finding_data.get("port"),
                    protocol=finding_data.get("protocol"),
                    description=finding_data.get("description"),
                    solution=finding_data.get("solution"),
                    impact=finding_data.get("impact"),
                )
            )
        idempotency_record.scan_summary_soc_id = scan_summary.id

    _persist_snapshot_artifact(
        session=session,
        payload=payload,
        company_id=company_id,
        scanner_type=scanner_type,
        summary_type=summary_type,
        domain=domain,
        scan_id=scan_id,
        scan_summary=scan_summary,
    )

    session.commit()
    return {"success": True, "message": "Scan report processed successfully", "ingest_status": "created", "domain": domain, "data": scan_summary.to_dict()}


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
    query = select(SnapshotArtifact).where(SnapshotArtifact.company_id == current_user.company_id)
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
    artifact = _get_snapshot_artifact_or_404(session, current_user.company_id, artifact_id)
    return artifact.to_dict()


@router.get("/snapshots/{artifact_id}/payload")
def get_snapshot_payload(artifact_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session)) -> dict:
    artifact = _get_snapshot_artifact_or_404(session, current_user.company_id, artifact_id)
    return {
        "snapshot": artifact.to_dict(),
        "payload": fetch_snapshot_payload(key=artifact.s3_key),
    }


@router.get("/agent/snapshots")
def agent_list_snapshot_artifacts(
    company_context: tuple[int, str] = Depends(get_request_company_context),
    session: Session = Depends(get_db_session),
    provider: str | None = None,
    snapshot_type: str | None = None,
    domain: str | None = None,
    scan_id: str | None = None,
    limit: int = 100,
) -> dict:
    company_id, actor = company_context
    if actor != "agent":
        raise ForbiddenError("Agent scope required")
    resolved_domain = normalize_domain(domain, default="soc") if domain else None
    if domain and not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    query = select(SnapshotArtifact).where(SnapshotArtifact.company_id == company_id)
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
def agent_get_snapshot_artifact(artifact_id: int, company_context: tuple[int, str] = Depends(get_request_company_context), session: Session = Depends(get_db_session)) -> dict:
    company_id, actor = company_context
    if actor != "agent":
        raise ForbiddenError("Agent scope required")
    artifact = _get_snapshot_artifact_or_404(session, company_id, artifact_id)
    return artifact.to_dict()


@router.get("/agent/snapshots/{artifact_id}/payload")
def agent_get_snapshot_payload(artifact_id: int, company_context: tuple[int, str] = Depends(get_request_company_context), session: Session = Depends(get_db_session)) -> dict:
    company_id, actor = company_context
    if actor != "agent":
        raise ForbiddenError("Agent scope required")
    artifact = _get_snapshot_artifact_or_404(session, company_id, artifact_id)
    return {
        "snapshot": artifact.to_dict(),
        "payload": fetch_snapshot_payload(key=artifact.s3_key),
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
    if _is_demo_company(current_user, session):
        payload = demo_scans(scanner_type=scanner_type_normalized, days=days, limit=limit, company_id=current_user.company_id)
        log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="SCAN_SUMMARIES_DEMO", payload={"count": payload.get("count", 0)})
        session.commit()
        return payload
    range_start, range_end = parse_analytics_range_args(start_date, end_date, days, default_days=days or 30, max_days=90)
    resolved_domain = _resolve_requested_domain(scanner_type_normalized, domain, default="soc")
    if not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    summary_model = _summary_model_for_domain(resolved_domain)
    query = select(summary_model).where(summary_model.company_id == current_user.company_id, summary_model.scanned_at >= range_start, summary_model.scanned_at <= range_end)
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
    if _is_demo_company(current_user, session):
        payload = demo_latest_scans(scanner_type=scanner_type_normalized, days=days, company_id=current_user.company_id)
        log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="SCAN_LATEST_DEMO", payload={"count": payload.get("count", 0)})
        session.commit()
        return payload
    range_start, range_end = parse_analytics_range_args(start_date, end_date, days, default_days=days or 30, max_days=90)
    resolved_domain = _resolve_requested_domain(scanner_type_normalized, domain, default="soc")
    if not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    summary_model = _summary_model_for_domain(resolved_domain)
    query = select(summary_model).where(summary_model.company_id == current_user.company_id, summary_model.scanned_at >= range_start, summary_model.scanned_at <= range_end)
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
    if _is_demo_company(current_user, session):
        payload = demo_scans_summary(days, current_user.company_id)
        log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="SCAN_DASHBOARD_DEMO", payload={"days": payload.get("period_days")})
        session.commit()
        return payload
    time_threshold = datetime.utcnow() - timedelta(days=days)
    total_scans = 0
    total_critical = total_high = total_medium = total_low = total_info = 0
    by_scanner_counts = {}
    latest_scan_candidates = []
    for summary_model in (ScanSummary, ScanSummaryNoc):
        total_scans += session.query(summary_model).filter(summary_model.company_id == current_user.company_id, summary_model.scanned_at >= time_threshold).count()
        totals = session.query(
            func.sum(summary_model.critical_count).label("critical"),
            func.sum(summary_model.high_count).label("high"),
            func.sum(summary_model.medium_count).label("medium"),
            func.sum(summary_model.low_count).label("low"),
            func.sum(summary_model.info_count).label("info"),
        ).filter(summary_model.company_id == current_user.company_id, summary_model.scanned_at >= time_threshold, summary_model.status == "completed").first()
        total_critical += int(totals.critical or 0) if totals else 0
        total_high += int(totals.high or 0) if totals else 0
        total_medium += int(totals.medium or 0) if totals else 0
        total_low += int(totals.low or 0) if totals else 0
        total_info += int(totals.info or 0) if totals else 0
        by_scanner_rows = session.query(summary_model.scanner_type, func.count(summary_model.id).label("count")).filter(summary_model.company_id == current_user.company_id, summary_model.scanned_at >= time_threshold).group_by(summary_model.scanner_type).all()
        for row in by_scanner_rows:
            by_scanner_counts[row.scanner_type] = by_scanner_counts.get(row.scanner_type, 0) + int(row.count or 0)
        latest_scan_candidates.extend(session.query(summary_model).filter(summary_model.company_id == current_user.company_id).order_by(summary_model.scanned_at.desc()).limit(5).all())
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
            ).filter(summary_model.company_id == current_user.company_id, summary_model.scanned_at >= day_start, summary_model.scanned_at < day_end, summary_model.status == "completed").first()
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
    company_context: tuple[int, str] = Depends(get_request_company_context),
    session: Session = Depends(get_db_session),
    scanner_type: str | None = None,
    status: str | None = None,
    days: int = 30,
    limit: int = 100,
    domain: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    company_id, actor = company_context
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
    query = select(summary_model).where(summary_model.company_id == company_id, summary_model.scanned_at >= range_start, summary_model.scanned_at <= range_end)
    if scanner_type_normalized:
        query = query.where(summary_model.scanner_type == scanner_type_normalized)
    if status:
        query = query.where(summary_model.status == status)
    scans = session.scalars(query.order_by(summary_model.scanned_at.desc()).limit(min(max(limit, 1), 500))).all()
    return {"scans": [s.to_dict() for s in scans], "count": len(scans), "domain": resolved_domain, "period_days": days, "period": {"start_date": range_start.isoformat(), "end_date": range_end.isoformat()}}


@router.get("/agent/summaries/{scan_summary_id}")
def agent_get_scan_summary(scan_summary_id: int, company_context: tuple[int, str] = Depends(get_request_company_context), session: Session = Depends(get_db_session), domain: str | None = None) -> dict:
    company_id, actor = company_context
    if actor != "agent":
        raise ForbiddenError("Agent scope required")
    resolved_domain = normalize_domain(domain, default="soc")
    if not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    summary_model = _summary_model_for_domain(resolved_domain)
    scan = session.scalar(select(summary_model).where(summary_model.id == scan_summary_id, summary_model.company_id == company_id))
    if not scan:
        raise NotFoundError("Scan summary not found")
    return scan.to_dict()


@router.get("/agent/summaries/{scan_summary_id}/findings")
def agent_get_scan_summary_findings(
    scan_summary_id: int,
    company_context: tuple[int, str] = Depends(get_request_company_context),
    session: Session = Depends(get_db_session),
    domain: str | None = None,
    severity: str | None = None,
    cve: str | None = None,
    host: str | None = None,
    limit: int = 200,
) -> dict:
    company_id, actor = company_context
    if actor != "agent":
        raise ForbiddenError("Agent scope required")
    resolved_domain = normalize_domain(domain, default="soc")
    if not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    summary_model = _summary_model_for_domain(resolved_domain)
    scan = session.scalar(select(summary_model).where(summary_model.id == scan_summary_id, summary_model.company_id == company_id))
    if not scan:
        raise NotFoundError("Scan summary not found")
    if resolved_domain == "noc":
        query = select(ScanNocEvent).where(ScanNocEvent.scan_summary_noc_id == scan_summary_id)
        if severity:
            query = query.where(ScanNocEvent.severity.ilike(f"%{severity}%"))
        if cve:
            query = query.where(ScanNocEvent.event_type.ilike(f"%{cve}%"))
        if host:
            query = query.where(ScanNocEvent.host.ilike(f"%{host}%"))
        findings = session.scalars(query.limit(min(max(limit, 1), 1000))).all()
        payload = [finding.to_dict() for finding in findings]
        return {"events": payload, "findings": payload, "count": len(payload), "scan_summary_id": scan_summary_id, "domain": resolved_domain}
    query = select(ScanFinding).where(ScanFinding.scan_summary_id == scan_summary_id)
    if severity:
        query = query.where(ScanFinding.severity.ilike(f"%{severity}%"))
    if cve:
        query = query.where(ScanFinding.cve.ilike(f"%{cve}%"))
    if host:
        query = query.where(ScanFinding.host.ilike(f"%{host}%"))
    findings = session.scalars(query.limit(min(max(limit, 1), 1000))).all()
    return {"findings": [finding.to_dict() for finding in findings], "count": len(findings), "scan_summary_id": scan_summary_id, "domain": resolved_domain}


@router.get("/agent/findings")
def agent_get_findings(
    company_context: tuple[int, str] = Depends(get_request_company_context),
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
    company_id, actor = company_context
    if actor != "agent":
        raise ForbiddenError("Agent scope required")
    resolved_domain = normalize_domain(domain, default="soc")
    if not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    scanner_type_normalized = normalize_integration_type(scanner_type) if scanner_type else None
    if scanner_type and not scanner_type_normalized:
        raise ValidationError(f"Invalid scanner_type. Allowed: {', '.join(ALLOWED_SCANNER_TYPES)}")
    range_start, range_end = parse_analytics_range_args(start_date, end_date, days, default_days=days or 30, max_days=90)
    if resolved_domain == "noc":
        query = session.query(ScanNocEvent, ScanSummaryNoc).join(ScanSummaryNoc, ScanNocEvent.scan_summary_noc_id == ScanSummaryNoc.id).filter(ScanSummaryNoc.company_id == company_id, ScanSummaryNoc.scanned_at >= range_start, ScanSummaryNoc.scanned_at <= range_end)
        if scanner_type_normalized:
            query = query.filter(ScanSummaryNoc.scanner_type == scanner_type_normalized)
        if severity:
            query = query.filter(ScanNocEvent.severity.ilike(f"%{severity}%"))
        if cve:
            query = query.filter(ScanNocEvent.event_type.ilike(f"%{cve}%"))
        if host:
            query = query.filter(ScanNocEvent.host.ilike(f"%{host}%"))
        rows = query.order_by(ScanSummaryNoc.scanned_at.desc()).limit(min(max(limit, 1), 1000)).all()
        result = []
        for event, summary in rows:
            payload = event.to_dict()
            payload["scan_summary"] = {"id": summary.id, "scanner_type": summary.scanner_type, "scan_id": summary.scan_id, "scan_name": summary.scan_name, "scanned_at": summary.scanned_at.isoformat() if summary.scanned_at else None}
            result.append(payload)
        return {"events": result, "findings": result, "count": len(result), "domain": resolved_domain, "period_days": days, "period": {"start_date": range_start.isoformat(), "end_date": range_end.isoformat()}}
    query = session.query(ScanFinding, ScanSummary).join(ScanSummary, ScanFinding.scan_summary_id == ScanSummary.id).filter(ScanSummary.company_id == company_id, ScanSummary.scanned_at >= range_start, ScanSummary.scanned_at <= range_end)
    if scanner_type_normalized:
        query = query.filter(ScanSummary.scanner_type == scanner_type_normalized)
    if severity:
        query = query.filter(ScanFinding.severity.ilike(f"%{severity}%"))
    if cve:
        query = query.filter(ScanFinding.cve.ilike(f"%{cve}%"))
    if host:
        query = query.filter(ScanFinding.host.ilike(f"%{host}%"))
    rows = query.order_by(ScanSummary.scanned_at.desc()).limit(min(max(limit, 1), 1000)).all()
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
    if _is_demo_company(current_user, session):
        payload = demo_scanner_analytics(scanner_type_normalized)
        log_audit(session, actor_user_id=current_user.id, action="VIEW", entity_type="SCAN_ANALYTICS_DEMO", payload={"scanner_type": scanner_type_normalized})
        session.commit()
        return payload
    range_start, range_end = parse_analytics_range_args(start_date, end_date, days, default_days=30, max_days=90)
    domain = _infer_domain_from_integration(scanner_type_normalized) or "soc"
    if domain == "noc":
        summary_model = ScanSummaryNoc
        findings_model = ScanNocEvent
        join_condition = ScanNocEvent.scan_summary_noc_id == ScanSummaryNoc.id
    else:
        summary_model = ScanSummary
        findings_model = ScanFinding
        join_condition = ScanFinding.scan_summary_id == ScanSummary.id
    summaries = session.scalars(select(summary_model).where(summary_model.company_id == current_user.company_id, summary_model.scanner_type == scanner_type_normalized, summary_model.scanned_at >= range_start, summary_model.scanned_at <= range_end)).all()
    top_items = []
    if domain == "noc":
        rows = session.query(ScanNocEvent.event_type, ScanNocEvent.severity, func.count(func.distinct(ScanNocEvent.host)).label("host_count")).join(ScanSummaryNoc, join_condition).filter(ScanSummaryNoc.company_id == current_user.company_id, ScanSummaryNoc.scanner_type == scanner_type_normalized, ScanSummaryNoc.scanned_at >= range_start, ScanSummaryNoc.scanned_at <= range_end, ScanNocEvent.event_type.is_not(None), ScanNocEvent.event_type != "").group_by(ScanNocEvent.event_type, ScanNocEvent.severity).all()
        for row in rows:
            top_items.append({"cve_id": row.event_type, "severity": row.severity, "hosts_affected": row.host_count, "cvss_score": None, "impact_score": row.host_count or 0})
    else:
        rows = session.query(ScanFinding.cve, ScanFinding.severity, func.count(func.distinct(ScanFinding.host)).label("host_count"), func.max(ScanFinding.cvss).label("cvss_score")).join(ScanSummary, join_condition).filter(ScanSummary.company_id == current_user.company_id, ScanSummary.scanner_type == scanner_type_normalized, ScanSummary.scanned_at >= range_start, ScanSummary.scanned_at <= range_end, ScanFinding.cve.is_not(None), ScanFinding.cve != "").group_by(ScanFinding.cve, ScanFinding.severity).all()
        for row in rows:
            impact = (row.host_count or 0) * (row.cvss_score or 0)
            top_items.append({"cve_id": row.cve, "severity": row.severity, "hosts_affected": row.host_count, "cvss_score": row.cvss_score, "impact_score": impact})
    top_items.sort(key=lambda item: item["impact_score"], reverse=True)
    trend = []
    range_start_day = range_start.replace(hour=0, minute=0, second=0, microsecond=0)
    range_end_day = range_end.replace(hour=0, minute=0, second=0, microsecond=0)
    total_days = max(1, (range_end_day - range_start_day).days + 1)
    for i in range(total_days):
        day_start = range_start_day + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        counts = session.query(func.sum(summary_model.critical_count).label("critical"), func.sum(summary_model.high_count).label("high"), func.sum(summary_model.medium_count).label("medium"), func.sum(summary_model.low_count).label("low"), func.sum(summary_model.info_count).label("info")).filter(summary_model.company_id == current_user.company_id, summary_model.scanner_type == scanner_type_normalized, summary_model.scanned_at >= day_start, summary_model.scanned_at < day_end, summary_model.status == "completed").first()
        trend.append({"date": day_start.strftime("%Y-%m-%d"), "critical": int(counts.critical or 0), "high": int(counts.high or 0), "medium": int(counts.medium or 0), "low": int(counts.low or 0), "info": int(counts.info or 0)})
    total_hosts = sum(summary.total_hosts or 0 for summary in summaries)
    if domain == "noc":
        most_critical_host_row = session.query(
            ScanNocEvent.host,
            func.count(ScanNocEvent.id).label("critical_count"),
        ).join(ScanSummaryNoc, join_condition).filter(
            ScanSummaryNoc.company_id == current_user.company_id,
            ScanSummaryNoc.scanner_type == scanner_type_normalized,
            ScanSummaryNoc.scanned_at >= range_start,
            ScanSummaryNoc.scanned_at <= range_end,
            ScanNocEvent.severity.ilike("%critical%"),
        ).group_by(ScanNocEvent.host).order_by(func.count(ScanNocEvent.id).desc()).first()
    else:
        most_critical_host_row = session.query(
            ScanFinding.host,
            func.count(ScanFinding.id).label("critical_count"),
        ).join(ScanSummary, join_condition).filter(
            ScanSummary.company_id == current_user.company_id,
            ScanSummary.scanner_type == scanner_type_normalized,
            ScanSummary.scanned_at >= range_start,
            ScanSummary.scanned_at <= range_end,
            ScanFinding.severity.ilike("%critical%"),
        ).group_by(ScanFinding.host).order_by(func.count(ScanFinding.id).desc()).first()
    recent_findings = []
    if domain == "noc":
        rows = session.query(ScanNocEvent, ScanSummaryNoc).join(ScanSummaryNoc, join_condition).filter(ScanSummaryNoc.company_id == current_user.company_id, ScanSummaryNoc.scanner_type == scanner_type_normalized, ScanSummaryNoc.scanned_at >= range_start, ScanSummaryNoc.scanned_at <= range_end).order_by(case((ScanNocEvent.severity.ilike("%critical%"), 1), (ScanNocEvent.severity.ilike("%high%"), 2), (ScanNocEvent.severity.ilike("%medium%"), 3), (ScanNocEvent.severity.ilike("%low%"), 4), else_=5), ScanSummaryNoc.scanned_at.desc()).limit(20).all()
        for event, summary in rows:
            recent_findings.append({"cve": event.event_type, "name": event.name, "host": event.host, "severity": event.severity, "cvss": None, "detectedAt": summary.scanned_at.isoformat() if summary.scanned_at else None})
    else:
        rows = session.query(ScanFinding, ScanSummary).join(ScanSummary, join_condition).filter(ScanSummary.company_id == current_user.company_id, ScanSummary.scanner_type == scanner_type_normalized, ScanSummary.scanned_at >= range_start, ScanSummary.scanned_at <= range_end).order_by(case((ScanFinding.severity.ilike("%critical%"), 1), (ScanFinding.severity.ilike("%high%"), 2), (ScanFinding.severity.ilike("%medium%"), 3), (ScanFinding.severity.ilike("%low%"), 4), else_=5), ScanSummary.scanned_at.desc()).limit(20).all()
        for finding, summary in rows:
            recent_findings.append({"cve": finding.cve, "name": finding.name, "host": finding.host, "severity": finding.severity, "cvss": finding.cvss, "detectedAt": summary.scanned_at.isoformat() if summary.scanned_at else None})
    return {"success": True, "domain": domain, "scanner_type": scanner_type_normalized, "period": {"start_date": range_start.isoformat(), "end_date": range_end.isoformat(), "days": max(1, (range_end - range_start).days + 1)}, "topCVEs": top_items[:10], "trend_7_days": trend, "hostDistribution": {"totalUniqueHosts": total_hosts, "avgVulnerabilitiesPerHost": round((sum((summary.critical_count + summary.high_count + summary.medium_count + summary.low_count + summary.info_count) for summary in summaries) / total_hosts), 2) if total_hosts else 0, "mostCriticalHost": {"host": most_critical_host_row.host if most_critical_host_row else None, "criticalCount": int(most_critical_host_row.critical_count or 0) if most_critical_host_row else 0}}, "recentFindings": recent_findings, "agentInfo": _build_agent_info(session, summary_model, current_user.company_id, scanner_type_normalized)}


@router.get("/{scan_summary_id}")
def get_scan(scan_summary_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session), domain: str | None = None) -> dict:
    resolved_domain = normalize_domain(domain, default="soc")
    if not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    if _is_demo_company(current_user, session):
        scan = demo_scan(scan_summary_id, current_user.company_id)
        if not scan:
            raise NotFoundError("Scan summary not found")
        return scan
    summary_model = _summary_model_for_domain(resolved_domain)
    scan = session.get(summary_model, scan_summary_id)
    if not scan or scan.company_id != current_user.company_id:
        raise NotFoundError("Scan summary not found")
    return scan.to_dict()


@router.get("/{scan_summary_id}/findings")
def get_scan_findings(scan_summary_id: int, current_user: User = Depends(get_current_user), session: Session = Depends(get_db_session), domain: str | None = None) -> dict:
    resolved_domain = normalize_domain(domain, default="soc")
    if not resolved_domain:
        raise ValidationError("Invalid domain. Allowed: soc, noc")
    if _is_demo_company(current_user, session):
        return demo_scan_findings(scan_summary_id)
    summary_model = _summary_model_for_domain(resolved_domain)
    findings_model = _findings_model_for_domain(resolved_domain)
    scan = session.scalar(select(summary_model).where(summary_model.id == scan_summary_id, summary_model.company_id == current_user.company_id))
    if not scan:
        raise NotFoundError("Scan summary not found")
    if resolved_domain == "noc":
        findings = session.scalars(select(findings_model).where(findings_model.scan_summary_noc_id == scan_summary_id)).all()
        payload = [finding.to_dict() for finding in findings]
        return {"events": payload, "findings": payload, "count": len(payload), "domain": resolved_domain}
    findings = session.scalars(select(findings_model).where(findings_model.scan_summary_id == scan_summary_id)).all()
    return {"findings": [finding.to_dict() for finding in findings], "count": len(findings), "domain": resolved_domain}
