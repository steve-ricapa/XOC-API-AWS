#!/bin/bash
# =============================================================================
# XOC Demo - XOC APPLIANCE Setup Script
# =============================================================================
# Run this on the XOC APPLIANCE server via GlobalProtect VPN
# Sets up demo environment: installs tools + places malicious file
# =============================================================================

set -euo pipefail

DEMO_DIR="/tmp/xoc-demo"
MALICIOUS_FILE="$DEMO_DIR/suspicious_backdoor.sh"
LOG_FILE="$DEMO_DIR/demo-setup.log"

mkdir -p "$DEMO_DIR"

echo "============================================" | tee "$LOG_FILE"
echo "XOC Demo Setup - $(date)" | tee -a "$LOG_FILE"
echo "============================================" | tee -a "$LOG_FILE"

# --- Step 1: Install demo tools ---
echo "" | tee -a "$LOG_FILE"
echo "[1/4] Installing demo tools..." | tee -a "$LOG_FILE"

if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq 2>/dev/null | tee -a "$LOG_FILE"
    sudo apt-get install -y -qq htop nmap curl wget net-tools 2>/dev/null | tee -a "$LOG_FILE"
elif command -v yum &>/dev/null; then
    sudo yum install -y -q htop nmap curl wget net-tools 2>/dev/null | tee -a "$LOG_FILE"
elif command -v dnf &>/dev/null; then
    sudo dnf install -y -q htop nmap curl wget net-tools 2>/dev/null | tee -a "$LOG_FILE"
else
    echo "  Package manager not detected, skipping tool installation" | tee -a "$LOG_FILE"
fi

echo "  Tools installed: htop, nmap, curl, wget, net-tools" | tee -a "$LOG_FILE"

# --- Step 2: Create suspicious "malicious" file ---
echo "" | tee -a "$LOG_FILE"
echo "[2/4] Creating suspicious file for demo..." | tee -a "$LOG_FILE"

cat > "$MALICIOUS_FILE" << 'MALICIOUS_EOF'
#!/bin/bash
# !!! WARNING: This is a SIMULATED malicious script for XOC Demo !!!
# It does NOT execute any harmful actions.
# Created: $(date)
# Purpose: Demo Sophia detection and Victor remediation

echo "[MALICIOUS] Simulated backdoor check-in"
echo "[MALICIOUS] Attempting to establish reverse shell to 192.168.1.100:4444"
echo "[MALICIOUS] Scanning for sensitive files in /etc/shadow, /etc/passwd"
echo "[MALICIOUS] Exfiltrating simulated data..."

# Simulated payload - does nothing harmful
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$TIMESTAMP] Backdoor heartbeat sent"

# Simulated persistence mechanism
echo "[MALICIOUS] Adding cron job for persistence"
echo "[MALICIOUS] Modifying /tmp/.hidden_config"

echo "[DONE] Malicious simulation complete"
MALICIOUS_EOF

chmod +x "$MALICIOUS_FILE"
echo "  Created: $MALICIOUS_FILE" | tee -a "$LOG_FILE"

# --- Step 3: Create a decoy config file that the "malware" references ---
echo "" | tee -a "$LOG_FILE"
echo "[3/4] Creating decoy config..." | tee -a "$LOG_FILE"

cat > "$DEMO_DIR/.hidden_config" << 'CONFIG_EOF'
# Simulated compromised config
C2_SERVER=192.168.1.100
BEACON_INTERVAL=30
ENCRYPTION_KEY=SIMULATED_KEY_12345
EXFIL_DOMAIN=evil-domain.example.com
CONFIG_EOF

chmod 600 "$DEMO_DIR/.hidden_config"
echo "  Created: $DEMO_DIR/.hidden_config" | tee -a "$LOG_FILE"

# --- Step 4: Create a "clean" file for install demo ---
echo "" | tee -a "$LOG_FILE"
echo "[4/4] Creating install demo file..." | tee -a "$LOG_FILE"

cat > "$DEMO_DIR/demo-install-package.sh" << 'INSTALL_EOF'
#!/bin/bash
# XOC Demo - Simulated package installation script
echo "[INSTALL] Installing XOC Security Agent v2.1.0..."
echo "[INSTALL] Checking dependencies..."
echo "[INSTALL] python3: OK"
echo "[INSTALL] pip3: OK"
echo "[INSTALL] Creating /opt/xoc-agent directory..."
echo "[INSTALL] Downloading agent binary..."
echo "[INSTALL] Configuring agent..."
echo "[INSTALL] Starting XOC Security Agent service..."
echo "[INSTALL] Installation complete!"
INSTALL_EOF

chmod +x "$DEMO_DIR/demo-install-package.sh"
echo "  Created: $DEMO_DIR/demo-install-package.sh" | tee -a "$LOG_FILE"

# --- Summary ---
echo "" | tee -a "$LOG_FILE"
echo "============================================" | tee -a "$LOG_FILE"
echo "Demo setup complete!" | tee -a "$LOG_FILE"
echo "============================================" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "Files created:" | tee -a "$LOG_FILE"
echo "  - $MALICIOUS_FILE (malicious file for Sophia to detect)" | tee -a "$LOG_FILE"
echo "  - $DEMO_DIR/.hidden_config (decoy config)" | tee -a "$LOG_FILE"
echo "  - $DEMO_DIR/demo-install-package.sh (install demo)" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "Demo steps:" | tee -a "$LOG_FILE"
echo "  1. Ask Sophia: 'Hay un archivo sospechoso en el servidor, necesito eliminarlo'" | tee -a "$LOG_FILE"
echo "  2. Sophia detects intent -> creates ticket automatically" | tee -a "$LOG_FILE"
echo "  3. Victor assesses -> generates remediation plan" | tee -a "$LOG_FILE"
echo "  4. Approve in xoc.app tickets panel -> Victor executes" | tee -a "$LOG_FILE"
echo "  5. Victor removes the malicious file -> ticket resolved" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "To verify the malicious file exists:" | tee -a "$LOG_FILE"
echo "  ls -la $MALICIOUS_FILE" | tee -a "$LOG_FILE"
echo "  cat $MALICIOUS_FILE" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "To clean up after demo:" | tee -a "$LOG_FILE"
echo "  rm -rf $DEMO_DIR" | tee -a "$LOG_FILE"
