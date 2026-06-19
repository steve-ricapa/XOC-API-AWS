# XOC-API-AWS

Base serverless del backend core sobre AWS usando Serverless Framework, FastAPI y Mangum.

## Stack base

- API Gateway HTTP API
- AWS Lambda (Python 3.12)
- FastAPI + Mangum
- Lambda Authorizer para JWT propio
- PostgreSQL vía RDS Proxy/DB URL externa
- EventBridge custom bus base

## Estructura

```text
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

## Estado actual

- una Lambda HTTP principal con `FastAPI + Mangum`
- `jwtAuthorizer` separado
- CORS configurado en FastAPI
- sesion DB por request
- endpoint real `GET /health`
- skeleton routers:
  - `/api/tickets/*`
- logica funcional ya migrada para:
  - `/api/auth/*`
  - `/api/onboarding/tenant`
  - `/api/companies/*` de esta fase
  - `/api/users/*`
  - `/api/tickets/*`
- modulo base `src/shared/events.py` listo para EventBridge

## Reglas aplicadas

- no Flask
- no `db.create_all()`
- no Uvicorn/Gunicorn en Lambda
- `uvicorn` solo para desarrollo local
- `/docs` y `/openapi.json` habilitados fuera de `prod`; deshabilitados en `prod`
- `jwtAuthorizer` valida, FastAPI solo consume claims/context

## Modelos base ya presentes

- `Company`
- `User`
- `AuditLog`
- `CompanyRuntimeSettings`

## Variables esperadas

Se resuelven por stage desde SSM / Secrets Manager:

- `JWT_SECRET_KEY_SSM_PATH`
- `DATABASE_SECRET_ARN`
- `DATABASE_URL_SSM_PATH`

Fallback local opcional:

- `JWT_SECRET_KEY`
- `DATABASE_URL`

## Comandos

```bash
npm install
pip install -r requirements.txt
npm run dev
serverless package --stage dev
serverless deploy --stage dev
serverless invoke local --function apiHttp
serverless logs --function apiHttp --stage dev
```

## Local

FastAPI local:

```bash
uvicorn src.handlers.api:app --reload --host 0.0.0.0 --port 8000
```

Health local:

```bash
GET http://localhost:8000/health
```

## Deploy serverless

```bash
serverless deploy --stage dev
serverless deploy --stage staging
serverless deploy --stage prod
```

## Endpoint actual

- `GET /health`

## Endpoints ya migrados

- `POST /api/auth/register` (410 legacy disabled)
- `POST /api/auth/login`
- `POST /api/auth/refresh`
- `POST /api/onboarding/tenant`
- `GET /api/companies`
- `GET /api/companies/{company_id}`
- `PUT /api/companies/{company_id}`
- `GET /api/companies/{company_id}/runtime-settings`
- `PUT /api/companies/{company_id}/runtime-settings`
- `GET /api/users`
- `GET /api/users/{user_id}`
- `POST /api/users`
- `PUT /api/users/{user_id}`
- `DELETE /api/users/{user_id}`
- `GET /api/tickets`
- `POST /api/tickets`
- `GET /api/tickets/{ticket_id}`
- `PUT /api/tickets/{ticket_id}`
- `DELETE /api/tickets/{ticket_id}`
- `PATCH /api/tickets/{ticket_id}/approve`
- `PATCH /api/tickets/{ticket_id}/reject`
- `PATCH /api/tickets/{ticket_id}/decision/select`
- `POST /api/tickets/agent-create`
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
- `POST /api/scans/ingest`
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

## Pendiente

- `admin`
- `superadmin`

Responde estado del servicio, stage y disponibilidad de DB.
