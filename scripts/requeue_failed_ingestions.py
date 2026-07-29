from __future__ import annotations

import json
import subprocess

import psycopg2


def load_db_credentials() -> dict:
    secret = subprocess.check_output(
        [
            "aws",
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            "xoc/api/prod/database",
            "--query",
            "SecretString",
            "--output",
            "text",
        ],
        text=True,
    )
    return json.loads(secret)


def main() -> None:
    creds = load_db_credentials()
    conn = psycopg2.connect(
        host=creds["host"],
        port=creds["port"],
        user=creds["username"],
        password=creds["password"],
        dbname=creds["dbname"],
    )
    conn.autocommit = False

    tenant_id = 8
    bucket = "xoc-prod-snapshots-811776156524"
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, upload_id, s3_key
        FROM pending_ingestions
        WHERE tenant_id = %s AND status = 'failed'
        ORDER BY id ASC
        """,
        (tenant_id,),
    )
    rows = cur.fetchall()

    requeued = 0
    skipped = 0
    for row_id, upload_id, pending_key in rows:
        quarantine_key = f"quarantine/{upload_id}.json"
        try:
            subprocess.check_call(
                ["aws", "s3api", "head-object", "--bucket", bucket, "--key", quarantine_key],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            skipped += 1
            continue

        subprocess.check_call(
            [
                "aws",
                "s3api",
                "copy-object",
                "--bucket",
                bucket,
                "--copy-source",
                f"{bucket}/{quarantine_key}",
                "--key",
                pending_key,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.check_call(
            ["aws", "s3api", "delete-object", "--bucket", bucket, "--key", quarantine_key],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cur.execute(
            "UPDATE pending_ingestions SET status='pending', error_message=NULL, updated_at=NOW() WHERE id=%s",
            (row_id,),
        )
        requeued += 1

    conn.commit()
    cur.close()
    conn.close()
    print({"failed_rows": len(rows), "requeued": requeued, "skipped": skipped})


if __name__ == "__main__":
    main()
