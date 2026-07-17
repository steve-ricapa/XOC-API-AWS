# Guía de consumo de la API XOC

**Base URL (producción):** `https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com`

---

## Autenticación

La API usa **JSON Web Tokens (JWT)** con algoritmo **HS256**. Los tokens se obtienen mediante `/auth/login` y deben enviarse en el header `Authorization: Bearer <token>`.

### Tipos de token

| Token    | Duración | Propósito |
|----------|----------|-----------|
| Access   | 5 minutos | Autenticar requests protegidos |
| Refresh  | 30 días   | Obtener un nuevo access token |

### Claims del Access Token

```json
{
  "sub": "1",
  "type": "access",
  "iat": 1700000000,
  "exp": 1700000300,
  "tenant_id": 4,
  "role": "ADMIN"
}
```

### Refresh Token

```json
{
  "sub": "1",
  "type": "refresh",
  "iat": 1700000000,
  "exp": 1702598300
}
```

---

## Flujo de autenticación

### 1. Login

```
POST /auth/login
Content-Type: application/json

{
  "email": "admin@xoc.com",
  "password": "Admin123!"
}
```

Respuesta exitosa (200):

```json
{
  "message": "Login successful",
  "user": {
    "id": 1,
    "tenant_id": 4,
    "username": "admin",
    "email": "admin@xoc.com",
    "role": "ADMIN",
    "created_at": "2026-06-20T22:49:46.291882",
    "tenant": {
      "id": 4,
      "name": "DemoCorp",
      "plan_status": "active"
    }
  },
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs..."
}
```

Error (401):

```json
{
  "error": "Invalid email or password",
  "code": "unauthorized"
}
```

### 2. Usar el token en requests protegidos

```
GET /tenant
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

### 3. Refrescar el access token

```
POST /auth/refresh
Content-Type: application/json
Authorization: Bearer <refresh_token>

{
  "refresh_token": "eyJhbGciOiJIUzI1NiIs..."
}
```

> **Nota:** El refresh token se envía tanto en el header `Authorization` como en el body `refresh_token`.

Respuesta (200):

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs..."
}
```

### 4. Onboarding de nuevo tenant

```
POST /onboarding/tenant
Content-Type: application/json

{
  "name": "MiEmpresa",
  "admin_email": "admin@miempresa.com",
  "admin_password": "SecurePass123!",
  "admin_username": "admin"
}
```

- Si `public_registration_enabled = true`: abierto al público.
- Si `public_registration_enabled = false`: requiere token SUPERADMIN.

Respuesta (201):

```json
{
  "success": true,
  "message": "Tenant and admin user created successfully",
  "tenant": { "id": 5, "name": "MiEmpresa", "plan_status": "ACTIVE" },
  "owner_user": { "id": 2, "email": "admin@miempresa.com", "role": "ADMIN" },
  "access_token": "...",
  "refresh_token": "..."
}
```

---

## Niveles de acceso

| Nivel | Header requerido | Descripción |
|-------|-----------------|-------------|
| **PÚBLICO** | Ninguno | Sin autenticación |
| **PROTEGIDO** | `Authorization: Bearer <access_token>` | JWT válido |
| **ADMIN** | `Authorization: Bearer <access_token>` | JWT válido + role=ADMIN |
| **SUPERADMIN** | `Authorization: Bearer <access_token>` | JWT válido + role=SUPERADMIN |
| **SUPERADMIN + Confirm** | `Authorization: Bearer <access_token>` + `X-Superadmin-Confirm: true` | Operaciones sensibles |

---

## Endpoints

### Health

| Método | Path        | Auth    | Descripción                          |
|--------|-------------|---------|--------------------------------------|
| GET    | `/health`   | PÚBLICO | Estado del servicio y base de datos  |

Respuesta:

```json
{
  "status": "healthy",
  "service": "xoc-api",
  "stage": "prod",
  "database": "available"
}
```

---

### Auth

| Método | Path                | Auth    | Descripción                           |
|--------|---------------------|---------|---------------------------------------|
| POST   | `/auth/login`       | PÚBLICO | Login con email y password            |
| POST   | `/auth/refresh`     | PROT    | Refrescar access token                |
| POST   | `/auth/register`    | PÚBLICO | **DEPRECADO** (410 Gone)              |
| POST   | `/onboarding/tenant`| PÚBLICO*| Crear tenant + admin                  |

---

### Tenant

| Método | Path                            | Auth    | Descripción                           |
|--------|---------------------------------|---------|---------------------------------------|
| GET    | `/tenant`                       | PROT    | Listar tenant(s) del usuario actual   |
| PUT    | `/tenant`                       | ADMIN   | Actualizar nombre del tenant          |
| GET    | `/tenant/agent-keys`            | ADMIN   | Listar API keys de agentes            |
| POST   | `/tenant/agent-keys`            | ADMIN   | Crear API key de agente               |
| GET    | `/tenant/agent-keys/{keyId}`    | ADMIN   | Obtener API key específica            |
| DELETE | `/tenant/agent-keys/{keyId}`    | ADMIN   | Eliminar API key                      |
| POST   | `/tenant/agent-keys/{keyId}/regenerate` | ADMIN | Regenerar API key              |
| POST   | `/tenant/agent-keys/{keyId}/toggle`     | ADMIN | Activar/desactivar API key     |
| GET    | `/tenant/runtime-settings`      | ADMIN   | Obtener runtime settings              |
| PUT    | `/tenant/runtime-settings`      | ADMIN   | Crear/actualizar runtime settings     |

Ejemplo: Crear API key de agente

```
POST /tenant/agent-keys
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "Scanner-Nessus",
  "integration_type": "NESSUS"
}
```

Respuesta (201):

```json
{
  "id": 10,
  "name": "Scanner-Nessus",
  "integration_type": "NESSUS",
  "is_active": true,
  "key_preview": "xoc_ak_ab12...",
  "created_at": "2026-07-16T12:00:00"
}
```

---

### Users

| Método | Path                 | Auth  | Descripción                            |
|--------|----------------------|-------|----------------------------------------|
| GET    | `/users`             | PROT  | Listar usuarios del tenant             |
| POST   | `/users`             | ADMIN | Crear usuario                          |
| GET    | `/users/{userId}`    | PROT  | Obtener usuario por ID                 |
| PUT    | `/users/{userId}`    | PROT  | Actualizar email, password o role      |
| DELETE | `/users/{userId}`    | ADMIN | Eliminar usuario                       |

Ejemplo: Crear usuario

```
POST /users
Authorization: Bearer <token>
Content-Type: application/json

{
  "email": "operador@miempresa.com",
  "username": "operador1",
  "password": "OperadorPass123!",
  "role": "USER"
}
```

---

### Tickets

| Método | Path                                  | Auth | Descripción                              |
|--------|---------------------------------------|------|------------------------------------------|
| GET    | `/tickets`                            | PROT | Listar tickets (filtro opcional `?status=`) |
| POST   | `/tickets`                            | PROT | Crear ticket                             |
| GET    | `/tickets/{ticketId}`                 | PROT | Obtener ticket                           |
| PUT    | `/tickets/{ticketId}`                 | PROT | Actualizar ticket                        |
| DELETE | `/tickets/{ticketId}`                 | PROT | Eliminar ticket                          |
| PATCH  | `/tickets/{ticketId}/approve`         | PROT | Aprobar ticket (requiere PREAPROBADO)    |
| PATCH  | `/tickets/{ticketId}/reject`          | PROT | Rechazar ticket (requiere PREAPROBADO)   |
| PATCH  | `/tickets/{ticketId}/decision/select` | PROT | Seleccionar opción de decisión pendiente |

Ejemplo: Crear ticket

```
POST /tickets
Authorization: Bearer <token>
Content-Type: application/json

{
  "subject": "Vulnerabilidad crítica en servidor web",
  "description": "Se detectó CVE-2024-XXXX en servidor 10.0.0.50",
  "severity": "CRITICAL"
}
```

---

### Scans

| Método | Path                                        | Auth | Descripción                              |
|--------|---------------------------------------------|------|------------------------------------------|
| GET    | `/scans`                                    | PROT | Listar scans (filtros: scanner_type, status, domain, date range) |
| GET    | `/scans/latest`                             | PROT | Último scan por target con totales       |
| GET    | `/scans/summary`                            | PROT | Dashboard: totales, tendencia 7 días     |
| POST   | `/scans/ingest`                             | PÚBL*| Ingestar resultados de scan (vía API key)|
| GET    | `/scans/{scanSummaryId}`                    | PROT | Obtener scan por ID                      |
| GET    | `/scans/{scanSummaryId}/findings`           | PROT | Hallazgos de un scan                     |
| GET    | `/findings/{findingId}`                     | PROT | Detalle de un hallazgo                   |
| GET    | `/scans/{scannerType}/analytics`            | PROT | Analíticas por tipo de scanner           |
| GET    | `/scans/snapshots`                          | PROT | Listar snapshots                         |
| GET    | `/scans/snapshots/{artifactId}`             | PROT | Obtener metadata de snapshot             |
| GET    | `/scans/snapshots/{artifactId}/payload`     | PROT | Obtener snapshot + payload desde S3      |

* `/scans/ingest` es de ruta pública pero valida API key + idempotency key internamente.

Ejemplo: Obtener resumen de scans

```json
GET /scans/summary
Authorization: Bearer <token>

{
  "period_days": 30,
  "total_scans": 0,
  "vulnerability_totals": {
    "critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0
  },
  "by_scanner": {},
  "latest_scans": [],
  "trend_7_days": []
}
```

#### Endpoints para agentes (scope agent JWT)

| Método | Path                                                | Auth       | Descripción                          |
|--------|-----------------------------------------------------|------------|--------------------------------------|
| GET    | `/scans/agent/snapshots`                            | Agent JWT  | Listar snapshots del tenant          |
| GET    | `/scans/agent/snapshots/{artifactId}`               | Agent JWT  | Obtener snapshot                     |
| GET    | `/scans/agent/snapshots/{artifactId}/payload`       | Agent JWT  | Obtener snapshot + payload           |
| GET    | `/scans/agent/summaries`                            | Agent JWT  | Listar summaries                     |
| GET    | `/scans/agent/summaries/{scanSummaryId}`            | Agent JWT  | Obtener summary                      |
| GET    | `/scans/agent/summaries/{scanSummaryId}/findings`   | Agent JWT  | Hallazgos (filtro: severity, cve, host) |
| GET    | `/scans/agent/findings`                             | Agent JWT  | Hallazgos entre scans (con filtros)  |

---

### Integrations

| Método | Path                                        | Auth  | Descripción                              |
|--------|---------------------------------------------|-------|------------------------------------------|
| GET    | `/integrations`                             | PROT  | Listar integraciones del tenant          |
| POST   | `/integrations`                             | ADMIN | Crear integración                        |
| GET    | `/integrations/{integrationId}`             | PROT  | Obtener integración                      |
| PUT    | `/integrations/{integrationId}`             | ADMIN | Actualizar integración                   |
| DELETE | `/integrations/{integrationId}`             | ADMIN | Eliminar integración                     |
| GET    | `/integrations/{integrationId}/credentials` | ADMIN | Ver credenciales (descifradas)           |
| GET    | `/integrations/dashboard/summary`           | PROT  | Resumen de dashboard                     |
| GET    | `/integrations/zabbix/summary`              | PROT  | Resumen Zabbix                           |
| GET    | `/integrations/zabbix/detailed`             | PROT  | Métricas detalladas Zabbix               |
| GET    | `/integrations/wazuh/summary`               | PROT  | Resumen Wazuh                            |
| GET    | `/integrations/nessus/summary`              | PROT  | Resumen Nessus                           |
| GET    | `/integrations/uptime_kuma/summary`         | PROT  | Resumen Uptime Kuma                      |

---

### Dashboard

| Método | Path                               | Auth | Descripción                                      |
|--------|------------------------------------|------|--------------------------------------------------|
| GET    | `/dashboard/home`                  | PROT | Pantalla principal consolidada del tenant        |
| GET    | `/dashboard/providers/{provider}`  | PROT | Pantalla por proveedor/integración operativa     |

Notas:

- `provider` soportado: `openvas`, `insightvm`, `nessus`, `wazuh`, `zabbix`, `uptime_kuma`.
- `GET /dashboard/providers/{provider}` acepta `preset`, `from`, `to` para filtros temporales.
- `recent_findings` ya incluye `id`, `domain`, `scan_id`, `scan_summary_soc_id`, `scan_summary_noc_id` para navegación del frontend.

---

### Findings

| Método | Path                     | Auth | Descripción                          |
|--------|--------------------------|------|--------------------------------------|
| GET    | `/findings/{findingId}`  | PROT | Obtener detalle puntual de hallazgo  |

---

### Alerts

| Método | Path                      | Auth | Descripción                          |
|--------|---------------------------|------|--------------------------------------|
| GET    | `/alerts/active`          | PROT | Alertas activas (filtro `?since=`)   |
| POST   | `/alerts/{alertId}/resolve` | PROT | Resolver alerta                    |

---

### Analytics

| Método | Path                                         | Auth | Descripción                          |
|--------|----------------------------------------------|------|--------------------------------------|
| GET    | `/analytics/incidents`                       | PROT | Conteo de incidentes por severidad   |
| GET    | `/analytics/response-time`                   | PROT | Tiempo promedio de respuesta         |
| GET    | `/analytics/vulnerability-distribution`      | PROT | Distribución de vulnerabilidades     |
| GET    | `/analytics/summary`                         | PROT | Resumen: alertas, vulns, tickets     |

---

### Systems

| Método | Path                 | Auth | Descripción                          |
|--------|----------------------|------|--------------------------------------|
| GET    | `/systems`           | PROT | Listar sistemas del tenant           |
| GET    | `/systems/status`    | PROT | Estado resumido de sistemas          |
| GET    | `/systems/{systemId}`| PROT | Obtener sistema por ID               |

---

### Vulnerabilities

| Método | Path                             | Auth | Descripción                          |
|--------|----------------------------------|------|--------------------------------------|
| GET    | `/vulnerabilities`               | PROT | Listar vulnerabilidades (filtros: status, severity) |
| GET    | `/vulnerabilities/{vulnId}`      | PROT | Obtener vulnerabilidad               |
| POST   | `/vulnerabilities/{vulnId}/patch`| PROT | Iniciar parche (status → "patching") |

---

### Reports

| Método | Path                    | Auth | Descripción                                  |
|--------|-------------------------|------|----------------------------------------------|
| POST   | `/reports`              | PROT | Solicitar generación asíncrona de reporte    |
| GET    | `/reports`              | PROT | Listar reportes del tenant                   |
| GET    | `/reports/{reportId}`   | PROT | Consultar estado y obtener URL de descarga   |

Respuesta esperada:

- `POST /reports` responde `202` con `reportId` y `status=PENDING`.
- `GET /reports/{reportId}` devuelve `PENDING`, `PROCESSING`, `COMPLETED` o `FAILED`.
- En `COMPLETED`, incluye `downloadUrl` temporal.

---

### Chat

| Método | Path                         | Auth | Descripción                          |
|--------|------------------------------|------|--------------------------------------|
| POST   | `/chat`                      | PROT | Enviar mensaje al agente SOPHIA      |
| GET    | `/chat/sessions`             | PROT | Listar sesiones del usuario          |
| GET    | `/chat/history`              | PROT | Historial de conversación            |
| DELETE | `/chat/sessions/{sessionId}` | PROT | Eliminar sesión                      |

---

### Agents

| Método | Path                                       | Auth | Descripción                          |
|--------|--------------------------------------------|------|--------------------------------------|
| POST   | `/agents/auth/token-from-user`             | PROT | Intercambia JWT de usuario por token de agente (scope `agent:invoke`) |

---

### Admin (tenant-scoped)

| Método | Path                                            | Auth    | Descripción                          |
|--------|-------------------------------------------------|---------|--------------------------------------|
| POST   | `/admin/agent-instances`                        | ADMIN   | Crear instancia de agente            |
| GET    | `/admin/agent-instances`                        | ADMIN   | Listar instancias del tenant         |
| GET    | `/admin/agent-instances/{instanceId}`           | ADMIN   | Obtener instancia                    |
| PATCH  | `/admin/agent-instances/{instanceId}`           | ADMIN   | Actualizar instancia                 |
| PATCH  | `/admin/agent-instances/{instanceId}/status`    | ADMIN   | Cambiar estado (ACTIVE/TO_PROVISION/DISABLED) |
| DELETE | `/admin/agent-instances/{instanceId}`           | ADMIN   | Eliminar (soft-delete) instancia     |

---

### Superadmin

#### Tenants

| Método | Path                                               | Auth         | Descripción                              |
|--------|----------------------------------------------------|--------------|------------------------------------------|
| GET    | `/superadmin/tenants`                              | SUPERADMIN   | Listar tenants (filtros: search, dates, paginación) |
| POST   | `/superadmin/tenants`                              | SUPERADMIN   | Crear tenant                             |
| GET    | `/superadmin/tenants/{tenantId}`                   | SUPERADMIN   | Detalle del tenant con conteos           |
| PATCH  | `/superadmin/tenants/{tenantId}`                   | SUPERADMIN   | Actualizar tenant                        |
| GET    | `/superadmin/tenants/{tenantId}/integrations`      | SUPERADMIN   | Integraciones + capabilities del tenant  |
| GET    | `/superadmin/tenants/{tenantId}/capabilities`      | SUPERADMIN   | Capacidades efectivas del tenant         |
| GET    | `/superadmin/tenants/{tenantId}/capability-templates` | SUPERADMIN | Templates asignados al tenant           |

#### Users

| Método | Path                                               | Auth         | Descripción                              |
|--------|----------------------------------------------------|--------------|------------------------------------------|
| GET    | `/superadmin/users`                                | SUPERADMIN   | Listar usuarios (filtros: tenant, role)  |
| POST   | `/superadmin/users`                                | SUPERADMIN   | Crear usuario en cualquier tenant        |
| GET    | `/superadmin/users/{userId}`                       | SUPERADMIN   | Obtener usuario                          |
| PATCH  | `/superadmin/users/{userId}`                       | SUPERADMIN   | Actualizar usuario                       |
| POST   | `/superadmin/users/{userId}/password-reset`        | SUPERADMIN*  | Resetear password (requiere header confirmación) |

#### Integrations

| Método | Path                                               | Auth         | Descripción                              |
|--------|----------------------------------------------------|--------------|------------------------------------------|
| GET    | `/superadmin/integrations`                         | SUPERADMIN   | Listar integraciones (filtros)           |
| GET    | `/superadmin/integrations/{integrationId}`         | SUPERADMIN   | Obtener integración                      |
| PATCH  | `/superadmin/integrations/{integrationId}`         | SUPERADMIN   | Actualizar integración                   |
| GET    | `/superadmin/integrations/{integrationId}/credentials` | SUPERADMIN* | Ver credenciales (requiere confirmación) |
| POST   | `/superadmin/integrations/{integrationId}/credentials` | SUPERADMIN* | Guardar credenciales cifradas           |

\* Requiere header `X-Superadmin-Confirm: true`.

#### Agent Instances

| Método | Path                                               | Auth         | Descripción                              |
|--------|----------------------------------------------------|--------------|------------------------------------------|
| GET    | `/superadmin/agent-instances`                      | SUPERADMIN   | Listar instancias (todas las cuentas)    |
| GET    | `/superadmin/agent-instances/{instanceId}`         | SUPERADMIN   | Obtener instancia (cross-tenant)         |
| PATCH  | `/superadmin/agent-instances/{instanceId}`         | SUPERADMIN   | Actualizar instancia                     |

#### Tickets

| Método | Path                                               | Auth         | Descripción                              |
|--------|----------------------------------------------------|--------------|------------------------------------------|
| GET    | `/superadmin/tickets`                              | SUPERADMIN   | Listar tickets (todas las cuentas)       |
| GET    | `/superadmin/tickets/{ticketId}`                   | SUPERADMIN   | Obtener ticket                           |
| PATCH  | `/superadmin/tickets/{ticketId}`                   | SUPERADMIN   | Actualizar estado del ticket             |

#### Chat

| Método | Path                                               | Auth         | Descripción                              |
|--------|----------------------------------------------------|--------------|------------------------------------------|
| GET    | `/superadmin/chat/sessions`                        | SUPERADMIN   | Listar sesiones de chat                  |
| GET    | `/superadmin/chat/sessions/{sessionId}`            | SUPERADMIN   | Obtener sesión                           |
| DELETE | `/superadmin/chat/sessions/{sessionId}`            | SUPERADMIN   | Eliminar sesión                          |
| GET    | `/superadmin/chat/history`                         | SUPERADMIN   | Historial de chat (proxied a SOPHIA)     |

#### Audit Logs

| Método | Path                                               | Auth         | Descripción                              |
|--------|----------------------------------------------------|--------------|------------------------------------------|
| GET    | `/superadmin/audit-logs`                           | SUPERADMIN   | Logs de auditoría (filtros, paginación)  |

#### Capability Templates

| Método | Path                                                         | Auth         | Descripción                              |
|--------|--------------------------------------------------------------|--------------|------------------------------------------|
| GET    | `/superadmin/capability-templates`                           | SUPERADMIN   | Listar templates                         |
| GET    | `/superadmin/capability-templates/{templateId}`              | SUPERADMIN   | Obtener template                         |
| POST   | `/superadmin/capability-templates`                           | SUPERADMIN   | Crear template                           |
| PATCH  | `/superadmin/capability-templates/{templateId}`              | SUPERADMIN   | Actualizar template                      |
| DELETE | `/superadmin/capability-templates/{templateId}`              | SUPERADMIN   | Eliminar template                        |
| GET    | `/superadmin/capability-templates/{templateId}/tenants`      | SUPERADMIN   | Tenants asignados al template            |
| PUT    | `/superadmin/capability-templates/{templateId}/tenants`      | SUPERADMIN   | Reemplazar asignaciones de tenants       |

---

## Auditoría

Cada operación relevante registra un log de auditoría. El endpoint para consulta manual es:

```
POST /audit
Authorization: Bearer <token>  (opcional)
Content-Type: application/json

{
  "action": "CUSTOM_EVENT",
  "entity_type": "TICKET",
  "entity_id": "123",
  "payload": { "detalle": "valor" }
}
```

---

## Formato de errores

Todos los errores siguen el mismo formato:

```json
{
  "error": "Descripción del error",
  "code": "error_code"
}
```

| Código                  | HTTP | Significado                              |
|-------------------------|------|------------------------------------------|
| `validation_error`      | 422  | Datos de entrada inválidos               |
| `unauthorized`          | 401  | Credenciales inválidas                   |
| `forbidden`             | 403  | No tiene permisos suficientes            |
| `conflict`              | 409  | Recurso duplicado (ej. email existente)  |
| `not_found`             | 404  | Recurso no encontrado                    |
| `internal_error`        | 500  | Error interno del servidor               |
| `endpoint_deprecated`   | 410  | Endpoint deshabilitado                   |

---

## Ejemplo completo: curl

```bash
# 1. Login
TOKEN=$(curl -s -X POST https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@xoc.com","password":"Admin123!"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. Consultar tenant
curl -s https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com/tenant \
  -H "Authorization: Bearer $TOKEN"

# 3. Listar tickets
curl -s https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com/tickets \
  -H "Authorization: Bearer $TOKEN"

# 4. Crear ticket
curl -s -X POST https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com/tickets \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"subject":"Test ticket","severity":"MEDIUM"}'
```

---

## Ejemplo completo: Python

```python
import requests

BASE = "https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com"

# Login
resp = requests.post(f"{BASE}/auth/login", json={
    "email": "admin@xoc.com",
    "password": "Admin123!",
})
data = resp.json()
access_token = data["access_token"]
refresh_token = data["refresh_token"]

headers = {"Authorization": f"Bearer {access_token}"}

# Consultar tenant
resp = requests.get(f"{BASE}/tenant", headers=headers)
print(resp.json())

# Refrescar token
resp = requests.post(f"{BASE}/auth/refresh",
    headers={"Authorization": f"Bearer {refresh_token}"},
    json={"refresh_token": refresh_token},
)
new_tokens = resp.json()
print(new_tokens["access_token"])
```

---

## Notas importantes

1. **Tokens expiran rápido**: El access token dura solo 5 minutos. Usa el refresh token para obtener uno nuevo antes de que expire.
2. **Paginación**: Endpoints que devuelven listas soportan `?limit=N&offset=N` o `?page=N&per_page=N` según el endpoint.
3. **Filtros**: Endpoints de listas soportan filtros por query params (`?status=active&severity=high`).
4. **Idempotencia**: El endpoint `/scans/ingest` requiere `idempotency_key` para evitar duplicados.
5. **CORS**: La API acepta orígenes configurados por tenant. Por defecto permite cualquier origen en desarrollo.
