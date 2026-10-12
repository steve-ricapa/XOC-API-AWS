# SOPHIA — Fase D.1b: inventario y gate de identidades

Fecha: 2026-09-13. Resultado: **ETAPA A realizada; ETAPA B detenida (NO-GO)**.

No se implementó el bloqueo directo de APIs. El gate obligatorio del prompt se
activa: SOPHIA Chat legacy y Voice comparten la identidad que se pretende negar.
Bloquearla hoy impediría las consultas de Voice. Además, no se puede certificar
el contrato de todos los consumidores VICTOR/legacy desplegados.

Se conservó D.1 parcial, se revalidó y se guardó en dos commits locales separados.
No hubo push, deploy, modificación remota, activación de flags ni rotación de claves.
No se tocaron Voice, VICTOR, Step Functions, C.3, Mobile, Web, schemas, SQL o RDS.
Las consultas de código/configuración Azure fueron de lectura; las pruebas de
APIs usaron transporte en memoria, claves ficticias y stores/proveedores simulados.

## 1. Git: antes y después

| Repo | Rama | HEAD inicial | Commit local D.1 parcial / HEAD final |
| --- | --- | --- | --- |
| XOC-API-AWS | main | e1fe34c56f288093d57f04507d6951e4998e0558 | 289ca5d02261c5988d43395f8fafc3249d2fb40a |
| XOC_MICROSERVICIO_AGENTES | main | fbf395a926757f72ff966f024e586265f67adbef | 6249064a9bd69bb388369d03f502b142bac93e1b |

Se ejecutaron status --short, branch --show-current, rev-parse HEAD, diff --stat,
diff y diff --check. Los cambios iniciales coincidían con los siete archivos
backend y seis runtime autorizados de D.1 parcial; no había cambios adicionales
en esos dos repos. Se añadieron al índice únicamente esos archivos.

Commits:

- Backend: feat: protect Sophia runtime and thread operations.
- Runtime: feat: require governed Sophia runtime operations.

Los dos commits anteriores de D en cada repo también se conservan. Ambos main
quedan tres commits por delante de sus referencias origin/main locales; no se
hizo fetch/pull/push en esta tarea. Eso no acredita el HEAD remoto actual.

Web se inspeccionó en f43d3ed6b2eac389cdf29e9fc9d052137fe188eb, sin cambios.
Mobile se inspeccionó en 303eac8a441275a5452ca79064c93c41295d82b5; su modificación
previa de package.json se conserva, sin stage, commit ni edición.

Los informes anteriores de D.1 parcial conservan su estado histórico «sin commit»
al redactarse. Esta sección actualiza ese dato. Este informe D.1b queda sin commit
para revisión; no se creó un commit que afirme haber cerrado el bypass.

## 2. Alcance y método del inventario

Se buscaron agent:invoke, agent_type, agentType, token-from-user,
create_agent_token, issue_agent_token, get_agent_token, create_access_token,
create_refresh_token y llamadas de firma JWT en el código versionado backend y
runtime. Se revisaron los callers reales, dependencias FastAPI, montaje de routers,
authorizer y rutas Serverless, no sólo los nombres de tools del modelo.

Se inspeccionaron tickets, scans, agents, reports/documents, integrations,
automation/cases, ops, admin/superadmin, tenant/users/audit, Chat y Live Voice.
La enumeración de decoradores se contrastó con los prefijos reales: las rutas
agent de scans son /scans/agent/*, NO /findings/agent/*.

Niveles de evidencia usados:

- **Local**: código de los HEAD anteriores más los commits D.1 parcial indicados.
- **Offline**: código real de firma/verificación/dependencies/router, con I/O simulado.
- **Azure actual**: metadata y lecturas explícitas del 13-sep descritas abajo.
- **Histórica**: auditoría del 11/12-sep; no se presenta como inspección nueva de PROD.
- **No acreditado**: bytes/commit/script de la Function y runtimes externos sin fuente.

No se consultaron tickets, scans, usuarios ni tokens reales para probar el bypass.

## 3. Inventario de emisores y familias de tokens

### 3.1 Agent tokens actuales: tres emisores concretos

Todos llaman a src/shared/auth.py::create_access_token. Ese helper firma HS256,
añade sub, type=access, iat y exp. No añade audience ni issuer. La clave proviene
de get_jwt_secret_key; no se leyó su valor.

| Emisor / archivo y función | Caller | Identidad y claims | TTL | Consumidor / destino |
| --- | --- | --- | --- | --- |
| routes/agents.py::authenticate_agent_from_user | POST /agents/auth/token-from-user; get_current_user + require_tenant_read_access | sub=agent-runtime-<tenant>-SOPHIA; agent_type=SOPHIA; scopes=[agent:invoke]; tenant_id=tenant efectivo; type=access; aud ausente | 3600 s | Proxy Voice acreditado; cualquier cliente humano autorizado puede obtener el mismo bearer; Azure SOPHIA legacy y APIs XOC que lo acepten |
| routes/chat.py::_build_agent_invoke_token | POST /chat cuando D OFF; GET /chat/history y DELETE /chat/sessions/{id} cuando thread capabilities OFF | sub=agent-runtime-<tenant>-SOPHIA; agent_type=SOPHIA en todos sus callers actuales; scopes=[agent:invoke]; tenant_id efectivo; type=access; aud ausente | 900 s | Function SOPHIA /run, /history y /threads/{id} resuelta por runtime settings/global config |
| workers/assess_ticket_automation.py::_build_service_token | handler del worker de evaluación/plan; también camino de plan personalizado vía worker | sub=agent-runtime-<tenant>-VICTOR; agent_type=VICTOR; scopes=[agent:invoke]; tenant_id del contexto/evento de orquestación; type=access; aud ausente | 900 s | VICTOR global/on-premise o runtime del tenant, según _resolve_victor_endpoint; Azure /api/agents/VictorDurableAgent/run en la implementación disponible |

El handler del worker construye el token antes de requests.post; no se considera
su URL prueba de identidad ni de revisión desplegada. Los contratos de los tres signers se
ejecutaron offline y confirmaron los TTL/claims de la tabla.

El JWT SOPHIA legacy NO contiene actor humano, role, delegación, canal Voice,
client_id o request_id. El exchange devuelve role/user_name/plan_status como
campos de respuesta separados: esos campos NO convierten el bearer en una
identidad exclusiva del proxy. Diferencias de TTL o del texto de sub tampoco lo hacen.

El helper Chat acepta un parámetro agent_type interno; no se encontró un caller
que lo tome del prompt/body ni un caller actual que lo use para VICTOR.

### 3.2 Otros firmantes: no confundirlos con agent:invoke

| Familia / emisor | Claims y duración | Uso / autoridad |
| --- | --- | --- |
| Access humano: routes/auth.py login, refresh_token y create_tenant_with_admin; helper shared/auth.py | sub usuario real, role y tenant cuando corresponde; type=access; 5 min; sin agent:invoke ni agent_type | Login y APIs humanas; refresh vuelve a cargar User |
| Refresh humano: shared/auth.py::create_refresh_token | type=refresh; sub usuario; 30 días; sin agent:invoke | POST /auth/refresh, no tools |
| Delegación: routes/superadmin.py::create_tenant_impersonation_token | sub operador, role, actor/acting tenant, delegation=true, scopes=[tenant:impersonate]; access, 1 h | Contexto humano delegado; no token VICTOR |
| C.3: chat.py::_ticket_confirmation_token | type=ai_ticket_confirmation; scope=ai:ticket:confirm; aud=xoc-chat-ticket-confirmation; actor/tenant/propuesta/action_plan; 300 s | Confirmación humana con JWT de usuario e idempotencia; no bearer genérico de tools |
| D: shared/sophia_tool_chat.py::capability | type=sophia_tool_capability; aud=xoc-ai-tool-gateway; scope=ai:tools; agent_type=SOPHIA; actor/role/effective_tenant/delegación/request/source/fase/hash; 120 s | XOC firma desde ToolContext autenticado; Azure valida el transporte |
| D.1: shared/sophia_thread_operations.py::headers_for_owned_session | type=sophia_thread_capability; aud=xoc-sophia-thread-operation; scope=sophia:thread:history o delete; actor/tenant/thread/operación/request/hash; 60 s | Sólo después de ownership de AgentSession; no agent:invoke |
| Runtime D: sophia_agent/gateway_protocol.py::continuation | type=sophia_tool_continuation; aud=xoc-sophia-tool-continuation; contexto y thread/run/call/tool/hash; 120 s | Continuación ligada al mismo turno; no concede acceso a APIs XOC |
| Legacy API-key exchange: POST /agents/auth/token | Emisión deshabilitada: 410 LEGACY_AGENT_AUTH_DISABLED | No firma tokens en la base actual |
| AgentApiKey / claves de ingesta | Credencial diferente de JWT agent:invoke, verificada por el helper de ingesta | /scans/upload-url e /scans/ingest; no atribuirlas a SOPHIA/VICTOR |

No se encontró un emisor agent:invoke sin agent_type en la fuente actual de los
dos repos. Eso NO prueba que no existan emisores antiguos o externos desplegados.
El proxy inspeccionado intercambia/reutiliza un token: no se encontró emisión
propia en fetch_agent_context ni ToolExecutor.

## 4. Qué valida hoy XOC

src/handlers/authorizers/jwt_authorizer.py valida HS256 y proyecta:
userId, tenantId, actorTenantId, actingTenantId, delegation, actorUserId, role,
tokenType, scopes y agentType. No impone una allowlist de agentes por endpoint.
No exige exp en options.require; no configura audience/issuer explícitos.

src/shared/dependencies.py:

- require_access_claims rechaza un type/tokenType presente distinto de access;
  permite access y no rechaza por sí solo un type ausente.
- get_request_tenant_context reconoce scopes agent:invoke y devuelve
  (tenant del contexto firmado, "agent"), sin distinguir SOPHIA/VICTOR/Voice.
- get_current_user_optional devuelve None para agent:invoke, sin validar agentType.
- get_current_user intenta cargar un User con id numérico; los sub de servicio
  actuales no son numéricos. Su ValueError NO debe presentarse como una policy
  explícita de denegación de agentes.

No se encontró protección central existing allowed_agent_types que ya resuelva
esta separación. No se añadió ninguna dependencia nueva durante este gate.

## 5. Inventario de endpoints relevantes

Convenciones de las tablas:

- A = authorizer JWT Serverless + require_access_claims. A no exige agent:invoke
  ni valida agent_type: un bearer agente type=access puede superar esa entrada.
- S = A + get_request_tenant_context + actor==agent. Exige agent:invoke, NO tipo.
- O = A + get_current_user_optional. Agent scope produce actor None.
- I = A + devices._request_identity. Tenant/sub no vacíos, role puede estar vacío.
- T = tenantId/tenant_id firmado, proyectado por XOC; no body como tenant.
- «Consumer conocido» identifica llamadas en código o evidencia histórica, no
  que cada operación haya ocurrido en PROD ni que toda ruta deba permitirse.

### 5.1 Tickets — src/handlers/domains/tickets_dynamo.py

Publicadas en serverless.tickets.js.

| Método / ruta | Auth | Clase | Tenant / actor | Resultado actual para SOPHIA legacy / consumers |
| --- | --- | --- | --- | --- |
| GET /tickets | A | READ_ONLY | T; sin ownership individual | Lectura de tenant permitida; consumers humanos; uso directo de agente posible |
| GET /tickets/{ticketId} | A | READ_ONLY | Clave de ticket con T | Permitida; SOPHIA legacy, VICTOR Azure y Voice tienen ticket_get |
| POST /tickets | A | WRITE | T; convierte sub/userId a int | Token servicio actual llega a ValueError/500, no a un DENY; ticket_create de SOPHIA/Voice apunta aquí |
| POST /tickets/agent | A | WRITE | T; pasa sub como created_by_user_id, sin conversión a int | **Permite crear** con bearer SOPHIA; endpoint alternativo a POST /tickets, sin C.3; consumer legítimo actual no acreditado |
| PUT /tickets/{ticketId} | A | WRITE | T; update_ticket_fields | **Permite actualizar**; BackendClient.ticket_patch y Voice usan PUT, no PATCH |
| DELETE /tickets/{ticketId} | A | DESTRUCTIVE | T; lookup y delete | **Permite borrar**; no tool registrada de borrado ni consumer agente legítimo acreditado |
| PATCH /tickets/{ticketId}/approve | A + rol/estado | OPERATIONAL_INTERNAL / WRITE | T, salvo rama de operadores; actor sub | Token servicio corriente sin role no alcanza rol USER; sí puede llegar al lookup antes del rechazo |
| PATCH /tickets/{ticketId}/reject | A + rol/estado | OPERATIONAL_INTERNAL / WRITE | Igual | Rechazo por rol con contrato corriente; no allowlist de tipo agente |
| PATCH /tickets/{ticketId}/decision/select | A + rol/estado | OPERATIONAL_INTERNAL / WRITE | T; rol requerido de pending_decision | Sin role no satisface el caso normal USER; consumer humano |
| PATCH /tickets/{ticketId}/decision/custom | A + rol/estado | OPERATIONAL_INTERNAL / WRITE | T; rol requerido de pending_decision | Igual; puede llamar worker VICTOR tras autorización humana |

No existe PATCH /tickets/{ticketId} genérico en el router/config actuales. Sí
existe PATCH /superadmin/tickets/{ticketId}, que exige usuario humano/operador.
Un 404/405 en el PATCH genérico no demuestra cierre de ticket_patch: éste usa PUT.
Las comprobaciones de rol de aprobaciones no constituyen una política para toda
la familia agent:invoke; tampoco garantizan cero lecturas ante un token agente.

### 5.2 Scans — src/handlers/routes/scans.py

Las OCHO rutas siguientes están declaradas en serverless.ops.js. Todas son S,
READ_ONLY, usan T del JWT y actor="agent". No validan agentType, actor humano,
role ni delegación. Reutilizan las consultas/helpers RDS existentes; no se
ejecutaron esas consultas contra RDS durante la auditoría.

| Método / ruta | Fuente | Consumer conocido |
| --- | --- | --- |
| GET /scans/agent/summaries | agent_get_scan_summaries | SOPHIA legacy y Voice; tráfico 200 de Voice observado el 12-sep |
| GET /scans/agent/summaries/{scanSummaryId} | agent_get_scan_summary | SOPHIA legacy y Voice: scan_summary_get |
| GET /scans/agent/summaries/{scanSummaryId}/findings | agent_get_scan_summary_findings | SOPHIA legacy y Voice: scan_summary_findings |
| GET /scans/agent/findings | agent_get_findings | SOPHIA legacy y Voice: scan_findings_list |
| GET /scans/agent/snapshots | agent_list_snapshot_artifacts | No caller legítimo acreditado en los runtimes inspeccionados |
| GET /scans/agent/snapshots/{artifactId} | agent_get_snapshot_artifact | No acreditado |
| GET /scans/agent/snapshots/{artifactId}/payload | agent_get_snapshot_payload | No acreditado; devuelve metadata y URL de descarga |
| GET /scans/agent/snapshots/{artifactId}/download-url | agent_get_snapshot_download_url | No acreditado; emite URL presignada |

No se encontró uso de scans en el handler VICTOR Azure disponible. No se infiere
por ello que un VICTOR on-premise no los use: falta acreditar esa implementación.

### 5.3 Más rutas que aceptan o procesan bearer agente

| Método / ruta | Archivo / infra | Auth | Clase | Tenant / actor y resultado actual |
| --- | --- | --- | --- | --- |
| GET /cases | routes/cases.py / serverless.automation.js | A | READ_ONLY | T para list_tenant_cases; sin tipo agente; consumer agente no acreditado |
| GET /cases/{caseId} | idem | A | READ_ONLY | T en case_key; sin tipo agente |
| GET /cases/ticket/{ticketId} | idem | A | READ_ONLY | **No valida T del resultado**: busca globalmente por ticket; hallazgo cross-tenant adicional |
| POST /automation/approval/callback | routes/approval.py / serverless.automation.js | A + rol condicional | OPERATIONAL_INTERNAL / WRITE | Lookup global por ticket; rol exigido si existe; sin tipo agente. Si no existe pasa al worker sin validar rol; no equivale a demostrar callback exitoso en PROD |
| POST /audit | routes/audit.py / serverless.tenant.js | O | WRITE | Agent scope acepta actor_user_id=None; contenido de auditoría viene del body; no tenant binding propio; consumer agente legítimo no acreditado |
| POST /devices | routes/devices.py / serverless.ops.js | I | WRITE | Usa T y sub de servicio como usuario; no tipo agente ni role obligatorio |
| DELETE /devices/{deviceId} | idem | I | WRITE (desregistro lógico) | Sólo clave T/sub/device de ese contexto; sin tipo agente |
| POST /notifications/test | idem | I | OPERATIONAL_INTERNAL / WRITE | Device bajo T/sub; no autoriza lectura de otro usuario, pero acepta identidad de servicio |
| POST /notifications/send | idem | A + _authorize_campaign_audience | WRITE | Role vacío del token agente actual produce 403; no ampliar roles para hacerlo pasar |
| POST /notifications/events/test | idem | A + rol QA | WRITE | Role vacío no satisface ADMIN/ADMIN_XOC delegado; 403 |
| GET /notifications/inbox | routes/notification_inbox.py / serverless.ops.js | I | READ_ONLY | T/sub; no valida tipo agente; consumer humano conocido |
| GET /notifications/inbox/unread-count | idem | I | READ_ONLY | T/sub; sin tipo agente |
| PATCH /notifications/inbox/{notificationId}/read | idem | I | WRITE | T/sub; sin tipo agente |
| PATCH /notifications/inbox/{notificationId}/archive | idem | I | WRITE | T/sub; sin tipo agente |

Estas rutas sumadas a tickets y scans son 32 entradas del inventario, incluyendo
las que fallan por validaciones adicionales. «Acepta bearer» no significa que
siempre retorna 2xx: existencia, payload, estado, rol y storage siguen influyendo.

Push/Inbox sólo se inventariaron porque el prompt exige no limitar los consumers;
NO se editaron sus rutas ni se enviaron notificaciones. No se propone darles
acceso a VICTOR por el hecho de que actualmente reconozcan un JWT de servicio.

### 5.4 Dominios restantes y rutas excluidas del allowlist de agentes

- Chat, C.3, Live Voice sessions, reports/documents normales, integrations,
  dashboard, admin/superadmin, xoc-ops, tenant/users y security_ops utilizan
  get_current_user más sus policies humanas. No se encontró un opt-in
  agent:invoke en esas rutas. La conversión numérica de sub de servicio puede
  fallar; no se declara una protección central explícita que hoy no existe.
- POST /agents/auth/token y GET /agents/instance/{id} responden 410.
- POST /agents/auth/token-from-user exige usuario humano; no permite elegir
  VICTOR. USER/ADMIN con tenant y operadores con delegación satisfacen la policy
  actual de lectura. Eso incluye SUPERADMIN delegado en el exchange legacy;
  no confundirlo con la policy D, que deniega SUPERADMIN para tools.
- /scans/upload-url y /scans/ingest son rutas públicas de API Gateway con
  validación de AgentApiKey propia; el bearer agent:invoke por sí solo no la
  reemplaza. Son OPERATIONAL_INTERNAL/WRITE de ingesta, no tools nuevas de D.
- /documents/local-generate y /documents/local/{id}/download son demostración
  local, desactivada por defecto; no forman parte de las rutas públicas
  declaradas del stack reports. No se activaron.
- Workers invocados por eventos/IAM/Step Functions no se clasifican como rutas
  HTTP accesibles sólo por poseer agent:invoke.

## 6. Voice: dependencia real que detiene ETAPA B

### Componentes y tokens

1. Web: src/services/voice-live.service.ts. Mobile:
   src/lib/services/sophia-live-ws.service.ts. Ambos usan el access token humano
   para abrir el WebSocket del proxy. No usan la capability D como JWT de usuario.
2. Proxy productivo: ca-sophia-voice-proxy, revisión
   ca-sophia-voice-proxy--0000037, imagen
   xocproxysophiaacr.azurecr.io/proxy-sophia:v15; tráfico 100% a latest.
3. Se releyó el 13-sep /app/app/backend_client.py en esa revisión.
   fetch_agent_context manda el JWT humano a POST /agents/auth/token-from-user,
   body agentType=settings.azure_agent_type (valor remoto SOPHIA), y toma
   access_token de la respuesta. No obtiene una identidad exclusiva de Voice.
4. Se releyó /app/app/tool_executor.py completo por tramos. Su _request usa
   Authorization Bearer context.agent_token contra BACKEND_BASE_URL.
   Este proxy no usa la clase shared.BackendClient del repo de la Function;
   tiene su propio cliente httpx, pero el camino directo es equivalente.

Configuración no secreta revalidada:

- BACKEND_BASE_URL: https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com
- BACKEND_AGENT_TOKEN_PATH: /agents/auth/token-from-user
- AZURE_AGENT_TYPE: SOPHIA
- AZURE_AGENT_NAME: Sophia; AZURE_PROJECT_NAME: sophia-project
- JWT_ALGORITHM: HS256; BACKEND_TIMEOUT_SECONDS: 10
- WebSocket público: wss://ca-sophia-voice-proxy.whitebeach-abb39dc7.eastus2.azurecontainerapps.io/ws

La conexión saliente Voice a Foundry fue acreditada en el diagnóstico del 12-sep:
wss://aoai-sophia-xoc-eus2.services.ai.azure.com/voice-agent/realtime,
API 2025-05-01-preview, agente nombrado Sophia del proyecto sophia-project.
No es el POST de prod-a-xoc-cons ni el assistant clásico de Chat. Ese endpoint
saliente y la definición Foundry no se reconsultaron en este turno.

### Tools directas del proxy, confirmadas por código remoto actual

| Tool | HTTP XOC |
| --- | --- |
| ticket_create | POST /tickets |
| ticket_get | GET /tickets/{id} |
| ticket_patch | PUT /tickets/{id} |
| scan_summaries_list | GET /scans/agent/summaries |
| scan_summary_get | GET /scans/agent/summaries/{id} |
| scan_summary_findings | GET /scans/agent/summaries/{id}/findings |
| scan_findings_list | GET /scans/agent/findings |

El token se toma del contexto del proxy, no de los arguments de la tool. Pero
no pasa por Tool Gateway, su Policy ni su Audit. No se encontró history/delete
de Azure en este dispatcher. La persistencia/listado/cierre de sesiones Voice
usa /live-voice-sessions con JWT humano en Web/Mobile; es un flujo distinto de
los threads de Chat protegidos por D.1 parcial.

Web conserva sophia.service.ts::tokenFromUser, pero no se encontraron callers
en src: su tipo de respuesta legacy agent_token tampoco coincide con access_token
del backend actual. No se tocó ese helper. Mobile no contiene un caller directo
del exchange; el intercambio lo hace el proxy.

**Límite de fuente de verdad:** el repo del proxy Voice no está en el workspace.
Leer código desplegado acredita su comportamiento, no su commit/repo/build.
No se editó el contenedor ni se inventó un proxy sustituto. No se atribuye aquí
la causa del silencio de Voice: ese diagnóstico sigue separado.

Conclusión: bloquear bearer SOPHIA en tickets/scans afectaría Voice de Web Y Mobile.
No hay un claim firmado actual que autorice sólo al proxy y niegue a otro
poseedor del mismo token. Ni URL, User-Agent, sub, IP ni source del body solucionan eso.

## 7. Missing/unknown agent_type y VICTOR

shared/agent_auth.py::get_agent_token en el runtime exige Bearer, firma, exp,
scopes lista con agent:invoke y tenant_id. Sólo compara agent_type si no está vacío.

Prueba offline con el verificador real y clave ficticia:

| Claim | Azure SOPHIA legacy | Azure VICTOR | APIs XOC A/S/O probadas |
| --- | --- | --- | --- |
| SOPHIA | Acepta | Deniega | Aceptan |
| VICTOR | Deniega | Acepta | Aceptan |
| Ausente | Acepta | Acepta | Aceptan |
| Vacío | Acepta | Acepta | Aceptan |
| EVIL | Deniega | Deniega | Aceptan |

No se identificó emisor actual sin tipo en los repos, pero tampoco se acreditó
ausencia de tokens/consumers legacy en producción. No se inspeccionaron JWT reales
ni se asumió que ya expiraron: el authorizer AWS ni siquiera exige exp presente,
y no hay inventario completo de todos los firmantes externos.

VICTOR Azure disponible importa ticket_get y ticket_patch; sus accesos directos
necesarios en esta fuente son GET /tickets/{id} y PUT /tickets/{id}. No se encontró
uso de POST /tickets/agent, DELETE, /audit, Push o scans en ese handler. Eso define
un candidato de permisos mínimos, NO autorización para bloquear otros VICTOR
desplegados: el worker puede resolver una URL on-premise/global cuya fuente y
contrato efectivo todavía deben acreditarse.

No se interpretó missing como VICTOR, SOPHIA ni humano. No se introdujeron
excepciones para que una prueba pasase.

## 8. Gate A: respuestas y política implementada

| Pregunta obligatoria | Respuesta |
| --- | --- |
| Q1: ¿Todos los VICTOR legítimos usan agent_type=VICTOR de forma acreditada? | Sí en el emisor y handler disponibles; **no determinable para todos los deployments/consumers externos**. |
| Q2: ¿Voice necesita APIs directas que el hardening bloquearía? | **Sí**, demostrado por configuración y código remoto; scans también tuvo tráfico histórico 200. |
| Q3: ¿Existe consumidor legítimo de tokens sin tipo? | **No determinable en PROD**. No hay emisor actual encontrado; los verificadores los admiten. |
| Q4: ¿Las APIs pueden distinguir SOPHIA de VICTOR? | El claim firmado distingue los tokens tipados; **no distingue SOPHIA Chat de Voice**, ni resuelve legacy ausente. |

**ETAPA B NO iniciada. Política D.1b implementada: ninguna. Endpoints nuevos
protegidos por D.1b: ninguno.** No existe todavía el DENY directo solicitado.
Los flags y la implementación D.1 parcial quedan intactos, con defaults false.

### Cambio contractual mínimo propuesto, NO implementado

1. Acreditar el repo/commit/deploy del proxy Voice y de VICTOR efectivo.
2. Separar identidad de componente y uso del token mediante emisión autenticada
   de servidor: Voice debe demostrar su identidad de servicio al emisor, además
   del contexto humano autorizado. Un client_id/token_use/audience firmado sería
   parte de un contrato a aprobar, no un campo seleccionable por cualquier cliente.
3. No basta añadir agentType=VOICE al exchange público ni confiar en source=voice:
   un cliente podría pedir la misma excepción. Tampoco basta un header estático
   sin autenticación de componente.
4. Definir para VICTOR una allowlist por operación, con tenant y alcance del
   trabajo; para Voice, inventario y autorización separados, incluyendo revisión
   de WRITE/confirmación humana. No declarar Voice gobernado por el cambio de Chat.
5. Acreditar/migrar emisores sin tipo y ventana de transición; luego missing y
   unknown pueden pasar a DENY explícito sin identidad inferida.
6. Con ese contrato aprobado y sus consumers preparados, añadir una dependencia
   pequeña de autorización de agentes a las rutas inventariadas. No cambiar
   permisos humanos ni replicar if SOPHIA en cada handler. Mantener capabilities
   de thread separadas de agent:invoke y ToolContext como autoridad de D.

token-from-user se conserva. Su uso como autorización de tools directas debe
considerarse conceptualmente deprecated_for_tools, pero **hoy NO está bloqueado**.

## 9. Hallazgos de seguridad adicionales

- HIGH: POST /tickets/agent permite la creación directa que el ValueError de
  POST /tickets oculta. El camino no requiere confirmation_token ni C.3.
- HIGH: PUT y DELETE tickets son alcanzables con la misma familia de bearer.
  Cerrar sólo el dispatcher Azure no los protege.
- HIGH: GET /cases/ticket/{id} no valida el tenant del resultado. En la prueba,
  contexto tenant A recibió un resultado simulado de B; el helper real consulta
  el índice sólo por ticket. No se buscaron IDs ni datos ajenos en PROD.
- HIGH: agent_type desconocido es aceptado por los helpers directos AWS; tokens
  sin tipo son aceptados tanto por Azure SOPHIA como por Azure VICTOR.
- MEDIUM/HIGH: /audit admite escritura de agente sin actor humano; Push/Inbox
  aceptan sub de servicio para algunas operaciones. Consumers a definir antes de
  un deny central. No se tocaron esos dominios.
- MEDIUM: /automation/approval/callback usa lookup global y valida rol sólo si
  encontró ticket. Con ticket ausente llega al worker (comprobado con worker
  simulado), pero el worker real intenta buscar el ticket con tenant 0; no se
  declara reanudación exitosa de Step Functions ni se hizo una llamada real.

Estos hallazgos se reportan, no se corrigen incidentalmente ni se usan para
justificar una expansión no autorizada de D.1b.

## 10. HS256 y latencia: pendientes, sin D.2

**HIGH — firma simétrica compartida.** Quien conoce la clave HMAC de validación
puede firmar, aunque se le llame verificador. Claims aud/scope/type limitan el
uso de un token intacto, no lo que puede fabricar un componente con la clave.

- Backend signer/authorizer con material JWT: puede fabricar access/refresh,
  tokens SOPHIA/VICTOR, delegación, C.3 y capabilities D/D.1 compatibles.
- Azure Function con el mismo material: tiene la misma capacidad criptográfica;
  no queda limitada a SOPHIA por conocer ese secreto.
- Proxy Voice: si su material HS256 coincide con el aceptado por XOC, su
  compromiso permite falsificar esas familias también. No se compararon valores
  secretos ni se afirma igualdad de claves sólo por el nombre de la variable.
- Cliente/modelo sin clave: no puede firmar claims nuevos; un bearer vigente sí
  puede reutilizarse donde la policy actual lo admita.

En rutas get_current_user aún se carga un usuario real; fabricar role no reemplaza
automáticamente ese registro. Eso no elimina la suplantación posible con la clave
comprometida. D.2 sigue siendo necesaria antes de MCP, como tarea independiente;
no se rotaron claves, no se crearon RSA/KMS/JWKS ni se cambió login/access tokens.

**MEDIUM/HIGH — latencia.** Se conserva Lambda Chat de 20 s y el diseño D actual:
deadline local 18 s después de preparación, HTTP hasta 9 s por fase y Azure 8 s
por fase. No es una garantía end-to-end: auth/sesiones, cold starts, SDK retries,
consultas y persistencia quedan parcialmente fuera de ese presupuesto.
History/delete mantienen timeout HTTP de 30 s; tampoco prueba que quepan en Lambda.
No se cambió ningún timeout.

El smoke futuro debe observar Duration/Init Duration/Errors y 502/504, latencia
frío/caliente, tiempo preparación/gateway/fase1/fase2/persistencia, reintentos,
request_id/audit_id, cero handlers ante DENY, exactamente un handler permitido y
ninguna segunda tool. Se mantienen los criterios propuestos en
[gate pre-deploy D](sophia-phase-d-predeploy-gate.md), no mediciones nuevas ni SLA.

## 11. Tests y pruebas ejecutadas en esta revisión

### Suites existentes

| Suite | Resultado 13-sep | Comparación con baseline |
| --- | --- | --- |
| Backend unittest discover -s tests | 236 ejecutados; 234 OK, 2 fallos, 0 errores | Igual al baseline D.1 parcial |
| Runtime pytest -q -p no:cacheprovider | 220 passed; 6 warnings de datetime.utcnow | Igual al baseline, contratos entre repos habilitados |
| Backend focal D/D.1/Chat/C.3/Gateway | 114/114 OK | Sin regresión |

Los dos fallos históricos backend siguen siendo:

1. test_assess_ticket_automation.AssessTicketAutomationTests.test_global_url_wins_and_marks_source_global:
   esperaba http://10.0.0.5:8000, obtuvo None.
2. test_assess_ticket_automation.AssessTicketAutomationTests.test_normalize_plans_skips_empty_and_keeps_legacy:
   esperaba [], obtuvo un plan con cmd=x.

No se corrigieron. No se declara verde la suite completa backend.

La suite focal incluye test_sophia_phase_d, test_sophia_phase_d_infra,
test_chat_access_hardening, test_chat_tool_gateway_integration,
test_chat_ticket_confirmations, test_tool_gateway_policy,
test_tool_gateway_executor y test_sophia_thread_operations.

Las ejecuciones usaron Python -B con un wrapper de unittest discover equivalente
al comando pedido, dependencias locales ya instaladas y red externa bloqueada.
Runtime usó su .venv y XOC_API_SOURCE apuntando al backend, sin cache de pytest.
AWS credentials, secret y tablas fueron ficticios; metadata EC2 deshabilitada;
AgentSession de contratos en SQLite efímero. No se instalaron dependencias.

### Diagnóstico adicional D.1b, no suite de enforcement

Se ejecutó un diagnóstico en memoria (sin añadir archivo de código) con:

- Tres emisores reales: exchange SOPHIA, Chat SOPHIA y worker VICTOR; claims/TTL
  comprobados sin imprimir JWT.
- Cinco identidades firmadas ficticias: SOPHIA, VICTOR, missing, vacío y EVIL.
- Verificador real Azure legacy para las dos identidades objetivo: diez resultados.
- Authorizer real XOC y routers/dependencies FastAPI reales; se sustituyó sólo
  la frontera de I/O y la entrega en memoria del contexto ya verificado.
- Cincuenta requests HTTP en memoria: diez escenarios por identidad. Ocho rutas
  de lectura/escritura aceptadas, POST /tickets falla incidentalmente, y callback
  con ticket ausente alcanza un worker simulado. Ningún store real fue escrito.
- Dos capabilities reales history/delete generadas con clave ficticia: ambas
  rechazadas por el authorizer (audience distinta). Además, si se proyectan sus
  claims en memoria, PUT ticket y GET scans las rechazan por token type (cuatro 403).

Las ocho respuestas aceptadas por cada identidad fueron:
GET tickets 200, GET ticket 200, POST tickets/agent 201, PUT ticket 200,
DELETE ticket 200, GET scans/agent/summaries 200, GET cases/ticket 200 y POST audit 201.
Son **evidencia del problema actual**, no pruebas de que D.1b ya lo cerró.

No se añadieron tests de una implementación D.1b inexistente tras el STOP.
No se encontraron suites Voice funcionales en Web/Mobile/backend/runtime
inspeccionados; los tests que usan source=voice son negativos del protocolo D,
no un smoke Voice. No se inició audio ni un modelo para esta auditoría.

## 12. Azure: evidencia actual y faltantes exactos

Lecturas con la sesión Entra del usuario, sin keys/SAS/publish profile:

| Dato | Evidencia |
| --- | --- |
| Suscripción | XOC-AZURE, 884feb5e-8492-4d88-8777-6391393372f5 |
| Tenant | 50ac7fef-2120-437f-ad31-6beb036a3723 |
| Resource group | rg-sophia-poc |
| Function recurso raíz | Microsoft.Web/sites/prod-a-xoc-cons; Running; functionapp,linux |
| Host | prod-a-xoc-cons-g9hvargsfqeafeap.centralus-01.azurewebsites.net |
| Slot objetivo | Recurso raíz/producción, no un recurso /slots. La auditoría anterior no encontró slots; no se enumeraron otra vez |
| Config auth consultada | JWT_ALGORITHM=HS256; SOPHIA_GATEWAY_REQUIRED, JWT_ISSUER y JWT_AUDIENCE ausentes en el filtro actual |
| Repo fuente confirmado por responsable | XOC_MICROSERVICIO_AGENTES/main; eso no acredita el commit desplegado |
| Commit/bytes PROD Function | No acreditados |
| Storage artefactos | Cuenta rgsophiapoc8952, container scm-releases |
| Lectura Entra metadata de blobs | Denegada el 13-sep: You do not have the required permissions needed to perform this operation. |
| Script real de deploy | No entregado ni encontrado en runtime/.agents/material de auditoría local revisado |

No se siguió la sugerencia automática de CLI de usar --auth-mode key. Para
completarlo, el responsable puede entregar el artefacto/script y procedencia
(SHA, rama, working tree, comando, dependencias, recurso/slot), o habilitar
Storage Blob Data Reader a la identidad auditora en ese container. Esta tarea
no modificó RBAC ni pidió permisos de escritura sobre Storage.

No se comparó byte a byte la Function con Git porque no se pudo leer el artefacto.
La lectura del código del proxy Voice no sustituye ese artefacto de la Function.

AWS no se reconsultó en este turno. La base histórica acreditada de Chat PROD
es fe3b25f13833fb300ad1049b0e7974b37be8e706, no main, según el gate del 11-sep.
Antes de un release hay que volver a acreditar sus bytes/CodeSha y aplicar sólo
los cambios aprobados. No llevar multi_planner ni cambios de otros dominios;
no se creó worktree/release ni se hizo SSH/pull/deploy desde EC2 aquí.

## 13. Compatibilidad, cambios y decisiones finales

| Comprobación | Estado |
| --- | --- |
| SOPHIA direct tools -> DENY | **NO**: bypass conservado al alcanzar el gate obligatorio |
| VICTOR legítimo -> funciona | Regresión local runtime OFF/ON pasa; producción no certificada ni modificada |
| Tool Gateway D READ_ONLY | Tests verdes; cuatro tools habilitadas: tickets.list, tickets.get, documents.list, notifications.unread_count |
| Dashboard/integrations tools | Siguen disabled |
| History/delete capabilities | Tests verdes; ownership, binding y cross-scope preservados |
| Chat/C.3/humanos | Regresiones focalizadas verdes; no cambio de autorización en D.1b |
| SUPERADMIN | Continúa DENY por defecto en Tool Gateway; no se amplió su IA |
| Voice | Sin cambios; comparte bearer SOPHIA y sigue usando tools directas |

Archivos guardados en el commit D.1 parcial backend:
serverless.chat.js, src/handlers/routes/chat.py,
src/shared/sophia_thread_operations.py, tests/test_sophia_thread_operations.py,
tests/test_sophia_phase_d_infra.py, docs/sophia-phase-d1-scope-gate.md y
docs/sophia-phase-d1-partial.md.

Archivos guardados en el commit D.1 parcial runtime:
sophia_agent/handler.py, sophia_agent/thread_operations.py,
local.settings.json.example, tests/test_gateway_enforcement.py,
tests/test_thread_xoc_contract.py y PHASE_D.md.

Nuevo archivo de este gate: **sólo este informe**. Ningún cambio funcional D.1b.

### Bypasses conocidos

- ¿SOPHIA Chat puede ejecutar una tool sin Gateway? **Sí**, el camino legacy
  sigue disponible con required OFF en la fuente disponible; activar sólo el
  enforcement Azure tampoco cierra APIs directas AWS. No se afirma equivalencia
  completa del artefacto PROD Function no obtenido.
- ¿Un bearer SOPHIA puede llamar una API directa de tools XOC? **Sí**, demostrado
  offline con auth y rutas reales, y respaldado para scans por evidencia histórica.
- ¿Voice usa un camino no gobernado? **Sí**, código remoto actual del proxy.

### Respuestas de salida

- ¿D.1b lista para commit de implementación? **No**. D.1 parcial sí quedó
  guardada en los dos commits locales; el informe D.1b queda para revisión.
- ¿D.1 integral cerrada? **No**.
- ¿D.2 es el siguiente paso inmediato? **No**: primero resolver el gate de
  identidades/consumers y completar D.1b. D.2 sigue obligatoria antes de MCP;
  puede planificarse aparte, no se inicia automáticamente.
- ¿Permitido preparar releases aislados sólo por terminar D.2? **No**: faltan
  D.1b, evidencia de artefactos/scripts, revisión de candidato y autorización.
- ¿MCP-1 puede empezar sólo con D.2 + deploy/smoke? **No, no basta**. Sí podría
  evaluarse después de D.1 integral + D.2 + releases acreditados + deploy/smoke
  satisfactorios y autorización expresa. MCP debe reutilizar el Gateway.

Gate operativo actual: **NO-GO** para deploy runtime Azure, deploy Chat incluso
con flags false como cierre de D.1b, activación coordinada, smoke de aceptación de
cierre e inicio MCP-1. No es prohibición de pruebas diagnósticas autorizadas.

**Próximo paso requerido:** obtener el repo/commit del proxy Voice y el contrato
desplegado de VICTOR, aprobar una separación autenticada de identidad de servicio,
y acreditar la Function Azure. No sacrificar Voice ni producción para marcar
cerrado un bypass que aún existe.
