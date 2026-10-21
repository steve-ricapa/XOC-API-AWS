#!/usr/bin/env bash
# =============================================================================
# Demo de gobernanza por rol con el tenant de pruebas.
#
# Crea tickets de distinto perfil, espera a que VICTOR genere los planes y
# valida la matriz de aprobacion:
#   USER       -> solo riesgo basic
#   ADMIN      -> basic, controlled y risky
#   ADMIN_XOC  -> todo (incluido critical)
#
# Uso:
#   ./scripts/demo_governance_matrix.sh --env-file local-output/test-tenant.env
#   ./scripts/demo_governance_matrix.sh --env-file local-output/test-tenant.env --execute
#
# Sin --execute solo crea tickets y muestra la matriz esperada (no aprueba).
# Con --execute primero prueba el rol insuficiente (debe dar 403) y luego
# aprueba con el rol correcto (debe dar 200).
# =============================================================================

set -euo pipefail

ENV_FILE=""
EXECUTE=false
WAIT_SECONDS="${WAIT_SECONDS:-300}"
STATUS=""
BODY=""

for arg in "$@"; do
    case "$arg" in
        --env-file) ENV_FILE="${2:-}"; shift 2 ;;
        --execute) EXECUTE=true; shift ;;
        --wait-seconds) WAIT_SECONDS="${2:-}"; shift 2 ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "Argumento desconocido: $arg" >&2; exit 2 ;;
    esac
done

if [[ -n "$ENV_FILE" ]]; then
    [[ -f "$ENV_FILE" ]] || { echo "No existe $ENV_FILE" >&2; exit 1; }
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

API_BASE="${API_BASE:-https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com}"
TENANT_ID="${TENANT_ID:-}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin.lab@xoc.local}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
USER_EMAIL="${USER_EMAIL:-analyst.lab@xoc.local}"
USER_PASSWORD="${USER_PASSWORD:-}"
OPERATOR_EMAIL="${OPERATOR_EMAIL:-xoc.operator@xoc.local}"
OPERATOR_PASSWORD="${OPERATOR_PASSWORD:-}"

[[ -n "$TENANT_ID" ]] || { echo "Falta TENANT_ID (usa --env-file)" >&2; exit 1; }
[[ -n "$ADMIN_PASSWORD" ]] || { echo "Falta ADMIN_PASSWORD" >&2; exit 1; }

log()  { printf '\033[0;32m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[!]\033[0m %s\n' "$*"; }
err()  { printf '\033[0;31m[x]\033[0m %s\n' "$*" >&2; }
rule() { printf '%s\n' "------------------------------------------------------------"; }

http() {
    local method="$1" path="$2" token="${3:-}" data="${4:-}"
    local args=(-sS -X "$method" "$API_BASE$path" -H "Content-Type: application/json")
    [[ -n "$token" ]] && args+=(-H "Authorization: Bearer $token")
    [[ -n "$data" ]] && args+=(-d "$data")
    local out
    out="$(curl "${args[@]}" -w $'\n%{http_code}')" || { err "curl fallo: $method $path"; exit 1; }
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
    [[ "$STATUS" == "200" ]] || return 1
    printf '%s' "$BODY" | jget access_token
}

ADMIN_TOKEN="$(login "$ADMIN_EMAIL" "$ADMIN_PASSWORD")" || { err "Login admin fallo: $BODY"; exit 1; }
USER_TOKEN="$(login "$USER_EMAIL" "$USER_PASSWORD")" || { err "Login user fallo: $BODY"; exit 1; }
OPERATOR_TOKEN=""
if [[ -n "$OPERATOR_PASSWORD" ]]; then
    OPERATOR_TOKEN="$(login "$OPERATOR_EMAIL" "$OPERATOR_PASSWORD" || true)"
    [[ -n "$OPERATOR_TOKEN" ]] || warn "Login ADMIN_XOC fallo; no habra prueba critical positiva"
fi

log "API=$API_BASE tenant=$TENANT_ID"

create_ticket() {
    local subject="$1" description="$2"
    http POST /tickets "$ADMIN_TOKEN" "$(python3 -c '
import json, sys
subject, description = sys.argv[1], sys.argv[2]
print(json.dumps({
    "subject": subject,
    "description": description,
    "priority": "high",
    "metadata": {"source": "governance-demo", "environment": "lab"},
}))
' "$subject" "$description")"
    [[ "$STATUS" == "201" ]] || { err "No se creo ticket ($STATUS): $BODY"; return 1; }
    printf '%s' "$BODY" | jget ticket.id
}

poll_preaprobado() {
    local ticket_id="$1" waited=0
    while (( waited < WAIT_SECONDS )); do
        http GET "/tickets/$ticket_id" "$ADMIN_TOKEN"
        if [[ "$STATUS" == "200" ]]; then
            local state
            state="$(printf '%s' "$BODY" | jget status)"
            case "$state" in
                PREAPROBADO) printf '%s' "$BODY"; return 0 ;;
                DERIVADO|DERIVED|FALLIDO|FAILED|RECHAZADO)
                    err "Ticket $ticket_id termino en $state"
                    printf '%s' "$BODY"
                    return 2
                    ;;
            esac
        fi
        sleep 10
        waited=$((waited + 10))
        printf '.' >&2
    done
    printf '\n' >&2
    err "Timeout esperando PREAPROBADO para $ticket_id"
    return 1
}

approve_as() {
    local token="$1" ticket_id="$2" label="$3"
    http PATCH "/tickets/$ticket_id/approve" "$token"
    printf '%s|%s' "$STATUS" "$(printf '%s' "$BODY" | jget error)"
    log "  $label -> HTTP $STATUS ${BODY:+}"
}

declare -a TICKET_IDS=() REQUIRED=() MAXRISK=() OPTIONS=() TITLES=()

SPECS=(
    "Revisar archivo sospechoso en servidor de pruebas|Se detecto /opt/xoc-test/incident/eicar_test.txt en el servidor de laboratorio. Solo se requiere listar y revisar el archivo para confirmar el hallazgo. Accion de lectura, sin cambios."
    "Servicio comprometido: reiniciar y actualizar configuracion|Se detecto persistencia en /etc/cron.d/xoc-test-lab y un proceso kworker sospechoso. Se requiere reiniciar el servicio afectado y actualizar su configuracion para contener la amenaza."
    "Erradicar backdoor y purgar artefactos|Se detecto una backdoor activa y multiples artefactos maliciosos en /opt/xoc-test/incident. Se requiere eliminar los archivos, borrar las llaves SSH no autorizadas y purgar el directorio comprometido."
)

log "Creando 3 tickets (perfil basic / controlled-risky / critical)"
for spec in "${SPECS[@]}"; do
    subject="${spec%%|*}"
    description="${spec#*|}"
    id="$(create_ticket "$subject" "$description")" || exit 1
    log "  ticket $id: $subject"
    TICKET_IDS+=("$id")
done

log "Esperando planes de VICTOR (hasta ${WAIT_SECONDS}s por ticket)"
READY=0
for idx in "${!TICKET_IDS[@]}"; do
    id="${TICKET_IDS[$idx]}"
    printf '  [%s] %s ' "$((idx+1))" "$id"
    if doc="$(poll_preaprobado "$id")"; then
        READY=$((READY+1))
        req="$(printf '%s' "$doc" | jget pending_decision.required_approver_role)"
        risk="$(printf '%s' "$doc" | jget pending_decision.max_risk_level)"
        opts="$(printf '%s' "$doc" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len((d.get("pending_decision") or {}).get("options") or []))')"
        titles="$(printf '%s' "$doc" | python3 -c '
import json, sys
d = json.load(sys.stdin)
for opt in (d.get("pending_decision") or {}).get("options") or []:
    print("      - %s: %s (risk=%s, steps=%s)" % (
        opt.get("option_id"), opt.get("title"), opt.get("risk_level"), opt.get("total_steps")
    ))
')"
        printf '\n'
        log "  required=$req risk=$risk options=$opts"
        [[ -n "$titles" ]] && printf '%s\n' "$titles"
        REQUIRED+=("$req"); MAXRISK+=("$risk"); OPTIONS+=("$opts")
    else
        printf '\n'
        REQUIRED+=(""); MAXRISK+=(""); OPTIONS+=("0")
    fi
done

rule
log "Matriz de gobernanza"
printf '%-38s %-12s %-10s %-8s %s\n' "ticket" "risk" "requiere" "opciones" "esperado"
for idx in "${!TICKET_IDS[@]}"; do
    req="${REQUIRED[$idx]}"
    case "$req" in
        USER) expect="USER aprueba; ADMIN y ADMIN_XOC tambien" ;;
        ADMIN) expect="USER -> 403; ADMIN/ADMIN_XOC -> 200" ;;
        SUPERADMIN|ADMIN_XOC) expect="USER/ADMIN -> 403; ADMIN_XOC -> 200" ;;
        *) expect="sin pending_decision (Victor no genero plan)" ;;
    esac
    printf '%-38s %-12s %-10s %-8s %s\n' "${TICKET_IDS[$idx]}" "${MAXRISK[$idx]:--}" "${req:-n/a}" "${OPTIONS[$idx]}" "$expect"
done
rule

if [[ "$READY" -eq 0 ]]; then
    err "Ningun ticket alcanzo PREAPROBADO. Revisa que VICTOR (10.20.0.22) sea alcanzable desde las Lambdas y que el backend nuevo este desplegado."
    exit 2
fi

if [[ "$EXECUTE" != "true" ]]; then
    warn "Modo dry-run. Re-ejecuta con --execute para probar 403/200 y aprobar."
    exit 0
fi

log "Ejecutando pruebas de aprobacion"
for idx in "${!TICKET_IDS[@]}"; do
    id="${TICKET_IDS[$idx]}"
    req="${REQUIRED[$idx]}"
    [[ -n "$req" ]] || { warn "$id sin plan; se omite"; continue; }
    rule
    log "Ticket $id (requiere $req)"
    case "$req" in
        USER)
            approve_as "$USER_TOKEN" "$id" "USER (esperado 200)"
            ;;
        ADMIN)
            approve_as "$USER_TOKEN" "$id" "USER (esperado 403)"
            approve_as "$ADMIN_TOKEN" "$id" "ADMIN (esperado 200)"
            ;;
        SUPERADMIN|ADMIN_XOC)
            approve_as "$USER_TOKEN" "$id" "USER (esperado 403)"
            approve_as "$ADMIN_TOKEN" "$id" "ADMIN (esperado 403)"
            if [[ -n "$OPERATOR_TOKEN" ]]; then
                approve_as "$OPERATOR_TOKEN" "$id" "ADMIN_XOC (esperado 200)"
            else
                warn "Sin token ADMIN_XOC; ticket $id queda PREAPROBADO"
            fi
            ;;
        *)
            warn "Rol requerido desconocido ($req) para $id"
            ;;
    esac
done

rule
log "Fin. Revisa los tickets en https://xoc-frontend.firebaseapp.com/dashboard (tenant $TENANT_ID)"
