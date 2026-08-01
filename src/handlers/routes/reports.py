from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
from datetime import date, timedelta
from uuid import uuid4

import boto3
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from src.reports.schemas import build_document_preview_response, build_document_response, validate_document_request
from src.reports.store import (
    create_document_job,
    get_document_job_or_404,
    list_tenant_document_jobs,
    serialize_report,
    table,
    update_document_status,
)
from src.persistence.db import get_db_session
from src.persistence.models import User
from src.shared.context import effective_tenant_id_of, normalize_role, require_tenant_read_access
from src.shared.dependencies import get_current_user, require_access_claims
from src.shared.errors import ForbiddenError, ValidationError
from src.shared.logging import logger

from src.reports.storage import download_generated_content, generate_download_url

router = APIRouter(prefix="/documents", tags=["documents"])

# Este registro sólo existe para descargar un documento generado durante una
# sesión local. No se usa ni se carga en Lambda/producción.
_local_demo_documents: dict[str, Path] = {}

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
        # En desarrollo local no se debe inicializar boto3 al importar el módulo:
        # el flujo inline no usa EventBridge ni requiere credenciales/región AWS.
        eventbridge = boto3.client("events", region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"))
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


def _minority_variant_for_role(role: str, parameters: dict | None) -> str:
    requested = str((parameters or {}).get("report_variant") or "").strip().lower().replace("-", "_")
    if role == "USER":
        return "client"
    if role == "ADMIN":
        return "client" if requested in {"client", "report_for_client"} else "client_admin"
    if role in {"ADMIN_XOC", "SUPERADMIN"}:
        return "client" if requested in {"client", "report_for_client"} else "client_admin"
    return "client"


def _normalize_minority_parameters(payload: dict, current_user: User) -> dict:
    parameters = dict(payload.get("parameters") or {})
    role = normalize_role(current_user.role)
    variant = _minority_variant_for_role(role, parameters)
    parameters["requester_role"] = role
    parameters["report_variant"] = variant
    parameters["template_variant"] = "report for admin client" if variant == "client_admin" else "report for client"
    payload["parameters"] = parameters
    return payload


def _should_process_inline() -> bool:
    mode = (os.environ.get("REPORTS_PROCESSING_MODE") or "").strip().lower()
    return mode == "inline" or (
        (os.environ.get("REPORTS_STORAGE_MODE") or "").strip().lower() == "local"
        and not os.environ.get("REPORT_EVENT_BUS_NAME")
    )


def _local_reports_demo_enabled() -> bool:
    """Habilita la prueba local explícita sin BD, API Gateway ni AWS."""
    return (os.environ.get("LOCAL_REPORTS_DEMO") or "").strip().lower() in {"1", "true", "yes"}


def _local_demo_payload(request_payload: dict) -> dict:
    """Construye un reporte demostrativo a partir de los datos del formulario.

    La ruta local no consulta hallazgos reales: esos datos pertenecen al flujo
    autenticado con BD. Se conservan los valores escritos por el administrador
    para poder comprobar que la plantilla correcta se rellena de extremo a
    extremo en el equipo de desarrollo.
    """
    parameters = request_payload.get("parameters") or {}
    variant = "client_admin" if str(parameters.get("report_variant", "")).lower().replace("-", "_") in {
        "client_admin", "admin_client", "report_for_admin_client"
    } else "client"
    today = date.today()
    period = f"Del {(today - timedelta(days=6)).strftime('%d/%m/%Y')} al {today.strftime('%d/%m/%Y')}"
    modules = parameters.get("modules") if isinstance(parameters.get("modules"), dict) else {}
    actions: list[str] = []
    for module in modules.values():
        if not isinstance(module, dict) or not module.get("enabled", True):
            continue
        title = str(module.get("title") or module.get("moduleId") or "Módulo")
        content = str(module.get("content") or "Sin detalle ingresado.").strip()
        software = ", ".join(str(value) for value in (module.get("software") or [])) or "Sin software asociado"
        actions.append(f"{title}: {content} (Software: {software}).")
    if not actions:
        actions.append("No se ingresaron módulos de reporte para esta prueba local.")

    severity_rows = [
        {"severity": "Informativa", "previous": "0", "current": "0"},
        {"severity": "Baja", "previous": "0", "current": "0"},
        {"severity": "Media", "previous": "0", "current": "0"},
        {"severity": "Alta", "previous": "0", "current": "0"},
        {"severity": "Crítica", "previous": "0", "current": "0"},
    ]
    domain_names = [
        "Gobierno y gestión", "Capital humano", "Endpoints", "Aplicaciones y APIs",
        "Cómputo y servidores", "Cloud y SaaS", "Red e infraestructura",
        "Perímetro de seguridad", "Servicios externos",
    ]
    security_news = str(parameters.get("security_news") or "").strip()
    payload = {
        "report_variant": variant,
        "template_variant": "report for admin client" if variant == "client_admin" else "report for client",
        "client_name": str(parameters.get("client_name") or "Cliente local"),
        "prepared_by": "TXDXSECURE",
        "period": period,
        "service_name": "Servicio de monitoreo proactivo XOC",
        "data_base": "Prueba local: no se consultó base de datos ni servicios AWS.",
        "executive_summary": "Documento de validación local generado a partir de la plantilla corporativa seleccionada.",
        "histogram_summary": "No hay hallazgos reales cargados en el modo local de prueba.",
        "vulnerability_comparison": {
            "summary": "Comparación preparada para validar las gráficas y la tabla de severidades de la plantilla.",
            "severity_rows": severity_rows,
        },
        "security_domains": [{"name": name, "summary": "Sin hallazgos reales en la prueba local.", "findings": []} for name in domain_names],
        "weekly_actions": actions,
        "results_and_next_actions": "La sección se generó desde la plantilla para validar su estructura local.",
        "results_obtained": "Resultado de la prueba: se creó el documento desde la plantilla, sin duplicar el reporte de acciones.",
        "reinforced_security": "Validación local de estructura y contenido del Minority Report.",
        "pending_findings": ["Conectar la base de datos local para incluir hallazgos reales."],
        "security_news": ([{
            "title": "Noticias de seguridad",
            "date": today.isoformat(),
            "source": "Administrador",
            "links": [],
            "summary": security_news,
            "recommendation": "Revisar la información y priorizar las acciones aplicables.",
        }] if security_news else []),
        "chart_data": {
            "client_name": str(parameters.get("client_name") or "Cliente local"),
            "previous_severity_summary": {},
            "current_severity_summary": {},
        },
    }
    return payload


def _local_payload_from_minority_mock(request_payload: dict) -> tuple[dict, list[dict]]:
    """Usa el mismo dataset local completo del POC XOC_Minority_Report.

    Es deliberadamente una dependencia exclusiva del modo LOCAL_REPORTS_DEMO:
    producción sigue construyendo el contexto desde su BD y nunca carga este
    módulo hermano. Así las tablas y gráficas locales se alimentan con datos
    coherentes (IDs, vulnerabilidades, hosts y severidades), no con ceros.
    """
    repository_root = Path(__file__).resolve().parents[3]
    mock_module_path = repository_root.parent / "XOC_Minority_Report" / "minority_mock_data.py"
    if not mock_module_path.is_file():
        raise RuntimeError(f"No se encontró el dataset local: {mock_module_path}")

    spec = importlib.util.spec_from_file_location("xoc_minority_local_mock", mock_module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("No se pudo cargar el dataset local de Minority Report")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    parameters = request_payload.get("parameters") or {}
    company_key = str(parameters.get("local_mock_company") or "interbank")
    result = module.build_mock_minority_report(company_key, range_mode="last_7_days")
    report_payload = dict(result["payload"])
    report_payload["report_variant"] = (
        "client_admin"
        if str(parameters.get("report_variant", "")).lower().replace("-", "_")
        in {"client_admin", "admin_client", "report_for_admin_client"}
        else "client"
    )
    report_payload["template_variant"] = (
        "report for admin client" if report_payload["report_variant"] == "client_admin" else "report for client"
    )

    modules = parameters.get("modules") if isinstance(parameters.get("modules"), dict) else {}
    module_actions: list[str] = []
    for module_data in modules.values():
        if not isinstance(module_data, dict) or not module_data.get("enabled", True):
            continue
        title = str(module_data.get("title") or module_data.get("moduleId") or "Módulo")
        content = str(module_data.get("content") or "Sin detalle ingresado.").strip()
        software = ", ".join(str(value) for value in (module_data.get("software") or [])) or "Sin software asociado"
        module_actions.append(f"{title}: {content} (Software: {software}).")
    if module_actions:
        # Estas acciones vienen del formulario; los resultados se conservan del
        # dataset para no duplicar literalmente el texto de acciones.
        report_payload["weekly_actions"] = module_actions

    manual_news = str(parameters.get("security_news") or "").strip()
    if manual_news:
        report_payload["security_news"] = [{
            "title": "Noticias de seguridad",
            "date": str((report_payload.get("mock_meta") or {}).get("period_end") or date.today().isoformat()),
            "source": "Administrador",
            "links": [],
            "summary": manual_news,
            "recommendation": "Revisar la información y priorizar las acciones aplicables.",
        }]

    # La plantilla rellena un párrafo en esta sección, no una lista.
    report_payload["results_obtained"] = (
        "Los resultados se derivan de los hallazgos y métricas del período; "
        "son independientes de las actividades escritas en el reporte de acciones."
    )
    return report_payload, list(result["charts"])


@router.post("/local-generate")
def generate_local_minority_report(payload: dict):
    """Genera un Minority Report local para comprobar las plantillas.

    Está bloqueado por defecto y jamás pasa por la autenticación/BD de la API
    productiva. Sólo debe activarse manualmente durante desarrollo.
    """
    if not _local_reports_demo_enabled():
        raise HTTPException(status_code=404, detail="Local reports demo is disabled")
    validation = validate_document_request(payload)
    if not validation["valid"]:
        raise ValidationError("; ".join(validation["errors"]))
    if payload.get("document_type") != "minority_report":
        raise ValidationError("La prueba local sólo admite minority_report")

    from src.reports.minority_docx import base_template_path, build_output_filename, generate_minority_report_docx

    report_payload, chart_images = _local_payload_from_minority_mock(payload)
    document_id = str(uuid4())
    output_dir = Path(__file__).resolve().parents[3] / "local-output" / "minority-demo" / document_id
    # Las tres imágenes ya se generaron desde el mismo dataset mock del POC.
    report_payload["chart_images"] = chart_images
    output_path = output_dir / build_output_filename(report_payload)
    generate_minority_report_docx(str(base_template_path(report_payload["report_variant"])), report_payload, str(output_path))
    _local_demo_documents[document_id] = output_path
    return {
        "documentId": document_id,
        "status": "COMPLETED",
        "documentType": "minority_report",
        "downloadUrl": f"/documents/local/{document_id}/download",
    }


@router.get("/local/{document_id}/download")
def download_local_minority_report(document_id: str):
    if not _local_reports_demo_enabled():
        raise HTTPException(status_code=404, detail="Local reports demo is disabled")
    path = _local_demo_documents.get(document_id)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Local document was not found")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=path.name,
    )


def _process_document_inline(tenant_id: int, document_id: str, document_type: str) -> None:
    from src.handlers.workers import report_collect, report_complete, report_generate_content, report_generate_docx, report_validate

    try:
        update_document_status(tenant_id, document_id, "PROCESSING")
        event = {
            "documentId": document_id,
            "tenantId": tenant_id,
            "documentType": document_type,
        }
        event = report_collect.handler(event, None)
        event = report_generate_content.handler(event, None)
        event = report_validate.handler(event, None)
        event = report_generate_docx.handler(event, None)
        report_complete.handler(event, None)
    except Exception as exc:
        logger.exception("Inline document generation failed for %s", document_id)
        update_document_status(
            tenant_id,
            document_id,
            "FAILED",
            error_code="inline_generation_error",
            error_message=str(exc)[:2000],
        )


@router.post("", status_code=202)
def request_document(payload: dict, claims: dict = Depends(require_access_claims), current_user: User = Depends(get_current_user)):
    validation = validate_document_request(payload)
    if not validation["valid"]:
        raise ValidationError("; ".join(validation["errors"]))

    require_tenant_read_access(current_user)
    _assert_document_permissions(payload["document_type"], current_user)
    if payload["document_type"] == "minority_report":
        payload = _normalize_minority_parameters(dict(payload), current_user)
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

    if _should_process_inline():
        _process_document_inline(tenant_id, document_id, payload["document_type"])
        item = get_document_job_or_404(tenant_id, document_id)
        response = build_document_response(serialize_report(item))
        if item.get("status") == "COMPLETED":
            response["downloadUrl"] = f"/documents/{document_id}/download"
        return response

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
    if item.get("status") == "COMPLETED" and item.get("preview_s3_key"):
        serialized["preview_url"] = generate_download_url(
            item["preview_s3_key"],
            bucket_name=item.get("preview_s3_bucket"),
            document_type=item.get("document_type"),
        )
        serialized["preview_format"] = item.get("preview_format") or "pdf"
    response = build_document_response(serialized)

    if item.get("status") == "COMPLETED" and item.get("s3_key"):
        if item.get("local_path"):
            response["downloadUrl"] = f"/documents/{document_id}/download"
        else:
            download_url = generate_download_url(
                item["s3_key"],
                bucket_name=item.get("s3_bucket"),
                document_type=item.get("document_type"),
            )
            response["downloadUrl"] = download_url

    return response


@router.get("/{document_id}/download")
def download_document(document_id: str, current_user: User = Depends(get_current_user)):
    require_tenant_read_access(current_user)
    tenant_id = effective_tenant_id_of(current_user)
    item = get_document_job_or_404(tenant_id, document_id)
    local_path = item.get("local_path")
    if not local_path:
        raise ForbiddenError("Local download is not available for this document")
    path = Path(local_path)
    if not path.is_file():
        raise ForbiddenError("Generated local document was not found")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=path.name,
    )


@router.get("/{document_id}/preview")
def get_document_preview(document_id: str, current_user: User = Depends(get_current_user)):
    require_tenant_read_access(current_user)
    tenant_id = effective_tenant_id_of(current_user)
    item = get_document_job_or_404(tenant_id, document_id)
    serialized = serialize_report(item)
    if item.get("status") == "COMPLETED" and item.get("preview_s3_key"):
        serialized["preview_url"] = generate_download_url(
            item["preview_s3_key"],
            bucket_name=item.get("preview_s3_bucket"),
            document_type=item.get("document_type"),
        )
        serialized["preview_format"] = item.get("preview_format") or "pdf"

    generated_content = None
    if item.get("status") == "COMPLETED":
        try:
            generated_content = download_generated_content(
                tenant_id=tenant_id,
                document_id=document_id,
                document_type=str(item.get("document_type") or ""),
            )
        except Exception as exc:
            logger.warning("Could not load preview artifact for document %s: %s", document_id, exc)

    response = build_document_preview_response(serialized, generated_content)
    if item.get("status") == "COMPLETED" and item.get("s3_key"):
        response["downloadUrl"] = (
            f"/documents/{document_id}/download"
            if item.get("local_path")
            else generate_download_url(
                item["s3_key"],
                bucket_name=item.get("s3_bucket"),
                document_type=item.get("document_type"),
            )
        )
    return response


@router.get("")
def list_documents(current_user: User = Depends(get_current_user), status: str | None = None, limit: int = 50):
    require_tenant_read_access(current_user)
    tenant_id = effective_tenant_id_of(current_user)
    documents = list_tenant_document_jobs(tenant_id, status=status, limit=min(limit, 200))
    return {"documents": documents, "count": len(documents)}
