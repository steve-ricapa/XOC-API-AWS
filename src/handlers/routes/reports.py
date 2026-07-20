from __future__ import annotations

import hashlib
import hmac
import json
import os

import boto3
from fastapi import APIRouter, Depends

from src.reports.schemas import build_document_response, validate_document_request
from src.reports.store import (
    create_document_job,
    get_document_job_or_404,
    list_tenant_document_jobs,
    serialize_report,
    table,
)
from src.persistence.db import get_db_session
from src.persistence.models import User
from src.shared.context import effective_tenant_id_of, normalize_role, require_tenant_read_access
from src.shared.dependencies import get_current_user, require_access_claims
from src.shared.errors import ForbiddenError, ValidationError
from src.shared.logging import logger

from src.reports.storage import generate_download_url

router = APIRouter(prefix="/documents", tags=["documents"])

eventbridge = boto3.client("events")

DOCUMENT_ROLE_ALLOWLIST = {
    "minority_report": {"ADMIN", "USER", "ADMIN_XOC", "SUPERADMIN"},
    "small_report": {"ADMIN", "USER", "ADMIN_XOC", "SUPERADMIN"},
    "informe_soporte": {"ADMIN", "USER", "ADMIN_XOC", "SUPERADMIN"},
}


def _publish_event(event_name: str, tenant_id: int, document_id: str, payload: dict) -> None:
    event_bus_name = os.environ.get("REPORT_EVENT_BUS_NAME", "")
    if not event_bus_name:
        logger.warning("REPORT_EVENT_BUS_NAME not set, skipping event publish")
        return
    try:
        eventbridge.put_events(
            Entries=[
                {
                    "Source": "xoc.document",
                    "DetailType": event_name,
                    "Detail": json.dumps(
                        {"tenant_id": tenant_id, "document_id": document_id, **payload}, default=str
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


def _assert_document_permissions(document_type: str, current_user: User) -> None:
    allowed_roles = DOCUMENT_ROLE_ALLOWLIST.get(document_type, set())
    role = normalize_role(current_user.role)
    if allowed_roles and role not in allowed_roles:
        raise ForbiddenError("User role is not allowed to request this document type")


@router.post("", status_code=202)
def request_document(payload: dict, claims: dict = Depends(require_access_claims), current_user: User = Depends(get_current_user)):
    validation = validate_document_request(payload)
    if not validation["valid"]:
        raise ValidationError("; ".join(validation["errors"]))

    require_tenant_read_access(current_user)
    _assert_document_permissions(payload["document_type"], current_user)
    tenant_id = effective_tenant_id_of(current_user)
    user_id = claims.get("userId") or claims.get("sub")

    request_hash = _compute_request_hash(payload)
    document_id, item = create_document_job(
        tenant_id=tenant_id,
        document_type=payload["document_type"],
        created_by_user_id=int(user_id) if user_id else None,
        filters=payload.get("filters"),
        parameters=payload.get("parameters"),
        request_payload=payload,
        request_hash=request_hash,
    )
    table.put_item(Item=item)

    _publish_event("document.requested", tenant_id, document_id, {
        "document_type": payload["document_type"],
        "request_hash": request_hash,
    })

    return {
        "documentId": document_id,
        "status": "PENDING",
    }


@router.get("/{document_id}")
def get_document_status(document_id: str, current_user: User = Depends(get_current_user)):
    require_tenant_read_access(current_user)
    tenant_id = effective_tenant_id_of(current_user)
    item = get_document_job_or_404(tenant_id, document_id)

    serialized = serialize_report(item)
    response = build_document_response(serialized)

    if item.get("status") == "COMPLETED" and item.get("s3_key"):
        download_url = generate_download_url(item["s3_key"])
        response["downloadUrl"] = download_url

    return response


@router.get("")
def list_documents(current_user: User = Depends(get_current_user), status: str | None = None, limit: int = 50):
    require_tenant_read_access(current_user)
    tenant_id = effective_tenant_id_of(current_user)
    documents = list_tenant_document_jobs(tenant_id, status=status, limit=min(limit, 200))
    return {"documents": documents, "count": len(documents)}
