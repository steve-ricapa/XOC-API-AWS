# =============================================================================
# XOC APPLIANCE DEMO - Postman / cURL Commands
# API: https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com
# =============================================================================

# --- 1. Login as superadmin ---
# POST /auth/login
curl -X POST https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@xoc.com","password":"test1234"}'
# => save access_token as {{TOKEN}}

# --- 2. Create tenant 8 (if not exists) ---
# POST /superadmin/tenants
curl -X POST https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com/superadmin/tenants \
  -H "Authorization: Bearer {{TOKEN}}" \
  -H "Content-Type: application/json" \
  -d '{"name":"XOC APPLIANCE","plan_status":"active"}'

# --- 3. Create admin user for tenant 8 ---
# POST /superadmin/users
curl -X POST https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com/superadmin/users \
  -H "Authorization: Bearer {{TOKEN}}" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": 8,
    "username": "xoc_admin",
    "email": "santiago.silva@xocappliance.com",
    "password": "XocDemo2026!",
    "role": "ADMIN"
  }'

# --- 4. Login as tenant 8 admin ---
# POST /auth/login
curl -X POST https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"santiago.silva@xocappliance.com","password":"XocDemo2026!"}'
# => save access_token as {{ADMIN_TOKEN}}

# --- 5. Create xoc_appliance integration ---
# POST /integrations
curl -X POST https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com/integrations \
  -H "Authorization: Bearer {{ADMIN_TOKEN}}" \
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
  }'
# => save integration.id as {{INTEGRATION_ID}}

# --- 6. Create system (Victor On-Premise) ---
# POST /systems
curl -X POST https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com/systems \
  -H "Authorization: Bearer {{ADMIN_TOKEN}}" \
  -H "Content-Type: application/json" \
  -d '{
    "integration_id": {{INTEGRATION_ID}},
    "name": "Victor On-Premise",
    "type": "server",
    "status": "online",
    "health_score": 85.5,
    "meta_info": {
      "ip": "10.20.0.22",
      "os": "Ubuntu 22.04",
      "location": "On-premise",
      "services": ["victor", "executor"]
    }
  }'

# --- 7. Create demo ticket (triggers Victor flow) ---
# POST /tickets
curl -X POST https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com/tickets \
  -H "Authorization: Bearer {{ADMIN_TOKEN}}" \
  -H "Content-Type: application/json" \
  -d '{
    "subject": "Suspicious file detected on production server",
    "description": "A potentially malicious file was detected at /tmp/suspicious_malware.sh on the production server. Immediate investigation and remediation required."
  }'
# => save ticket.id as {{TICKET_ID}}

# --- 8. Check ticket status (poll) ---
# GET /tickets/{id}
curl https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com/tickets/{{TICKET_ID}} \
  -H "Authorization: Bearer {{ADMIN_TOKEN}}"

# --- 9. Approve ticket (when PREAPROBADO) ---
# PATCH /tickets/{id}/approve
curl -X PATCH https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com/tickets/{{TICKET_ID}}/approve \
  -H "Authorization: Bearer {{ADMIN_TOKEN}}"

# --- 10. Reject ticket (alternative) ---
# PATCH /tickets/{id}/reject
curl -X PATCH https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com/tickets/{{TICKET_ID}}/reject \
  -H "Authorization: Bearer {{ADMIN_TOKEN}}"

# =============================================================================
# CREDENTIALS SUMMARY
# =============================================================================
# Superadmin:  admin@xoc.com / test1234
# Tenant 8:     santiago.silva@xocappliance.com / XocDemo2026!
# Victor IP:    10.20.0.22 (port 8000=victor, 8888=executor)
# =============================================================================
