## Frontend API Consumption Guide

Base URL de producción: `https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com`

Este documento resume qué endpoint debe consumir cada pantalla y qué recomendaciones seguir para cerrar la integración frontend sin ambigüedad.

### Pantallas y endpoints

| Pantalla / flujo | Endpoint principal | Notas |
|---|---|---|
| Login | `POST /auth/login` | Devuelve `access_token`, `refresh_token` y `user` |
| Refresh de sesión | `POST /auth/refresh` | Usar antes de expirar o ante `401` |
| Home principal | `GET /dashboard/home` | Fuente principal para overview del tenant |
| Pantalla por integración | `GET /dashboard/providers/{provider}` | No usar `GET /integrations/{integrationId}` para esta pantalla |
| Configuración de integraciones | `GET /integrations` | Lista de integraciones configuradas |
| Crear integración | `POST /integrations` | Admin |
| Editar integración | `PUT /integrations/{integrationId}` | Admin |
| Eliminar integración | `DELETE /integrations/{integrationId}` | Admin |
| Ver integración puntual | `GET /integrations/{integrationId}` | Config/admin, no dashboard |
| Credenciales de integración | `GET /integrations/{integrationId}/credentials` | Sensible, Admin solamente |
| Lista de scans | `GET /scans` | Soporta filtros |
| Detalle de scan | `GET /scans/{scanSummaryId}` | Cabecera/resumen del scan |
| Findings de un scan | `GET /scans/{scanSummaryId}/findings` | Tabla de hallazgos |
| Detalle de finding | `GET /findings/{findingId}` | Endpoint nuevo para navegación puntual |
| Findings cross-scan para agentes | `GET /scans/agent/findings` | Flujo agent JWT |
| Vulnerabilidades | `GET /vulnerabilities` | Lista separada de `FindingIndex` |
| Detalle de vulnerabilidad | `GET /vulnerabilities/{vulnId}` | No asumir que equivale a finding |
| Alertas activas | `GET /alerts/active` | NOC / monitoring |
| Resolver alerta | `POST /alerts/{alertId}/resolve` | Acción operacional |
| Usuarios | `GET /users` | Tenant scoped |
| Tenant settings | `GET /tenant`, `PUT /tenant` | Admin para update |
| Agent API keys | `/tenant/agent-keys/*` | Crear, listar, regenerar, toggle, borrar |
| Reports | `POST /reports`, `GET /reports`, `GET /reports/{reportId}` | Flujo asíncrono |

### Reglas de consumo recomendadas

1. Separar configuración de operación.

- Usar `/dashboard/*` para pantallas operativas.
- Usar `/integrations/*` para CRUD/configuración.
- Evitar mezclar ambos contratos en una misma vista.

2. Navegar hallazgos siempre por `findingId`.

- Las tablas de `recent_findings` y findings por scan deben navegar usando `id`.
- El detalle oficial es `GET /findings/{findingId}`.
- No reconstruir el hallazgo desde `scan_id + cve + host`.

3. No asumir que `finding` y `vulnerability` son lo mismo.

- `FindingIndex` es el índice operativo de hallazgos.
- `Vulnerability` es otra entidad con su propio lifecycle.
- Usar `/findings/{findingId}` para detalle de hallazgo.
- Usar `/vulnerabilities/{vulnId}` para detalle de vulnerabilidad.

4. Tratar `credentials` como flujo sensible.

- Si la UI no necesita mostrar credenciales en claro, no consumir `GET /integrations/{integrationId}/credentials`.
- Preferir formularios de replace/update sin re-hidratar secretos completos.
- Si se consume, restringir la pantalla a admins y no persistir esos valores en cache local.

5. Hacer polling explícito para reports.

- `POST /reports` responde `202`, no bloquea hasta generar el archivo.
- Poll recomendado: `GET /reports/{reportId}` cada 3-5 segundos.
- Cortar polling en `COMPLETED` o `FAILED`.

6. Manejar `401` y `403` distinto.

- `401`: refrescar token o forzar login.
- `403`: mostrar falta de permisos, no reintentar.

7. Evitar depender de campos ambiguos.

- En findings del dashboard usar:
  - `id`
  - `domain`
  - `scan_id`
  - `scan_summary_soc_id`
  - `scan_summary_noc_id`
- En frontend, esos campos bastan para deep-link y troubleshooting.

### Recomendaciones de implementación frontend

1. Crear un cliente API por dominio.

- `authApi`
- `dashboardApi`
- `integrationsApi`
- `scansApi`
- `findingsApi`
- `reportsApi`

2. Centralizar refresh de token.

- Interceptor único para `401`.
- Reintentar una sola vez tras `POST /auth/refresh`.

3. Normalizar modelos UI.

- Mantener adaptación a camelCase dentro del frontend si hace falta.
- No pedir al backend mezcla de formatos por pantalla.

4. No usar `GET /integrations/dashboard/summary` como reemplazo del home nuevo.

- El home oficial es `GET /dashboard/home`.
- `GET /integrations/dashboard/summary` puede quedar como compat o bloque auxiliar, pero no como contrato principal nuevo.

5. Mantener fallback de navegación.

- Si una tabla trae `finding.id`, navegar a detalle.
- Si no lo trae, tratarlo como bug de contrato y no inventar lookup secundario en frontend.

### Endpoints especialmente importantes para cerrar frontend

- `GET /dashboard/home`
- `GET /dashboard/providers/{provider}`
- `GET /scans/{scanSummaryId}/findings`
- `GET /findings/{findingId}`
- `GET /vulnerabilities/{vulnId}`
- `GET /integrations`
- `POST /reports`
- `GET /reports/{reportId}`

### Estado actual

El backend ya expone los endpoints necesarios para:

- home principal
- pantallas por integración
- listas de findings
- detalle de finding
- reports asíncronos
- gestión de integraciones
- credenciales de integración
- agent API keys

Lo que queda como decisión funcional es solo cuánto del flujo sensible de credenciales quiere mostrar realmente el frontend.
