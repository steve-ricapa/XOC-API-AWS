#!/usr/bin/env bash
# =============================================================================
# Prepara el laboratorio VICTOR (MV xoc-test) para la demo de tickets.
#
# Hace, de forma idempotente:
#   1. Asegura el mount de /opt/xoc-test dentro del contenedor executor.
#   2. Siembra los artefactos maliciosos simulados (EICAR, backdoor, etc.).
#   3. Reconstruye/levanta VICTOR y verifica herramientas de diagnostico
#      (file, ps, netstat) y que el executor VEA los artefactos.
#
# Uso (en la MV del laboratorio, donde vive /opt/victor):
#   XOC_AWS_DIR=~/XOC_AWS VICTOR_DIR=/opt/victor sudo -E ./scripts/prepare-victor-lab-demo.sh
#
# Variables:
#   VICTOR_DIR    directorio de VICTOR (default /opt/victor)
#   XOC_AWS_DIR   copia del repo backend con scripts/test-server-seed.sh
#   XOC_TEST_ROOT raiz de artefactos (default /opt/xoc-test)
# =============================================================================

set -euo pipefail

VICTOR_DIR="${VICTOR_DIR:-/opt/victor}"
XOC_AWS_DIR="${XOC_AWS_DIR:-$HOME/XOC_AWS}"
XOC_TEST_ROOT="${XOC_TEST_ROOT:-/opt/xoc-test}"
SEED_SCRIPT_URL="${SEED_SCRIPT_URL:-https://raw.githubusercontent.com/steve-ricapa/XOC-API-AWS/main/scripts/test-server-seed.sh}"

log() { printf '\033[0;36m[*]\033[0m %s\n' "$*"; }
ok()  { printf '\033[0;32m[OK]\033[0m %s\n' "$*"; }
warn(){ printf '\033[0;33m[!]\033[0m %s\n' "$*"; }

[[ "$(id -u)" -eq 0 ]] || { echo "Ejecuta con sudo (necesario para sembrar y montar)." >&2; exit 1; }
[[ -d "$VICTOR_DIR" ]] || { echo "No existe $VICTOR_DIR" >&2; exit 1; }

# --- 1. Mount /opt/xoc-test en el executor ------------------------------------
log "Asegurando override de compose con mount $XOC_TEST_ROOT"
OVERRIDE="$VICTOR_DIR/docker-compose.override.yml"
if [[ -f "$OVERRIDE" ]] && grep -q "$XOC_TEST_ROOT:$XOC_TEST_ROOT" "$OVERRIDE"; then
    ok "El override ya monta $XOC_TEST_ROOT"
else
    if [[ -f "$OVERRIDE" ]]; then
        warn "Override existente sin el mount; se reescribe"
    fi
    cat > "$OVERRIDE" <<EOF_OVERRIDE
services:
  executor:
    volumes:
      - /tmp:/tmp:rw
      - /var/quarantine:/var/quarantine:rw
      - $XOC_TEST_ROOT:$XOC_TEST_ROOT:rw
EOF_OVERRIDE
    ok "Override creado en $OVERRIDE"
fi

# --- 2. Sembrar artefactos ----------------------------------------------------
log "Sembrando artefactos en $XOC_TEST_ROOT/incident"
mkdir -p "$XOC_TEST_ROOT"
if [[ -f "$XOC_AWS_DIR/scripts/test-server-seed.sh" ]]; then
    bash "$XOC_AWS_DIR/scripts/test-server-seed.sh"
elif [[ -x "$XOC_TEST_ROOT/seed.sh" ]]; then
    "$XOC_TEST_ROOT/seed.sh"
else
    curl -fsSL --retry 3 "$SEED_SCRIPT_URL" -o "$XOC_TEST_ROOT/seed.sh"
    chmod +x "$XOC_TEST_ROOT/seed.sh"
    "$XOC_TEST_ROOT/seed.sh"
fi
ok "Artefactos sembrados"

# --- 3. Rebuild / up ----------------------------------------------------------
log "Construyendo y levantando VICTOR"
if docker compose version >/dev/null 2>&1; then COMPOSE=(docker compose); else COMPOSE=(docker-compose); fi
( cd "$VICTOR_DIR" && "${COMPOSE[@]}" up -d --build )

log "Esperando health de VICTOR"
healthy=false
for _ in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then healthy=true; break; fi
    sleep 5
done
[[ "$healthy" == "true" ]] && ok "VICTOR healthy" || warn "VICTOR no respondio /health a tiempo"

# --- 4. Verificaciones --------------------------------------------------------
log "Verificando herramientas de diagnostico en el executor"
( cd "$VICTOR_DIR" && "${COMPOSE[@]}" exec -T executor sh -c \
    "for t in file ps netstat ip stat head ls; do command -v \$t >/dev/null && echo \"  \$t OK\" || echo \"  \$t MISSING\"; done" ) || warn "No se pudo ejecutar la verificacion"

log "Verificando que el executor vea los artefactos"
( cd "$VICTOR_DIR" && "${COMPOSE[@]}" exec -T executor sh -c "ls -la $XOC_TEST_ROOT/incident | head -20" ) \
    || warn "El executor no ve $XOC_TEST_ROOT/incident (revisa el mount)"

ok "Laboratorio listo para la demo"
