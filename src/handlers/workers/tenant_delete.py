from __future__ import annotations

import json

from src.persistence.db import session_scope
from src.persistence.models import Tenant, User
from src.reports.schemas import DOCUMENT_TYPES
from src.reports.storage import delete_documents_for_tenant, delete_legacy_reports_for_tenant
from src.reports.store import delete_tenant_document_jobs
from src.shared.context import log_audit
from src.shared.logging import logger
from src.shared.tickets_store import delete_tenant_tickets


def handler(event: dict, context) -> dict:
    results: list[dict] = []
    for record in event.get("Records", []):
        body = json.loads(record.get("body", "{}"))
        tenant_id = int(body["tenant_id"])
        actor_user_id = int(body["actor_user_id"])
        tenant_name = body.get("tenant_name") or f"Tenant-{tenant_id}"
        summary = body.get("summary") or {}
        try:
            dynamo_tickets_deleted = delete_tenant_tickets(tenant_id)
            document_jobs_deleted = delete_tenant_document_jobs(tenant_id)
            s3_documents_deleted = delete_documents_for_tenant(tenant_id, sorted(DOCUMENT_TYPES))
            s3_legacy_deleted = delete_legacy_reports_for_tenant(tenant_id)

            with session_scope() as session:
                tenant = session.get(Tenant, tenant_id)
                if tenant:
                    tenant_users = list(session.query(User).filter(User.tenant_id == tenant_id).all())
                    log_audit(
                        session,
                        actor_user_id=actor_user_id,
                        action="DELETE_COMPLETED",
                        entity_type="TENANT",
                        entity_id=tenant_id,
                        payload={
                            "name": tenant_name,
                            "precheck_summary": summary,
                            "cleanup": {
                                "tenant_users_deleted": len(tenant_users),
                                "dynamo_tickets_deleted": dynamo_tickets_deleted,
                                "document_jobs_deleted": document_jobs_deleted,
                                "s3_documents_deleted": s3_documents_deleted,
                                "s3_legacy_deleted": s3_legacy_deleted,
                            },
                        },
                    )
                    for tenant_user in tenant_users:
                        session.delete(tenant_user)
                    session.delete(tenant)

            results.append({
                "tenant_id": tenant_id,
                "status": "DELETED",
                "dynamo_tickets_deleted": dynamo_tickets_deleted,
                "document_jobs_deleted": document_jobs_deleted,
                "s3_documents_deleted": s3_documents_deleted,
                "s3_legacy_deleted": s3_legacy_deleted,
            })
        except Exception as exc:
            logger.exception("Tenant deletion failed for tenant_id=%s", tenant_id)
            with session_scope() as session:
                tenant = session.get(Tenant, tenant_id)
                if tenant:
                    tenant.plan_status = "DELETING_FAILED"
                    log_audit(
                        session,
                        actor_user_id=actor_user_id,
                        action="DELETE_FAILED",
                        entity_type="TENANT",
                        entity_id=tenant_id,
                        payload={"name": tenant_name, "precheck_summary": summary, "error": str(exc)[:2000]},
                    )
            raise
    return {"results": results}
