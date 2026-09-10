# SOPHIA: controles previos a la integración MCP

Fecha: 2026-09-10. Estado: implementado y probado localmente sobre `70f140b`.
Sin commit, push, despliegue ni consultas a servicios remotos en esta tarea.

## Alcance

Se cierran dos pendientes del Tool Gateway: limitar la identidad de agente
solicitada por usuarios y comprobar la pertenencia local de hilos de Chat/History.
No se implementa todavía un servidor MCP, una capability nueva, la segunda
llamada al modelo con resultados de herramientas ni integración de Voice.

Archivos de aplicación modificados:

- `src/handlers/routes/agents.py`
- `src/handlers/routes/chat.py`

Se agregan pruebas en `tests/test_chat_access_hardening.py` y documentación.
No se modifican workers, Step Functions, aprobaciones, planes, creación/confirmación
de tickets, push, frontend web, mobile, runtime externo, JWT de login ni infraestructura.

## 1. Intercambio de token de agente

`POST /agents/auth/token-from-user` acepta exclusivamente `SOPHIA`.

- Body omitido, `null` o `{}`: se conserva el valor predeterminado `SOPHIA`.
- `agentType` string: se normalizan espacios exteriores y capitalización.
- `agentType` nulo explícito, vacío, no string o fuera de la lista: 400
  `validation_error`, mensaje fijo `Unsupported agentType`.
- Un body que no es objeto se rechaza (la capa HTTP puede devolver 422 por tipo).
- Se validan primero los permisos existentes y el tenant efectivo. No se cambia
  la política de roles/delegación de este endpoint.
- No se aceptan tenant, usuario, scopes ni rol del body para construir claims.
- Se preservan formato de respuesta, scope `agent:invoke`, duración de una hora
  y auditoría existentes. No se agregan logs de tokens.

La lista es deliberadamente `SOPHIA`, no todos los tipos de `AgentInstance`:
`SVAFUNC` identifica configuración de infraestructura, no un permiso de invocación
otorgable por este endpoint. `VICTOR` sigue obteniendo su token desde los workers
existentes; esos tokens y su ejecución no se modifican.

Se hizo opcional el body porque el servicio web revisado llama al endpoint sin
body. No se renombra `access_token` a `agent_token`: el tipo legacy del método web
no coincide con ese campo y no se cambia en este alcance.

## 2. Pertenencia de conversaciones

Se reutilizan el modelo `AgentSession` y la tabla existente `agent_sessions`.
La autoridad es el usuario autenticado y su tenant efectivo, nunca el hilo del
body/query por sí solo.

| Solicitud | Resolución |
| --- | --- |
| `session_id` / `sessionId` | Buscar sesión por id + usuario + tenant efectivo. |
| `thread_id` / `threadId` | Buscar vínculo local por hilo + usuario + tenant efectivo. |
| Sesión e hilo juntos | Exigir que ambos identifiquen la misma fila. |
| Ambos alias | Aceptar si coinciden tras normalización; rechazar contradicciones. |
| Chat sin IDs | Conservar selección de última sesión `sophia_chat` del mismo usuario/tenant, o crear conversación. |
| Chat con `new_session` | Conservar conversación nueva; ignorar IDs viejos y no reenviarlos ni usar sus cookies. |
| Chat demo | Conservar selección de `sophia_demo` del mismo usuario/tenant; IDs del cliente no seleccionan otro hilo. |
| History sin vínculo o sin hilo guardado | Rechazar; no consultar el runtime. |

Los IDs de sesión explícitos inválidos ya no caen silenciosamente en la última
conversación. Los de hilo son strings opacos de hasta 500 caracteres, sin espacios
exteriores ni caracteres de control; no se exige prefijo de un proveedor específico.
`null` en un identificador opcional se trata como ausencia, no como autorización.

Una sesión inexistente y una ajena devuelven el mismo 400 `validation_error` /
`Agent session not found`. No se revelan dueño, tenant ni contenido. Si la consulta
local falla, tampoco se reenvía el hilo como alternativa.

El hilo enviado al runtime sale de la fila autorizada. En acceso directo por hilo,
esa fila es también la que recibe actualizaciones y proporciona cookies de afinidad;
no se reemplaza el hilo de otra conversación reciente.

## 3. Protección del vínculo al recibir respuestas

Antes de procesar herramientas o propuestas de una respuesta exitosa:

- Un hilo de continuación no puede ser reemplazado por otro ID del runtime.
- Un hilo nuevo no puede adoptar un ID que ya esté vinculado localmente a otro
  usuario o tenant.
- Un ID malformado, sustitución o vínculo ajeno produce 502
  `sophia_thread_mismatch` con mensaje fijo, sin persistir el cambio.

Esto no convierte al runtime externo en confiable ni sustituye su auditoría.
No hay un índice único nuevo ni garantía transaccional global de identidad de
hilos; se confía en que el runtime genere identificadores únicos. No se hizo un
backfill de vínculos antiguos, limpieza de duplicados ni recuperación automática.
La selección local y la llamada externa tampoco forman una transacción distribuida.

## 4. Datos y compatibilidad

No hay SQL textual, migraciones, tablas, índices, columnas o modelos nuevos.
Se agregan filtros ORM sobre el modelo existente. El código conserva las escrituras
normales de sesiones y auditoría; **no** se afirma que Chat deje de usar RDS.
Durante esta tarea no se conectó a RDS real ni se ejecutaron escrituras remotas.

Web/mobile normales continúan enviando `session_id` o `new_session`. Demo puede
enviar un hilo, pero el backend sigue resolviendo su propia sesión demo. No se
necesita una nueva build nativa por este cambio backend.

Impactos intencionales que deben comunicarse antes de desplegar:

- Scripts/clientes que intercambian un token de usuario por `VICTOR`, `SVAFUNC`
  u otro tipo serán rechazados. No se encontró ese uso en los clientes revisados.
- Un hilo histórico que sólo existe en el proveedor y carece de vínculo local
  ya no puede usarse pasando su ID. Abrir una conversación nueva o revisar su
  recuperación mediante una tarea autorizada; no adoptar IDs a ciegas.
- Si el runtime rota el ID de una conversación existente, se devolverá 502 en
  vez de aceptar la sustitución silenciosa. Validar ese contrato en el smoke real.
- La comprobación de vínculo por `external_thread_id` debe observarse en cuanto
  a latencia en producción; no se añade un índice dentro de esta tarea.

## 5. Pruebas

30 pruebas nuevas, todas OK. Se usa SQLite efímero en memoria con la tabla del
modelo existente para evaluar filtros ORM reales; no un mock que siempre devuelve
la sesión esperada. HTTP externo, firma y auditoría del intercambio se mockean.
También se prueba el endpoint HTTP de intercambio con `TestClient`, incluido body
omitido. `httpx` se usa únicamente para pruebas y ya estaba instalado localmente.

Cobertura: allowlist y tipos inválidos, identidad confiable, roles/delegación,
aliases, pertenencia usuario/tenant, pareja sesión/hilo contradictoria, ausencia de
llamadas externas al denegar, conversación inicial/nueva/demo, afinidad, timeout,
error de DB y rechazo de sustitución/adopción de hilos desde el runtime.

Regresión enfocada: **75 pruebas OK**:

```powershell
python -B -m unittest tests.test_chat_access_hardening tests.test_chat_tool_gateway_integration tests.test_chat_ticket_confirmations tests.test_tool_gateway_policy tests.test_tool_gateway_executor -q
```

Suite completa final con Moto disponible, credenciales ficticias y conexiones
externas bloqueadas por el arnés de prueba: **197 ejecutadas; 195 OK y 2 fallos
preexistentes** en `test_assess_ticket_automation`:

- `test_global_url_wins_and_marks_source_global`
- `test_normalize_plans_skips_empty_and_keeps_legacy`

Son los fallos ya documentados antes de esta tarea. Ni ese worker ni sus pruebas
se modificaron. No se considera la suite completa verde ni se arregla planificación
como parte de estos controles. El arnés permite loopback para el event loop de
Windows y TestClient, pero bloquea conexiones externas; algunos tests legacy
intentan acceder a AWS sin mock y sus excepciones se capturan por el código existente.

## 6. Próximos pasos, sin ejecución automática

1. Revisar/autorizar commit y despliegue acotado del stack Chat, comprobando qué
   código está realmente desplegado. No desplegar `main` completo de forma ciega:
   existen releases de producción aislados para cambios anteriores.
2. Smoke con dos usuarios y dos tenants: abrir/continuar/historial, nueva
   conversación, demo, delegación y rechazos cruzados sin llamadas externas.
3. Repetir propuesta -> confirmar -> mismo ticket ante replay; comprobar que el
   resultado de automatización y la sincronización mobile siguen su flujo normal.
4. Coordinar el runtime externo para capabilities limitadas y retorno de resultados
   al modelo antes de declarar integrada la parte MCP; Voice requiere revisión aparte.

La sincronización visual web sigue pendiente y no se incluye en este cambio.
