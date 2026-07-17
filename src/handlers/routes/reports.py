from __future__ import annotations

import hashlib
import hmac
import json
import os

import boto3
from fastapi import APIRouter, Depends

from src.reports.schemas import build_report_response, validate_report_request
from src.reports.store import (
    create_report_item,
    get_report,
    get_report_or_404,
    list_tenant_reports,
    now_iso,
    serialize_report,
    table,
    update_report_status,
)
from src.persistence.db import get_db_session
from src.persistence.models import User
from src.shared.context import effective_tenant_id_of, require_tenant_read_access
from src.shared.dependencies import get_current_user, require_access_claims
from src.shared.errors import ValidationError
from src.shared.logging import logger

from src.reports.storage import generate_download_url

router = APIRouter(prefix="/reports", tags=["reports"])

eventbridge = boto3.client("events")


def _publish_event(event_name: str, tenant_id: int, report_id: str, payload: dict) -> None:
    event_bus_name = os.environ.get("REPORT_EVENT_BUS_NAME", "")
    if not event_bus_name:
        logger.warning("REPORT_EVENT_BUS_NAME not set, skipping event publish")
        return
    try:
        eventbridge.put_events(
            Entries=[
                {
                    "Source": "xoc.report",
                    "DetailType": event_name,
                    "Detail": json.dumps(
                        {"tenant_id": tenant_id, "report_id": report_id, **payload}, default=str
                    ),
                    "EventBusName": event_bus_name,
                }
            ]
        )
    except Exception as exc:
        logger.warning("Failed to emit report event %s: %s", event_name, exc)


def _compute_request_hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


@router.post("", status_code=202)
def request_report(payload: dict, claims: dict = Depends(require_access_claims), current_user: User = Depends(get_current_user)):
    validation = validate_report_request(payload)
    if not validation["valid"]:
        raise ValidationError("; ".join(validation["errors"]))

    tenant_id = effective_tenant_id_of(current_user)
    user_id = claims.get("userId") or claims.get("sub")

    request_hash = _compute_request_hash(payload)
    report_id, item = create_report_item(
        tenant_id=tenant_id,
        report_type=payload["report_type"],
        created_by_user_id=int(user_id) if user_id else None,
        filters=payload.get("filters"),
        request_payload=payload,
        request_hash=request_hash,
    )
    table.put_item(Item=item)

    _publish_event("report.requested", tenant_id, report_id, {
        "report_type": payload["report_type"],
        "request_hash": request_hash,
    })

    return {
        "reportId": report_id,
        "status": "PENDING",
    }


@router.get("/{report_id}")
def get_report_status(report_id: str, current_user: User = Depends(get_current_user)):
    require_tenant_read_access(current_user)
    tenant_id = effective_tenant_id_of(current_user)
    item = get_report_or_404(tenant_id, report_id)

    serialized = serialize_report(item)
    response = build_report_response(serialized)

    if item.get("status") == "COMPLETED" and item.get("s3_key"):
        download_url = generate_download_url(item["s3_key"])
        response["downloadUrl"] = download_url

    return response


@router.get("")
def list_reports(current_user: User = Depends(get_current_user), status: str | None = None, limit: int = 50):
    require_tenant_read_access(current_user)
    tenant_id = effective_tenant_id_of(current_user)
    reports = list_tenant_reports(tenant_id, status=status, limit=min(limit, 200))
    return {"reports": reports, "count": len(reports)}
