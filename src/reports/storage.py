from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import boto3

from src.shared.config import get_settings, get_snapshots_bucket_name


def _s3() -> boto3.client:
    return boto3.client("s3", region_name=get_settings().app_region)


def build_template_key(report_type: str) -> str:
    return f"report-templates/{report_type}/current.docx"


def build_report_s3_key(
    tenant_id: int,
    report_id: str,
    filename: str = "generated.docx",
) -> str:
    stage = get_settings().app_stage
    return f"{stage}/reports/{tenant_id}/{report_id}/{filename}"


def build_artifact_s3_key(
    tenant_id: int,
    report_id: str,
    artifact_name: str,
) -> str:
    stage = get_settings().app_stage
    return f"{stage}/reports/{tenant_id}/{report_id}/artifacts/{artifact_name}"


def download_template(report_type: str, local_path: str) -> str:
    bucket = get_snapshots_bucket_name()
    key = build_template_key(report_type)
    _s3().download_file(bucket, key, local_path)
    return local_path


def template_exists(report_type: str) -> bool:
    try:
        bucket = get_snapshots_bucket_name()
        key = build_template_key(report_type)
        _s3().head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def upload_report(tenant_id: int, report_id: str, local_path: str, filename: str = "generated.docx") -> dict:
    bucket = get_snapshots_bucket_name()
    key = build_report_s3_key(tenant_id, report_id, filename)
    extra_args = {"ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    _s3().upload_file(local_path, bucket, key, ExtraArgs=extra_args)
    response = _s3().head_object(Bucket=bucket, Key=key)
    return {
        "s3_bucket": bucket,
        "s3_key": key,
        "s3_version_id": response.get("VersionId", ""),
        "size_bytes": response.get("ContentLength", 0),
    }


def upload_artifact(tenant_id: int, report_id: str, artifact_name: str, data: dict) -> str:
    bucket = get_snapshots_bucket_name()
    key = build_artifact_s3_key(tenant_id, report_id, artifact_name)
    _s3().put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
        ContentType="application/json",
    )
    return f"s3://{bucket}/{key}"


def download_artifact(s3_uri: str) -> dict:
    if not s3_uri.startswith("s3://"):
        raise ValueError(f"Invalid S3 URI: {s3_uri}")
    parts = s3_uri.replace("s3://", "").split("/", 1)
    bucket = parts[0]
    key = parts[1]
    response = _s3().get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


def generate_download_url(s3_key: str, expires_in: int = 3600) -> str:
    bucket = get_snapshots_bucket_name()
    url = _s3().generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket, "Key": s3_key},
        ExpiresIn=expires_in,
    )
    return url
