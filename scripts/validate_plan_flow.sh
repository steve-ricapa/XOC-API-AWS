#!/usr/bin/env bash
# =============================================================================
# Validacion del flujo de planes + gobernanza por solucion (preview IA).
#
# Comprueba, contra el API desplegado, que:
#   1. Un ticket genera action_plans (varios) y pending_decision.options.
#   2. El endpoint de preview analiza la solucion custom sin cambiar el estado.
#   3. La gobernanza se evalua sobre el plan resultante:
#        - USER proponiendo un plan basic  -> 200 y can_execute=true
#        - USER proponiendo "rm -f ..."    -> 200, can_execute=false + mensaje
#        - USER ejecutando "rm -f ..."     -> 403
#        - ADMIN declarando basic sobre "rm -rf /" -> riesgo critical (no se puede maquillar)
#   4. Al ejecutar un plan basic, el ticket termina RESUELTO/EXECUTED con
#      execution_logs.timeline y execution_summary poblados.
#
# Uso:
#   ./scripts/validate_plan_flow.sh --env-file /c/Users/ssilvar/.xoc/test-tenant.env
#   ./scripts/validate_plan_flow.sh --env-file ... --no-create   # usa ticket existente
# =============================================================================

set -uo pipefail

ENV_FILE=""
NO_CREATE=false
WAIT_SECONDS="${WAIT_SECONDS:-300}"
STATUS=""
BODY=""
PASS=0
FAIL=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file) ENV_FILE="${2:-}"; shift 2 ;;
        --no-create) NO_CREATE=true; shift ;;
        --wait-seconds) WAIT_SECONDS="${2:-}"; shift 2 ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "Argumento desconocido: $1" >&2; exit 2 ;;
    esac
done

if [[ -n "$ENV_FILE" ]]; then
    [[ -f "$ENV_FILE" ]] || { echo "No existe $ENV_FILE" >&2; exit 1; }
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

API_BASE="${API_BASE:-https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin.lab@xoc.local}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
USER_EMAIL="${USER_EMAIL:-analyst.lab@xoc.local}"
USER_PASSWORD="${USER_PASSWORD:-}"

[[ -n "$ADMIN_PASSWORD" ]] || { echo "Falta ADMIN_PASSWORD (usa --env-file)" >&2; exit 1; }

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python

log()  { printf '\033[0;36m[*]\033[0m %s\n' "$*"; }
ok()   { printf '\033[0;32m[PASS]\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '\033[0;31m[FAIL]\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
warn() { printf '\033[0;33m[!]\033[0m %s\n' "$*"; }
rule() { printf '%s\n' "------------------------------------------------------------"; }

http() {
    local method="$1" path="$2" token="${3:-}" data="${4:-}"
    local args=(-sS -X "$method" "$API_BASE$path" -H "Content-Type: application/json")
    [[ -n "$token" ]] && args+=(-H "Authorization: Bearer $token")
    [[ -n "$data" ]] && args+=(-d "$data")
    local out
    out="$(curl "${args[@]}" -w $'\n%{http_code}')" || { echo "curl fallo: $method $path" >&2; exit 1; }
    STATUS="${out##*$'\n'}"
    BODY="${out%$'\n'*}"
}

jget() {
    "$PY" -c '
import json, sys
raw = sys.stdin.read()
try:
    value = json.loads(raw) if raw.strip() else None
except Exception:
    value = None
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
    http POST /auth/login "" "$("$PY" -c 'import json,sys; print(json.dumps({"email": sys.argv[1], "password": sys.argv[2]}))' "$1" "$2")"
    [[ "$STATUS" == "200" ]] || return 1
    printf '%s' "$BODY" | jget access_token
}

create_ticket() {
    http POST /tickets "$ADMIN_TOKEN" "$("$PY" -c '
import json, sys
print(json.dumps({
    "subject": sys.argv[1],
    "description": sys.argv[2],
    "priority": "high",
    "metadata": {"source": "validate-plan-flow", "environment": "lab"},
}))
' "$1" "$2")"
    [[ "$STATUS" == "201" ]] || { bad "No se creo ticket ($STATUS): $BODY"; return 1; }
    printf '%s' "$BODY" | jget ticket.id
}

poll_status() {
    local ticket_id="$1" wanted="$2" waited=0
    while (( waited < WAIT_SECONDS )); do
        http GET "/tickets/$ticket_id" "$ADMIN_TOKEN"
        if [[ "$STATUS" == "200" ]]; then
            local state; state="$(printf '%s' "$BODY" | jget status)"
            case "$state" in
                "$wanted") printf '%s' "$BODY"; return 0 ;;
                FALLIDO|FAILED|DERIVADO|DERIVED|RECHAZADO|REJECTED)
                    printf '%s' "$BODY"; return 2 ;;
            esac
        fi
        sleep 10; waited=$((waited+10)); printf '.' >&2
    done
    printf '\n' >&2
    return 1
}

preview_custom() {
    local token="$1" ticket_id="$2" json="$3"
    http PATCH "/tickets/$ticket_id/decision/custom/preview" "$token" "$json"
}

submit_custom() {
    local token="$1" ticket_id="$2" json="$3"
    http PATCH "/tickets/$ticket_id/decision/custom" "$token" "$json"
}

ADMIN_TOKEN="$(login "$ADMIN_EMAIL" "$ADMIN_PASSWORD")" || { echo "Login admin fallo: $BODY" >&2; exit 1; }
USER_TOKEN="$(login "$USER_EMAIL" "$USER_PASSWORD")" || { echo "Login user fallo: $BODY" >&2; exit 1; }
log "API=$API_BASE admin y user autenticados"

# --- Probe rapido del endpoint de preview ------------------------------------
log "Probe del endpoint de preview (debe existir tras deploy:tickets:prod)"
http PATCH "/tickets/__probe__/decision/custom/preview" "$ADMIN_TOKEN" '{"custom_plan_steps":[{"command":"ls"}]}'
if [[ "$STATUS" == "404" && "$BODY" == *"Not Found"* ]]; then
    bad "El endpoint /decision/custom/preview NO esta desplegado (404). Ejecuta: npm run deploy:tickets:prod"
    echo; echo "Resultado: $PASS pasaron, $FAIL fallaron"; exit 2
fi
ok "Endpoint de preview responde (HTTP $STATUS)"

rule
log "Preparando ticket de prueba"
if $NO_CREATE; then
    TICKET_ID="$(curl -sS "$API_BASE/tickets" -H "Authorization: Bearer $ADMIN_TOKEN" | "$PY" -c '
import json,sys
ts=json.load(sys.stdin).get("tickets",[])
pre=[t for t in ts if t.get("status")=="PREAPROBADO"]
print(pre[0]["id"] if pre else "")
')"
    [[ -n "$TICKET_ID" ]] || { bad "No hay tickets PREAPROBADO para --no-create"; echo; echo "Resultado: $PASS pasaron, $FAIL fallaron"; exit 2; }
else
    TICKET_ID="$(create_ticket "Validacion plan flow $(date +%H%M%S)" "Se detecto /opt/xoc-test/incident/eicar_test.txt. Solo se requiere listar y revisar el archivo para confirmar el hallazgo.")" || exit 1
fi
log "Ticket $TICKET_ID"

DOC="$(poll_status "$TICKET_ID" PREAPROBADO)" || { bad "El ticket no llego a PREAPROBADO (Victor/lab)"; echo "$DOC" | "$PY" -m json.tool 2>/dev/null | head -20; echo; echo "Resultado: $PASS pasaron, $FAIL fallaron"; exit 2; }

# 1. action_plans + options
AP="$(printf '%s' "$DOC" | "$PY" -c 'import json,sys; print(len((json.load(sys.stdin).get("action_plans") or [])))')"
OPTS="$(printf '%s' "$DOC" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print(len((d.get("pending_decision") or {}).get("options") or []))')"
if [[ "${AP:-0}" -ge 1 ]]; then ok "action_plans generados: $AP"; else bad "action_plans vacio"; fi
if [[ "${OPTS:-0}" -ge 1 ]]; then ok "pending_decision.options: $OPTS"; else bad "pending_decision.options vacio"; fi

# 2. Preview safe as USER
SAFE='{"custom_plan_steps":[{"order":1,"action":"shell","command":"ls -la /opt/xoc-test/incident","description":"Listar artefactos","risk_level":"basic"}]}'
preview_custom "$USER_TOKEN" "$TICKET_ID" "$SAFE"
if [[ "$STATUS" == "200" ]]; then
    CAN="$(printf '%s' "$BODY" | jget can_execute)"
    RISK="$(printf '%s' "$BODY" | jget requirement.max_risk_level)"
    if [[ "$CAN" == "True" && "$RISK" == "basic" ]]; then
        ok "Preview safe (USER): can_execute=$CAN risk=$RISK"
    else
        bad "Preview safe inesperado: can_execute=$CAN risk=$RISK"
    fi
else
    bad "Preview safe devolvio HTTP $STATUS: $BODY"
fi

# 3. Preview risky as USER (plan se analiza pero no se puede ejecutar)
RISKY='{"custom_plan_steps":[{"order":1,"action":"shell","command":"rm -f /opt/xoc-test/incident/backdoor.sh","description":"Eliminar backdoor","risk_level":"basic"}]}'
preview_custom "$USER_TOKEN" "$TICKET_ID" "$RISKY"
if [[ "$STATUS" == "200" ]]; then
    CAN="$(printf '%s' "$BODY" | jget can_execute)"
    RISK="$(printf '%s' "$BODY" | jget requirement.max_risk_level)"
    MSG="$(printf '%s' "$BODY" | jget governance_message)"
    if [[ "$CAN" == "False" && "$RISK" == "risky" && -n "$MSG" ]]; then
        ok "Preview risky (USER bloqueado): risk=$RISK can_execute=$CAN -> $MSG"
    else
        bad "Preview risky inesperado: can_execute=$CAN risk=$RISK msg='$MSG'"
    fi
else
    bad "Preview risky devolvio HTTP $STATUS: $BODY"
fi

# 4. Declared basic cannot mask rm -rf /
MASK='{"custom_plan_steps":[{"order":1,"action":"shell","command":"rm -rf /","risk_level":"basic"}]}'
preview_custom "$ADMIN_TOKEN" "$TICKET_ID" "$MASK"
if [[ "$STATUS" == "200" ]]; then
    RISK="$(printf '%s' "$BODY" | jget requirement.max_risk_level)"
    CAN="$(printf '%s' "$BODY" | jget can_execute)"
    if [[ "$RISK" == "critical" && "$CAN" == "False" ]]; then
        ok "Inferencia por comando: 'rm -rf /' declarado basic -> risk=$RISK (ADMIN no puede)"
    else
        bad "Inferencia por comando inesperada: risk=$RISK can_execute=$CAN"
    fi
else
    bad "Preview mask devolvio HTTP $STATUS: $BODY"
fi

# 5. Submit risky as USER -> 403
submit_custom "$USER_TOKEN" "$TICKET_ID" "$RISKY"
if [[ "$STATUS" == "403" ]]; then
    ok "Submit risky como USER -> 403 esperado"
else
    bad "Submit risky como USER devolvio HTTP $STATUS (esperado 403): $BODY"
fi

# 6. Submit safe as USER -> 200 y ejecucion
submit_custom "$USER_TOKEN" "$TICKET_ID" "$SAFE"
if [[ "$STATUS" == "200" ]]; then
    ok "Submit safe como USER -> 200"
else
    bad "Submit safe como USER devolvio HTTP $STATUS: $BODY"
fi

DONE="$(poll_status "$TICKET_ID" RESUELTO)"
if [[ $? -eq 0 ]]; then
    TL="$(printf '%s' "$DONE" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); logs=d.get("execution_logs") or {}; print(len((logs.get("timeline") if isinstance(logs,dict) else []) or []))')"
    FR="$(printf '%s' "$DONE" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); logs=d.get("execution_logs") or {}; print((logs.get("final_result") or {}).get("status") if isinstance(logs,dict) else "")')"
    SUM="$(printf '%s' "$DONE" | jget execution_summary)"
    EST="$(printf '%s' "$DONE" | jget execution_status)"
    if [[ "${TL:-0}" -ge 1 ]]; then ok "execution_logs.timeline tiene $TL eventos"; else bad "execution_logs.timeline vacio"; fi
    if [[ "$FR" == "completed" ]]; then ok "final_result.status=completed"; else bad "final_result.status='$FR'"; fi
    if [[ "$EST" == "EXECUTED" ]]; then ok "execution_status=EXECUTED"; else bad "execution_status='$EST'"; fi
    if [[ -n "$SUM" ]]; then ok "execution_summary: ${SUM:0:90}"; else bad "execution_summary vacio"; fi
else
    bad "El ticket no termino RESUELTO (revisa labs Victor / execution_logs)"
fi

rule
echo "Resultado: $PASS pasaron, $FAIL fallaron"
[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1
