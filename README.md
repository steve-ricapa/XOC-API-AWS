# XOC-API-AWS

Backend core de XOC migrado a AWS con Serverless Framework, FastAPI y Mangum.

## Estado actual

Migrado y compilando:

- auth
- onboarding tenant
- companies
- users
- tickets
- integrations
- systems
- alerts
- vulnerabilities
- analytics
- scans
- admin
- superadmin
- agents
- audit
- chat

Infra prod preparada:

- `infra/network-prod.yml`
- `infra/data-prod.yml`
- `infra/storage-prod.yml`

## Arquitectura objetivo prod

- API Gateway HTTP API
- Lambda `apiHttp` con FastAPI + Mangum
- Lambda `jwtAuthorizer` separada
- RDS PostgreSQL privada
- S3 para snapshots pesados
- VPC privada para `apiHttp`
- NAT Gateway para salida HTTP hacia Azure/SOPHIA
- EventBridge bus base preparado, pero no usado todavía para estos módulos

## Decisiones importantes

- `jwtAuthorizer` queda fuera de VPC
- `apiHttp` entra a VPC solo en `prod`
- RDS no es pública
- snapshots pesados no van al core relacional; van a S3
- chat sigue siendo HTTP normal hacia Azure Function de SOPHIA
- no tocar Voice, Speech, Live Voice, WebSocket, Foundry realtime, MAD ni ETL

## Estructura

```text
infra/
  network-prod.yml
  data-prod.yml
  storage-prod.yml
serverless/
  functions.yml
  resources.yml
  stages/
src/
  handlers/
    api.py
    routes/
    authorizers/
  persistence/
  shared/
serverless.yml
requirements.txt
package.json
```

## Endpoints migrados

### Base

- `GET /health`

### Auth y onboarding

- `POST /api/auth/register` (410 legacy disabled)
- `POST /api/auth/login`
- `POST /api/auth/refresh`
- `POST /api/onboarding/tenant`

### Companies

- `GET /api/companies`
- `GET /api/companies/{company_id}`
- `PUT /api/companies/{company_id}`
- `GET /api/companies/{company_id}/runtime-settings`
- `PUT /api/companies/{company_id}/runtime-settings`

### Users

- `GET /api/users`
- `GET /api/users/{user_id}`
- `POST /api/users`
- `PUT /api/users/{user_id}`
- `DELETE /api/users/{user_id}`

### Tickets

- `GET /api/tickets`
- `POST /api/tickets`
- `GET /api/tickets/{ticket_id}`
- `PUT /api/tickets/{ticket_id}`
- `DELETE /api/tickets/{ticket_id}`
- `PATCH /api/tickets/{ticket_id}/approve`
- `PATCH /api/tickets/{ticket_id}/reject`
- `PATCH /api/tickets/{ticket_id}/decision/select`
- `POST /api/tickets/agent-create`

### Integrations

- `GET /api/integrations`
- `GET /api/integrations/{integration_id}`
- `POST /api/integrations`
- `PUT /api/integrations/{integration_id}`
- `DELETE /api/integrations/{integration_id}`
- `GET /api/integrations/{integration_id}/credentials`
- `GET /api/integrations/zabbix/summary`
- `GET /api/integrations/zabbix/detailed`
- `GET /api/integrations/wazuh/summary`
- `GET /api/integrations/nessus/summary`
- `GET /api/integrations/uptime_kuma/summary`
- `GET /api/integrations/dashboard/summary`

### Scans

- `POST /api/scans/ingest`
- `GET /api/scans/snapshots`
- `GET /api/scans/snapshots/{artifact_id}`
- `GET /api/scans/snapshots/{artifact_id}/payload`
- `GET /api/scans`
- `GET /api/scans/latest`
- `GET /api/scans/summary`
- `GET /api/scans/{scan_summary_id}`
- `GET /api/scans/{scan_summary_id}/findings`
- `GET /api/scans/{scanner_type}/analytics`
- `GET /api/scans/agent/summaries`
- `GET /api/scans/agent/summaries/{scan_summary_id}`
- `GET /api/scans/agent/summaries/{scan_summary_id}/findings`
- `GET /api/scans/agent/findings`
- `GET /api/scans/agent/snapshots`
- `GET /api/scans/agent/snapshots/{artifact_id}`
- `GET /api/scans/agent/snapshots/{artifact_id}/payload`

### Systems, alerts, vulnerabilities, analytics

- `GET /api/systems/status`
- `GET /api/systems`
- `GET /api/systems/{system_id}`
- `GET /api/alerts/active`
- `POST /api/alerts/{alert_id}/resolve`
- `GET /api/vulnerabilities`
- `GET /api/vulnerabilities/{vuln_id}`
- `POST /api/vulnerabilities/{vuln_id}/patch`
- `GET /api/analytics/incidents`
- `GET /api/analytics/response-time`
- `GET /api/analytics/vulnerability-distribution`
- `GET /api/analytics/summary`

### Admin

- `POST /api/admin/agent-instances`
- `GET /api/admin/agent-instances`
- `GET /api/admin/agent-instances/{instance_id}`
- `PATCH /api/admin/agent-instances/{instance_id}`
- `PATCH /api/admin/agent-instances/{instance_id}/status`
- `DELETE /api/admin/agent-instances/{instance_id}`
- endpoints legacy de keys/activation en 410

### Superadmin

- companies
- users
- integrations
- agent-instances
- tickets
- chat administración
- audit-logs
- capability-templates
- endpoints legacy de keys/activation en 410

### Agents

- `POST /api/agents/auth/token` (410 legacy disabled)
- `POST /api/agents/auth/token-from-user`
- `GET /api/agents/instance/{instance_id}` (410 deprecated)

### Audit

- `POST /api/audit`

### Chat

- `GET /api/chat/sessions`
- `GET /api/chat/history`
- `DELETE /api/chat/sessions/{session_id}`
- `POST /api/chat`

## Requisitos para la MV de despliegue

- AWS CLI configurado con credenciales válidas
- Node.js y npm
- Python 3.12
- `serverless` disponible
- acceso a la cuenta AWS destino

## Dependencias locales

```bash
npm install
pip install -r requirements.txt
```

## Variables y secretos esperados

### SSM

- `/xoc/api/prod/jwt-secret-key` (crear manualmente antes del deploy)

### Secrets Manager

- secret de base de datos prod (lo crea `infra/data-prod.yml`) — contiene `database_url` que el código lee automáticamente

### Env inyectadas por `serverless.yml`

- `APP_STAGE`
- `APP_REGION`
- `JWT_SECRET_KEY_SSM_PATH`
- `DATABASE_SECRET_ARN`
- `SNAPSHOTS_BUCKET_NAME`
- `CORS_ALLOWED_ORIGINS`
- `EVENT_BUS_NAME`

## Orden de despliegue prod

No desplegar backend antes de tener los tres stacks de infra.

### 1. Crear secret JWT prod

```bash
aws ssm put-parameter \
  --name "/xoc/api/prod/jwt-secret-key" \
  --type "SecureString" \
  --value "REEMPLAZAR_CON_UN_SECRETO_REAL" \
  --overwrite
```

### 2. Desplegar red

```bash
aws cloudformation deploy \
  --stack-name xoc-infra-network-prod \
  --template-file infra/network-prod.yml
```

### 3. Obtener outputs de red

```bash
aws cloudformation describe-stacks \
  --stack-name xoc-infra-network-prod \
  --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" \
  --output table
```

Necesitarás estos outputs para el stack de data:

- `DbPrivateSubnetAId`
- `DbPrivateSubnetBId`
- `RdsSecurityGroupId`

### 4. Desplegar data

```bash
aws cloudformation deploy \
  --stack-name xoc-infra-data-prod \
  --template-file infra/data-prod.yml \
  --parameter-overrides \
    DbSubnetAId=REEMPLAZAR \
    DbSubnetBId=REEMPLAZAR \
    RdsSecurityGroupId=REEMPLAZAR \
    DatabasePassword=REEMPLAZAR_CON_PASSWORD_SEGURA
```

### 5. Desplegar storage

```bash
aws cloudformation deploy \
  --stack-name xoc-infra-storage-prod \
  --template-file infra/storage-prod.yml
```

### 6. Validar outputs de data y storage

```bash
aws cloudformation describe-stacks \
  --stack-name xoc-infra-data-prod \
  --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" \
  --output table

aws cloudformation describe-stacks \
  --stack-name xoc-infra-storage-prod \
  --query "Stacks[0].Outputs[*].[OutputKey,OutputValue]" \
  --output table
```

### 7. Desplegar backend

```bash
serverless deploy --stage prod
```

### 8. Bootstrap inicial del esquema

Si la RDS está vacía, crear tablas desde la MV:

```bash
python scripts/bootstrap_schema.py
```

## Notas sobre Serverless Framework v4

`serverless` v4 puede pedir login o licencia antes de `print`, `package` o `deploy`.

Si ves ese error en la MV, resuélvelo antes de continuar:

- `serverless login`
- o configurar la licencia/flujo que use tu equipo

## Validaciones después del deploy

### Infra

1. La RDS no debe quedar pública
2. `apiHttp` debe usar subnets privadas Lambda
3. `jwtAuthorizer` debe seguir fuera de VPC
4. NAT debe permitir salida HTTP hacia Azure/SOPHIA
5. bucket S3 de snapshots debe quedar privado

### Smoke tests backend

1. `GET /health`
2. login
3. refresh token
4. `POST /api/agents/auth/token-from-user`
5. `POST /api/audit`
6. `POST /api/chat`
7. lectura/escritura real en DB
8. llamada HTTP real a Azure/SOPHIA desde chat

## Desarrollo local

```bash
uvicorn src.handlers.api:app --reload --host 0.0.0.0 --port 8000
```

Health local:

```bash
GET http://localhost:8000/health
```

## Qué falta implementar todavía

Esto todavía no está cerrado de punta a punta:

### 1. Snapshots pesados en S3

Ya quedó implementada la persistencia base para `POST /api/scans/ingest`:

- sube el payload crudo a S3
- genera keys ordenadas por stage/company/provider/type/fecha
- registra metadata en Postgres

Todavía falta, si se necesita después:

- exponer lectura/descarga explícita del snapshot
- decidir qué pantallas o endpoints consumen ese raw JSON

### 2. Metadata de snapshots en Postgres

Ya quedó agregado el modelo `SnapshotArtifact` con campos como:

- `company_id`
- `integration_id`
- `provider`
- `snapshot_type`
- `domain`
- `source`
- `status`
- `scan_id`
- `external_id`
- `s3_bucket`
- `s3_key`
- `content_type`
- `size_bytes`
- `checksum`
- `captured_at`
- `received_at`
- `summary_json`
- `created_at`

Ya hay endpoints para consultar metadata y descargar payload crudo.

### 3. Estrategia de migraciones DB

Ya existe un bootstrap inicial:

- `python scripts/bootstrap_schema.py`

Pero todavía falta decidir una estrategia formal de evolución de esquema a futuro:

- Alembic
- SQL versionado
- otro flujo controlado de migraciones

### 4. CORS y dominios reales

`serverless/stages/prod.yml` todavía tiene placeholder:

- `https://api.example.com`

Reemplazar por el origen real del frontend antes del deploy final.

## Contrato actual de snapshots

Persistencia base cerrada:

- raw JSON completo en S3
- metadata operativa en `snapshot_artifacts`
- escritura automática desde `POST /api/scans/ingest`
- lectura por usuario o por token `agent:invoke`

Contrato actual del artifact:

- `provider`: origen lógico del snapshot, por ejemplo `nessus`, `zabbix`, `wazuh`
- `snapshot_type`: tipo funcional del payload, por ejemplo `vulnerability`, `security_events`, `noc_health`
- `domain`: `soc` o `noc`
- `source`: origen interno del guardado, hoy `scan_ingest`
- `status`: estado del artifact, hoy `stored`
- `summary_json`: resumen ligero para listar sin descargar el raw completo

El helper reusable para futuros payloads pesados quedó en:

- `src/shared/snapshots.py`

## Qué no tocar

No migrar ni mezclar en esta fase:

- Voice
- Speech
- Live Voice
- WebSocket
- Foundry realtime
- MAD
- ETL

## Resumen operativo

Si lo vas a desplegar desde una MV, el flujo correcto es:

1. clonar repo
2. instalar dependencias
3. configurar AWS CLI
4. crear JWT secret en SSM
5. desplegar `network`
6. desplegar `data`
7. desplegar `storage`
8. desplegar backend `serverless`
9. correr smoke tests
10. recién después avanzar con snapshots S3 + metadata DB
