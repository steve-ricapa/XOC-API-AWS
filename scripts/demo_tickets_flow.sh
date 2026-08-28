#!/bin/bash
# demo_tickets_flow.sh - Script de demostración del flujo completo de tickets
# Demuestra: Creación de ticket → Victor assessment → Plan → Aprobación → Ejecución
#
# Uso:
#   ./scripts/demo_tickets_flow.sh --tenant-id <id> [--user-jwt <token>] [--victor-url <url>]
#
# Este script prueba el flujo completo de remediación de archivos maliciosos:
# 1. Verifica conectividad con VICTOR
# 2. Crea un ticket de prueba con descripción de archivo malicioso
# 3. Verifica que el workflow de Step Functions se inicie
# 4. Monitorea el progreso del workflow
# 5. Aprueba/rechaza el ticket si es necesario
# 6. Verifica el resultado final

set -euo pipefail

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuración por defecto
DEFAULT_VICTOR_URL="http://10.20.0.22:8000"
DEFAULT_API_BASE="https://api.xoc.app"
DEFAULT_REGION="us-east-1"

# Variables
TENANT_ID=""
USER_JWT=""
VICTOR_URL="${DEFAULT_VICTOR_URL}"
API_BASE="${DEFAULT_API_BASE}"
REGION="${DEFAULT_REGION}"
AUTO_APPROVE=false
SKIP_VICTOR_CHECK=false
VERBOSE=false

# Función para mostrar ayuda
show_help() {
    echo "Uso: $0 --tenant-id <id> [opciones]"
    echo ""
    echo "Obligatorio:"
    echo "  --tenant-id <id>           ID del tenant para la demo"
    echo ""
    echo "Opcionales:"
    echo "  --user-jwt <token>         JWT del usuario para autenticación"
    echo "  --victor-url <url>         URL de VICTOR (default: ${DEFAULT_VICTOR_URL})"
    echo "  --api-base <url>           URL base de la API (default: ${DEFAULT_API_BASE})"
    echo "  --region <region>          Región AWS (default: ${DEFAULT_REGION})"
    echo "  --auto-approve             Aprobar automáticamente pasos riesgosos"
    echo "  --skip-victor-check        No verificar conectividad con VICTOR"
    echo "  --verbose                  Mostrar logs detallados"
    echo "  -h, --help                 Mostrar esta ayuda"
    echo ""
    echo "Ejemplo:"
    echo "  $0 --tenant-id 123 --user-jwt eyJhbGciOiJIUzI1NiIs..."
    echo "  $0 --tenant-id 123 --auto-approve --verbose"
}

# Función para logging
log() {
    local level=$1
    shift
    local message="$@"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    
    case $level in
        INFO)  echo -e "${GREEN}[${timestamp}] INFO:${NC} ${message}" ;;
        WARN)  echo -e "${YELLOW}[${timestamp}] WARN:${NC} ${message}" ;;
        ERROR) echo -e "${RED}[${timestamp}] ERROR:${NC} ${message}" ;;
        DEBUG) 
            if [ "$VERBOSE" = true ]; then
                echo -e "${CYAN}[${timestamp}] DEBUG:${NC} ${message}"
            fi
            ;;
        STEP)  echo -e "${BLUE}[${timestamp}] STEP:${NC} ${message}" ;;
    esac
}

# Función para verificar prerrequisitos
check_prerequisites() {
    log STEP "Verificando prerrequisitos..."
    
    # Verificar AWS CLI
    if ! command -v aws &> /dev/null; then
        log ERROR "AWS CLI no está instalado"
        exit 1
    fi
    
    # Verificar curl
    if ! command -v curl &> /dev/null; then
        log ERROR "curl no está instalado"
        exit 1
    fi
    
    # Verificar jq
    if ! command -v jq &> /dev/null; then
        log ERROR "jq no está instalado"
        exit 1
    fi
    
    # Verificar credenciales AWS
    if ! aws sts get-caller-identity &> /dev/null; then
        log ERROR "Credenciales AWS no configuradas o inválidas"
        exit 1
    fi
    
    local account_id=$(aws sts get-caller-identity --query Account --output text)
    log INFO "Cuenta AWS: ${account_id}"
    
    log INFO "Prerrequisitos verificados ✓"
}

# Función para verificar conectividad con VICTOR
check_victor_connectivity() {
    if [ "$SKIP_VICTOR_CHECK" = true ]; then
        log WARN "Saltando verificación de VICTOR"
        return 0
    fi
    
    log STEP "Verificando conectividad con VICTOR..."
    
    local health_url="${VICTOR_URL}/health"
    local response
    local http_code
    
    response=$(curl -s -w "%{http_code}" -o /tmp/victor_health.json \
        --connect-timeout 10 \
        --max-time 30 \
        "${health_url}" 2>/dev/null) || true
    
    http_code="${response: -3}"
    local body=$(cat /tmp/victor_health.json 2>/dev/null || echo "{}")
    
    if [ "$http_code" = "200" ]; then
        log INFO "VICTOR está disponible ✓"
        log DEBUG "Response: ${body}"
        return 0
    else
        log WARN "VICTOR no está disponible (HTTP ${http_code})"
        log WARN "El flujo continuará sin conectar con VICTOR"
        return 1
    fi
}

# Función para verificar endpoint de assessment
check_assessment_endpoint() {
    log STEP "Verificando endpoint de assessment..."
    
    local test_payload='{
        "phase": "assessment",
        "ticketId": "test-00000000-0000-0000-0000-000000000000",
        "tenantId": '"${TENANT_ID}"',
        "subject": "Test connectivity",
        "description": "Test"
    }'
    
    local response
    local http_code
    
    response=$(curl -s -w "%{http_code}" -o /tmp/victor_assessment.json \
        --connect-timeout 10 \
        --max-time 30 \
        -X POST \
        -H "Content-Type: application/json" \
        -d "${test_payload}" \
        "${VICTOR_URL}/api/agents/VictorDurableAgent/run" 2>/dev/null) || true
    
    http_code="${response: -3}"
    local body=$(cat /tmp/victor_assessment.json 2>/dev/null || echo "{}")
    
    if [ "$http_code" = "200" ]; then
        log INFO "Endpoint de assessment disponible ✓"
        log DEBUG "Response: ${body}"
        return 0
    else
        log WARN "Endpoint de assessment no disponible (HTTP ${http_code})"
        return 1
    fi
}

# Función para generar ticket ID único
generate_ticket_id() {
    if command -v uuidgen &> /dev/null; then
        uuidgen | tr '[:upper:]' '[:lower:]'
    else
        # Fallback para sistemas sin uuidgen
        cat /proc/sys/kernel/random/uuid 2>/dev/null || \
        python3 -c "import uuid; print(str(uuid.uuid4()))" 2>/dev/null || \
        echo "demo-$(date +%s)-$RANDOM"
    fi
}

# Función para crear ticket de prueba
create_test_ticket() {
    log STEP "Creando ticket de prueba para archivo malicioso..."
    
    local ticket_id=$(generate_ticket_id)
    local subject="Alerta: Archivo malicioso detectado en servidor Web"
    local description="Se ha detectado un archivo sospechoso en /tmp/trojan.sh que podría ser un script malicioso. 
El archivo fue creado recientemente y presenta características de malware:
- Permiso de ejecución activo
- Contenido ofuscado
- Intenta establecer conexión con IP externa 185.220.101.45:4444

Se requiere investigación inmediata y remediación para evitar compromiso del sistema."

    log INFO "Ticket ID: ${ticket_id}"
    log INFO "Subject: ${subject}"
    
    # Construir payload del ticket
    local payload=$(cat <<EOF
{
    "subject": "${subject}",
    "description": "${description}",
    "status": "PENDING",
    "priority": "high",
    "category": "security_incident",
    "metadata": {
        "source": "demo_script",
        "created_by": "demo_automation",
        "file_path": "/tmp/trojan.sh",
        "threat_type": "malware",
        "severity": "high"
    }
}
EOF
)
    
    log DEBUG "Payload: ${payload}"
    
    # Guardar payload para referencia
    echo "${payload}" > /tmp/ticket_payload.json
    
    log INFO "Ticket de prueba creado ✓"
    echo "${ticket_id}"
}

# Función para iniciar workflow de Step Functions
start_automation_workflow() {
    local ticket_id=$1
    local subject=$2
    local description=$3
    
    log STEP "Iniciando workflow de Step Functions..."
    
    local workflow_name="xoc-api-automation-prod-workflow"
    local execution_name="demo-ticket-${ticket_id:0:30}"
    
    local input=$(cat <<EOF
{
    "input": {
        "ticketId": "${ticket_id}",
        "tenantId": ${TENANT_ID},
        "subject": "${subject}",
        "description": "${description}"
    }
}
EOF
)
    
    log DEBUG "Workflow input: ${input}"
    
    local execution_arn
    execution_arn=$(aws stepfunctions start-execution \
        --state-machine-arn "arn:aws:states:${REGION}:$(aws sts get-caller-identity --query Account --output text):stateMachine:${workflow_name}" \
        --name "${execution_name}" \
        --input "${input}" \
        --query 'executionArn' \
        --output text 2>/dev/null)
    
    if [ $? -eq 0 ] && [ -n "${execution_arn}" ]; then
        log INFO "Workflow iniciado ✓"
        log INFO "Execution ARN: ${execution_arn}"
        echo "${execution_arn}"
        return 0
    else
        log ERROR "Error al iniciar workflow"
        return 1
    fi
}

# Función para monitorear workflow
monitor_workflow() {
    local execution_arn=$1
    local max_wait=300  # 5 minutos máximo
    local interval=10
    local elapsed=0
    
    log STEP "Monitoreando workflow..."
    log INFO "Execution ARN: ${execution_arn}"
    log INFO "Tiempo máximo de espera: ${max_wait}s"
    
    while [ ${elapsed} -lt ${max_wait} ]; do
        local status
        status=$(aws stepfunctions describe-execution \
            --execution-arn "${execution_arn}" \
            --query 'status' \
            --output text 2>/dev/null)
        
        local output
        output=$(aws stepfunctions describe-execution \
            --execution-arn "${execution_arn}" \
            --query 'output' \
            --output text 2>/dev/null || echo "null")
        
        log DEBUG "Status: ${status}"
        
        case ${status} in
            RUNNING)
                log INFO "Workflow ejecutándose... (${elapsed}s)"
                
                # Verificar si está esperando aprobación
                if echo "${output}" | grep -q "PREAPROBADO\|AWAITING_APPROVAL"; then
                    log WARN "Workflow esperando aprobación"
                    return 2  # Código especial para aprobación pendiente
                fi
                ;;
            SUCCEEDED)
                log INFO "Workflow completado exitosamente ✓"
                log INFO "Output: ${output}"
                return 0
                ;;
            FAILED)
                log ERROR "Workflow falló"
                log ERROR "Output: ${output}"
                return 1
                ;;
            TIMED_OUT)
                log ERROR "Workflow expiró"
                return 1
                ;;
            *)
                log DEBUG "Status: ${status}"
                ;;
        esac
        
        sleep ${interval}
        elapsed=$((elapsed + interval))
    done
    
    log WARN "Tiempo máximo de espera alcanzado"
    return 0
}

# Función para aprobar ticket manualmente
approve_ticket() {
    local ticket_id=$1
    
    log STEP "Aprobando ticket ${ticket_id}..."
    
    # Obtener JWT si no está configurado
    if [ -z "${USER_JWT}" ]; then
        log WARN "No se proporcionó JWT. Intentando obtener token de servicio..."
        
        # Aquí se podría implementar lógica para obtener token de servicio
        log ERROR "Se requiere JWT para aprobar tickets"
        return 1
    fi
    
    local response
    response=$(curl -s -w "%{http_code}" -o /tmp/approve_response.json \
        --connect-timeout 10 \
        --max-time 30 \
        -X PATCH \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${USER_JWT}" \
        "${API_BASE}/tickets/${ticket_id}/approve" 2>/dev/null)
    
    local http_code="${response: -3}"
    local body=$(cat /tmp/approve_response.json 2>/dev/null || echo "{}")
    
    if [ "$http_code" = "200" ]; then
        log INFO "Ticket aprobado exitosamente ✓"
        log DEBUG "Response: ${body}"
        return 0
    else
        log ERROR "Error al aprobar ticket (HTTP ${http_code})"
        log ERROR "Response: ${body}"
        return 1
    fi
}

# Función para verificar estado del ticket
check_ticket_status() {
    local ticket_id=$1
    
    log STEP "Verificando estado del ticket..."
    
    local response
    response=$(curl -s -w "%{http_code}" -o /tmp/ticket_status.json \
        --connect-timeout 10 \
        --max-time 30 \
        -H "Authorization: Bearer ${USER_JWT}" \
        "${API_BASE}/tickets/${ticket_id}" 2>/dev/null)
    
    local http_code="${response: -3}"
    local body=$(cat /tmp/ticket_status.json 2>/dev/null || echo "{}")
    
    if [ "$http_code" = "200" ]; then
        local status=$(echo "${body}" | jq -r '.status // "unknown"')
        local execution_status=$(echo "${body}" | jq -r '.execution_status // "unknown"')
        
        log INFO "Estado del ticket: ${status}"
        log INFO "Estado de ejecución: ${execution_status}"
        echo "${body}" | jq '.'
        return 0
    else
        log ERROR "Error al obtener estado del ticket (HTTP ${http_code})"
        return 1
    fi
}

# Función para ejecutar assessment directo a VICTOR
test_victor_assessment() {
    local ticket_id=$1
    local subject=$2
    local description=$3
    
    log STEP "Probando assessment directo a VICTOR..."
    
    local payload=$(cat <<EOF
{
    "phase": "assessment",
    "ticketId": "${ticket_id}",
    "tenantId": ${TENANT_ID},
    "subject": "${subject}",
    "description": "${description}"
}
EOF
)
    
    log DEBUG "Assessment payload: ${payload}"
    
    local response
    response=$(curl -s -w "%{http_code}" -o /tmp/victor_assessment.json \
        --connect-timeout 10 \
        --max-time 60 \
        -X POST \
        -H "Content-Type: application/json" \
        -d "${payload}" \
        "${VICTOR_URL}/api/agents/VictorDurableAgent/run" 2>/dev/null)
    
    local http_code="${response: -3}"
    local body=$(cat /tmp/victor_assessment.json 2>/dev/null || echo "{}")
    
    if [ "$http_code" = "200" ]; then
        log INFO "Assessment completado ✓"
        log DEBUG "Response: ${body}"
        echo "${body}" | jq '.'
        return 0
    else
        log ERROR "Error en assessment (HTTP ${http_code})"
        log ERROR "Response: ${body}"
        return 1
    fi
}

# Función para generar plan directo a VICTOR
test_victor_plan() {
    local ticket_id=$1
    local subject=$2
    local description=$3
    
    log STEP "Generando plan directo a VICTOR..."
    
    local payload=$(cat <<EOF
{
    "phase": "plan",
    "ticketId": "${ticket_id}",
    "tenantId": ${TENANT_ID},
    "subject": "${subject}",
    "description": "${description}"
}
EOF
)
    
    log DEBUG "Plan payload: ${payload}"
    
    local response
    response=$(curl -s -w "%{http_code}" -o /tmp/victor_plan.json \
        --connect-timeout 10 \
        --max-time 120 \
        -X POST \
        -H "Content-Type: application/json" \
        -d "${payload}" \
        "${VICTOR_URL}/api/agents/VictorDurableAgent/run" 2>/dev/null)
    
    local http_code="${response: -3}"
    local body=$(cat /tmp/victor_plan.json 2>/dev/null || echo "{}")
    
    if [ "$http_code" = "200" ]; then
        log INFO "Plan generado ✓"
        log DEBUG "Response: ${body}"
        echo "${body}" | jq '.'
        return 0
    else
        log ERROR "Error al generar plan (HTTP ${http_code})"
        log ERROR "Response: ${body}"
        return 1
    fi
}

# Función para ejecutar plan directo a VICTOR
test_victor_execute() {
    local ticket_id=$1
    local plan=$2
    
    log STEP "Ejecutando plan directo a VICTOR..."
    
    local payload=$(cat <<EOF
{
    "phase": "execute",
    "ticketId": "${ticket_id}",
    "tenantId": ${TENANT_ID},
    "plan": ${plan}
}
EOF
)
    
    log DEBUG "Execute payload: ${payload}"
    
    local response
    response=$(curl -s -w "%{http_code}" -o /tmp/victor_execute.json \
        --connect-timeout 10 \
        --max-time 180 \
        -X POST \
        -H "Content-Type: application/json" \
        -d "${payload}" \
        "${VICTOR_URL}/api/agents/VictorDurableAgent/run" 2>/dev/null)
    
    local http_code="${response: -3}"
    local body=$(cat /tmp/victor_execute.json 2>/dev/null || echo "{}")
    
    if [ "$http_code" = "200" ]; then
        log INFO "Ejecución completada ✓"
        log DEBUG "Response: ${body}"
        echo "${body}" | jq '.'
        return 0
    else
        log ERROR "Error en ejecución (HTTP ${http_code})"
        log ERROR "Response: ${body}"
        return 1
    fi
}

# Función para mostrar resumen
show_summary() {
    local ticket_id=$1
    local execution_arn=$2
    local status=$3
    
    echo ""
    echo "=========================================="
    echo "       RESUMEN DE LA DEMOSTRACIÓN"
    echo "=========================================="
    echo ""
    echo "Ticket ID:        ${ticket_id}"
    echo "Tenant ID:        ${TENANT_ID}"
    echo "VICTOR URL:       ${VICTOR_URL}"
    echo "Execution ARN:    ${execution_arn:-N/A}"
    echo "Estado final:     ${status}"
    echo ""
    echo "Archivos generados:"
    echo "  - /tmp/ticket_payload.json    (Payload del ticket)"
    echo "  - /tmp/victor_assessment.json (Respuesta assessment)"
    echo "  - /tmp/victor_plan.json       (Respuesta plan)"
    echo "  - /tmp/victor_execute.json    (Respuesta ejecución)"
    echo ""
    echo "Para verificar el ticket:"
    echo "  aws dynamodb get-item \\"
    echo "    --table-name xoc-api-tickets-prod-tickets \\"
    echo "    --key '{\"pk\": {\"S\": \"TICKET#${TENANT_ID}\"}, \"sk\": {\"S\": \"TICKET#${ticket_id}\"}}' \\"
    echo "    --region ${REGION}"
    echo ""
    echo "Para ver logs del workflow:"
    echo "  aws stepfunctions describe-execution \\"
    echo "    --execution-arn '${execution_arn}' \\"
    echo "    --region ${REGION}"
    echo ""
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
        --victor-url)
            VICTOR_URL="$2"
            shift 2
            ;;
        --api-base)
            API_BASE="$2"
            shift 2
            ;;
        --region)
            REGION="$2"
            shift 2
            ;;
        --auto-approve)
            AUTO_APPROVE=true
            shift
            ;;
        --skip-victor-check)
            SKIP_VICTOR_CHECK=true
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

# Validar argumentos obligatorios
if [ -z "${TENANT_ID}" ]; then
    log ERROR "Se requiere --tenant-id"
    show_help
    exit 1
fi

# Ejecución principal
main() {
    echo ""
    echo "=========================================="
    echo "  DEMO: Flujo Completo de Tickets XOC"
    echo "=========================================="
    echo ""
    echo "Tenant ID: ${TENANT_ID}"
    echo "VICTOR URL: ${VICTOR_URL}"
    echo "API Base: ${API_BASE}"
    echo "Región: ${REGION}"
    echo ""
    
    # 1. Verificar prerrequisitos
    check_prerequisites
    
    # 2. Verificar conectividad con VICTOR
    local victor_available=false
    if check_victor_connectivity; then
        victor_available=true
        check_assessment_endpoint || true
    fi
    
    # 3. Crear ticket de prueba
    local ticket_id=$(create_test_ticket)
    local subject="Alerta: Archivo malicioso detectado en servidor Web"
    local description="Se ha detectado un archivo sospechoso en /tmp/trojan.sh que podría ser un script malicioso. 
El archivo fue creado recientemente y presenta características de malware:
- Permiso de ejecución activo
- Contenido ofuscado
- Intenta establecer conexión con IP externa 185.220.101.45:4444

Se requiere investigación inmediata y remediación para evitar compromiso del sistema."
    
    # 4. Si VICTOR está disponible, probar directamente
    if [ "$victor_available" = true ]; then
        log INFO "VICTOR está disponible. Probando flujo directo..."
        
        # Test assessment
        test_victor_assessment "${ticket_id}" "${subject}" "${description}" || true
        
        # Test plan
        local plan_response=$(test_victor_plan "${ticket_id}" "${subject}" "${description}" || echo "{}")
        
        # Test execute si hay plan
        if [ -n "${plan_response}" ] && [ "${plan_response}" != "{}" ]; then
            local plan_json=$(echo "${plan_response}" | jq -c '.plan // .')
            if [ "${plan_json}" != "null" ] && [ "${plan_json}" != "{}" ]; then
                test_victor_execute "${ticket_id}" "${plan_json}" || true
            fi
        fi
    else
        log WARN "VICTOR no disponible. Solo se creará el ticket."
    fi
    
    # 5. Iniciar workflow de Step Functions
    log STEP "Iniciando workflow de Step Functions..."
    local execution_arn
    execution_arn=$(start_automation_workflow "${ticket_id}" "${subject}" "${description}" || echo "")
    
    if [ -n "${execution_arn}" ]; then
        # 6. Monitorear workflow
        log STEP "Monitoreando progreso del workflow..."
        local workflow_status
        monitor_workflow "${execution_arn}"
        workflow_status=$?
        
        # 7. Manejar aprobación si es necesario
        if [ ${workflow_status} -eq 2 ]; then
            log WARN "Workflow esperando aprobación"
            
            if [ "${AUTO_APPROVE}" = true ]; then
                log INFO "Auto-aprobando ticket..."
                approve_ticket "${ticket_id}" || log WARN "No se pudo auto-aprobar"
            else
                log INFO "Para aprobar manualmente:"
                log INFO "  curl -X PATCH -H 'Authorization: Bearer <JWT>' \\"
                log INFO "    -H 'Content-Type: application/json' \\"
                log INFO "    '${API_BASE}/tickets/${ticket_id}/approve'"
                
                log INFO "O ejecutar:"
                log INFO "  $0 --tenant-id ${TENANT_ID} --user-jwt <JWT> --auto-approve"
            fi
        fi
        
        # 8. Verificar estado final
        check_ticket_status "${ticket_id}" || true
        
        # 9. Mostrar resumen
        show_summary "${ticket_id}" "${execution_arn}" "Completado"
    else
        log WARN "No se pudo iniciar workflow de Step Functions"
        log INFO "El ticket fue creado pero no se inició el workflow automático"
        
        # Verificar estado del ticket
        check_ticket_status "${ticket_id}" || true
        
        show_summary "${ticket_id}" "" "Ticket creado sin workflow"
    fi
    
    echo ""
    echo "=========================================="
    echo "  DEMOSTRACIÓN COMPLETADA"
    echo "=========================================="
    echo ""
}

# Ejecutar
main