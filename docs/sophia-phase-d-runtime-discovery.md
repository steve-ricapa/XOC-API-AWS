# SOPHIA — Fase D: descubrimiento y gate de código fuente

> Registro histórico de la inspección del 10-sep. El 11-sep el responsable confirmó
> `XOC_MICROSERVICIO_AGENTES/main` como fuente y se implementó D sólo en local.
> Estado y entregable actuales: [sophia-phase-d-local.md](sophia-phase-d-local.md).
> Lo que sigue conserva los hallazgos y bloqueos que existían antes de recibir el repo.

Fecha: 2026-09-10.
Resultado: **DETENIDA antes de implementar: fuente de verdad del runtime externo no identificada/disponible.**

## 0. Precondición de smoke

El usuario confirmó después del último despliegue: «Si bro, todo bien», respondiendo
a la prueba de conversación nueva, continuación, reapertura/historial y conversación
previa. Se acepta esa confirmación como smoke funcional de Chat aprobado por el usuario.
No equivale a una nueva prueba automatizada ni acredita por sí sola toda la matriz
de seguridad multi-tenant de Fase D. No se encontró un fallo funcional reportado en ese smoke.

La inspección de esta tarea fue de sólo lectura, local y mediante SSH a la EC2
autorizada. No se invocó SOPHIA, no se consultó RDS, no se leyeron valores de secretos,
no se autenticó como usuario y no se modificó infraestructura remota.

## 1. Runtime identificado y lo que falta

Se siguió el código real `src/handlers/routes/chat.py` y se comprobó la configuración
efectiva de la Lambda `xoc-api-chat-prod-chatAgentsApi`, cuenta `811776156524`,
región `us-east-1`.

Configuración global efectiva, coincidente con `serverless/stages/prod.yml`:

```text
AGENTS_FUNCTION_BASE_URL:
https://prod-a-xoc-cons-g9hvargsfqeafeap.centralus-01.azurewebsites.net

AGENTS_FUNCTION_ROUTE_SOPHIA:
/api/agents/SophiaDurableAgent/run

AGENTS_FUNCTION_ROUTE_SOPHIA_HISTORY:
/api/agents/SophiaDurableAgent/history

AGENTS_FUNCTION_ROUTE_SOPHIA_DELETE:
/api/agents/SophiaDurableAgent/threads
```

Destino global construido para Chat:

```text
POST https://prod-a-xoc-cons-g9hvargsfqeafeap.centralus-01.azurewebsites.net/api/agents/SophiaDurableAgent/run
```

El hostname apunta a alojamiento Azure, y las rutas lo identifican como
`SophiaDurableAgent`. No se tiene evidencia del recurso Azure exacto, tipo de hosting,
suscripción, implementación interna o pipeline. No se deduce el nombre del repositorio
del nombre del endpoint.

**Advertencia de tenant:** `_resolve_agent_routes()` prioriza una configuración
activa de `TenantRuntimeSettings` con `function_base_url`. Sólo en su ausencia usa
la URL global. Las rutas particulares también pueden sobrescribir las globales.
No se consultaron esas filas; no se certifica que la URL global sea la usada por
todos los tenants ni por el tenant del último smoke.

VICTOR tiene configuración global separada (`AGENTS_FUNCTION_BASE_URL_VICTOR`);
no se usa su destino como sustituto de SOPHIA ni se toca su contrato.

| Dato requerido del runtime | Resultado |
| --- | --- |
| URL global llamada por XOC | Comprobada en configuración Lambda PROD. |
| URL efectiva por cada tenant | No comprobada; puede existir override. |
| Repositorio fuente | No identificado/disponible entre los repos revisados. |
| Branch fuente | Desconocida. |
| Commit desplegado | Desconocido. |
| Pipeline/comando de deploy | Desconocido. |
| Contrato interno/verificación del token | Sólo se conoce el lado consumidor XOC. |
| Otros runtimes/slots/variantes por tenant | No se puede certificar su inventario. |
| PROD y una copia local corresponden al mismo código | No demostrable. |

Se buscaron fuentes/entrypoints de runtime en las carpetas del workspace y repos
Git en `/home/ubuntu` de la EC2 de deploy (búsqueda acotada hasta profundidad 3).
En esa EC2 sólo aparecieron el repo XOC_AWS y sus worktrees de releases ya conocidos.
Esto no afirma que el runtime no exista en otra máquina, directorio o repositorio:
afirma que su fuente autorizada no está identificada en los accesos revisados.

**Gate obligatorio aplicado:** no se crea Function sustituta, endpoint alternativo,
servidor MCP ni adaptador inventado; tampoco se edita código desplegado en Azure/AWS.

## 2. Estado Git inicial

| Repositorio/copia | Rama | HEAD | Estado |
| --- | --- | --- | --- |
| XOC-API-AWS local | main | `10e3908f199340253241b6d475277f6dd7e3f0c5` | Limpio al iniciar. |
| Release Chat local | release/chat-hardening-prod-20260910 | `fe3b25f13833fb300ad1049b0e7974b37be8e706` | Limpio. |
| XOC_PAGE | main | `f43d3ed6b2eac389cdf29e9fc9d052137fe188eb` | Limpio; no modificado. |
| XOC-APP-MOBILE | main | `303eac8a441275a5452ca79064c93c41295d82b5` | Cambio previo de package.json preservado. |
| EC2 /home/ubuntu/XOC_AWS | main | `046a0f0b9a7ee7e8d0d24738b4b882859ae38408` | Cambios previos en dashboard.py y dashboard_store.py preservados. |
| Runtime SOPHIA | No identificado | No identificado | No disponible para implementación. |

También se inventariaron `prueba-script-xoc` y `XOC_Minority_Report`: repos de reportes,
con cambios previos preservados, no identificados como fuente del runtime SOPHIA.
Los otros worktrees de backend son releases de XOC-API-AWS, no repos externos de SOPHIA.

La Lambda de Chat inspeccionada permanece `Active/Successful`, con CodeSha256
`H/uO4Z5cNLVyRGLy/CIBibwN/Gen8BXzvrvW+6niP2E=`, igual al release de hardening ya
desplegado. No se asume que producción sea main.

## 3. Flujo anterior comprobado en XOC

```text
POST /chat
→ get_current_user y reglas existentes de acceso
→ effective_tenant_id del contexto autenticado
→ AgentSession del mismo tenant/usuario
→ configuración por tenant o global
→ token de servicio SOPHIA
→ POST al runtime externo
→ validación del vínculo de thread_id devuelto
→ normalización de respuesta
→ tool_request opcional procesado por Tool Gateway
→ metadata.toolGateway con resultado
→ propuesta de ticket si corresponde, sin creación automática
→ persistencia normal de la sesión
→ respuesta al cliente
```

XOC envía al runtime:

```json
{
  "message": "mensaje del usuario",
  "user_name": "nombre o email resuelto por XOC",
  "thread_id": "opcional, resuelto desde sesión autorizada",
  "chat_mode": "consulta, sólo en demo"
}
```

Además envía `thread_id` como query parameter cuando existe, Authorization Bearer
con token de servicio y cookies de afinidad existentes. El timeout tiene default
60 segundos y puede configurarse por entorno. Existe retry acotado para el error
de run activo del runtime; no se modifica dentro de esta inspección.

El lado XOC reconoce `text`/`message`, `thread_id`, `action_plan`, `metadata` y las
variantes de petición de herramienta. No se puede certificar qué variantes emite
realmente el runtime sin sus fuentes o evidencia de su contrato.

## 4. Flujo nuevo de Fase D

**No implementado.** Sigue siendo objetivo, no comportamiento disponible:

```text
usuario → XOC → SOPHIA → tool_request → Tool Gateway
→ ToolResult sanitizado → segundo turno SOPHIA → respuesta natural final
```

No se añadió una segunda llamada ni se simuló una respuesta de modelo en producción.

## 5. Contrato tool_request existente

`_clean_agent_response()` ya reconoce, sin cambios en esta tarea:

```text
tool_request
toolRequest
tool_call
toolCall
```

Dentro del objeto, XOC acepta `tool_name`, `toolName` o `name`, y `arguments`.
Forma canónica compatible con el parser existente:

```json
{
  "tool_request": {
    "tool_name": "xoc.tickets.list",
    "arguments": {"limit": 20}
  }
}
```

Esto es el contrato aceptado por XOC, **no una prueba de emisión del runtime**.
El parser actual convierte `arguments` falsy a `{}`; las pruebas de malformados
de Fase D deberán cubrir esa distinción antes de decidir un cambio focalizado.
No se rehízo el parser.

## 6. ToolResult y segundo turno

Contrato Python existente en `src/tool_gateway/schemas.py`:

```text
ToolResult:
  status: allowed | denied | needs_approval
  data: dict | None
  error: string | None
  code: string | None
  audit_id: string | None
```

Actualmente Chat lo presenta como `metadata.toolGateway` (`toolName`, `status`,
`code`, `auditId`, `data` cuando existe). No se devuelve al runtime en un segundo turno.
No se diseñó ni agregó un contrato paralelo sin conocer cómo el runtime consume
resultados. La proyección segura de `data` debe revisarse antes de enviarla al modelo;
no se afirma que todos los campos de los stores sean aptos para ese fin.

## 7. Capability y autoridad

El token de servicio actual de Chat dura 15 minutos y conserva:

```text
sub = agent-runtime-<tenant>-SOPHIA
type = access
scopes = [agent:invoke]
tenant_id
agent_type = SOPHIA
iat, exp
```

No incluye actor_user_id, actor_role, delegation_active o request_id, ni la audiencia
propuesta de Fase D. No se modificó: falta auditar qué valida el runtime y si comparte
esa validación con otros agentes. No se emiten capabilities nuevas ni se comparten JWT.

ToolContext sí se construye en XOC desde el usuario autenticado:
tenant_id, effective_tenant_id, user_id, role, delegation_active, request_id y
source=`sophia_chat`. El resultado/modelo no sustituye ese contexto.

## 8. Tools habilitadas

Comprobadas contra el registry real; todas READ_ONLY:

- `xoc.tickets.list`
- `xoc.tickets.get`
- `xoc.documents.list`
- `xoc.notifications.unread_count`

Permanecen disabled: `xoc.dashboard.summary` y `xoc.integrations.summary`.
No se agregó ni habilitó ninguna herramienta.

## 9. Policies e aislamiento

Policy actual:

- USER y ADMIN: allowlist READ_ONLY existente.
- ADMIN_XOC: requiere delegación y tenant efectivo.
- SUPERADMIN: DENY para herramientas IA.
- Ownership tenant/user enviado como argumentos: DENY antes del handler.
- Desconocidas/disabled/destructivas: DENY; no handlers de escrituras registrados.
- Tickets get: usa `get_tenant_ticket_or_404(effective_tenant_id, ticket_id)`.
- Tickets list/documentos: stores del tenant efectivo.
- Unread count: tenant efectivo y usuario autenticado.

Distinción a cubrir en Fase D: la lista de argumentos de ownership de policy cubre
tenant/user, pero no incluye explícitamente role/delegation/access_token/JWT.
Los handlers actuales rechazan esos campos como argumentos extra antes de acceder
al store. Eso no cumple literalmente «cero entrada al handler» para esas claves;
deberá revisarse focalizadamente si se reanuda la fase. No se encontró una ampliación
de autoridad por esos argumentos, ni se modificó la policy para continuar.

## 10. Protección anti-loop

No existe todavía el segundo turno de Fase D, por lo que no está implementado su
contador/guard contra una segunda herramienta. La rutina actual procesa una
petición normalizada por respuesta. No se introdujeron chains, loops, herramientas
paralelas ni ejecución autónoma. El límite end-to-end debe probarse con el runtime real.

## 11. Accesos alternativos del runtime

No auditables sin código/configuración del runtime. Se desconoce si tiene acceso
directo a APIs XOC, Lambda, DynamoDB, RDS, administración, agent keys o Secrets Manager.
La autoridad IAM de la EC2 de deploy no demuestra los permisos de la Function Azure.
No se afirma que esos caminos estén ausentes o gobernados. No se eliminó infraestructura.

## 12. Archivos y pruebas

Único archivo nuevo de esta tarea: este reporte local.
Archivos de aplicación modificados: **ninguno**, en todos los repos revisados.
Sin commit, push, release nuevo, deploy, cambios de configuración o SQL/migraciones.

No se agregaron ni ejecutaron pruebas de Fase D: el gate obliga a detenerse antes
de implementar. No se repitió la suite completa en esta inspección.
Referencia del estado anterior, no una ejecución nueva: 75 pruebas enfocadas OK;
suite completa 197 pruebas, 195 OK y los dos fallos históricos de Victor documentados.

## 13. Seguridad, compatibilidad y riesgos

- HIGH — No puede demostrarse la fuente de verdad del runtime: bloquea implementación.
- HIGH — Permisos y rutas alternativas del runtime no auditados: no certificar
  gobernanza end-to-end ni emitir una capability distinta sin revisar su verificador.
- MEDIUM — URL efectiva puede variar por tenant; no existe inventario validado aquí.
- MEDIUM — Token actual no lleva todo el contexto propuesto para Fase D.
- MEDIUM — Faltan sanitización/proyección de ToolResult, contrato del segundo turno,
  límite anti-loop y pruebas de errores/malformados con el runtime.
- LOW — No existe evidencia nueva de regresión por esta tarea: no se cambió código.

No se introdujeron tenant/user del modelo, secretos en respuestas/logs, SQL libre,
shell para el modelo, herramientas destructivas, permiso SUPERADMIN para IA ni
auto-ticket. No se afirma que el runtime externo esté libre de esos accesos:
queda sin auditar. Los comandos de shell usados fueron diagnósticos del operador,
no herramientas expuestas a SOPHIA.

Chat normal y sesiones cuentan con la confirmación funcional del usuario al inicio.
Ticket proposals, confirmación C.3 e idempotencia siguen sin cambios; no se hizo una
nueva prueba autenticada de creación/replay durante esta inspección.
Voice, MCP, AgentCore, Step Functions, VICTOR, push y clientes permanecen fuera de alcance.

## 14. Información/acceso exactos para desbloquear

Solicitar al responsable del runtime, sin contraseñas ni tokens:

1. URL del repositorio fuente que atiende
   `/api/agents/SophiaDurableAgent/run` del hostname global identificado.
2. Acceso de lectura/clonado a ese repo y la ubicación del servicio/entrypoint.
3. Rama y commit realmente desplegados; evidencia del pipeline/build/release que
   los vincula con ese endpoint.
4. Comando o workflow actual de deploy, nombre del recurso Azure/slot y responsable.
5. Confirmar si hay otras variantes activas por tenant y cuál usa el tenant del smoke.
6. Ubicación del código que verifica el token `agent:invoke`, especialmente si es
   compartido con VICTOR u otros agentes.
7. Para auditar caminos alternativos, acceso de sólo lectura a su configuración de
   identidades/permisos y referencias de secretos, **sin pegar valores de secretos**.

No basta con la URL remota o acceso SSH a la EC2 de despliegue de XOC.
Si el repo se entrega, verificar sus instrucciones, branch/HEAD/estado antes de editar.

## 15. Decisión de salida

### ¿Fase D queda lista para commit?

**No.** Sólo hay un reporte de descubrimiento; no existe implementación D para revisar.

### ¿Queda lista para deploy controlado?

**No.** Falta código fuente del runtime, implementación, tests y revisión.

### ¿El circuito ya permite comenzar MCP-1 después del smoke?

**No.** El smoke aprobado corresponde al Chat endurecido anterior, no a un circuito
Fase D con segundo turno. MCP-1 requerirá implementar D, autorizar su deploy y validar
su propio smoke. No se empieza MCP automáticamente.
