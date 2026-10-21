#!/usr/bin/env bash
# =============================================================================
# Crea el tenant de pruebas XOC + usuarios de gobernanza + integracion del
# servidor de prueba. Pensado para ejecutarse desde AWS CloudShell (Linux).
#
# Requisitos: curl y python3 (CloudShell los trae).
#
# Uso:
#   export SUPERADMIN_EMAIL="tu-superadmin@xoc.app"
#   export SUPERADMIN_PASSWORD="********"
#   export SERVER_PRIVATE_IP="10.20.0.123"
#   export SERVER_PUBLIC_IP="54.123.45.67"
#   ./scripts/create_test_tenant.sh
#
# Opcionales:
#   API_BASE (default prod), TENANT_NAME, PLAN_STATUS (ACTIVE|DEMO),
#   ADMIN_EMAIL, ADMIN_PASSWORD, USER_EMAIL, USER_PASSWORD,
#   OPERATOR_EMAIL, OPERATOR_PASSWORD, AGENT_KEY_NAME, SYSTEM_NAME,
#   OUTPUT_FILE
# =============================================================================

set -euo pipefail

API_BASE="${API_BASE:-https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com}"
TENANT_NAME="${TENANT_NAME:-XOC Test Lab}"
PLAN_STATUS="${PLAN_STATUS:-ACTIVE}"
SERVER_PRIVATE_IP="${SERVER_PRIVATE_IP:-}"
SERVER_PUBLIC_IP="${SERVER_PUBLIC_IP:-}"
DEMO_PASSWORD="${DEMO_PASSWORD:-XocLab2026!}"

ADMIN_EMAIL="${ADMIN_EMAIL:-admin.lab@xoc.local}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-$DEMO_PASSWORD}"
USER_EMAIL="${USER_EMAIL:-analyst.lab@xoc.local}"
USER_PASSWORD="${USER_PASSWORD:-$DEMO_PASSWORD}"
OPERATOR_EMAIL="${OPERATOR_EMAIL:-xoc.operator@xoc.local}"
OPERATOR_PASSWORD="${OPERATOR_PASSWORD:-$DEMO_PASSWORD}"

AGENT_KEY_NAME="${AGENT_KEY_NAME:-xoc-test-nmap}"
SYSTEM_NAME="${SYSTEM_NAME:-XOC Test Target}"
OUTPUT_FILE="${OUTPUT_FILE:-${XDG_CONFIG_HOME:-$HOME}/.xoc/test-tenant.env}"

SUPERADMIN_EMAIL="${SUPERADMIN_EMAIL:-}"
SUPERADMIN_PASSWORD="${SUPERADMIN_PASSWORD:-}"

STATUS=""
BODY=""

log()  { printf '\033[0;32m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[0;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

http() {
    local method="$1" path="$2" token="${3:-}" data="${4:-}"
    local args=(-sS -X "$method" "$API_BASE$path" -H "Content-Type: application/json")
    [[ -n "$token" ]] && args+=(-H "Authorization: Bearer $token")
    [[ -n "$data" ]] && args+=(-d "$data")
    local out
    out="$(curl "${args[@]}" -w $'\n%{http_code}')" || die "curl fallo en $method $path"
    STATUS="${out##*$'\n'}"
    BODY="${out%$'\n'*}"
}

jget() {
    python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    value = json.loads(raw) if raw.strip() else None
except Exception:
    print("")
    sys.exit(0)
for part in sys.argv[1].split("."):
    if isinstance(value, dict):
        value = value.get(part)
    elif isinstance(value, list):
        try:
            value = value[int(part)]
        except Exception:
            value = None
    else:
        value = None
    if value is None:
        break
print("" if value is None else value)
' "$1"
}

login() {
    local email="$1" password="$2"
    http POST /auth/login "" "$(python3 -c 'import json,sys; print(json.dumps({"email": sys.argv[1], "password": sys.argv[2]}))' "$email" "$password")"
    if [[ "$STATUS" != "200" ]]; then
        return 1
    fi
    printf '%s' "$BODY" | jget access_token
}

require_var() {
    local name="$1"
    [[ -n "${!name:-}" ]] || die "Falta la variable $name"
}

[[ -n "$SUPERADMIN_EMAIL" ]] || die "Define SUPERADMIN_EMAIL"
[[ -n "$SUPERADMIN_PASSWORD" ]] || die "Define SUPERADMIN_PASSWORD"
require_var SERVER_PRIVATE_IP

log "API: $API_BASE"

# --- 1. Superadmin login ----------------------------------------------------
log "Login superadmin: $SUPERADMIN_EMAIL"
SUPER_TOKEN="$(login "$SUPERADMIN_EMAIL" "$SUPERADMIN_PASSWORD")" || die "Credenciales de superadmin invalidas. Detalle: $BODY"

# --- 2. Tenant ---------------------------------------------------------------
log "Buscando tenant '$TENANT_NAME'"
http GET '/superadmin/tenants?limit=200' "$SUPER_TOKEN"
[[ "$STATUS" == "200" ]] || die "No se pudo listar tenants ($STATUS): $BODY"
TENANT_ID="$(printf '%s' "$BODY" | python3 -c '
import json, sys
name = sys.argv[1].strip().lower()
data = json.load(sys.stdin)
for tenant in data.get("tenants", []):
    if str(tenant.get("name", "")).strip().lower() == name:
        print(tenant.get("id", ""))
        break
' "$TENANT_NAME")"

if [[ -z "$TENANT_ID" ]]; then
    log "Creando tenant '$TENANT_NAME' ($PLAN_STATUS)"
    http POST /superadmin/tenants "$SUPER_TOKEN" "$(python3 -c 'import json,sys; print(json.dumps({"name": sys.argv[1], "plan_status": sys.argv[2]}))' "$TENANT_NAME" "$PLAN_STATUS")"
    [[ "$STATUS" == "201" || "$STATUS" == "200" ]] || die "No se pudo crear tenant ($STATUS): $BODY"
    TENANT_ID="$(printf '%s' "$BODY" | jget tenant.id)"
else
    log "Tenant existente id=$TENANT_ID"
fi
[[ -n "$TENANT_ID" ]] || die "No se obtuvo TENANT_ID"

ensure_user() {
    local email="$1" password="$2" role="$3" tenant="$4"
    log "Asegurando usuario $email ($role)"
    local payload
    payload="$(python3 -c '
import json, sys
email, password, role, tenant = sys.argv[1:5]
payload = {"email": email, "password": password, "role": role, "username": email.split("@")[0]}
if tenant:
    payload["tenant_id"] = int(tenant)
print(json.dumps(payload))
' "$email" "$password" "$role" "$tenant")"
    http POST /superadmin/users "$SUPER_TOKEN" "$payload"
    if [[ "$STATUS" == "201" || "$STATUS" == "200" ]]; then
        log "  creado"
        return 0
    fi
    if login "$email" "$password" >/dev/null; then
        warn "  ya existia; login OK"
        return 0
    fi
    die "No se pudo crear ni loguear $email ($STATUS): $BODY"
}

ensure_user "$OPERATOR_EMAIL" "$OPERATOR_PASSWORD" "ADMIN_XOC" ""
ensure_user "$ADMIN_EMAIL" "$ADMIN_PASSWORD" "ADMIN" "$TENANT_ID"
ensure_user "$USER_EMAIL" "$USER_PASSWORD" "USER" "$TENANT_ID"

# --- 3. Login tenant admin ---------------------------------------------------
ADMIN_TOKEN="$(login "$ADMIN_EMAIL" "$ADMIN_PASSWORD")" || die "Login admin tenant fallo: $BODY"

# --- 4. Integracion del servidor --------------------------------------------
log "Buscando integracion xoc_appliance host=$SERVER_PRIVATE_IP"
http GET /integrations "$ADMIN_TOKEN"
INTEGRATION_ID=""
if [[ "$STATUS" == "200" ]]; then
    INTEGRATION_ID="$(printf '%s' "$BODY" | python3 -c '
import json, sys
host = sys.argv[1]
data = json.load(sys.stdin)
for item in data.get("integrations", []):
    extra = item.get("extra_json") or {}
    if item.get("provider") == "xoc_appliance" and str(extra.get("host", "")) == host:
        print(item.get("id", ""))
        break
' "$SERVER_PRIVATE_IP")"
fi

if [[ -z "$INTEGRATION_ID" ]]; then
    log "Creando integracion xoc_appliance"
    http POST /integrations "$ADMIN_TOKEN" "$(python3 -c '
import json, sys
private_ip, public_ip = sys.argv[1], sys.argv[2]
print(json.dumps({
    "provider": "xoc_appliance",
    "credentials": {
        "host": private_ip,
        "public_ip": public_ip,
        "api_key": "xoc-test-target",
        "ssh_user": "ubuntu",
    },
    "extra_json": {
        "description": "XOC Test Target (laboratorio)",
        "host": private_ip,
        "public_ip": public_ip,
        "os": "Ubuntu 24.04",
        "environment": "lab",
        "malware_manifest": "/opt/xoc-test/incident/manifest.json",
    },
}))
' "$SERVER_PRIVATE_IP" "$SERVER_PUBLIC_IP")"
    [[ "$STATUS" == "201" || "$STATUS" == "200" ]] || die "No se pudo crear integracion ($STATUS): $BODY"
    INTEGRATION_ID="$(printf '%s' "$BODY" | jget integration.id)"
else
    log "Integracion existente id=$INTEGRATION_ID"
fi

# --- 5. System ---------------------------------------------------------------
log "Buscando system '$SYSTEM_NAME'"
http GET /systems "$ADMIN_TOKEN"
SYSTEM_ID=""
if [[ "$STATUS" == "200" ]]; then
    SYSTEM_ID="$(printf '%s' "$BODY" | python3 -c '
import json, sys
name = sys.argv[1]
data = json.load(sys.stdin)
for item in data.get("systems", []):
    if str(item.get("name", "")) == name:
        print(item.get("id", ""))
        break
' "$SYSTEM_NAME")"
fi

if [[ -z "$SYSTEM_ID" ]]; then
    log "Creando system"
    http POST /systems "$ADMIN_TOKEN" "$(python3 -c '
import json, sys
integration_id, name, private_ip, public_ip = sys.argv[1:5]
print(json.dumps({
    "integration_id": int(integration_id),
    "name": name,
    "type": "server",
    "status": "online",
    "health_score": 72.5,
    "meta_info": {
        "ip": private_ip,
        "public_ip": public_ip,
        "os": "Ubuntu 24.04",
        "environment": "lab",
        "services": ["ssh", "cron", "ssm"],
        "incident_manifest": "/opt/xoc-test/incident/manifest.json",
    },
}))
' "$INTEGRATION_ID" "$SYSTEM_NAME" "$SERVER_PRIVATE_IP" "$SERVER_PUBLIC_IP")"
    [[ "$STATUS" == "201" || "$STATUS" == "200" ]] || die "No se pudo crear system ($STATUS): $BODY"
    SYSTEM_ID="$(printf '%s' "$BODY" | jget system.id)"
else
    log "System existente id=$SYSTEM_ID"
fi

# --- 6. Agent key para ingesta de scans -------------------------------------
log "Asegurando agent key '$AGENT_KEY_NAME' (scanner nmap)"
http GET /tenant/agent-keys "$ADMIN_TOKEN"
AGENT_KEY_ID=""
if [[ "$STATUS" == "200" ]]; then
    AGENT_KEY_ID="$(printf '%s' "$BODY" | python3 -c '
import json, sys
name = sys.argv[1]
data = json.load(sys.stdin)
for item in data.get("agent_keys", []):
    if str(item.get("name", "")) == name:
        print(item.get("id", ""))
        break
' "$AGENT_KEY_NAME")"
fi

AGENT_KEY=""
if [[ -z "$AGENT_KEY_ID" ]]; then
    http POST /tenant/agent-keys "$ADMIN_TOKEN" "$(python3 -c 'import json,sys; print(json.dumps({"name": sys.argv[1], "integration_type": "nmap"}))' "$AGENT_KEY_NAME")"
    [[ "$STATUS" == "201" || "$STATUS" == "200" ]] || die "No se pudo crear agent key ($STATUS): $BODY"
    AGENT_KEY="$(printf '%s' "$BODY" | jget agent_key.api_key)"
else
    warn "Agent key ya existia; regenerando para obtener el valor"
    http POST "/tenant/agent-keys/$AGENT_KEY_ID/regenerate" "$ADMIN_TOKEN"
    [[ "$STATUS" == "200" ]] || die "No se pudo regenerar agent key ($STATUS): $BODY"
    AGENT_KEY="$(printf '%s' "$BODY" | jget agent_key.api_key)"
fi

# --- 6b. Runtime settings: Victor on-premise del tenant ----------------------
VICTOR_BASE_URL="${VICTOR_BASE_URL:-http://$SERVER_PRIVATE_IP:8000}"
if [[ "${SET_VICTOR_RUNTIME_SETTINGS:-true}" == "true" ]]; then
    log "Configurando runtime settings (Victor on-premise) -> $VICTOR_BASE_URL"
    http PUT /tenant/runtime-settings "$ADMIN_TOKEN" "$(python3 -c '
import json, sys
base_url = sys.argv[1]
print(json.dumps({
    "function_base_url": base_url,
    "function_route_victor": "/api/agents/VictorDurableAgent/run",
    "is_active": True,
}))
' "$VICTOR_BASE_URL")"
    [[ "$STATUS" == "200" || "$STATUS" == "201" ]] || die "No se pudo guardar runtime settings ($STATUS): $BODY"
    warn "Runtime settings son compartidos con Sophia para este tenant; el lab no usa chat."
fi

# --- 7. Resumen --------------------------------------------------------------
mkdir -p "$(dirname "$OUTPUT_FILE")"
umask 077
cat > "$OUTPUT_FILE" <<EOF
API_BASE=$API_BASE
TENANT_ID=$TENANT_ID
TENANT_NAME=$TENANT_NAME
INTEGRATION_ID=$INTEGRATION_ID
SYSTEM_ID=$SYSTEM_ID
SERVER_PRIVATE_IP=$SERVER_PRIVATE_IP
SERVER_PUBLIC_IP=$SERVER_PUBLIC_IP
ADMIN_EMAIL=$ADMIN_EMAIL
ADMIN_PASSWORD=$ADMIN_PASSWORD
USER_EMAIL=$USER_EMAIL
USER_PASSWORD=$USER_PASSWORD
OPERATOR_EMAIL=$OPERATOR_EMAIL
OPERATOR_PASSWORD=$OPERATOR_PASSWORD
AGENT_KEY_NAME=$AGENT_KEY_NAME
AGENT_KEY=$AGENT_KEY
VICTOR_BASE_URL=$VICTOR_BASE_URL
EOF
chmod 600 "$OUTPUT_FILE"

cat <<EOF

============================================================
 Tenant de pruebas listo
============================================================
 Tenant:        $TENANT_NAME (id=$TENANT_ID, plan=$PLAN_STATUS)
 Integracion:   id=$INTEGRATION_ID (xoc_appliance)
 System:        id=$SYSTEM_ID ($SYSTEM_NAME)
 Servidor:      privada=$SERVER_PRIVATE_IP publica=$SERVER_PUBLIC_IP

 Usuarios:
   ADMIN_XOC (aprueba todo):   $OPERATOR_EMAIL / $OPERATOR_PASSWORD
   ADMIN tenant (basic..risky):$ADMIN_EMAIL / $ADMIN_PASSWORD
   USER (solo basic):          $USER_EMAIL / $USER_PASSWORD

 Agent key ($AGENT_KEY_NAME): $AGENT_KEY
 Archivo con credenciales:    $OUTPUT_FILE

 Siguiente paso:
   ./scripts/demo_governance_matrix.sh --env-file $OUTPUT_FILE
============================================================
EOF
