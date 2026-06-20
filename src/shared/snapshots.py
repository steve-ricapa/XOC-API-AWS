from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import boto3
from sqlalchemy.orm import Session

from src.persistence.models import SnapshotArtifact
from src.shared.config import get_settings, get_snapshots_bucket_name


def _snapshot_client():
    return boto3.client("s3", region_name=get_settings().app_region)


def build_snapshot_checksum(payload: dict[str, Any]) -> tuple[str, bytes]:
    raw_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw_bytes).hexdigest(), raw_bytes


def build_snapshot_s3_key(*, company_id: int, provider: str, snapshot_type: str, captured_at: datetime | None = None, snapshot_id: str | None = None) -> str:
    timestamp = captured_at or datetime.utcnow()
    normalized_provider = (provider or "unknown").strip().lower().replace(" ", "_")
    normalized_type = (snapshot_type or "snapshot").strip().lower().replace(" ", "_")
    unique_id = snapshot_id or str(uuid.uuid4())
    stage = get_settings().app_stage
    return (
        f"{stage}/company/{company_id}/provider/{normalized_provider}/"
        f"type/{normalized_type}/{timestamp:%Y/%m/%d}/{unique_id}.json"
    )


def upload_snapshot_payload(*, key: str, payload: dict[str, Any]) -> tuple[str, int]:
    checksum, raw_bytes = build_snapshot_checksum(payload)
    _snapshot_client().put_object(
        Bucket=get_snapshots_bucket_name(),
        Key=key,
        Body=raw_bytes,
        ContentType="application/json",
    )
    return checksum, len(raw_bytes)


def fetch_snapshot_payload(*, key: str) -> dict[str, Any]:
    response = _snapshot_client().get_object(Bucket=get_snapshots_bucket_name(), Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


@dataclass(frozen=True)
class SnapshotArtifactInput:
    company_id: int
    provider: str
    snapshot_type: str
    domain: str
    source: str | None = None
    status: str = "stored"
    integration_id: int | None = None
    scan_id: str | None = None
    external_id: str | None = None
    scan_summary_soc_id: int | None = None
    scan_summary_noc_id: int | None = None
    captured_at: datetime | None = None
    received_at: datetime | None = None
    summary_json: dict[str, Any] | None = None
    content_type: str = "application/json"


def store_snapshot_artifact(*, session: Session, payload: dict[str, Any], artifact_input: SnapshotArtifactInput, existing_artifact: SnapshotArtifact | None = None) -> SnapshotArtifact:
    bucket_name = get_snapshots_bucket_name()
    key = existing_artifact.s3_key if existing_artifact else build_snapshot_s3_key(
        company_id=artifact_input.company_id,
        provider=artifact_input.provider,
        snapshot_type=artifact_input.snapshot_type,
        captured_at=artifact_input.captured_at,
    )
    checksum, size_bytes = upload_snapshot_payload(key=key, payload=payload)

    artifact = existing_artifact or SnapshotArtifact(
        company_id=artifact_input.company_id,
        integration_id=artifact_input.integration_id,
        provider=artifact_input.provider,
        snapshot_type=artifact_input.snapshot_type,
        domain=artifact_input.domain,
        source=artifact_input.source,
        status=artifact_input.status,
        scan_id=artifact_input.scan_id,
        external_id=artifact_input.external_id,
        scan_summary_soc_id=artifact_input.scan_summary_soc_id,
        scan_summary_noc_id=artifact_input.scan_summary_noc_id,
        s3_bucket=bucket_name,
        s3_key=key,
        content_type=artifact_input.content_type,
        captured_at=artifact_input.captured_at,
        received_at=artifact_input.received_at,
        summary_json=artifact_input.summary_json,
    )
    artifact.integration_id = artifact_input.integration_id
    artifact.provider = artifact_input.provider
    artifact.snapshot_type = artifact_input.snapshot_type
    artifact.domain = artifact_input.domain
    artifact.source = artifact_input.source
    artifact.status = artifact_input.status
    artifact.scan_id = artifact_input.scan_id
    artifact.external_id = artifact_input.external_id
    artifact.scan_summary_soc_id = artifact_input.scan_summary_soc_id
    artifact.scan_summary_noc_id = artifact_input.scan_summary_noc_id
    artifact.s3_bucket = bucket_name
    artifact.s3_key = key
    artifact.content_type = artifact_input.content_type
    artifact.size_bytes = size_bytes
    artifact.checksum = checksum
    artifact.summary_json = artifact_input.summary_json
    artifact.captured_at = artifact_input.captured_at
    artifact.received_at = artifact_input.received_at or artifact.received_at

    if existing_artifact is None:
        session.add(artifact)
    return artifact
