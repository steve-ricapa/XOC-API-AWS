Schema patch scripts kept here are for existing environments where the SQLAlchemy
model set has drifted from the live database and `scripts/bootstrap_schema.py`
cannot be run directly from the normal deploy host.

Current patches:

- `live_voice_schema.sql`
  Creates `live_voice_sessions` and `live_voice_messages`.
- `pending_ingestions_schema.sql`
  Creates `pending_ingestions` used by `POST /scans/upload-url`.

Recommended usage from the RDS-reachable VM documented in `AGENTS.md`:

1. Copy the SQL file to the VM.
2. Read DB connection values from secret `xoc/api/prod/database`.
3. Run `psql` with `sslmode=require` and `-v ON_ERROR_STOP=1`.

Example shape:

```bash
SECRET=$(aws secretsmanager get-secret-value --secret-id xoc/api/prod/database --query SecretString --output text)
USER=$(echo "$SECRET" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('username') or d.get('user'))")
PASS=$(echo "$SECRET" | python3 -c "import sys,json; print(json.load(sys.stdin)['password'])")
HOST=$(echo "$SECRET" | python3 -c "import sys,json; print(json.load(sys.stdin)['host'])")
PORT=$(echo "$SECRET" | python3 -c "import sys,json; print(json.load(sys.stdin).get('port') or 5432)")
DB=$(echo "$SECRET" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('dbname') or d.get('database') or 'xoc')")
PGPASSWORD="$PASS" psql "host=$HOST port=$PORT dbname=$DB user=$USER sslmode=require" -v ON_ERROR_STOP=1 -f scripts/live_voice_schema.sql
```

These scripts are intentionally idempotent so they can be re-run safely.
