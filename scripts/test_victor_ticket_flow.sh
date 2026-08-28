#!/bin/bash
# test_victor_ticket_flow.sh - Prueba rápida del flujo de tickets con VICTOR
# Versión simplificada para testing directo
#
# Uso: ./scripts/test_victor_ticket_flow.sh --victor-url <url> --tenant-id <id>

set -euo pipefail

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuración
VICTOR_URL="${VICTOR_URL:-http://10.20.0.22:8000}"
TENANT_ID="${TENANT_ID:-1}"
TICKET_ID="test-$(date +%s)-$RANDOM"

# Función para logging
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
    esac
}

# Función para verificar VICTOR
check_victor() {
    log STEP "Verificando VICTOR en ${VICTOR_URL}..."
    
    local response=$(curl -s -w "\n%{http_code}" \
        --connect-timeout 10 \
        --max-time 30 \
        "${VICTOR_URL}/health" 2>/dev/null)
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "200" ]; then
        log INFO "VICTOR disponible"
        echo "$body" | jq '.' 2>/dev/null || echo "$body"
        return 0
    else
        log ERROR "VICTOR no disponible (HTTP $http_code)"
        return 1
    fi
}

# Función para test de assessment
test_assessment() {
    log STEP "Probando assessment..."
    
    local payload=$(cat <<EOF
{
    "phase": "assessment",
    "ticketId": "${TICKET_ID}",
    "tenantId": ${TENANT_ID},
    "subject": "Alerta: Archivo malicioso detectado",
    "description": "Se detectó un archivo sospechoso en /tmp/trojan.sh que podría ser malware."
}
EOF
)
    
    log INFO "Payload:"
    echo "$payload" | jq '.'
    
    local response=$(curl -s -w "\n%{http_code}" \
        --connect-timeout 10 \
        --max-time 60 \
        -X POST \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "${VICTOR_URL}/api/agents/VictorDurableAgent/run" 2>/dev/null)
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "200" ]; then
        log INFO "Assessment exitoso"
        echo "$body" | jq '.'
        return 0
    else
        log ERROR "Assessment falló (HTTP $http_code)"
        echo "$body" | jq '.' 2>/dev/null || echo "$body"
        return 1
    fi
}

# Función para test de plan
test_plan() {
    log STEP "Probando generación de plan..."
    
    local payload=$(cat <<EOF
{
    "phase": "plan",
    "ticketId": "${TICKET_ID}",
    "tenantId": ${TENANT_ID},
    "subject": "Alerta: Archivo malicioso detectado",
    "description": "Se detectó un archivo sospechoso en /tmp/trojan.sh que podría ser malware."
}
EOF
)
    
    log INFO "Payload:"
    echo "$payload" | jq '.'
    
    local response=$(curl -s -w "\n%{http_code}" \
        --connect-timeout 10 \
        --max-time 120 \
        -X POST \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "${VICTOR_URL}/api/agents/VictorDurableAgent/run" 2>/dev/null)
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "200" ]; then
        log INFO "Plan generado exitosamente"
        echo "$body" | jq '.'
        return 0
    else
        log ERROR "Generación de plan falló (HTTP $http_code)"
        echo "$body" | jq '.' 2>/dev/null || echo "$body"
        return 1
    fi
}

# Función para test de ejecución (dry-run)
test_execute_dry_run() {
    log STEP "Probando ejecución (dry-run)..."
    
    local plan=$(cat <<EOF
{
    "plan": {
        "steps": [
            {
                "order": 1,
                "action": "shell",
                "command": "ls -la /tmp/trojan.sh",
                "description": "Verificar archivo",
                "risk_level": "basic"
            }
        ]
    }
}
EOF
)
    
    local payload=$(cat <<EOF
{
    "phase": "execute",
    "ticketId": "${TICKET_ID}",
    "tenantId": ${TENANT_ID},
    ${plan}
}
EOF
)
    
    log INFO "Payload:"
    echo "$payload" | jq '.'
    
    local response=$(curl -s -w "\n%{http_code}" \
        --connect-timeout 10 \
        --max-time 180 \
        -X POST \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "${VICTOR_URL}/api/agents/VictorDurableAgent/run" 2>/dev/null)
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "200" ]; then
        log INFO "Ejecución completada"
        echo "$body" | jq '.'
        return 0
    else
        log ERROR "Ejecución falló (HTTP $http_code)"
        echo "$body" | jq '.' 2>/dev/null || echo "$body"
        return 1
    fi
}

# Mostrar ayuda
show_help() {
    echo "Uso: $0 [opciones]"
    echo ""
    echo "Opciones:"
    echo "  --victor-url <url>    URL de VICTOR (default: http://10.20.0.22:8000)"
    echo "  --tenant-id <id>      ID del tenant (default: 1)"
    echo "  --ticket-id <id>      ID del ticket (auto-generado si no se especifica)"
    echo "  --assessment          Solo probar assessment"
    echo "  --plan                Solo probar generación de plan"
    echo "  --execute             Solo probar ejecución"
    echo "  --full                Probar flujo completo (assessment → plan → execute)"
    echo "  -h, --help            Mostrar esta ayuda"
    echo ""
    echo "Ejemplos:"
    echo "  $0 --full"
    echo "  $0 --assessment --victor-url http://localhost:8000"
    echo "  $0 --plan --tenant-id 123"
}

# Variables para control
RUN_ASSESSMENT=false
RUN_PLAN=false
RUN_EXECUTE=false
RUN_FULL=false

# Parsear argumentos
while [[ $# -gt 0 ]]; do
    case $1 in
        --victor-url)
            VICTOR_URL="$2"
            shift 2
            ;;
        --tenant-id)
            TENANT_ID="$2"
            shift 2
            ;;
        --ticket-id)
            TICKET_ID="$2"
            shift 2
            ;;
        --assessment)
            RUN_ASSESSMENT=true
            shift
            ;;
        --plan)
            RUN_PLAN=true
            shift
            ;;
        --execute)
            RUN_EXECUTE=true
            shift
            ;;
        --full)
            RUN_FULL=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Opción desconocida: $1"
            show_help
            exit 1
            ;;
    esac
done

# Si no se especificó nada, ejecutar flujo completo
if [ "$RUN_ASSESSMENT" = false ] && [ "$RUN_PLAN" = false ] && [ "$RUN_EXECUTE" = false ] && [ "$RUN_FULL" = false ]; then
    RUN_FULL=true
fi

# Ejecución principal
main() {
    echo ""
    echo "=========================================="
    echo "  TEST: Flujo de Tickets con VICTOR"
    echo "=========================================="
    echo ""
    echo "VICTOR URL: ${VICTOR_URL}"
    echo "Tenant ID: ${TENANT_ID}"
    echo "Ticket ID: ${TICKET_ID}"
    echo ""
    
    # Verificar VICTOR
    if ! check_victor; then
        log ERROR "No se puede continuar sin VICTOR"
        exit 1
    fi
    
    echo ""
    
    # Ejecutar tests según configuración
    if [ "$RUN_FULL" = true ] || [ "$RUN_ASSESSMENT" = true ]; then
        test_assessment
        echo ""
    fi
    
    if [ "$RUN_FULL" = true ] || [ "$RUN_PLAN" = true ]; then
        test_plan
        echo ""
    fi
    
    if [ "$RUN_FULL" = true ] || [ "$RUN_EXECUTE" = true ]; then
        test_execute_dry_run
        echo ""
    fi
    
    echo "=========================================="
    echo "  TEST COMPLETADO"
    echo "=========================================="
    echo ""
}

# Ejecutar
main