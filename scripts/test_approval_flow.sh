#!/bin/bash
# test_approval_flow.sh - Prueba del flujo de aprobación de tickets
# Verifica que el mecanismo de aprobación funcione correctamente

set -euo pipefail

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuración
TENANT_ID="${TENANT_ID:-1}"
API_BASE="${API_BASE:-https://api.xoc.app}"
USER_JWT="${USER_JWT:-}"
TICKET_ID="${TICKET_ID:-}"

# Función para logging
log() {
    local level=$1
    shift
    local message="$@"
    
    case $level in
        INFO)  echo -e "${GREEN}✓${NC} ${message}" ;;
        WARN)  echo -e "${YELLOW}!${NC} ${message}" ;;
        ERROR) echo -e "${RED}✗${NC} ${message}" ;;
        STEP)  echo -e "${BLUE}→${NC} ${message}" ;;
    esac
}

# Verificar prerrequisitos
check_prerequisites() {
    log STEP "Verificando prerrequisitos..."
    
    if [ -z "$USER_JWT" ]; then
        log ERROR "Se requiere USER_JWT"
        echo "Uso: USER_JWT=<token> ./scripts/test_approval_flow.sh"
        exit 1
    fi
    
    if [ -z "$TICKET_ID" ]; then
        log WARN "No se proporcionó TICKET_ID, se creará uno nuevo"
    fi
    
    log INFO "Prerrequisitos OK"
}

# Crear ticket de prueba con risk_level risky
create_risky_ticket() {
    log STEP "Creando ticket de prueba con risk_level risky..."
    
    local payload=$(cat <<EOF
{
    "subject": "Eliminar archivo de configuración obsoleto",
    "description": "Se requiere eliminar el archivo /etc/old_config.conf que contiene credenciales obsoletas y representa un riesgo de seguridad. El archivo tiene permisos de lectura global y puede ser explotado.",
    "status": "PENDING",
    "priority": "high",
    "metadata": {
        "risk_level": "risky",
        "file_path": "/etc/old_config.conf",
        "requires_admin_xoc": true
    }
}
EOF
)
    
    log INFO "Payload:"
    echo "$payload" | jq '.'
    
    local response=$(curl -s -w "\n%{http_code}" \
        --connect-timeout 10 \
        --max-time 30 \
        -X POST \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $USER_JWT" \
        -d "$payload" \
        "$API_BASE/tickets" 2>/dev/null)
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "201" ]; then
        TICKET_ID=$(echo "$body" | jq -r '.ticket.ticket_id')
        log INFO "Ticket creado: $TICKET_ID"
        echo "$body" | jq '.'
        return 0
    else
        log ERROR "Error al crear ticket (HTTP $http_code)"
        echo "$body" | jq '.' 2>/dev/null || echo "$body"
        return 1
    fi
}

# Verificar estado del ticket
check_ticket_status() {
    log STEP "Verificando estado del ticket..."
    
    local response=$(curl -s -w "\n%{http_code}" \
        --connect-timeout 10 \
        --max-time 30 \
        -H "Authorization: Bearer $USER_JWT" \
        "$API_BASE/tickets/$TICKET_ID" 2>/dev/null)
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "200" ]; then
        local status=$(echo "$body" | jq -r '.status')
        local pending_decision=$(echo "$body" | jq -r '.pending_decision')
        
        log INFO "Estado: $status"
        
        if [ "$pending_decision" != "null" ] && [ -n "$pending_decision" ]; then
            log INFO "Pendiente de decisión:"
            echo "$pending_decision" | jq '.'
        fi
        
        echo "$body" | jq '.'
        return 0
    else
        log ERROR "Error al obtener ticket (HTTP $http_code)"
        return 1
    fi
}

# Aprobar ticket
approve_ticket() {
    log STEP "Aprobando ticket..."
    
    local response=$(curl -s -w "\n%{http_code}" \
        --connect-timeout 10 \
        --max-time 30 \
        -X PATCH \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $USER_JWT" \
        "$API_BASE/tickets/$TICKET_ID/approve" 2>/dev/null)
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "200" ]; then
        log INFO "Ticket aprobado exitosamente"
        echo "$body" | jq '.'
        return 0
    else
        log ERROR "Error al aprobar ticket (HTTP $http_code)"
        echo "$body" | jq '.' 2>/dev/null || echo "$body"
        return 1
    fi
}

# Rechazar ticket
reject_ticket() {
    log STEP "Rechazando ticket..."
    
    local response=$(curl -s -w "\n%{http_code}" \
        --connect-timeout 10 \
        --max-time 30 \
        -X PATCH \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $USER_JWT" \
        "$API_BASE/tickets/$TICKET_ID/reject" 2>/dev/null)
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "200" ]; then
        log INFO "Ticket rechazado exitosamente"
        echo "$body" | jq '.'
        return 0
    else
        log ERROR "Error al rechazar ticket (HTTP $http_code)"
        echo "$body" | jq '.' 2>/dev/null || echo "$body"
        return 1
    fi
}

# Seleccionar decisión
select_decision() {
    local option_id=$1
    
    log STEP "Seleccionando decisión: $option_id..."
    
    local response=$(curl -s -w "\n%{http_code}" \
        --connect-timeout 10 \
        --max-time 30 \
        -X PATCH \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $USER_JWT" \
        -d "{\"selected_option_id\": \"$option_id\"}" \
        "$API_BASE/tickets/$TICKET_ID/decision/select" 2>/dev/null)
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "200" ]; then
        log INFO "Decisión seleccionada exitosamente"
        echo "$body" | jq '.'
        return 0
    else
        log ERROR "Error al seleccionar decisión (HTTP $http_code)"
        echo "$body" | jq '.' 2>/dev/null || echo "$body"
        return 1
    fi
}

# Listar tickets del tenant
list_tickets() {
    log STEP "Listando tickets del tenant..."
    
    local response=$(curl -s -w "\n%{http_code}" \
        --connect-timeout 10 \
        --max-time 30 \
        -H "Authorization: Bearer $USER_JWT" \
        "$API_BASE/tickets?limit=10" 2>/dev/null)
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)
    
    if [ "$http_code" = "200" ]; then
        log INFO "Tickets encontrados:"
        echo "$body" | jq '.tickets[] | {ticket_id, subject, status, created_at}'
        return 0
    else
        log ERROR "Error al listar tickets (HTTP $http_code)"
        return 1
    fi
}

# Mostrar ayuda
show_help() {
    echo "Uso: $0 [opciones]"
    echo ""
    echo "Opciones:"
    echo "  --create-risky    Crear ticket con risk_level risky"
    echo "  --check           Verificar estado del ticket"
    echo "  --approve         Aprobar el ticket"
    echo "  --reject          Rechazar el ticket"
    echo "  --select <id>     Seleccionar una opción de decisión"
    echo "  --list            Listar tickets del tenant"
    echo "  --full            Ejecutar flujo completo"
    echo "  -h, --help        Mostrar esta ayuda"
    echo ""
    echo "Variables de entorno:"
    echo "  USER_JWT          JWT del usuario (requerido)"
    echo "  TENANT_ID         ID del tenant (default: 1)"
    echo "  API_BASE          URL base de la API (default: https://api.xoc.app)"
    echo "  TICKET_ID         ID del ticket (opcional)"
    echo ""
    echo "Ejemplos:"
    echo "  # Crear ticket risky y aprobarlo"
    echo "  USER_JWT=eyJhbGci... ./scripts/test_approval_flow.sh --full"
    echo ""
    echo "  # Verificar estado de un ticket"
    echo "  USER_JWT=eyJhbGci... TICKET_ID=abc-123 ./scripts/test_approval_flow.sh --check"
}

# Variables de control
RUN_CREATE=false
RUN_CHECK=false
RUN_APPROVE=false
RUN_REJECT=false
RUN_SELECT=false
RUN_LIST=false
RUN_FULL=false
SELECT_OPTION=""

# Parsear argumentos
while [[ $# -gt 0 ]]; do
    case $1 in
        --create-risky)
            RUN_CREATE=true
            shift
            ;;
        --check)
            RUN_CHECK=true
            shift
            ;;
        --approve)
            RUN_APPROVE=true
            shift
            ;;
        --reject)
            RUN_REJECT=true
            shift
            ;;
        --select)
            RUN_SELECT=true
            SELECT_OPTION="$2"
            shift 2
            ;;
        --list)
            RUN_LIST=true
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

# Si no se especificó nada, ejecutar lista
if [ "$RUN_CREATE" = false ] && [ "$RUN_CHECK" = false ] && [ "$RUN_APPROVE" = false ] && [ "$RUN_REJECT" = false ] && [ "$RUN_SELECT" = false ] && [ "$RUN_LIST" = false ] && [ "$RUN_FULL" = false ]; then
    RUN_LIST=true
fi

# Ejecución principal
main() {
    echo ""
    echo "=========================================="
    echo "  TEST: Flujo de Aprobación de Tickets"
    echo "=========================================="
    echo ""
    echo "Tenant ID: $TENANT_ID"
    echo "API Base: $API_BASE"
    echo "Ticket ID: ${TICKET_ID:-auto}"
    echo ""
    
    check_prerequisites
    
    if [ "$RUN_FULL" = true ]; then
        # Flujo completo: crear → verificar → aprobar
        create_risky_ticket
        echo ""
        check_ticket_status
        echo ""
        approve_ticket
        echo ""
        check_ticket_status
    else
        if [ "$RUN_CREATE" = true ]; then
            create_risky_ticket
            echo ""
        fi
        
        if [ "$RUN_CHECK" = true ]; then
            check_ticket_status
            echo ""
        fi
        
        if [ "$RUN_APPROVE" = true ]; then
            approve_ticket
            echo ""
        fi
        
        if [ "$RUN_REJECT" = true ]; then
            reject_ticket
            echo ""
        fi
        
        if [ "$RUN_SELECT" = true ]; then
            select_decision "$SELECT_OPTION"
            echo ""
        fi
        
        if [ "$RUN_LIST" = true ]; then
            list_tickets
            echo ""
        fi
    fi
    
    echo "=========================================="
    echo "  TEST COMPLETADO"
    echo "=========================================="
    echo ""
}

# Ejecutar
main