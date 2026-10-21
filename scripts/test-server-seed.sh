#!/usr/bin/env bash
# =============================================================================
# XOC test target seed
# =============================================================================
# Crea artefactos SIMULADOS de malware en una EC2 de laboratorio para disparar
# el flujo de tickets/planes de XOC. No ejecuta acciones destructivas, no abre
# conexiones de red y no instala persistencia real por defecto.
#
# Uso:
#   sudo ./scripts/test-server-seed.sh
#   sudo ./scripts/test-server-seed.sh --install-persistence
#   sudo ./scripts/test-server-seed.sh --verify
#
# Salida: /opt/xoc-test/incident/manifest.json
# =============================================================================

set -euo pipefail

ROOT_DIR="${XOC_TEST_ROOT:-/opt/xoc-test}"
INCIDENT_DIR="$ROOT_DIR/incident"
LOG_FILE="$ROOT_DIR/logs/seed.log"
INSTALL_PERSISTENCE=false
VERIFY_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --install-persistence) INSTALL_PERSISTENCE=true ;;
        --verify) VERIFY_ONLY=true ;;
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0
            ;;
        *)
            echo "Argumento desconocido: $arg" >&2
            exit 2
            ;;
    esac
done

mkdir -p "$INCIDENT_DIR" "$ROOT_DIR/logs"

log() {
    local ts
    ts="$(date -Is)"
    echo "[$ts] $*" | tee -a "$LOG_FILE"
}

if [[ "$VERIFY_ONLY" == "true" ]]; then
    log "Verificando artefactos en $INCIDENT_DIR"
    find "$INCIDENT_DIR" -maxdepth 1 -type f -printf '%M %10s %p\n' | sort
    if [[ -f "$INCIDENT_DIR/manifest.json" ]]; then
        log "Manifest OK"
        exit 0
    fi
    log "Manifest ausente; ejecuta el seed"
    exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Ejecuta como root (sudo)." >&2
    exit 1
fi

log "Creando artefactos simulados de malware (sin ejecucion real)"

# --- 1. EICAR: firma AV inofensiva -----------------------------------------
cat > "$INCIDENT_DIR/eicar_test.txt" <<'EICAR_EOF'
X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*
EICAR_EOF

# --- 2. Backdoor shell simulada ---------------------------------------------
cat > "$INCIDENT_DIR/backdoor.sh" <<'BACKDOOR_EOF'
#!/bin/bash
# SIMULADO - no abre conexiones reales
C2_HOST="192.0.2.10"
C2_PORT="4444"
echo "[SIMULATED BACKDOOR] would connect to ${C2_HOST}:${C2_PORT}"
echo "[SIMULATED BACKDOOR] would persist in /etc/cron.d and ~/.bashrc"
echo "[SIMULATED BACKDOOR] would exfiltrate /etc/passwd (no real read)"
BACKDOOR_EOF
chmod 0755 "$INCIDENT_DIR/backdoor.sh"

# --- 3. Reverse shell Python simulada ---------------------------------------
cat > "$INCIDENT_DIR/reverse_shell.py" <<'PY_EOF'
#!/usr/bin/env python3
"""SIMULADO: reverse shell de laboratorio. No se conecta a ningun host."""
import socket  # noqa: F401

C2 = ("192.0.2.10", 4444)


def main() -> None:
    print(f"[SIMULATED REVERSE SHELL] payload for {C2[0]}:{C2[1]} (no connection performed)")


if __name__ == "__main__":
    main()
PY_EOF
chmod 0755 "$INCIDENT_DIR/reverse_shell.py"

# --- 4. Webshell PHP simulada ----------------------------------------------
cat > "$INCIDENT_DIR/webshell.php" <<'PHP_EOF'
<?php
// SIMULADO: webshell de laboratorio. No ejecuta comandos reales.
$cmd = $_GET['cmd'] ?? '';
echo "[SIMULATED WEBSHELL] would execute: " . htmlspecialchars($cmd);
?>
PHP_EOF

# --- 5. Crypto miner simulado ----------------------------------------------
cat > "$INCIDENT_DIR/crypto_miner.sh" <<'MINER_EOF'
#!/bin/bash
# SIMULADO - no descarga ni mina nada
STRATUM="stratum+tcp://pool.example.invalid:3333"
WALLET="SIMULATED_WALLET_ADDRESS"
echo "[SIMULATED MINER] would run xmrig --url ${STRATUM} --user ${WALLET}"
echo "[SIMULATED MINER] high CPU process would be named kworker"
MINER_EOF
chmod 0755 "$INCIDENT_DIR/crypto_miner.sh"

# --- 6. Persistencia simulada (archivo de muestra, no activo) --------------
cat > "$INCIDENT_DIR/persistence.cron" <<'CRON_EOF'
# SIMULADO - no instalado por defecto
* * * * * root /opt/xoc-test/incident/backdoor.sh >> /var/log/xoc-test-heartbeat.log 2>&1
CRON_EOF

# --- 7. Config oculta con IOCs ---------------------------------------------
cat > /tmp/.xoc_hidden_config <<'CONFIG_EOF'
# SIMULADO - configuracion de C2 de laboratorio
C2_SERVER=192.0.2.10
C2_PORT=4444
BEACON_INTERVAL=30
EXFIL_DOMAIN=evil-domain.example.invalid
ENCRYPTION_KEY=SIMULATED_KEY_12345
CONFIG_EOF
chmod 0600 /tmp/.xoc_hidden_config

# --- 8. Llave SSH backdoor simulada (no autorizada) ------------------------
cat > "$INCIDENT_DIR/ssh_backdoor_key.pub" <<'KEY_EOF'
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAISIMULATEDXOCLABKEYBACKDOORONLY0000000000000 xoc-lab-backdoor
KEY_EOF

# --- 9. Binario disfrazado + SUID simulado ---------------------------------
if [[ -x /bin/true ]]; then
    cp /bin/true /usr/local/bin/kworker
    chmod 0755 /usr/local/bin/kworker
    cp /bin/true /usr/local/bin/pkexecx
    chmod 4755 /usr/local/bin/pkexecx
fi

# --- 10. Persistencia real opcional ---------------------------------------
if [[ "$INSTALL_PERSISTENCE" == "true" ]]; then
    cat > /etc/cron.d/xoc-test-lab <<CRON_ACTIVE_EOF
# XOC LAB SIMULADO - heartbeat inofensivo cada minuto
* * * * * root echo "\$(date -Is) xoc-lab-heartbeat" >> /var/log/xoc-test-heartbeat.log
CRON_ACTIVE_EOF
    chmod 0644 /etc/cron.d/xoc-test-lab
    log "Persistencia simulada instalada en /etc/cron.d/xoc-test-lab"
else
    log "Persistencia NO instalada (usa --install-persistence para el ejemplo)"
fi

# --- 11. Manifest para el ticket ------------------------------------------
python3 - "$INCIDENT_DIR" "$ROOT_DIR" <<'PY_MANIFEST'
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

incident_dir = Path(sys.argv[1])
root_dir = Path(sys.argv[2])

entries = [
    ("eicar_test.txt", "malware_signature", "critical", "EICAR antivirus test signature"),
    ("backdoor.sh", "backdoor", "critical", "Reverse shell/backdoor script"),
    ("reverse_shell.py", "reverse_shell", "critical", "Python reverse shell payload"),
    ("webshell.php", "webshell", "risky", "PHP webshell"),
    ("crypto_miner.sh", "cryptominer", "risky", "Cryptocurrency miner script"),
    ("persistence.cron", "persistence", "controlled", "Cron persistence sample"),
    ("ssh_backdoor_key.pub", "unauthorized_key", "critical", "Unauthorized SSH key"),
]

files = []
for name, kind, severity, description in entries:
    path = incident_dir / name
    if not path.exists():
        continue
    data = path.read_bytes()
    files.append(
        {
            "path": str(path),
            "name": name,
            "type": kind,
            "severity": severity,
            "description": description,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "mtime": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        }
    )

extra = [
    ("/tmp/.xoc_hidden_config", "hidden_config", "controlled"),
    ("/usr/local/bin/kworker", "masqueraded_binary", "risky"),
    ("/usr/local/bin/pkexecx", "suspicious_suid", "critical"),
]
for raw_path, kind, severity in extra:
    path = Path(raw_path)
    if not path.exists():
        continue
    files.append(
        {
            "path": str(path),
            "name": path.name,
            "type": kind,
            "severity": severity,
            "description": f"Simulated IOC ({kind})",
            "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "mtime": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        }
    )

manifest = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "hostname": os.uname().nodename,
    "root": str(root_dir),
    "incident_count": len(files),
    "files": files,
}
out = incident_dir / "manifest.json"
out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(f"Manifest: {out} ({len(files)} artefactos)")
PY_MANIFEST

log "Seed completo. Artefactos en $INCIDENT_DIR"
log "Verifica con: sudo $0 --verify"
log "Ticket sugerido: archivos maliciosos detectados en $(hostname) - revisar manifest"
