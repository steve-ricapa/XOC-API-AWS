# Guía de Demostración: Flujo de Tickets XOC

## Resumen

Esta guía explica cómo probar el flujo completo de tickets con VICTOR para la demostración de eliminación de archivos maliciosos.

## Arquitectura del Flujo

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   xoc.app       │────▶│  XOC-API-AWS     │────▶│  VICTOR Agent   │
│   (Frontend)    │     │  (Lambda + SF)   │     │  (Docker)       │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                              │                         │
                              ▼                         ▼
                        ┌──────────┐             ┌──────────┐
                        │ DynamoDB │             │ Executor │
                        │ (Tickets)│             │ (Shell)  │
                        └──────────┘             └──────────┘
```

## Scripts Disponibles

### 1. `verify_ticket_system.sh`
Verifica que todos los componentes estén listos.

```bash
./scripts/verify_ticket_system.sh <tenant-id>
```

### 2. `demo_tickets_flow.sh`
Demo completa end-to-end.

```bash
./scripts/demo_tickets_flow.sh --tenant-id <id> [--user-jwt <token>]
```

### 3. `test_victor_ticket_flow.sh`
Prueba rápida de VICTOR directamente.

```bash
./scripts/test_victor_ticket_flow.sh --full
```

### 4. `quick_victor_test.sh`
Prueba ultra-rápida de conectividad.

```bash
./scripts/quick_victor_test.sh [victor-url] [tenant-id]
```

### 5. `test_approval_flow.sh`
Prueba del flujo de aprobación.

```bash
USER_JWT=<token> ./scripts/test_approval_flow.sh --full
```

## Pasos para la Demo

### Opción 1: Demo Completa (Recomendada)

```bash
# 1. Verificar que el sistema esté listo
./scripts/verify_ticket_system.sh 123

# 2. Ejecutar demo completa
./scripts/demo_tickets_flow.sh --tenant-id 123

# 3. Si VICTOR está disponible, probar directamente
./scripts/test_victor_ticket_flow.sh --full
```

### Opción 2: Prueba Rápida de VICTOR

```bash
# Solo probar VICTOR (sin AWS)
./scripts/quick_victor_test.sh http://10.20.0.22:8000 123

# O con el script más detallado
./scripts/test_victor_ticket_flow.sh --full --victor-url http://10.20.0.22:8000
```

### Opción 3: Prueba de Aprobación

```bash
# Crear ticket risky y aprobarlo
USER_JWT=eyJhbGci... ./scripts/test_approval_flow.sh --full

# Solo verificar estado
USER_JWT=eyJhbGci... TICKET_ID=abc-123 ./scripts/test_approval_flow.sh --check
```

## Flujo de la Demostración

### Paso 1: Creación del Ticket

El script crea un ticket con:
- **Subject**: "Alerta: Archivo malicioso detectado en servidor Web"
- **Description**: Detalles del archivo `/tmp/trojan.sh` con características de malware

### Paso 2: Assessment (VICTOR)

VICTOR evalúa si puede resolver el ticket:
```json
{
    "canResolve": true,
    "confidence": 0.9,
    "assessment_type": "malware_remediation"
}
```

### Paso 3: Generación del Plan

VICTOR genera un plan de 4 fases:

| Fase | Riesgo | Acciones |
|------|--------|----------|
| 1. Detección | basic | `ls -la`, `file`, `head`, `ps aux` |
| 2. Contención | controlled | `mkdir -p /var/quarantine`, `mv /tmp/trojan.sh /var/quarantine/` |
| 3. Remediación | risky | `rm -f /var/quarantine/trojan.sh` |
| 4. Verificación | basic | `ls -la`, verificar procesos |

### Paso 4: Aprobación

Si hay pasos "risky", el workflow pausa para aprobación:
- **Rol requerido**: ADMIN_XOC
- **Timeout**: 7 días

### Paso 5: Ejecución

VICTOR ejecuta cada paso vía `laptop_agent.py`:
```json
{
    "status": "completed",
    "all_success": true,
    "step_results": [...]
}
```

### Paso 6: Verificación

Se verifica que el ticket esté en estado `RESUELTO`.

## Niveles de Riesgo

| Nivel | Rol Requerido | Ejemplos |
|-------|---------------|----------|
| basic | USER | `ls`, `file`, `head`, `ps` |
| controlled | ADMIN | `mkdir`, `mv`, `chmod` |
| risky | ADMIN_XOC | `rm`, `kill`, `systemctl stop` |
| critical | SUPERADMIN | `wipe`, `drop`, `reinitialize` |

## Variables de Entorno

### Para los scripts

```bash
# VICTOR
VICTOR_URL=http://10.20.0.22:8000

# AWS
TENANT_ID=123
AWS_REGION=us-east-1

# API
API_BASE=https://api.xoc.app
USER_JWT=eyJhbGci...
```

### En AWS Lambda (ya configuradas)

```yaml
AGENTS_FUNCTION_BASE_URL: http://10.20.0.22:8000
AGENTS_FUNCTION_ROUTE_VICTOR: /api/agents/VictorDurableAgent/run
EVENT_BUS_NAME: xoc-api-tickets-prod-bus
TICKETS_TABLE_NAME: xoc-api-tickets-prod-tickets
```

## Troubleshooting

### VICTOR no responde

```bash
# Verificar health
curl http://10.20.0.22:8000/health

# Verificar logs en XOC APPLIANCE
ssh -i ~/.ssh/xoc-ec2 ubuntu@10.20.0.22 "docker logs victor-server --tail 50"
```

### Step Functions no inicia

```bash
# Verificar que el workflow existe
aws stepfunctions list-state-machines --region us-east-1

# Verificar ejecuciones recientes
aws stepfunctions list-executions \
  --state-machine-arn arn:aws:states:us-east-1:ACCOUNT:stateMachine:xoc-api-automation-prod-workflow \
  --max-results 10
```

### Ticket no se actualiza

```bash
# Verificar item en DynamoDB
aws dynamodb get-item \
  --table-name xoc-api-tickets-prod-tickets \
  --key '{"pk":{"S":"TICKET#123"},"sk":{"S":"TICKET#ticket-id"}}' \
  --region us-east-1
```

### Error de aprobación

```bash
# Verificar pending_decision
aws dynamodb get-item \
  --table-name xoc-api-tickets-prod-tickets \
  --key '{"pk":{"S":"TICKET#123"},"sk":{"S":"TICKET#ticket-id"}}' \
  --projection-expression "pending_decision" \
  --region us-east-1
```

## Comandos Rápidos

### Verificar sistema completo
```bash
./scripts/verify_ticket_system.sh 123
```

### Probar VICTOR directamente
```bash
./scripts/quick_victor_test.sh
```

### Demo completa con aprobación automática
```bash
./scripts/demo_tickets_flow.sh --tenant-id 123 --auto-approve
```

### Probar solo assessment
```bash
./scripts/test_victor_ticket_flow.sh --assessment
```

### Probar solo plan
```bash
./scripts/test_victor_ticket_flow.sh --plan
```

## Archivos Generados

Los scripts generan archivos en `/tmp/`:

- `/tmp/ticket_payload.json` - Payload del ticket
- `/tmp/victor_assessment.json` - Respuesta del assessment
- `/tmp/victor_plan.json` - Respuesta del plan
- `/tmp/victor_execute.json` - Respuesta de ejecución

## Notas Importantes

1. **VICTOR debe estar corriendo** en el XOC APPLIANCE (Docker)
2. **Los tickets "risky" requieren** rol ADMIN_XOC para aprobar
3. **El timeout de aprobación** es de 7 días
4. **Los scripts usan** `curl`, `jq`, y `aws-cli` como dependencias
5. **En producción**, el flujo es asincrónico vía Step Functions