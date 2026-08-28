#!/bin/bash
set -euo pipefail

# =============================================================================
# XOC APPLIANCE DEMO SETUP
# Run from EC2 (~/XOC_AWS) or dev VM after backend deploy
# =============================================================================

API="https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com"

echo "=== STEP 0: Check existing superadmin ==="
USERS_RESULT=$(curl -s "$API/superadmin/users" -H "Authorization: Bearer $TOKEN" 2>/dev/null || echo "[]")
echo "$USERS_RESULT" | python3 -m json.tool 2>/dev/null || echo "(need token first)"

# =============================================================================
# STEP 1: Login as superadmin (or create one in DB)
# =============================================================================
echo ""
echo "=== STEP 1: Login as superadmin ==="
echo "Trying admin@xoc.com / test1234..."
LOGIN_RESULT=$(curl -s -X POST "$API/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@xoc.com","password":"test1234"}')

echo "$LOGIN_RESULT" | python3 -m json.tool 2>/dev/null || echo "$LOGIN_RESULT"

TOKEN=$(echo "$LOGIN_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || echo "")

if [ -z "$TOKEN" ]; then
  echo ""
  echo "!!! No superadmin found. Need to create one in DB first."
  echo "SSH to dev VM (3.235.129.140) and run:"
  echo ""
  echo "  SECRET=\$(aws secretsmanager get-secret-value --secret-id xoc/api/prod/database --query SecretString --output text)"
  echo "  USER=\$(echo \"\$SECRET\" | python3 -c \"import sys,json; print(json.load(sys.stdin)['username'])\")"
  echo "  PASS=\$(echo \"\$SECRET\" | python3 -c \"import sys,json; print(json.load(sys.stdin)['password'])\")"
  echo "  HOST=\$(echo \"\$SECRET\" | python3 -c \"import sys,json; print(json.load(sys.stdin)['host'])\")"
  echo "  DB=\$(echo \"\$SECRET\" | python3 -c \"import sys,json; print(json.load(sys.stdin)['dbname'])\")"
  echo ""
  echo "  PGPASSWORD=\"\$PASS\" psql -h \"\$HOST\" -U \"\$USER\" -d \"\$DB\" -c \\\""
  echo "    INSERT INTO users (email, password_hash, role, full_name, tenant_id) \\\""
  echo "    VALUES ('admin@xoc.com', '\\\$2b\\\$12\\\$LJ3m4ys3Lz0wqV9r5k5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e', 'SUPERADMIN', 'XOC Superadmin', NULL)\\\""
  echo "    ON CONFLICT (email) DO NOTHING;\\\""
  echo ""
  echo "  (password_hash above is a placeholder - use python to hash 'test1234')"
  echo ""
  exit 1
fi

echo "TOKEN obtained: ${TOKEN:0:20}..."

# =============================================================================
# STEP 2: Check tenant 8 exists
# =============================================================================
echo ""
echo "=== STEP 2: Verify tenant 8 (XOC APPLIANCE) ==="
TENANT_RESULT=$(curl -s "$API/superadmin/tenants" -H "Authorization: Bearer $TOKEN")
echo "$TENANT_RESULT" | python3 -m json.tool 2>/dev/null || echo "$TENANT_RESULT"

TENANT_8=$(echo "$TENANT_RESULT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
tenants = data if isinstance(data, list) else data.get('tenants', [])
for t in tenants:
    if t.get('id') == 8:
        print(json.dumps(t))
        break
" 2>/dev/null || echo "")

if [ -z "$TENANT_8" ]; then
  echo "Tenant 8 not found. Creating..."
  curl -s -X POST "$API/superadmin/tenants" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"name":"XOC APPLIANCE","plan_status":"active"}' | python3 -m json.tool
else
  echo "Tenant 8 found: $TENANT_8"
fi

# =============================================================================
# STEP 3: Create admin user for tenant 8
# =============================================================================
echo ""
echo "=== STEP 3: Create admin user for tenant 8 ==="
curl -s -X POST "$API/superadmin/users" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": 8,
    "username": "xoc_admin",
    "email": "santiago.silva@xocappliance.com",
    "password": "XocDemo2026!",
    "role": "ADMIN"
  }' | python3 -m json.tool 2>/dev/null || echo "(user may already exist)"

# =============================================================================
# STEP 4: Login as tenant 8 admin
# =============================================================================
echo ""
echo "=== STEP 4: Login as tenant 8 admin ==="
ADMIN_LOGIN=$(curl -s -X POST "$API/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"santiago.silva@xocappliance.com","password":"XocDemo2026!"}')

ADMIN_TOKEN=$(echo "$ADMIN_LOGIN" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || echo "")

if [ -z "$ADMIN_TOKEN" ]; then
  echo "Failed to login as admin. Response:"
  echo "$ADMIN_LOGIN"
  exit 1
fi

echo "Admin token obtained: ${ADMIN_TOKEN:0:20}..."

# =============================================================================
# STEP 5: Create xoc_appliance integration
# =============================================================================
echo ""
echo "=== STEP 5: Create xoc_appliance integration ==="
INTEGRATION_RESULT=$(curl -s -X POST "$API/integrations" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "xoc_appliance",
    "credentials": {
      "host": "10.20.0.22",
      "api_key": "victor-onpremise-key"
    },
    "extra_json": {
      "description": "Victor On-Premise Appliance",
      "location": "On-premise"
    }
  }')
echo "$INTEGRATION_RESULT" | python3 -m json.tool 2>/dev/null || echo "$INTEGRATION_RESULT"

INTEGRATION_ID=$(echo "$INTEGRATION_RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('integration',{}).get('id', d.get('id','')))" 2>/dev/null || echo "")
echo "Integration ID: $INTEGRATION_ID"

# =============================================================================
# STEP 6: Create system (Victor On-Premise server)
# =============================================================================
echo ""
echo "=== STEP 6: Create system (Victor On-Premise) ==="
if [ -n "$INTEGRATION_ID" ] && [ "$INTEGRATION_ID" != "" ]; then
  SYSTEM_RESULT=$(curl -s -X POST "$API/systems" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{
      \"integration_id\": $INTEGRATION_ID,
      \"name\": \"Victor On-Premise\",
      \"type\": \"server\",
      \"status\": \"online\",
      \"health_score\": 85.5,
      \"meta_info\": {
        \"ip\": \"10.20.0.22\",
        \"os\": \"Ubuntu 22.04\",
        \"location\": \"On-premise\",
        \"services\": [\"victor\", \"executor\"]
      }
    }")
  echo "$SYSTEM_RESULT" | python3 -m json.tool 2>/dev/null || echo "$SYSTEM_RESULT"
else
  echo "Skipping system creation (no integration_id)"
fi

# =============================================================================
# STEP 7: Create demo ticket (triggers Victor flow)
# =============================================================================
echo ""
echo "=== STEP 7: Create demo ticket ==="
TICKET_RESULT=$(curl -s -X POST "$API/tickets" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "subject": "Suspicious file detected on production server",
    "description": "A potentially malicious file was detected at /tmp/suspicious_malware.sh on the production server. Immediate investigation and remediation required."
  }')
echo "$TICKET_RESULT" | python3 -m json.tool 2>/dev/null || echo "$TICKET_RESULT"

TICKET_ID=$(echo "$TICKET_RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('ticket',{}).get('id',''))" 2>/dev/null || echo "")
echo "Ticket ID: $TICKET_ID"

# =============================================================================
# SUMMARY
# =============================================================================
echo ""
echo "============================================="
echo "  DEMO SETUP COMPLETE"
echo "============================================="
echo ""
echo "  Tenant:      XOC APPLIANCE (ID: 8)"
echo "  Admin Login: santiago.silva@xocappliance.com / XocDemo2026!"
echo "  Integration: ID=$INTEGRATION_ID (xoc_appliance)"
echo "  System:      Victor On-Premise"
echo "  Ticket:      ID=$TICKET_ID"
echo ""
echo "  Next steps:"
echo "  1. SSH to Victor on-premise (10.20.0.22)"
echo "  2. Create malicious file: echo '#!/bin/bash' > /tmp/suspicious_malware.sh"
echo "  3. Login to xoc.app with the admin credentials"
echo "  4. Watch Victor process the ticket through the flow"
echo "  5. Approve the ticket when it reaches PREAPROBADO"
echo "============================================="
