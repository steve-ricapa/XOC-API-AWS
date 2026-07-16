# XOC-API-AWS

Backend de XOC sobre AWS Lambda + API Gateway HTTP API + FastAPI.

## Estado oficial

La arquitectura objetivo y oficial es **multi-stack**, con rutas HTTP limpias **sin prefijo `/api`**.

Contrato oficial:

- `/health`
- `/auth/*`
- `/onboarding/tenant`
- `/tenant/*`
- `/users/*`
- `/tickets/*`
- `/chat/*`
- `/agents/*`
- `/integrations/*`
- `/scans/*`
- `/alerts/*`
- `/analytics/*`
- `/systems/*`
- `/vulnerabilities/*`
- `/admin/*`
- `/superadmin/*`

No se mantiene compatibilidad con:

- `/api/*`
- `company` / `company_id`
- `apiHttp`
- tickets SQL legacy

## Arquitectura actual

Stacks oficiales:

1. `xoc-api-shared`
2. `xoc-api-tickets`
3. `xoc-api-auth`
4. `xoc-api-chat`
5. `xoc-api-ops`
6. `xoc-api-tenant`
7. `xoc-api-admin`

Ownership:

- `xoc-api-shared`: HTTP API + authorizer
- `xoc-api-tickets`: DynamoDB tickets + EventBridge + Step Functions + `/tickets/*`
- `xoc-api-auth`: `/health`, `/auth/*`, `/onboarding/tenant`
- `xoc-api-chat`: `/chat/*`, `/agents/*`
- `xoc-api-ops`: scans, integrations, alerts, analytics, systems, vulnerabilities
- `xoc-api-tenant`: `/tenant/*`, `/users/*`, `/audit`
- `xoc-api-admin`: `/admin/*`, `/superadmin/*`

## Infraestructura prod

- RDS PostgreSQL privada
- S3 para snapshots
- VPC privada para Lambdas que usan DB/S3 privado
- DynamoDB para tickets
- EventBridge + Step Functions para workflow de tickets
- `jwtAuthorizer` fuera de VPC

Notas operativas de prod:

- La tabla de tickets activa PITR solo en `prod` para recuperación point-in-time.
- El workflow de Step Functions de tickets sigue siendo un placeholder V1: recibe eventos y no ejecuta todavía orquestación real.
- La red prod usa un solo NAT Gateway para dos AZ. Reduce costo, pero introduce dependencia de una sola AZ para salida a internet desde subnets privadas.
- El bucket de snapshots aplica lifecycle para controlar costos de objetos pesados y versiones antiguas.

## Topologia de stages

`dev`, `staging` y `prod` mantienen la misma topologia base:

- app tier en 1 AZ
- una subnet publica
- una subnet privada Lambda
- un NAT Gateway
- `jwtAuthorizer` fuera de VPC
- `tickets` fuera de VPC

Restriccion importante:

- RDS administrado en VPC sigue conservando dos subnets privadas de DB por compatibilidad con `DBSubnetGroup`.
- Eso no significa HA regional ni HA completa. Solo evita romper el despliegue de RDS.

Stacks de red esperados:

- `xoc-infra-network-dev`
- `xoc-infra-network-staging`
- `xoc-infra-network-prod`

## Endpoints oficiales

### Base

- `GET /health`

### Auth

- `POST /auth/register` (410)
- `POST /auth/login`
- `POST /auth/refresh`
- `POST /onboarding/tenant`

### Tenant

- `GET /tenant`
- `PUT /tenant`
- `GET /tenant/runtime-settings`
- `PUT /tenant/runtime-settings`
- `GET /tenant/agent-keys`
- `POST /tenant/agent-keys`
- `GET /tenant/agent-keys/{key_id}`
- `DELETE /tenant/agent-keys/{key_id}`
- `POST /tenant/agent-keys/{key_id}/regenerate`
- `POST /tenant/agent-keys/{key_id}/toggle`

### Users

- `GET /users`
- `GET /users/{user_id}`
- `POST /users`
- `PUT /users/{user_id}`
- `DELETE /users/{user_id}`

### Tickets

- `GET /tickets`
- `POST /tickets`
- `GET /tickets/{ticket_id}`
- `PUT /tickets/{ticket_id}`
- `DELETE /tickets/{ticket_id}`
- `PATCH /tickets/{ticket_id}/approve`
- `PATCH /tickets/{ticket_id}/reject`
- `PATCH /tickets/{ticket_id}/decision/select`

### Integrations

- `GET /integrations`
- `POST /integrations`
- `GET /integrations/dashboard/summary`
- `GET /integrations/zabbix/summary`
- `GET /integrations/zabbix/detailed`
- `GET /integrations/wazuh/summary`
- `GET /integrations/nessus/summary`
- `GET /integrations/uptime_kuma/summary`
- `GET /integrations/{integration_id}`
- `PUT /integrations/{integration_id}`
- `DELETE /integrations/{integration_id}`
- `GET /integrations/{integration_id}/credentials`

### Scans

- `POST /scans/ingest`
- `GET /scans`
- `GET /scans/latest`
- `GET /scans/summary`
- `GET /scans/{scan_summary_id}`
- `GET /scans/{scan_summary_id}/findings`
- `GET /scans/{scanner_type}/analytics`
- `GET /scans/snapshots`
- `GET /scans/snapshots/{artifact_id}`
- `GET /scans/snapshots/{artifact_id}/payload`
- `GET /scans/agent/snapshots`
- `GET /scans/agent/snapshots/{artifact_id}`
- `GET /scans/agent/snapshots/{artifact_id}/payload`
- `GET /scans/agent/summaries`
- `GET /scans/agent/summaries/{scan_summary_id}`
- `GET /scans/agent/summaries/{scan_summary_id}/findings`
- `GET /scans/agent/findings`

### Security Ops

- `GET /systems/status`
- `GET /systems`
- `GET /systems/{system_id}`
- `GET /alerts/active`
- `POST /alerts/{alert_id}/resolve`
- `GET /vulnerabilities`
- `GET /vulnerabilities/{vuln_id}`
- `POST /vulnerabilities/{vuln_id}/patch`
- `GET /analytics/incidents`
- `GET /analytics/response-time`
- `GET /analytics/vulnerability-distribution`
- `GET /analytics/summary`

### Admin

- `POST /admin/agent-instances`
- `GET /admin/agent-instances`
- `GET /admin/agent-instances/{instance_id}`
- `PATCH /admin/agent-instances/{instance_id}`
- `PATCH /admin/agent-instances/{instance_id}/status`
- `DELETE /admin/agent-instances/{instance_id}`

### Superadmin

- `GET /superadmin/tenants`
- `POST /superadmin/tenants`
- `GET /superadmin/tenants/{tenant_id}`
- `PATCH /superadmin/tenants/{tenant_id}`
- `GET /superadmin/users`
- `POST /superadmin/users`
- `GET /superadmin/integrations`
- `GET /superadmin/tickets`
- `GET /superadmin/chat/sessions`
- `GET /superadmin/audit-logs`
- `GET /superadmin/capability-templates`

### Agents

- `POST /agents/auth/token` (410)
- `POST /agents/auth/token-from-user`
- `GET /agents/instance/{instance_id}` (410)

### Audit

- `POST /audit`

### Chat

- `GET /chat/sessions`
- `GET /chat/history`
- `DELETE /chat/sessions/{session_id}`
- `POST /chat`

## Secrets y configuración

`prod` no debe llevar secretos en el repo.

Variables de stage:

- `jwtSecretKey`: usar solo en `dev`/`staging` demo
- `jwtSecretKeySsmPath`: usar en `prod`

Variables de entorno relevantes:

- `JWT_SECRET_KEY`
- `JWT_SECRET_KEY_SSM_PATH`
- `DATABASE_SECRET_ARN`
- `SNAPSHOTS_BUCKET_NAME`
- `EVENT_BUS_NAME`
- `TICKETS_TABLE_NAME`

## Deploy multi-stack

Secuencia completa por stage:

1. red
2. data
3. storage
4. `xoc-api-shared`
5. `xoc-api-tickets`
6. `xoc-api-auth`
7. `xoc-api-chat`
8. `xoc-api-tenant`
9. `xoc-api-admin`
10. `xoc-api-ops`

### Deploy `dev`

Infra base:

```bash
aws cloudformation deploy --stack-name xoc-infra-network-dev --template-file infra/network-dev.yml
aws cloudformation deploy --stack-name xoc-infra-data-dev --template-file infra/data-prod.yml
aws cloudformation deploy --stack-name xoc-infra-storage-dev --template-file infra/storage-prod.yml
```

Servicios:

```bash
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage dev --config serverless.shared.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage dev --config serverless.tickets.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage dev --config serverless.auth.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage dev --config serverless.chat.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage dev --config serverless.tenant.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage dev --config serverless.admin.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage dev --config serverless.ops.js
```

### Deploy `staging`

Infra base:

```bash
aws cloudformation deploy --stack-name xoc-infra-network-staging --template-file infra/network-staging.yml
aws cloudformation deploy --stack-name xoc-infra-data-staging --template-file infra/data-prod.yml
aws cloudformation deploy --stack-name xoc-infra-storage-staging --template-file infra/storage-prod.yml
```

Servicios:

```bash
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage staging --config serverless.shared.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage staging --config serverless.tickets.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage staging --config serverless.auth.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage staging --config serverless.chat.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage staging --config serverless.tenant.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage staging --config serverless.admin.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage staging --config serverless.ops.js
```

### Deploy `prod`

Infra base:

```bash
aws cloudformation deploy --stack-name xoc-infra-network-prod --template-file infra/network-prod.yml
aws cloudformation deploy --stack-name xoc-infra-data-prod --template-file infra/data-prod.yml
aws cloudformation deploy --stack-name xoc-infra-storage-prod --template-file infra/storage-prod.yml
```

Servicios:

```bash
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage prod --config serverless.shared.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage prod --config serverless.tickets.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage prod --config serverless.auth.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage prod --config serverless.chat.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage prod --config serverless.tenant.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage prod --config serverless.admin.js
NODE_OPTIONS="--max-old-space-size=4096" sls deploy --stage prod --config serverless.ops.js
```

### Notas de despliegue

- `shared` va primero porque crea el HTTP API y el authorizer compartido.
- `tickets` va segundo porque crea DynamoDB, EventBridge y Step Functions.
- `auth`, `chat`, `tenant`, `admin` y `ops` asumen VPC en todos los stages.
- Si `prod` usa JWT por SSM, el parámetro definido en `jwtSecretKeySsmPath` debe existir antes del deploy.

La guía detallada y notas operativas están en `MULTI_SERVICE_DEPLOY.md`.
