#!/bin/bash
# verify_ticket_system.sh - Verificación rápida del sistema de tickets
# Verifica: AWS, VICTOR, DynamoDB, Step Functions
#
# Uso: ./scripts/verify_ticket_system.sh [tenant-id]

set -euo pipefail

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuración
TENANT_ID="${1:-1}"
VICTOR_URL="${VICTOR_URL:-http://10.20.0.22:8000}"
REGION="${AWS_REGION:-us-east-1}"

# Contadores
PASS=0
FAIL=0
WARN=0

# Función para logging
log() {
    local level=$1
    shift
    local message="$@"
    
    case $level in
        PASS) echo -e "${GREEN}  ✓ PASS:${NC} ${message}"; ((PASS++)) ;;
        FAIL) echo -e "${RED}  ✗ FAIL:${NC} ${message}"; ((FAIL++)) ;;
        WARN) echo -e "${YELLOW}  ! WARN:${NC} ${message}"; ((WARN++)) ;;
        INFO) echo -e "${BLUE}  → INFO:${NC} ${message}" ;;
        SECTION) echo -e "\n${CYAN}━━━ $message ━━━${NC}" ;;
    esac
}

# Verificar AWS CLI
verify_aws() {
    log SECTION "AWS CLI"
    
    if command -v aws &> /dev/null; then
        log PASS "AWS CLI instalado"
        
        local account=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
        if [ -n "$account" ]; then
            log PASS "Credenciales AWS configuradas (Account: $account)"
        else
            log FAIL "Credenciales AWS no configuradas"
        fi
    else
        log FAIL "AWS CLI no instalado"
    fi
}

# Verificar VICTOR
verify_victor() {
    log SECTION "VICTOR Agent"
    
    log INFO "URL: ${VICTOR_URL}"
    
    # Health check
    local response=$(curl -s -w "\n%{http_code}" \
        --connect-timeout 5 \
        --max-time 10 \
        "${VICTOR_URL}/health" 2>/dev/null || echo -e "\n000")
    
    local http_code=$(echo "$response" | tail -n1)
    
    if [ "$http_code" = "200" ]; then
        log PASS "VICTOR health check OK"
    elif [ "$http_code" = "000" ]; then
        log FAIL "VICTOR no accesible (timeout)"
    else
        log FAIL "VICTOR health check falló (HTTP $http_code)"
    fi
    
    # Assessment endpoint
    local assessment_response=$(curl -s -w "\n%{http_code}" \
        --connect-timeout 5 \
        --max-time 10 \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"phase":"assessment","ticketId":"test","tenantId":1,"subject":"test","description":"test"}' \
        "${VICTOR_URL}/api/agents/VictorDurableAgent/run" 2>/dev/null || echo -e "\n000")
    
    local assessment_code=$(echo "$assessment_response" | tail -n1)
    
    if [ "$assessment_code" = "200" ]; then
        log PASS "Assessment endpoint disponible"
    elif [ "$assessment_code" = "000" ]; then
        log WARN "Assessment endpoint no accesible"
    else
        log WARN "Assessment endpoint respondió con HTTP $assessment_code"
    fi
}

# Verificar DynamoDB
verify_dynamodb() {
    log SECTION "DynamoDB"
    
    local table_name="xoc-api-tickets-prod-tickets"
    
    # Verificar tabla
    if aws dynamodb describe-table \
        --table-name "$table_name" \
        --region "$REGION" \
        --query 'Table.TableStatus' \
        --output text &>/dev/null; then
        
        local status=$(aws dynamodb describe-table \
            --table-name "$table_name" \
            --region "$REGION" \
            --query 'Table.TableStatus' \
            --output text 2>/dev/null)
        
        if [ "$status" = "ACTIVE" ]; then
            log PASS "Tabla DynamoDB activa: $table_name"
            
            # Contar items del tenant
            local count=$(aws dynamodb scan \
                --table-name "$table_name" \
                --filter-expression "pk = :pk" \
                --expression-attribute-values '{":pk":{"S":"TICKET#'"$TENANT_ID"'"}}' \
                --select COUNT \
                --region "$REGION" \
                --query 'Count' \
                --output text 2>/dev/null || echo "0")
            
            log INFO "Tickets para tenant $TENANT_ID: $count"
        else
            log FAIL "Tabla DynamoDB en estado: $status"
        fi
    else
        log FAIL "Tabla DynamoDB no encontrada: $table_name"
    fi
}

# Verificar Step Functions
verify_step_functions() {
    log SECTION "Step Functions"
    
    local workflow_name="xoc-api-automation-prod-workflow"
    
    if aws stepfunctions describe-state-machine \
        --state-machine-arn "arn:aws:states:${REGION}:$(aws sts get-caller-identity --query Account --output text):stateMachine:${workflow_name}" \
        --region "$REGION" \
        --query 'status' \
        --output text &>/dev/null; then
        
        log PASS "State Machine activa: $workflow_name"
        
        # Listar ejecuciones recientes
        local executions=$(aws stepfunctions list-executions \
            --state-machine-arn "arn:aws:states:${REGION}:$(aws sts get-caller-identity --query Account --output text):stateMachine:${workflow_name}" \
            --max-results 5 \
            --region "$REGION" \
            --query 'executions[].{name:name,status:status,startDate:startDate}' \
            --output text 2>/dev/null || echo "")
        
        if [ -n "$executions" ]; then
            log INFO "Últimas ejecuciones:"
            echo "$executions" | while read line; do
                echo "        $line"
            done
        fi
    else
        log FAIL "State Machine no encontrada: $workflow_name"
    fi
}

# Verificar EventBridge
verify_eventbridge() {
    log SECTION "EventBridge"
    
    local bus_name="xoc-api-events"
    
    # No hay comando directo para verificar el bus, pero podemos intentar put_events
    # En su lugar, verificamos los permisos
    log INFO "Verificando permisos de EventBridge..."
    
    # Intentar listar buses (esto requiere permisos)
    if aws events list-event-buses \
        --name-prefix "xoc" \
        --region "$REGION" \
        --query 'EventBuses[].Name' \
        --output text &>/dev/null; then
        
        log PASS "EventBridge accesible"
    else
        log WARN "No se pudo verificar EventBridge (permisos insuficientes)"
    fi
}

# Verificar API Gateway
verify_api_gateway() {
    log SECTION "API Gateway"
    
    local api_name="xoc-api-shared-prod"
    
    local api_id=$(aws apigatewayv2 get-apis \
        --region "$REGION" \
        --query "Items[?Name=='${api_name}'].ApiId" \
        --output text 2>/dev/null || echo "")
    
    if [ -n "$api_id" ] && [ "$api_id" != "None" ]; then
        log PASS "API Gateway encontrada: $api_name (ID: $api_id)"
        
        # Verificar endpoints de tickets
        local routes=$(aws apigatewayv2 get-routes \
            --api-id "$api_id" \
            --region "$REGION" \
            --query "Items[?contains(RouteKey, 'tickets')].RouteKey" \
            --output text 2>/dev/null || echo "")
        
        if [ -n "$routes" ]; then
            log PASS "Rutas de tickets configuradas"
        else
            log WARN "No se encontraron rutas de tickets"
        fi
    else
        log FAIL "API Gateway no encontrada: $api_name"
    fi
}

# Mostrar resumen
show_summary() {
    echo ""
    echo "=========================================="
    echo "       RESUMEN DE VERIFICACIÓN"
    echo "=========================================="
    echo ""
    echo -e "  ${GREEN}PASS:${NC} $PASS"
    echo -e "  ${RED}FAIL:${NC} $FAIL"
    echo -e "  ${YELLOW}WARN:${NC} $WARN"
    echo ""
    
    if [ $FAIL -eq 0 ]; then
        echo -e "  ${GREEN}Estado: SISTEMA OPERATIVO${NC}"
        echo ""
        echo "  El sistema de tickets está listo para la demo."
        echo "  Ejecuta: ./scripts/demo_tickets_flow.sh --tenant-id $TENANT_ID"
    else
        echo -e "  ${RED}Estado: PROBLEMAS DETECTADOS${NC}"
        echo ""
        echo "  Revisa los errores antes de continuar."
    fi
    echo ""
}

# Ejecución principal
main() {
    echo ""
    echo "=========================================="
    echo "  VERIFICACIÓN DEL SISTEMA DE TICKETS"
    echo "=========================================="
    echo ""
    echo "Tenant ID: ${TENANT_ID}"
    echo "Región: ${REGION}"
    
    verify_aws
    verify_victor
    verify_dynamodb
    verify_step_functions
    verify_eventbridge
    verify_api_gateway
    
    show_summary
}

# Ejecutar
main