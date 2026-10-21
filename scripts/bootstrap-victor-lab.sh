#!/usr/bin/env bash
# =============================================================================
# XOC lab bootstrap - prepara una EC2 Ubuntu (22.04/24.04) como laboratorio:
#   1. swap (para t3.micro)
#   2. Docker + compose plugin
#   3. artefactos maliciosos simulados en /opt/xoc-test
#   4. VICTOR on-premise (server + executor) con /opt/xoc-test montado
#
# Uso en una instancia ya creada:
#   curl -fsSL https://raw.githubusercontent.com/steve-ricapa/XOC-API-AWS/main/scripts/bootstrap-victor-lab.sh -o /tmp/bootstrap.sh
#   sudo ANTHROPIC_API_KEY="..." bash /tmp/bootstrap.sh
#
# Variables (todas opcionales salvo la key para generar planes reales):
#   ANTHROPIC_API_KEY, ANTHROPIC_MODEL, AZURE_ENDPOINT, AZURE_DEPLOYMENT
#   INSTALL_VICTOR=true|false, SWAP_SIZE=2G, XOC_TEST_ROOT=/opt/xoc-test
#   VICTOR_REPO_URL, VICTOR_REPO_REF, VICTOR_DIR=/opt/victor
#   SEED_SCRIPT_URL, DOCKER_INSTALL_URL
# =============================================================================

set -euo pipefail

ROOT_DIR="${XOC_TEST_ROOT:-/opt/xoc-test}"
INCIDENT_DIR="$ROOT_DIR/incident"
VICTOR_DIR="${VICTOR_DIR:-/opt/victor}"
INSTALL_VICTOR="${INSTALL_VICTOR:-true}"
SWAP_SIZE="${SWAP_SIZE:-2G}"
VICTOR_REPO_URL="${VICTOR_REPO_URL:-https://github.com/steve-ricapa/VICTOR-XOC-ClaudeC.git}"
VICTOR_REPO_REF="${VICTOR_REPO_REF:-main}"
SEED_SCRIPT_URL="${SEED_SCRIPT_URL:-https://raw.githubusercontent.com/steve-ricapa/XOC-API-AWS/main/scripts/test-server-seed.sh}"
DOCKER_INSTALL_URL="${DOCKER_INSTALL_URL:-https://get.docker.com}"

ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-sonnet-4-6}"
AZURE_ENDPOINT="${AZURE_ENDPOINT:-https://aoai-sophia-xoc-eus2.services.ai.azure.com/anthropic}"
AZURE_DEPLOYMENT="${AZURE_DEPLOYMENT:-claude-sonnet-4-6}"

LOG_FILE="/var/log/xoc-lab-bootstrap.log"

log() {
    local ts
    ts="$(date -Is)"
    echo "[$ts] $*" | tee -a "$LOG_FILE"
}

die() {
    log "ERROR: $*"
    exit 1
}

[[ "$(id -u)" -eq 0 ]] || die "Ejecuta como root (sudo)."

mkdir -p "$ROOT_DIR/logs" "$INCIDENT_DIR"
touch "$LOG_FILE"

log "Iniciando bootstrap del laboratorio XOC"

# --- 1. Paquetes base --------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y -o DPkg::Lock::Timeout=180 curl ca-certificates git jq unzip

# --- 2. Swap (necesario en t3.micro para compilar VICTOR) --------------------
if ! swapon --show 2>/dev/null | grep -q .; then
    log "Creando swap de $SWAP_SIZE"
    if ! fallocate -l "$SWAP_SIZE" /swapfile 2>/dev/null; then
        dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    fi
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
else
    log "Swap ya configurada; se omite"
fi

# --- 3. Seed de artefactos simulados ----------------------------------------
log "Sembrando artefactos simulados en $INCIDENT_DIR"
SEED_TMP="$(mktemp /tmp/xoc-seed.XXXXXX.sh)"
if curl -fsSL --retry 3 --connect-timeout 10 "$SEED_SCRIPT_URL" -o "$SEED_TMP"; then
    chmod +x "$SEED_TMP"
    XOC_TEST_ROOT="$ROOT_DIR" "$SEED_TMP"
else
    log "WARN: no se pudo descargar el seed; creando seed minimo"
    printf 'X5O!P%%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*\n' > "$INCIDENT_DIR/eicar_test.txt"
    cat > "$INCIDENT_DIR/backdoor.sh" <<'EOF_BACKDOOR'
#!/bin/bash
echo "[SIMULATED BACKDOOR] beacon to 192.0.2.10:4444 (no network call)"
EOF_BACKDOOR
    chmod +x "$INCIDENT_DIR/backdoor.sh"
    printf '{"generated_at":"%s","source":"bootstrap-fallback","files":["%s/eicar_test.txt","%s/backdoor.sh"]}\n' \
        "$(date -Is)" "$INCIDENT_DIR" "$INCIDENT_DIR" > "$INCIDENT_DIR/manifest.json"
fi
rm -f "$SEED_TMP"

if [[ "$INSTALL_VICTOR" != "true" ]]; then
    log "INSTALL_VICTOR=false; laboratorio sembrado sin VICTOR"
    echo "XOC lab ready (seed only)" > "$ROOT_DIR/READY"
    exit 0
fi

# --- 4. Docker + compose plugin ---------------------------------------------
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    log "Instalando Docker (docker-ce + compose plugin)"
    curl -fsSL "$DOCKER_INSTALL_URL" -o /tmp/get-docker.sh
    sh /tmp/get-docker.sh
fi
systemctl enable --now docker
usermod -aG docker ubuntu 2>/dev/null || true
mkdir -p /var/quarantine

if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    die "Docker Compose no disponible"
fi

# --- 5. VICTOR on-premise ----------------------------------------------------
if [[ -d "$VICTOR_DIR/.git" ]]; then
    log "Actualizando VICTOR en $VICTOR_DIR"
    git -C "$VICTOR_DIR" fetch --all --tags --prune
    git -C "$VICTOR_DIR" checkout "$VICTOR_REPO_REF"
    git -C "$VICTOR_DIR" reset --hard "origin/$VICTOR_REPO_REF" || true
else
    log "Clonando VICTOR en $VICTOR_DIR"
    mkdir -p "$(dirname "$VICTOR_DIR")"
    git clone --branch "$VICTOR_REPO_REF" "$VICTOR_REPO_URL" "$VICTOR_DIR"
fi

if [[ -z "$ANTHROPIC_API_KEY" ]]; then
    log "WARN: ANTHROPIC_API_KEY vacia; VICTOR arrancara pero no podra generar planes"
fi

log "Escribiendo $VICTOR_DIR/.env"
{
    printf 'ANTHROPIC_API_KEY=%s\n' "$ANTHROPIC_API_KEY"
    printf 'ANTHROPIC_MODEL=%s\n' "$ANTHROPIC_MODEL"
    printf 'AZURE_ENDPOINT=%s\n' "$AZURE_ENDPOINT"
    printf 'AZURE_DEPLOYMENT=%s\n' "$AZURE_DEPLOYMENT"
    printf 'EXECUTOR_URL=http://executor:8888\n'
    printf 'PORT=8000\n'
    printf 'STAGE=prod\n'
} > "$VICTOR_DIR/.env"
chmod 600 "$VICTOR_DIR/.env"

cat > "$VICTOR_DIR/docker-compose.override.yml" <<EOF_OVERRIDE
services:
  executor:
    volumes:
      - /tmp:/tmp:rw
      - /var/quarantine:/var/quarantine:rw
      - $ROOT_DIR:$ROOT_DIR:rw
EOF_OVERRIDE

log "Construyendo y levantando VICTOR (puede tardar varios minutos)"
cd "$VICTOR_DIR"
"${COMPOSE[@]}" up -d --build

healthy=false
for _ in $(seq 1 90); do
    if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
        healthy=true
        break
    fi
    sleep 5
done

if [[ "$healthy" == "true" ]]; then
    echo "VICTOR healthy" > "$VICTOR_DIR/READY"
    log "VICTOR healthy en http://127.0.0.1:8000"
else
    log "WARN: VICTOR no respondio /health en el tiempo esperado"
fi

TOKEN="$(curl -sX PUT http://169.254.169.254/latest/api/token \
    -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null || true)"
PRIVATE_IP="$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
    http://169.254.169.254/latest/meta-data/local-ipv4 2>/dev/null || true)"
if [[ -n "$PRIVATE_IP" ]]; then
    log "Runtime setting para el tenant: http://$PRIVATE_IP:8000"
fi

echo "XOC lab ready" > "$ROOT_DIR/READY"
log "Bootstrap completado. Artefactos: $INCIDENT_DIR"
