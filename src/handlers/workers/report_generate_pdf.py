from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from src.reports.storage import download_document_file, upload_preview_pdf
from src.shared.logging import logger


def handler(event: dict, context) -> dict:
    document_id = event.get("documentId")
    tenant_id = event.get("tenantId")
    s3_key = event.get("s3Key")
    s3_bucket = event.get("s3Bucket")
    document_type = event.get("documentType", "")

    if not all([document_id, tenant_id, s3_key, s3_bucket]):
        raise ValueError("documentId, tenantId, s3Key, and s3Bucket are required")

    tenant_id = int(tenant_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        docx_path = os.path.join(tmpdir, os.path.basename(s3_key))
        output_filename = os.path.splitext(os.path.basename(s3_key))[0] + ".pdf"
        output_path = os.path.join(tmpdir, output_filename)
        download_document_file(s3_bucket=s3_bucket, s3_key=s3_key, local_path=docx_path)
        _convert_docx_to_pdf(docx_path=docx_path, output_dir=tmpdir)
        if not os.path.isfile(output_path):
            raise RuntimeError(f"PDF preview was not generated: {output_path}")
        result = upload_preview_pdf(
            tenant_id,
            document_id,
            document_type,
            output_path,
            filename=os.path.basename(output_path),
        )
        logger.info("PDF preview uploaded for document %s: s3://%s/%s", document_id, result["s3_bucket"], result["s3_key"])

    return {
        **event,
        "previewS3Bucket": result["s3_bucket"],
        "previewS3Key": result["s3_key"],
        "previewS3VersionId": result["s3_version_id"],
        "previewSizeBytes": result["size_bytes"],
        "previewFormat": "pdf",
        "previewLocalPath": result.get("local_path"),
    }


def _convert_docx_to_pdf(*, docx_path: str, output_dir: str) -> None:
    soffice = os.environ.get("LIBREOFFICE_BIN") or "libreoffice"
    env = os.environ.copy()
    env.setdefault("HOME", "/tmp")
    env.setdefault("TMPDIR", "/tmp")
    profile_dir = os.path.join(output_dir, "lo-profile")
    os.makedirs(profile_dir, exist_ok=True)
    profile_uri = Path(profile_dir).resolve().as_uri()
    command = [
        soffice,
        "--headless",
        f"-env:UserInstallation={profile_uri}",
        "--convert-to",
        "pdf",
        "--outdir",
        output_dir,
        docx_path,
    ]
    logger.info("Converting DOCX to PDF with LibreOffice: %s", os.path.basename(docx_path))
    completed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=120)
    if completed.returncode != 0:
        raise RuntimeError(
            "LibreOffice conversion failed. "
            f"stdout={completed.stdout[-1000:]} stderr={completed.stderr[-1000:]}"
        )
