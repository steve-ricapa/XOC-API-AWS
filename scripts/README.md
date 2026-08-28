Schema patch scripts kept here are for existing environments where the SQLAlchemy
model set has drifted from the live database and `scripts/bootstrap_schema.py`
cannot be run directly from the normal deploy host.

Current patches:

- `live_voice_schema.sql`
  Creates `live_voice_sessions` and `live_voice_messages`.
- `pending_ingestions_schema.sql`
  Creates `pending_ingestions` used by `POST /scans/upload-url`.
- `finding_index_schema.sql`
  Creates `finding_index` used by dashboard summaries and scan findings queries.
- `tenant_preferences_schema.sql`
  Creates `tenant_preferences` used by tenant dashboard visibility and health-index preferences.

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

---

## Ticket Demo Scripts

Scripts for testing the complete ticket automation flow with VICTOR.

### Quick Start

```bash
# 1. Verify system is ready
./scripts/verify_ticket_system.sh <tenant-id>

# 2. Run complete demo
./scripts/demo_tickets_flow.sh --tenant-id <tenant-id>

# 3. Quick test of VICTOR only
./scripts/test_victor_ticket_flow.sh --full
```

### Script Details

#### `verify_ticket_system.sh`

Verifies all components are ready:
- AWS CLI and credentials
- VICTOR agent connectivity
- DynamoDB table status
- Step Functions workflow
- EventBridge
- API Gateway endpoints

```bash
./scripts/verify_ticket_system.sh 123
```

#### `demo_tickets_flow.sh`

Complete end-to-end demo:
1. Verifies prerequisites
2. Checks VICTOR connectivity
3. Creates test ticket (malicious file detection)
4. Starts Step Functions workflow
5. Monitors execution
6. Handles approval if needed

```bash
# Basic usage
./scripts/demo_tickets_flow.sh --tenant-id 123

# With auto-approve for risky steps
./scripts/demo_tickets_flow.sh --tenant-id 123 --auto-approve

# With user JWT for manual approval
./scripts/demo_tickets_flow.sh --tenant-id 123 --user-jwt eyJhbGci...

# Skip VICTOR check (if VICTOR is down)
./scripts/demo_tickets_flow.sh --tenant-id 123 --skip-victor-check

# Verbose output
./scripts/demo_tickets_flow.sh --tenant-id 123 --verbose
```

#### `test_victor_ticket_flow.sh`

Quick test of VICTOR endpoints directly:

```bash
# Test all phases
./scripts/test_victor_ticket_flow.sh --full

# Test only assessment
./scripts/test_victor_ticket_flow.sh --assessment

# Test only plan generation
./scripts/test_victor_ticket_flow.sh --plan

# Test against local VICTOR
./scripts/test_victor_ticket_flow.sh --victor-url http://localhost:8000 --full
```

### Demo Flow

The demo simulates a real security incident:

1. **Ticket Creation**: A ticket is created with subject "Alerta: Archivo malicioso detectado en servidor Web"

2. **Assessment Phase**: VICTOR evaluates if it can resolve the ticket
   - Response: `{"canResolve": true, "confidence": 0.9}`

3. **Plan Generation**: VICTOR creates a remediation plan
   - Phase 1 (basic): Detection - `ls -la`, `file`, `head`
   - Phase 2 (controlled): Containment - `mkdir -p /var/quarantine`, `mv /tmp/trojan.sh /var/quarantine/`
   - Phase 3 (risky): Remediation - `rm -f /var/quarantine/trojan.sh`
   - Phase 4 (basic): Verification - `ls -la`, check processes

4. **Approval Gate**: If any step is "risky", workflow pauses for human approval
   - Role required: ADMIN_XOC
   - Timeout: 7 days

5. **Execution**: After approval, VICTOR executes each step via laptop_agent.py

6. **Verification**: Check ticket status → RESUELTO

### Risk Levels

| Risk Level | Required Role | Example Commands |
|------------|---------------|------------------|
| basic | USER | `ls -la`, `file`, `head` |
| controlled | ADMIN | `mkdir`, `mv`, `chmod` |
| risky | ADMIN_XOC | `rm -f`, `kill`, `systemctl stop` |
| critical | SUPERADMIN | `wipe`, `drop`, `reinitialize` |

### Troubleshooting

**VICTOR not responding:**
```bash
# Check VICTOR health
curl http://10.20.0.22:8000/health

# Check logs on XOC APPLIANCE
ssh -i ~/.ssh/xoc-ec2 ubuntu@10.20.0.22 "docker logs victor-server"
```

**Step Functions not starting:**
```bash
# Check workflow exists
aws stepfunctions list-state-machines --region us-east-1

# Check execution history
aws stepfunctions list-executions \
  --state-machine-arn arn:aws:states:us-east-1:ACCOUNT:stateMachine:xoc-api-automation-prod-workflow \
  --max-results 10
```

**DynamoDB ticket not updating:**
```bash
# Check ticket item
aws dynamodb get-item \
  --table-name xoc-api-tickets-prod-tickets \
  --key '{"pk":{"S":"TICKET#123"},"sk":{"S":"TICKET#ticket-id"}}' \
  --region us-east-1
```
