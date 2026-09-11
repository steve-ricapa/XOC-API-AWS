# SOPHIA — Fase D: implementación local y entrega coordinada

> Actualización pre-deploy: ver [sophia-phase-d-predeploy-gate.md](sophia-phase-d-predeploy-gate.md).
> Se preservó este código y se preparan commits locales separados. El gate detectó
> bypass legacy HIGH; no hay GO de deploy/activación. El informe de implementación
> que sigue conserva el estado y pruebas de su cierre anterior al gate.

Fecha: 2026-09-11. Estado: implementación local, **apagada por defecto**.
Sin commit, push, release PROD ni deploy. No se invocó un modelo real.

## 1. Fuente de verdad y precondiciones

El usuario confirmó el smoke funcional previo de Chat (conversación nueva,
continuación, historial y reapertura). No se reportó un blocker en ese smoke.
Esto no sustituye el smoke real de Fase D ni una auditoría completa de Azure.

El responsable confirmó, por medio del usuario, que
`XOC_MICROSERVICIO_AGENTES`, rama `main`, es la fuente de la Function
`prod-a-xoc-cons`. Indicó que su agente hace `git pull` y despliega mediante
un script propio. Ese script no se recibió ni se inventó un reemplazo.
Se acepta esta confirmación para levantar el gate de disponibilidad de fuente
y continuar LOCALMENTE; **no prueba qué commit está publicado**.

| Repo | Rama | HEAD inicial, sin cambiar | Estado inicial |
| --- | --- | --- | --- |
| XOC-API-AWS | main | `10e3908f199340253241b6d475277f6dd7e3f0c5` | Sólo reporte previo untracked `docs/sophia-phase-d-runtime-discovery.md`. |
| XOC_MICROSERVICIO_AGENTES | main | `cc9e8a8fee9a2adcab54c9a2ebff5897f39f7931` | Limpio. |

Runtime remoto Git: `https://github.com/steve-ricapa/XOC_MICROSERVICIO_AGENTES.git`.
Entrypoint: `function_app.py` → `sophia_agent.handler.main`.
Hosting según código: Azure Functions Python; el manejo de threads/runs usa REST
de Foundry en `sophia_agent/foundry_client.py`, no un nuevo runtime.

URL global comprobada en la inspección AWS anterior del 10-sep:
`https://prod-a-xoc-cons-g9hvargsfqeafeap.centralus-01.azurewebsites.net`.
Rutas: `/api/agents/SophiaDurableAgent/run`, `/history`, `/threads/{thread_id}`.
Las tres existen en este repo. `TenantRuntimeSettings` puede sobrescribir el
destino global; no se consultó RDS ni se certifican todos esos overrides.

Pendiente del responsable antes de deploy: commit actualmente publicado, script
y revisión que usa, recurso/slot exacto y configuración del agente Foundry.
Azure CLI local no tiene una sesión iniciada. No hace falta compartir la cuenta,
contraseña, tokens o claves para revisar/commitear este trabajo local.

PROD AWS sigue siendo un release aislado, no `main`. La última evidencia de Chat
corresponde a `fe3b25f13833fb300ad1049b0e7974b37be8e706`; no se volvió a verificar
remotamente ni se modificó en esta implementación.

## 2. Flujo anterior comprobado

`POST /chat` autentica, resuelve tenant efectivo y valida la propiedad de
`AgentSession`; envía mensaje/thread a SOPHIA con un token `agent:invoke`.

En el runtime, `build_tool_registry()` registra `ticket_create`, `ticket_get`,
`ticket_patch` y cuatro consultas de scans. `poll_run()` atiende todas las tools
solicitadas y puede continuar varias rondas. Los handlers llaman `BackendClient`
directamente. Esto describe un camino existente, no garantiza que el backend
desplegado autorice cada operación.

XOC ya reconocía `tool_request`, `toolRequest`, `tool_call`, `toolCall` y podía
ejecutar el gateway; sólo devolvía `metadata.toolGateway`, sin segunda llamada
al modelo. La confirmación C.3 se ejecuta separadamente por acción humana.

## 3. Nuevo flujo implementado

Con `SOPHIA_TOOL_GATEWAY_ENABLED=true` exclusivamente en Chat:

1. XOC resuelve el contexto autenticado y el thread autorizado existentes.
2. Crea request_id propio y capability firmada, limitada a ese cuerpo/fase.
3. Envía `phase=request` a la misma ruta SOPHIA ya configurada.
4. La Function verifica capability/contexto ANTES de obtener credenciales Foundry.
5. Crea/continúa el thread con sólo las cuatro tools READ_ONLY anunciadas.
6. Si Foundry pide una tool, la Function NO la ejecuta: devuelve `tool_request`
   más una continuación opaca firmada, con su thread/run/call y contexto ligados.
7. XOC reutiliza el parser de aliases, construye ToolRequest y ejecuta su
   ToolExecutor/policy con el contexto original. Ignora autoridad del modelo.
8. XOC proyecta ToolResult y envía `phase=result`, con una nueva capability ligada
   al cuerpo, a la misma URL. No sigue redirecciones HTTP.
9. La Function valida continuación/contexto y el run/call pendiente real de Foundry;
   entrega un solo tool output. No añade otro mensaje de usuario ni crea otro run.
10. Devuelve la respuesta natural de ese run; XOC conserva sesión e identidad.

Con el flag apagado se conserva el flujo anterior. No hay fallback a ese flujo
si una solicitud del protocolo nuevo falla: una versión incompatible falla cerrada.

## 4. Contrato exacto de tool_request y transporte

Primer cuerpo que XOC construye (no se reenvía el body completo del cliente):

```json
{
  "protocol": "xoc-tool-gateway-v1",
  "phase": "request",
  "request_id": "UUID-generado-por-XOC",
  "message": "Muéstrame mis tickets",
  "user_name": "nombre-resuelto-por-XOC",
  "thread_id": "thread_opcional_validado"
}
```

`chat_mode=consulta` sigue siendo servidor/demostración, con cero tools.
Respuesta intermedia de la Function:

```json
{
  "protocol": "xoc-tool-gateway-v1",
  "request_id": "mismo-request-id",
  "thread_id": "thread_del_run",
  "tool_request": {
    "tool_name": "xoc.tickets.list",
    "arguments": {"limit": 20}
  },
  "continuation": "continuacion-firmada-solo-entre-servidores"
}
```

El parser existente de XOC sigue reconociendo los cuatro aliases. El wrapper D
rechaza múltiples campos de tool, nombres contradictorios o un objeto malformado.
`arguments=null/false/[]/0/""` llega como inválido a policy, no se transforma en `{}`.
No se cambió el contrato compartido `AgentOutput` usado por otros agentes.

## 5. ToolResult al modelo

Se proyecta el contrato existente, no se introduce un segundo executor/DTO:

```json
{
  "request_id": "mismo-request-id",
  "tool_name": "xoc.tickets.list",
  "status": "allowed",
  "code": "allowed",
  "audit_id": "id-del-audit-existente",
  "data": {
    "tickets": [{"ticket_id": "t1", "subject": "VPN", "status": "PENDING"}],
    "count": 1,
    "truncated": false
  }
}
```

Denegación: status `denied`, code genérico `denied`, data `null`. No se envía la
razón interna de policy. Error de proyección: `denied/unavailable/null`.

Proyección tickets: ticket_id/id, subject, status, severity, created_at, updated_at.
Proyección documentos: document_id, document_type, variant, status y fechas.
Unread: sólo count. Máximo 50 filas de las hasta 200 consultadas por el helper;
strings acotados, JSON proyectado hasta 64.000 caracteres y validación defensiva
del tenant de cada fila antes de truncar.

Excluidos: cuerpos completos, parámetros de jobs, filtros, logs, URLs/S3, ARNs,
errores internos, auditorías completas, credenciales y campos de tokens. Hay
redacción adicional para patrones JWT/Bearer/secret en texto; **no es una garantía
DLP universal para cualquier secreto que alguien escriba en un título**.

El resultado final del cliente contiene text/thread_id/session_id y un resumen
seguro `metadata.toolGateway`. Nunca capability ni continuación opaca. C.3 conserva
su confirmation_token únicamente en su respuesta de propuesta humana existente.

## 6. Capability y frontera de confianza

HS256 con el material de firma existente, TTL 120 segundos; type
`sophia_tool_capability`, sub `xoc-sophia-chat`, aud `xoc-ai-tool-gateway`,
scope `ai:tools`, agent_type SOPHIA. Claims preservados:
actor_user_id, actor_role, effective_tenant_id, delegation_active, request_id,
source `sophia_chat`, phase, body_sha256, iat y exp.

No es type=access y no lleva scopes=[agent:invoke]. El verificador legado rechaza
esta capability; no sirve como token del usuario ni de VICTOR. No se emite desde
`/agents/auth/token-from-user`, no se envía al cliente ni se loguea completa.

La continuación usa audiencia distinta `xoc-sophia-tool-continuation` y liga
thread_id, run_id, call_id, hash de la tool y el mismo actor/contexto/request.
El segundo cuerpo está firmado por XOC y el estado pendiente de Foundry se valida
antes de submit. Un replay contra un run completado se rechaza. Esto no introduce
un ledger global de replay para el primer POST; las capabilities siguen siendo
bearer de corta vida, internas, no un mecanismo de exactamente-una-vez distribuido.

La autorización real sigue siendo el ToolContext de XOC. Ni el resultado remoto
ni sus claims reemplazan current_user/tenant/delegación dentro del gateway.
No se modificó `shared/agent_auth.py` del runtime ni el contrato de VICTOR.

## 7. Tools habilitadas

Registry XOC, sin altas ni habilitaciones nuevas:

| Nombre XOC | Alias de función anunciado a Foundry |
| --- | --- |
| xoc.tickets.list | xoc_tickets_list |
| xoc.tickets.get | xoc_tickets_get |
| xoc.documents.list | xoc_documents_list |
| xoc.notifications.unread_count | xoc_notifications_unread_count |

Los aliases son transporte, no nuevas tools ni autorizaciones. Una prueba compara
el catálogo del runtime con el registry real. dashboard.summary e
integrations.summary siguen disabled; no se conectan sus adapters RDS.

**Pendiente de plantilla resuelto localmente con autorización del usuario
(11-sep):** se añadieron dos statements en `serverless.chat.js`, mediante el
`additionalIamStatements` existente. Ambos permiten únicamente `dynamodb:Query`:

- `SophiaReadDocumentJobs`: tabla `xoc-api-reports-<stage>-reports` y su índice
  específico `StatusIndex`, usados por `list_tenant_document_jobs`.
- `SophiaCountUnreadNotifications`: tabla
  `xoc-api-ops-<stage>-user-notification-inbox` y su índice específico
  `UserStatusCreatedAtIndex`, usado por `unread_count_for_user` con Select=COUNT.

Los ARNs usan la región/cuenta de Serverless y el stage seleccionado. No se
añadieron comodines, Scan, escrituras, acceso S3 ni recursos DynamoDB nuevos.
La variable `USER_NOTIFICATION_INBOX_TABLE_NAME` se configuró sólo en
`chatAgentsApi`; `REPORTS_TABLE_NAME` ya viene del entorno común y no cambió.
Las definiciones de las tablas/índices de reports y ops permanecen intactas.
Referencia IAM consultada: [tablas e índices de DynamoDB](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/iam-policy-specific-table-indexes.html).

Esto NO convierte todo el rol de Chat en read-only: se conservaron sus permisos
anteriores, incluido el bloque DynamoDB amplio preexistente de dev y las
escrituras de tickets en prod. Los nuevos grants pertenecen al rol compartido de
la Lambda de Chat, no a una ruta individual. El aislamiento tenant/user sigue
en ToolContext/policy/helpers; no se cambió esa autorización ni la lógica Voice.

Las pruebas locales verifican plantilla y protocolo, **no acceso efectivo en AWS**.
No se comprobó ni modificó el IAM desplegado. Fase D sigue apagada por defecto;
falta publicar coordinadamente y hacer smoke real antes de certificar las tools.

## 8. Policy, ownership y auditoría

USER/ADMIN: READ_ONLY según registry. ADMIN_XOC: delegación válida y tenant efectivo.
SUPERADMIN: bloqueado. La Function también rechaza ese contexto antes de Foundry;
el gateway conserva su DENY para cualquier petición que lo alcance.

Se amplió focalizadamente la lista de claves de autoridad prohibidas de policy
(role, actor, delegation, access/refresh token, JWT y aliases) para asegurar
cero entrada al handler, no sólo rechazo posterior del argumento en el handler.
No se amplió ninguna autorización de rol/tool.

`tickets.get` usa el helper real `get_tenant_ticket_or_404`, con clave DynamoDB
existente pk `TICKET#<tenant>`, sk `TICKET#<ticket>`; no usa una búsqueda global.
Listados usan los helpers de tenant; unread usa tenant y usuario autenticados.

Se mantiene el audit existente: audit_id, request_id, tool_name, tenant efectivo,
usuario, rol, source, decision/code y risk/access level. No argumentos completos,
JWT, documentos, secretos ni nuevo sistema de auditoría/persistencia.

## 9. Anti-loop y errores

Por petición XOC: máximo un tool_request, una ejecución de ToolExecutor y un
segundo HTTP al runtime. En Foundry se anuncian sólo funciones READ_ONLY y
parallel_tool_calls=false. Varias tools iniciales se rechazan sin ejecutar.
Si el segundo turno solicita otra tool, se corta la cadena sin ejecutarla.
XOC tiene además un guard independiente para cualquier alias de segunda tool.

No retries del protocolo D, ni reejecución de tools ante timeout/HTTP error,
ni fallback a BackendClient, SQL, shell, APIs administrativas o herramientas legacy.
La Function entrega errores genéricos; no usa el error_response legado con traceback.
Si el segundo turno falla, XOC mantiene thread/sesión y avisa que no se reintentó
la herramienta. Una respuesta de otro thread nunca se adopta.

Presupuesto runtime: 8 s por fase, HTTP Foundry hasta 4 s por operación/restante.
XOC: hasta 9 s por llamada, presupuesto de 18 s para la secuencia HTTP; no es un
hard deadline transaccional de todos los accesos DynamoDB/RDS existentes.
Se conserva el timeout compartido de Lambda de 20 s; no se cambia Voice para D.
Hay que medir cold starts/latencia del modelo en smoke; no se prometen esos tiempos.

Cleanup best-effort cancela únicamente el run Foundry de esta petición que quedó
pendiente por error/segunda tool. **No cancela Step Functions de AWS**, ni busca
otros runs. Si Azure no confirma cleanup, puede permanecer activo hasta expirar;
no se compensa ejecutando otra tool ni eliminando el thread.

## 10. Archivos modificados por repo

XOC-API-AWS:

- `src/shared/sophia_tool_chat.py`: capability, proyección segura y segundo turno.
- `src/handlers/routes/chat.py`: rama opt-in, manteniendo sesiones y C.3.
- `src/tool_gateway/policy.py`: rechazo temprano de argumentos de autoridad.
- `serverless.chat.js`: flag default false, variable Inbox sólo de Chat y dos
  grants Query acotados a reports/Inbox y sus índices específicos.
- `tests/test_sophia_phase_d.py`: pruebas nuevas.
- `tests/test_sophia_phase_d_infra.py`: cuatro pruebas offline de plantilla,
  cada una validada en dev/staging/prod; requiere Node.js, no AWS/Serverless CLI.
- Este reporte y actualización del encabezado del reporte histórico de discovery.

XOC_MICROSERVICIO_AGENTES:

- `sophia_agent/gateway_protocol.py`: capability/continuación y validación.
- `sophia_agent/gateway_runtime.py`: protocolo bounded sobre Foundry existente.
- `sophia_agent/handler.py`: despacho opt-in en la misma ruta.
- `sophia_agent/foundry_client.py`: timeout por instancia, default legado intacto.
- `tests/test_gateway_runtime.py`, `tests/test_gateway_xoc_contract.py`.
- `PHASE_D.md`: contrato y handoff al responsable de Azure.

Sin cambios en árboles Mobile/Web, VICTOR, Voice/proxy, Step Functions, Push/Inbox,
autorización reports/documents, modelos SQL, migraciones, backfill ni idempotencia C.3.
Sin dependencias de producción nuevas. Se creó `.venv` local ignorado para pytest.

## 11. Pruebas y reproducibilidad

Baseline ejecutado antes de editar: 75 pruebas enfocadas backend OK y 95 runtime OK.
Baseline histórico completo backend: 197, de las cuales 195 OK y 2 fallos VICTOR.
El reporte de ejecución final se indica debajo; no confundir pruebas mock con smoke.

Resultados finales de esta implementación:

| Verificación | Resultado |
| --- | --- |
| Backend: cinco grupos existentes + Fase D + infraestructura | **100/100 OK** (75 existentes + 21 de protocolo + 4 de plantilla). |
| Runtime: suite completa + contrato entre repos (ejecución previa al ajuste IAM; código runtime sin cambios en este ajuste) | **142/142 OK** (95 existentes + 47 nuevas). |
| Backend: unittest discover completo offline después del ajuste IAM | **222 ejecutadas, 220 OK, 2 fallos históricos, 0 errores, 0 omitidas**. |
| git diff --check en ambos repos | OK; sólo avisos de conversión LF/CRLF de Git. |
| Config Chat evaluada con Node frente a HEAD, dev/staging/prod | Idéntica salvo dos grants Query y las variables locales flag/Inbox; timeout sigue en 20 s. No se ejecutó package/deploy ni resolución remota de Serverless. |

Fallos históricos, sin tocar código/tests de VICTOR:

1. `test_assess_ticket_automation.AssessTicketAutomationTests.test_global_url_wins_and_marks_source_global`:
   espera `http://10.0.0.5:8000`, obtiene `None`.
2. `test_assess_ticket_automation.AssessTicketAutomationTests.test_normalize_plans_skips_empty_and_keeps_legacy`:
   espera lista vacía, obtiene un plan normalizado con `cmd=x`.

La suite completa devuelve exit code 1 por esos dos fallos; no se declara verde.
Warnings: uso histórico de datetime.utcnow y fixtures de fallos simulados.
Ninguna prueba envió notificaciones, creó tickets remotos o invocó modelos reales.

Backend enfocado:

```powershell
python -B -m unittest tests.test_sophia_phase_d_infra tests.test_sophia_phase_d tests.test_chat_access_hardening tests.test_chat_tool_gateway_integration tests.test_chat_ticket_confirmations tests.test_tool_gateway_policy tests.test_tool_gateway_executor -q
```

Runtime (desde su repo y entorno local de pruebas):

```powershell
$env:AZURE_AI_PROJECT_API_KEY = 'offline-test-only'
$env:XOC_API_SOURCE = 'C:\Users\saalc\OneDrive\Escritorio\XOC\backend y pruebas\XOC-API-AWS'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
```

La suite de runtime usa mocks; la variable de API key es ficticia y evita que
tests históricos busquen credenciales reales. La prueba entre repos se omite si
XOC_API_SOURCE no se proporciona. No poner una API key real para estas pruebas.

Completa backend: descubrimiento equivalente a
`python -B -m unittest discover -s tests`, ejecutado dentro de un wrapper que
bloquea socket.connect hacia fuera de loopback, con AWS credentials ficticias,
AWS_EC2_METADATA_DISABLED=true, región us-east-1 y Moto local existente.
No deshabilitar logging: algunas pruebas usan assertLogs. Tests históricos intentan
accesos AWS no totalmente mockeados; el wrapper los bloquea. Sin consultas RDS.
La ejecución inicial dentro del sandbox no pudo leer `six.py` de las dependencias
locales de Moto; se repitió con autorización fuera del sandbox, conservando el
bloqueo de red. Los 222 resultados corresponden a esa repetición completa.

Cobertura: happy path, get con clave real, catálogo, desconocidas/disabled/write,
injection, roles/delegación, proyección, audit seguro, aliases, malformed/falsy,
segunda tool, timeout/HTTP errors, continuación firmada/cuerpo/contexto/replay,
dos tenants/dos usuarios, Chat/sesiones y regresión de propuestas/confirmación.

## 12. Seguridad y clasificación de accesos anteriores

Nuevo camino: no tenant/user desde modelo; no secretos en ToolResult; no SQL libre,
shell ni tools destructivas; no SUPERADMIN ni creación automática de tickets.
Los helpers persistentes existentes se reutilizan sin cambiar schemas/query SQL.

Auditado estáticamente en el runtime anterior:

- BackendClient: POST /tickets, GET/PUT /tickets/{id}, consultas /scans/agent/*.
  Son caminos alternativos existentes. D no los instancia ni usa sus handlers.
- Foundry: REST con API key o DefaultAzureCredential; se conserva el cliente.
- `get_agent_token` compartido por SOPHIA/VICTOR: se conserva intacto.
- No se hallaron llamadas directas boto3/DynamoDB/RDS/Secrets Manager/shell en las
  rutas Python revisadas de este runtime. Esto NO demuestra ausencia de permisos
  de su identidad Azure o herramientas configuradas remotamente.
- Errores/logs/health legacy pueden incluir diagnósticos, argumentos y tracebacks.
  El nuevo handler no usa esas respuestas de error. No se endurecieron globalmente
  history/delete/health ni VICTOR de forma incidental.

El modo legacy sigue disponible por compatibilidad; no debe declararse todo el
runtime protegido por D. Tampoco Voice ni clientes que invoquen la Function directamente.

## 13. Riesgos y bloqueos para activación

- HIGH: rutas legacy y firma HS256 compartida preexistentes. La capability es
  distinta y no habilita APIs, pero no elimina el poder del material de firma
  que ya existe en el runtime. Auditoría/rotación/separación de claves requerirían
  un trabajo coordinado independiente; no se cambiaron aquí.
- HIGH para deploy: commit publicado/script/slot y configuración remota no
  verificados. No hacer pull+deploy ciego de main AWS ni asumir paridad PROD/local.
- MEDIUM: IAM/env de reports/Inbox corregidos y probados en la plantilla local,
  pero no desplegados ni verificados en AWS. Ver sección 7; el smoke debe validar
  acceso efectivo a las cuatro tools antes de certificar la integración.
- MEDIUM: inventario de overrides de tenants y permisos del runtime no acreditado.
- MEDIUM: latencia/cold starts y API Foundry `2025-05-01` deben validarse realmente.
  Los mocks prueban el contrato, no el servicio remoto ni su configuración.
- MEDIUM: cleanup del run es best-effort; puede quedar pendiente tras un timeout
  ambiguo. No hay nuevo ledger/idempotencia distribuida de runs.
- LOW: warnings históricos de datetime/fixtures y dos tests históricos de VICTOR.

Referencias primarias consultadas para el transporte existente de Foundry:
[Create Run](https://learn.microsoft.com/ja-jp/rest/api/aifoundry/aiagents/runs/create-run?view=rest-aifoundry-aiagents-v1),
[Submit Tool Outputs](https://learn.microsoft.com/en-us/rest/api/aifoundry/aiagents/runs/submit-tool-outputs-to-run?view=rest-aifoundry-aiagents-v1),
[Cancel Run](https://learn.microsoft.com/ja-jp/rest/api/aifoundry/aiagents/runs/cancel-run?view=rest-aifoundry-aiagents-v1).
La referencia v1 respalda operaciones de threads/runs; no se cambió ni se certificó
la versión efectiva del deployment a partir de esas páginas.

## 14. Compatibilidad y coordinación posterior

Chat legacy, sesión/history/delete y ticket proposals/C.3 siguen cubiertos por
las pruebas existentes. En modo D, una consulta que ya usó una tool no dispara
la heurística genérica de propuesta por palabras como "revisar"; no modifica
el endpoint de confirmación ni permite escribir desde el modelo.

Después de revisión y autorización (NO ejecutado):

1. Registrar diff/commit por cada repo; no mezclar árboles Git.
2. El responsable Azure revisa el script y publica el commit runtime acordado.
   Preservar configuración, secretos y VICTOR; no ejecutar update remoto manual.
3. Preparar release backend aislado desde la base PROD verificada, incluyendo sólo
   D y sus dependencias ya existentes, no multi_planner ni cambios ajenos de main.
4. Inicialmente mantener el flag false; validar la versión del runtime y scope,
   e incluir/verificar el ajuste IAM/env ya autorizado de la sección 7 en el
   release aislado de Chat. No requiere crear tablas ni desplegar ops/reports.
5. Autorizar activación coordinada de SOPHIA_TOOL_GATEWAY_ENABLED=true mediante
   la configuración Serverless del stack Chat. No es un cambio de Mobile/Web.
6. Smoke real: Chat normal/continuación/history, tickets list/get, documentos,
   unread, denial/cross-tenant, segunda tool y propuesta+confirmación humana.
7. Si falla, desactivar la rama D o revertir el release acordado. Esto no convierte
   el modo legacy en gobernado ni justifica ejecutar una tool alternativa.

## 15. Decisión de salida

¿Lista para commit? **Sí, como implementación local opt-in con los pendientes
documentados; no como release certificado listo para activación.**
No se realizó commit ni push en esta tarea.

¿Lista para deploy controlado? **No todavía.** IAM/env local queda resuelto; falta
cerrar la revisión del release, trazabilidad publicada/script y coordinación con
el responsable Azure, y autorizar el despliegue. Nada se publicó en esta tarea.

¿Puede comenzar MCP-1 ya? **No.** Primero deploy controlado de D y smoke real de D
aprobado. MCP reutilizará gateway/policy/context/audit; no se implementó ni inició.
