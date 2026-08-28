#!/bin/bash
# demo_sophia_flow.sh - Demo completo: SOPHIA detecta vulnerabilidad → Ticket → VICTOR
#
# Este script demuestra el flujo completo:
# 1. Verifica VICTOR y SOPHIA
# 2. Opcionalmente crea un archivo malicioso en el APPLIANCE
# 3. Envía mensaje a SOPHIA preguntando por vulnerabilidades
# 4. Verifica que se cree un ticket automáticamente
# 5. Monitorea el workflow de Step Functions
# 6. Pide aprobación si es necesario
#
# Uso:
#   ./scripts/demo_sophia_flow.sh --tenant-id <id> --user-jwt <token>
#   ./scripts/demo_sophia_flow.sh --tenant-id <id> --user-jwt <token> --create-malicious
#   ./scripts/demo_sophia_flow.sh --tenant-id <id> --user-jwt <token> --create-malicious --auto-approve

set -euo pipefail

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuración
DEFAULT_API_BASE="https://api.xoc.app"
DEFAULT_VICTOR_URL="http://10.20.0.22:8000"
DEFAULT_REGION="us-east-1"

# Variables
TENANT_ID=""
USER_JWT=""
API_BASE="${DEFAULT_API_BASE}"
VICTOR_URL="${DEFAULT_VICTOR_URL}"
REGION="${DEFAULT_REGION}"
CREATE_MALICIOUS=false
AUTO_APPROVE=false
VERBOSE=false

log() {
    local level=$1
    shift
    local message="$@"
    local timestamp=$(date '+%H:%M:%S')
    
    case $level in
        INFO)  echo -e "${GREEN}[${timestamp}] ✓${NC} ${message}" ;;
        WARN)  echo -e "${YELLOW}[${timestamp}] !${NC} ${message}" ;;
        ERROR) echo -e "${RED}[${timestamp}] ✗${NC} ${message}" ;;
        STEP)  echo -e "${BLUE}[${timestamp}] →${NC} ${message}" ;;
        DEBUG) 
            if [ "$VERBOSE" = true ]; then
                echo -e "${CYAN}[${timestamp}] ·${NC} ${message}"
            fi
            ;;
    esac
}

show_help() {
    echo "Uso: $0 --tenant-id <id> --user-jwt <token> [opciones]"
    echo ""
    echo "Obligatorio:"
    echo "  --tenant-id <id>       ID del tenant"
    echo "  --user-jwt <token>     JWT del usuario"
    echo ""
    echo "Opcionales:"
    echo "  --api-base <url>       URL base de la API (default: ${DEFAULT_API_BASE})"
    echo "  --victor-url <url>     URL de VICTOR (default: ${DEFAULT_VICTOR_URL})"
    echo "  --create-malicious     Crear archivo malicioso en el APPLIANCE"
    echo "  --auto-approve         Aprobar automáticamente pasos riesgosos"
    echo "  --verbose              Mostrar logs detallados"
    echo "  -h, --help             Mostrar esta ayuda"
}

check_prerequisites() {
    log STEP "Verificando prerrequisitos..."
    
    for cmd in curl jq aws; do
        if ! command -v $cmd &> /dev/null; then
            log ERROR "$cmd no está instalado"
            exit 1
        fi
    done
    
    log INFO "Prerrequisitos OK"
}

verify_victor() {
    log STEP "Verificando VICTOR..."
    
    local response=$(curl -s -w "\n%{http_code}" \
        --connect-timeout 5 \
        --max-time 10 \
        "${VICTOR_URL}/health" 2>/dev/null || echo -e "\n000")
    
    local http_code=$(echo "$response" | tail -n1)
    
    if [ "$http_code" = "200" ]; then
        log INFO "VICTOR disponible"
        return 0
    else
        log WARN "VICTOR no disponible (HTTP $http_code)"
        return 1
    fi
}

create_malicious_file() {
    log STEP "Creando archivo malicioso en el APPLIANCE..."
    
    local ssh_key="${HOME}/.ssh/xoc-ec2"
    local appliance_ip="10.20.0.22"
    
    if [ ! -f "$ssh_key" ]; then
        log WARN "Llave SSH no encontrada: $ssh_key"
        log INFO "Creando archivo malicioso localmente para prueba..."
        
        mkdir -p /tmp/xoc-demo
        cat > /tmp/xoc-demo/trojan.sh << 'EOF'
#!/bin/bash
# Demo: Archivo malicioso simulado para testing
# Este archivo es inofensivo y solo sirve para demostración

echo "[DEMO] Este es un archivo malicioso simulado"
echo "[DEMO] En producción, este sería un script real de malware"
echo "[DEMO] VICTOR debería detectarlo y crear un plan de remediación"

# Simular actividad sospechosa
echo "[DEMO] Conectando a IP externa 185.220.101.45:4444..."
echo "[DEMO] Descargando payload..."
echo "[DEMO] Estableciendo persistencia..."
EOF
        chmod +x /tmp/xoc-demo/trojan.sh
        
        log INFO "Archivo creado en /tmp/xoc-demo/trojan.sh"
        log INFO "Para copiarlo al APPLIANCE:"
        log INFO "  scp -i $ssh_key /tmp/xoc-demo/trojan.sh ubuntu@${appliance_ip}:/tmp/trojan.sh"
        
        return 0
    fi
    
    local ssh_cmd="printf '#!/bin/bash\necho \"pepe123\"\n' > /tmp/ssh-askpass.sh && chmod +x /tmp/ssh-askpass.sh && eval \$(ssh-agent -s) > /dev/null && SSH_ASKPASS_REQUIRE=force SSH_ASKPASS=/tmp/ssh-askpass.sh ssh-add $ssh_key </dev/null 2>&1 && ssh -A -o ConnectTimeout=20 -o ServerAliveInterval=15 ubuntu@${appliance_ip}"
    
    # Crear archivo malicioso en el APPLIANCE
    eval $ssh_cmd "cat > /tmp/trojan.sh << 'MALICIOUS_EOF'
#!/bin/bash
# Demo: Archivo malicioso simulado para testing
echo \"[DEMO] Archivo malicioso detectado en \$(hostname)\"
echo \"[DEMO] Conectando a 185.220.101.45:4444...\"
MALICIOUS_EOF
chmod +x /tmp/trojan.sh && ls -la /tmp/trojan.sh" 2>/dev/null
    
    if [ $? -eq 0 ]; then
        log INFO "Archivo malicioso creado en el APPLIANCE: /tmp/trojan.sh"
        return 0
    else
        log WARN "No se pudo crear archivo en el APPLIANCE"
        return 1
    fi
}

send_message_to_sophia() {
    log STEP "Enviando mensaje a SOPHIA..."
    
    local message="Hola SOPHIA, necesito que revises el estado de seguridad del servidor. ¿Puedes indicarme cuál es la vulnerabilidad más reciente detectada en el sistema?特别amente si hay archivos sospechosos o maliciosos en /tmp/"
    
    log INFO "Mensaje: ${message}"
    
    local response=$(curl -s -w "\n%{http_code}" \
        --connect-timeout 10 \
        --max-time 120 \
        -X POST \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${USER_JWT}" \
        -d "{\"message\": \"${message}\"}" \
        "${API_BASE}/chat" 2>/dev/null)
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "200" ]; then
        log INFO "Respuesta de SOPHIA recibida"
        echo "$body" | jq '.'
        
        # Verificar si se creó un ticket
        local ticket_created=$(echo "$body" | jq -r '.ticket_created // false')
        local ticket_id=$(echo "$body" | jq -r '.ticket_id // empty')
        
        if [ "$ticket_created" = "true" ] && [ -n "$ticket_id" ]; then
            log INFO "Ticket creado automáticamente: $ticket_id"
            echo "$ticket_id"
            return 0
        else
            log WARN "SOPHIA no creó ticket automáticamente"
            log INFO "Esto puede ser porque SOPHIA no tiene configurada la creación automática de tickets"
            return 1
        fi
    else
        log ERROR "Error al enviar mensaje a SOPHIA (HTTP $http_code)"
        echo "$body" | jq '.' 2>/dev/null || echo "$body"
        return 1
    fi
}

create_ticket_directly() {
    log STEP "Creando ticket directamente via API..."
    
    local subject="Alerta: Archivo malicioso detectado en servidor"
    local description="Se ha detectado un archivo sospechoso en /tmp/trojan.sh que podría ser un script malicioso.
El archivo fue creado recientemente y presenta características de malware:
- Permiso de ejecución activo
- Contenido ofuscado
- Intenta establecer conexión con IP externa 185.220.101.45:4444

Se requiere investigación inmediata y remediación."
    
    local payload=$(cat <<EOF
{
    "subject": "${subject}",
    "description": "${description}",
    "status": "PENDING",
    "severity": "high",
    "metadata": {
        "source": "demo_sophia_flow",
        "file_path": "/tmp/trojan.sh",
        "threat_type": "malware",
        "severity": "high"
    }
}
EOF
)
    
    log DEBUG "Payload: ${payload}"
    
    local response=$(curl -s -w "\n%{http_code}" \
        --connect-timeout 10 \
        --max-time 30 \
        -X POST \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${USER_JWT}" \
        -d "${payload}" \
        "${API_BASE}/tickets" 2>/dev/null)
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "201" ]; then
        local ticket_id=$(echo "$body" | jq -r '.ticket.ticket_id')
        log INFO "Ticket creado: $ticket_id"
        echo "$body" | jq '.'
        echo "$ticket_id"
        return 0
    else
        log ERROR "Error al crear ticket (HTTP $http_code)"
        echo "$body" | jq '.' 2>/dev/null || echo "$body"
        return 1
    fi
}

monitor_ticket_workflow() {
    local ticket_id=$1
    
    log STEP "Monitoreando workflow del ticket..."
    log INFO "Ticket ID: $ticket_id"
    
    # Esperar a que el workflow se inicie
    sleep 5
    
    # Verificar estado del ticket
    local max_wait=300
    local interval=10
    local elapsed=0
    
    while [ $elapsed -lt $max_wait ]; do
        local response=$(curl -s -w "\n%{http_code}" \
            --connect-timeout 10 \
            --max-time 30 \
            -H "Authorization: Bearer ${USER_JWT}" \
            "${API_BASE}/tickets/${ticket_id}" 2>/dev/null)
        
        local http_code=$(echo "$response" | tail -n1)
        local body=$(echo "$response" | head -n-1)
        
        if [ "$http_code" = "200" ]; then
            local status=$(echo "$body" | jq -r '.status')
            local execution_status=$(echo "$body" | jq -r '.execution_status // "N/A"')
            local pending_decision=$(echo "$body" | jq -r '.pending_decision // null')
            
            log INFO "Estado: ${status} | Ejecución: ${execution_status}"
            
            case $status in
                PREAPROBADO)
                    if [ "$pending_decision" != "null" ] && [ -n "$pending_decision" ]; then
                        log WARN "Ticket esperando aprobación"
                        
                        if [ "$AUTO_APPROVE" = true ]; then
                            log INFO "Auto-aprobando ticket..."
                            approve_ticket "$ticket_id"
                            return $?
                        else
                            log INFO "Para aprobar:"
                            log INFO "  curl -X PATCH -H 'Authorization: Bearer <JWT>' \\"
                            log INFO "    -H 'Content-Type: application/json' \\"
                            log INFO "    '${API_BASE}/tickets/${ticket_id}/approve'"
                            return 2
                        fi
                    fi
                    ;;
                RESUELTO)
                    log INFO "Ticket resuelto exitosamente"
                    echo "$body" | jq '.'
                    return 0
                    ;;
                FALLIDO|FAILED)
                    log ERROR "Ticket falló"
                    echo "$body" | jq '.'
                    return 1
                    ;;
                RECHAZADO)
                    log WARN "Ticket rechazado"
                    echo "$body" | jq '.'
                    return 1
                    ;;
            esac
        fi
        
        sleep $interval
        elapsed=$((elapsed + interval))
    done
    
    log WARN "Tiempo máximo de espera alcanzado"
    return 0
}

approve_ticket() {
    local ticket_id=$1
    
    log STEP "Aprobando ticket ${ticket_id}..."
    
    local response=$(curl -s -w "\n%{http_code}" \
        --connect-timeout 10 \
        --max-time 30 \
        -X PATCH \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${USER_JWT}" \
        "${API_BASE}/tickets/${ticket_id}/approve" 2>/dev/null)
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "200" ]; then
        log INFO "Ticket aprobado"
        echo "$body" | jq '.'
        return 0
    else
        log ERROR "Error al aprobar (HTTP $http_code)"
        echo "$body" | jq '.' 2>/dev/null || echo "$body"
        return 1
    fi
}

# Parsear argumentos
while [[ $# -gt 0 ]]; do
    case $1 in
        --tenant-id)
            TENANT_ID="$2"
            shift 2
            ;;
        --user-jwt)
            USER_JWT="$2"
            shift 2
            ;;
        --api-base)
            API_BASE="$2"
            shift 2
            ;;
        --victor-url)
            VICTOR_URL="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --create-malicious)
            CREATE_MALICIOUS=true
            shift
            ;;
        --auto-approve)
            AUTO_APPROVE=true
            shift
            ;;
        --verbose)
            VERBOSE=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            log ERROR "Opción desconocida: $1"
            show_help
            exit 1
            ;;
    esac
done

# Validar argumentos
if [ -z "${TENANT_ID}" ] || [ -z "${USER_JWT}" ]; then
    log ERROR "Se requieren --tenant-id y --user-jwt"
    show_help
    exit 1
fi

# Ejecución principal
main() {
    echo ""
    echo "=========================================="
    echo "  DEMO: SOPHIA → Ticket → VICTOR"
    echo "=========================================="
    echo ""
    echo "Tenant ID: ${TENANT_ID}"
    echo "API Base: ${API_BASE}"
    echo "VICTOR URL: ${VICTOR_URL}"
    echo ""
    
    # 1. Verificar prerrequisitos
    check_prerequisites
    
    # 2. Verificar VICTOR
    verify_victor || true
    
    # 3. Crear archivo malicioso (opcional)
    if [ "${CREATE_MALICIOUS}" = true ]; then
        create_malicious_file || true
    fi
    
    # 4. Enviar mensaje a SOPHIA
    log STEP "Intentando flujo con SOPHIA..."
    local ticket_id=$(send_message_to_sophia || echo "")
    
    # 5. Si SOPHIA no creó ticket, crearlo directamente
    if [ -z "${ticket_id}" ]; then
        log WARN "SOPHIA no creó ticket automáticamente"
        log INFO "Creando ticket directamente..."
        ticket_id=$(create_ticket_directly || echo "")
    fi
    
    if [ -z "${ticket_id}" ]; then
        log ERROR "No se pudo crear el ticket"
        exit 1
    fi
    
    # 6. Monitorear workflow
    monitor_ticket_workflow "${ticket_id}"
    local result=$?
    
    echo ""
    echo "=========================================="
    echo "  DEMO COMPLETADA"
    echo "=========================================="
    echo ""
    echo "Ticket ID: ${ticket_id}"
    echo "Para verificar el ticket:"
    echo "  curl -H 'Authorization: Bearer <JWT>' '${API_BASE}/tickets/${ticket_id}' | jq '.'"
    echo ""
}

# Ejecutar
main