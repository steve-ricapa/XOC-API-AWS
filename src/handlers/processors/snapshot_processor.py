import hashlib
import json
from datetime import datetime, timezone

import boto3
from sqlalchemy import select

from src.persistence.db import get_engine
from src.persistence.models import (
    AgentApiKey,
    FindingIndex,
    IngestIdempotencyRecord,
    Integration,
    PendingIngestion,
    ScanSummary,
    ScanSummaryNoc,
    SnapshotArtifact,
)
from src.shared.config import get_settings, get_snapshots_bucket_name
from src.shared.snapshots import build_snapshot_s3_key

S3_CLIENT = boto3.client("s3")
STATUS_ACCEPTED = "accepted"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED_VALIDATION = "failed_validation"
STATUS_FAILED_RETRYABLE = "failed_retryable"


class ValidationIngestionError(Exception):
    pass


def _schema_version(payload: dict) -> str:
    value = payload.get("schema_version") or payload.get("schemaVersion") or "v1"
    return str(value).strip()[:30] or "v1"


def _claim_pending_ingestion(session, pending: PendingIngestion, request_id: str | None) -> bool:
    refreshed = session.get(PendingIngestion, pending.id)
    if not refreshed or refreshed.status not in {STATUS_ACCEPTED, STATUS_FAILED_RETRYABLE}:
        return False
    refreshed.status = STATUS_PROCESSING
    refreshed.attempt_count = int(refreshed.attempt_count or 0) + 1
    refreshed.processing_started_at = datetime.utcnow()
    refreshed.processor_request_id = request_id
    refreshed.last_error_code = None
    refreshed.error_message = None
    session.commit()
    pending.status = refreshed.status
    pending.attempt_count = refreshed.attempt_count
    return True


def _mark_pending(session, pending: PendingIngestion, *, status: str, error_code: str | None = None, error_message: str | None = None, processed_s3_key: str | None = None, quarantine_s3_key: str | None = None, checksum: str | None = None, size_bytes: int | None = None, scan_id: str | None = None, schema_version: str | None = None) -> None:
    db_pending = session.get(PendingIngestion, pending.id)
    if not db_pending:
        return
    db_pending.status = status
    db_pending.last_error_code = error_code
    db_pending.error_message = error_message[:4000] if error_message else None
    db_pending.processing_completed_at = datetime.utcnow()
    if processed_s3_key:
        db_pending.processed_s3_key = processed_s3_key
    if quarantine_s3_key:
        db_pending.quarantine_s3_key = quarantine_s3_key
    if checksum:
        db_pending.checksum = checksum
    if size_bytes is not None:
        db_pending.size_bytes = size_bytes
    if scan_id:
        db_pending.scan_id = scan_id
    if schema_version:
        db_pending.schema_version = schema_version
    session.commit()
SOC_SCANNER_TYPES = {"openvas", "insightvm", "nessus", "qualys", "tenable", "rapid7", "wazuh"}
NOC_SCANNER_TYPES = {"zabbix", "uptime_kuma"}
SCANNER_DEFAULT_SUMMARY_TYPE = {
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
SUMMARY_TYPE_DOMAIN_MAP = {
    "vulnerability": "soc",
    "security_events": "soc",
    "noc_health": "noc",
    "availability": "noc",
}


def _resolve_domain(scanner_type: str) -> str:
    summary_type = SCANNER_DEFAULT_SUMMARY_TYPE.get(scanner_type, "vulnerability")
    return SUMMARY_TYPE_DOMAIN_MAP.get(summary_type, "soc")


def _summary_model_for_domain(domain: str):
    return ScanSummaryNoc if domain == "noc" else ScanSummary


def _normalize_severity(value: str) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    for key in ("critical", "high", "medium", "low"):
        if key in normalized:
            return key
    if "info" in normalized or "log" in normalized or "information" in normalized:
        return "info"
    return normalized


def _move_s3_object(bucket: str, source_key: str, dest_key: str) -> None:
    S3_CLIENT.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": source_key}, Key=dest_key)
    S3_CLIENT.delete_object(Bucket=bucket, Key=source_key)


def _resolve_integration_id(session, tenant_id: int, provider: str) -> int | None:
    integration = session.scalar(
        select(Integration).where(
            Integration.tenant_id == tenant_id,
            (Integration.type == provider) | (Integration.provider == provider),
        )
    )
    return integration.id if integration else None


def handler(event: dict, context) -> dict:
    bucket_name = get_snapshots_bucket_name()
    engine = get_engine()

    for record in event.get("Records", []):
        bucket = bucket_name
        key = ""

        if record.get("eventSource") == "aws:s3":
            s3_info = record.get("s3", {})
            bucket = s3_info.get("bucket", {}).get("name", bucket_name)
            key = s3_info.get("object", {}).get("key", "")
        elif record.get("eventSource") == "aws:sqs":
            try:
                body = json.loads(record.get("body") or "{}")
            except json.JSONDecodeError:
                continue
            inner_records = body.get("Records") if isinstance(body, dict) else None
            if not inner_records or not isinstance(inner_records, list):
                continue
            s3_info = (inner_records[0] or {}).get("s3", {})
            bucket = s3_info.get("bucket", {}).get("name", bucket_name)
            key = s3_info.get("object", {}).get("key", "")
        else:
            continue

        if not key.startswith("pending/"):
            continue

        upload_id = key.split("/")[-1].replace(".json", "")

        from sqlalchemy.orm import Session as _Session
        session = _Session(bind=engine)
        try:
            pending = session.scalar(select(PendingIngestion).where(PendingIngestion.upload_id == upload_id))
            if not pending or pending.status not in {STATUS_ACCEPTED, STATUS_FAILED_RETRYABLE}:
                session.close()
                if not pending:
                    S3_CLIENT.delete_object(Bucket=bucket, Key=key)
                continue

            if not _claim_pending_ingestion(session, pending, getattr(context, "aws_request_id", None)):
                session.close()
                continue

            response = S3_CLIENT.get_object(Bucket=bucket, Key=key)
            raw = response["Body"].read().decode("utf-8")
            payload = json.loads(raw)
            checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            size_bytes = len(raw.encode("utf-8"))
            schema_version = _schema_version(payload)

            tenant_id = pending.tenant_id
            scanner_type = pending.scanner_type
            scan_id = (payload.get("scan_id") or payload.get("scanId") or "").strip()
            if not scan_id:
                raise ValidationIngestionError("scan_id is required in uploaded payload")

            scan_summary_data = payload.get("scan_summary") or {}
            findings_data = payload.get("findings") or []
            if not isinstance(scan_summary_data, dict):
                raise ValidationIngestionError("scan_summary must be an object")
            if not isinstance(findings_data, list):
                raise ValidationIngestionError("findings must be a list")

            if pending.api_key_id:
                agent_key = session.get(AgentApiKey, pending.api_key_id)
                if agent_key:
                    agent_key.last_used_at = datetime.utcnow()

            validated_findings = []
            for finding_raw in findings_data:
                if not isinstance(finding_raw, dict):
                    continue
                name = (finding_raw.get("name") or "").strip()
                if not name:
                    continue
                severity = _normalize_severity(finding_raw.get("severity") or finding_raw.get("severity_level"))
                if not severity:
                    continue
                finding_raw["severity"] = severity
                validated_findings.append(finding_raw)

            domain = _resolve_domain(scanner_type)
            provider = (pending.provider or scanner_type or "").strip().lower()
            summary_type = SCANNER_DEFAULT_SUMMARY_TYPE.get(scanner_type, "vulnerability")
            summary_model = _summary_model_for_domain(domain)
            existing = session.scalar(select(summary_model).where(summary_model.tenant_id == tenant_id, summary_model.scan_id == scan_id))
            if existing:
                scan_summary = existing
            else:
                scan_summary = summary_model(tenant_id=tenant_id, scan_id=scan_id, scanned_at=datetime.utcnow())
                session.add(scan_summary)

            scanned_at_str = scan_summary_data.get("scanned_at") or scan_summary_data.get("scannedAt")
            if scanned_at_str:
                try:
                    parsed = datetime.fromisoformat(scanned_at_str.replace("Z", "+00:00"))
                    if parsed.tzinfo is not None:
                        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
                    scan_summary.scanned_at = parsed
                except Exception:
                    pass

            scan_summary.scanner_type = scanner_type
            scan_summary.summary_type = summary_type
            scan_summary.status = scan_summary_data.get("status", "completed")
            scan_summary.agent_api_key_id = pending.api_key_id

            results_raw = scan_summary_data.get("results") or payload.get("results", {})
            critical = int(results_raw.get("critical", 0))
            high = int(results_raw.get("high", 0))
            medium = int(results_raw.get("medium", 0))
            low = int(results_raw.get("low", 0))
            info = int(results_raw.get("info", 0))

            recalculated = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
            for f in validated_findings:
                sev = f.get("severity")
                if sev in recalculated:
                    recalculated[sev] += 1

            scan_summary.critical_count = critical or recalculated["critical"]
            scan_summary.high_count = high or recalculated["high"]
            scan_summary.medium_count = medium or recalculated["medium"]
            scan_summary.low_count = low or recalculated["low"]
            scan_summary.info_count = info or recalculated["info"]
            scan_summary.cvss_max = float(scan_summary_data.get("cvss_max", 0.0))
            scan_summary.total_hosts = scan_summary_data.get("total_hosts")
            scan_summary.scan_name = scan_summary_data.get("scan_name") or scan_summary_data.get("target")
            scan_summary.meta_info = scan_summary_data.get("meta")
            scan_summary.received_at = datetime.utcnow()
            session.flush()

            if pending.idempotency_key:
                existing_irecord = session.scalar(
                    select(IngestIdempotencyRecord).where(
                        IngestIdempotencyRecord.tenant_id == tenant_id,
                        IngestIdempotencyRecord.idempotency_key == pending.idempotency_key,
                    )
                )
                if not existing_irecord:
                    session.add(
                        IngestIdempotencyRecord(
                            tenant_id=tenant_id,
                            idempotency_key=pending.idempotency_key,
                            request_hash="",
                            domain=domain,
                        )
                    )
                    session.flush()

            prod_key = build_snapshot_s3_key(
                tenant_id=tenant_id,
                provider=provider,
                snapshot_type=summary_type,
                captured_at=scan_summary.scanned_at,
            )
            _move_s3_object(bucket, key, prod_key)

            integration_id = _resolve_integration_id(session, tenant_id, provider)

            existing_artifact = session.scalar(
                select(SnapshotArtifact).where(
                    SnapshotArtifact.tenant_id == tenant_id,
                    SnapshotArtifact.scan_id == scan_id,
                    SnapshotArtifact.provider == provider,
                )
            )
            if existing_artifact:
                artifact = existing_artifact
                session.query(FindingIndex).filter_by(snapshot_artifact_id=artifact.id).delete()
            else:
                artifact = SnapshotArtifact(
                    tenant_id=tenant_id,
                    integration_id=integration_id,
                    provider=provider,
                    snapshot_type=summary_type,
                    domain=domain,
                    source="snapshot_upload",
                    status="stored",
                    scan_id=scan_id,
                    scan_summary_soc_id=scan_summary.id if domain != "noc" else None,
                    scan_summary_noc_id=scan_summary.id if domain == "noc" else None,
                    s3_bucket=bucket,
                    s3_key=prod_key,
                    content_type="application/json",
                    size_bytes=size_bytes,
                    checksum=checksum,
                    captured_at=scan_summary.scanned_at,
                    received_at=datetime.utcnow(),
                    summary_json={
                        "scanner_type": scanner_type,
                        "domain": domain,
                        "findings_count": len(validated_findings),
                    },
                )
                session.add(artifact)

            artifact.integration_id = integration_id
            artifact.provider = provider
            artifact.snapshot_type = summary_type
            artifact.domain = domain
            artifact.source = "snapshot_upload"
            artifact.status = "stored"
            artifact.scan_id = scan_id
            artifact.scan_summary_soc_id = scan_summary.id if domain != "noc" else None
            artifact.scan_summary_noc_id = scan_summary.id if domain == "noc" else None
            artifact.s3_bucket = bucket
            artifact.s3_key = prod_key
            artifact.content_type = "application/json"
            artifact.size_bytes = size_bytes
            artifact.checksum = checksum
            artifact.captured_at = scan_summary.scanned_at
            artifact.received_at = datetime.utcnow()
            artifact.summary_json = {
                "scanner_type": scanner_type,
                "domain": domain,
                "findings_count": len(validated_findings),
            }
            session.flush()

            for idx, fd in enumerate(validated_findings):
                session.add(
                    FindingIndex(
                        tenant_id=tenant_id,
                        snapshot_artifact_id=artifact.id,
                        scan_summary_soc_id=artifact.scan_summary_soc_id,
                        scan_summary_noc_id=artifact.scan_summary_noc_id,
                        scan_id=scan_id,
                        scanner_type=scanner_type,
                        domain=domain,
                        finding_idx=idx,
                        severity=fd.get("severity"),
                        name=fd.get("name"),
                        cve=fd.get("cve"),
                        host=fd.get("host"),
                        port=fd.get("port"),
                        protocol=fd.get("protocol"),
                        status=fd.get("status"),
                        event_type=fd.get("event_type") or fd.get("type") or fd.get("category"),
                        service=fd.get("service"),
                        cvss=fd.get("cvss"),
                        description=fd.get("description"),
                        solution=fd.get("solution"),
                        impact=fd.get("impact"),
                        s3_bucket=bucket,
                        s3_key=prod_key,
                        detected_at=scan_summary.scanned_at,
                    )
                )

            _mark_pending(
                session,
                pending,
                status=STATUS_COMPLETED,
                processed_s3_key=prod_key,
                checksum=checksum,
                size_bytes=size_bytes,
                scan_id=scan_id,
                schema_version=schema_version,
            )

        except ValidationIngestionError as exc:
            session.rollback()
            quarantine_key = f"quarantine/{upload_id}.json"
            try:
                _move_s3_object(bucket, key, quarantine_key)
            except Exception:
                quarantine_key = None
            try:
                _mark_pending(
                    session,
                    pending,
                    status=STATUS_FAILED_VALIDATION,
                    error_code="validation_error",
                    error_message=str(exc),
                    quarantine_s3_key=quarantine_key,
                )
            except Exception:
                session.rollback()
        except Exception as exc:
            session.rollback()
            try:
                _mark_pending(
                    session,
                    pending,
                    status=STATUS_FAILED_RETRYABLE,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
            except Exception:
                session.rollback()
            raise
        finally:
            session.close()

    return {"status": "ok"}
