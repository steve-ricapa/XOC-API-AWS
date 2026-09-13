# SOPHIA — D.1 parcial: cierre de entrypoints y operaciones de thread

Fecha: 2026-09-13. **Implementado y validado localmente, sin commit/push/deploy.**

El usuario autorizó continuar con el alcance parcial después del gate documentado
en `sophia-phase-d1-scope-gate.md`: `/run`, history y delete de SOPHIA Chat.
Las APIs directas de tickets/scans, la identidad compartida de Voice y los tokens
legacy sin tipo quedan pendientes. **Esto no es un cierre integral de D.1.**

## 1. Estado Git y límites

| Repo | Rama | HEAD preservado |
| --- | --- | --- |
| XOC-API-AWS | main | e1fe34c56f288093d57f04507d6951e4998e0558 |
| XOC_MICROSERVICIO_AGENTES | main | fbf395a926757f72ff966f024e586265f67adbef |

Se conservaron los dos commits locales de D en cada repositorio y el informe del
gate anterior. No se tocaron las ramas/revisiones aisladas de PROD. Main no se
considera equivalente al código desplegado.

No se modificaron Voice/proxy, web/mobile, VICTOR/shared agent auth/workers,
Step Functions, RDS/models/schema/SQL/migraciones, login/access/refresh,
token-from-user, registry de tools, C.3 ni permisos IAM.

## 2. Azure `/run`: enforcement exclusivo de SOPHIA

Nuevo flag de servidor: `SOPHIA_GATEWAY_REQUIRED`, default `false`.

- OFF: conserva el dispatcher legacy; el protocolo D existente sigue disponible.
- ON: protocolo ausente, desconocido, JSON inválido o body que intente desactivar
  la protección devuelve 403 `gateway_required`, antes de FoundryClient,
  BackendClient, build_tool_registry y run_threaded del camino legacy.
- `protocol=xoc-tool-gateway-v1` sigue entrando al verificador D existente: requiere
  capability D válida. Un bearer genérico agent:invoke no basta aunque el body
  declare el protocolo correcto.
- Sólo `false` desactiva este enforcement; valores desconocidos fallan cerrados.
  La configuración no se obtiene del body, query, JWT ni instrucciones del modelo.

No se rehízo el protocolo D, el parser de tools, el executor, policy ni auditoría.
Se mantienen las cuatro tools READ_ONLY existentes y el límite de una ejecución
y un segundo turno. No hay nuevos permisos WRITE ni MCP.

## 3. History/delete: authority y contrato

Nuevo flag en **la función Chat de AWS**, no global:
`SOPHIA_THREAD_CAPABILITIES_ENABLED`, default `false` en `serverless.chat.js`.

Con ON, las rutas existentes de XOC validan primero:

1. Usuario/contexto autenticado y permisos existentes de lectura del tenant.
2. Coincidencia de AgentSession con usuario y tenant efectivo.
3. Thread persistido, nunca una identidad/ownership elegida por el runtime.

El signer comprueba nuevamente actor/tenant contra esa fila. No consulta tablas
nuevas ni introduce SQL; reutiliza el ownership y modelo existentes.

XOC envía únicamente al runtime un bearer HS256 de servicio:

```text
sub                  = xoc-sophia-chat
type                 = sophia_thread_capability
aud                  = xoc-sophia-thread-operation
scope                = sophia:thread:history | sophia:thread:delete
agent_type           = SOPHIA
source               = sophia_chat
operation            = history | delete
thread_id            = thread de la AgentSession autorizada
actor_user_id        = usuario autenticado
effective_tenant_id  = tenant efectivo autenticado
request_id           = UUID generado por XOC para esta operación
parameters_sha256    = hash canónico de operación/thread/limit/order
iat / exp            = emisión y expiración (60 segundos)
```

Headers: `Authorization: Bearer ...` y `X-XOC-Request-Id` con ese request_id.
El header selecciona validación estricta durante la transición; **no concede
autorización**. Azure verifica firma, audience exacta, type/scope/source/sub,
agente, IDs positivos, operación, thread, parámetros, request_id e iat/exp.
Se reutiliza el decoder D, con vida máxima verificable de 120 s; XOC emite 60 s.

History usa GET con thread_id/limit/order; delete usa DELETE con thread en la
ruta. Se rechazan aliases contradictorios, query de otro actor/tenant, cambio de
thread, cambio de parámetros y permiso history utilizado para delete. En modo
capability se aceptan los IDs Foundry `thread_...`/`thread-...`, también usados
por D; el transporte legacy OFF conserva su validación anterior.

Azure verifica **antes de crear FoundryClient**. No introduce lookup propio de
ownership, no reasigna threads y no acepta un user/tenant alternativo del cliente.
La autoridad sigue en XOC. USER/ADMIN y operadores delegados conservan la policy
existente de history; esto NO concede herramientas IA a SUPERADMIN.

Un bearer legítimo robado sigue siendo reutilizable para su operación exacta
dentro de su TTL. No es un token de un solo uso ni prueba de posesión. No se
inventó un almacén de nonces ni una garantía de revocación inmediata.

## 4. Transición y manejo de errores

| Azure required | AWS tools D | AWS thread capabilities | Resultado |
| --- | --- | --- | --- |
| false | false | false | Compatibilidad legacy actual, no cierre de seguridad |
| false | true | true | Transporte nuevo disponible; legacy todavía accesible |
| true | true | true | Entry points SOPHIA protegidos; APIs directas aún pendientes |
| true | false o thread false | según ruta | Ruta desalineada denegada; no fallback legacy |

Azure acepta una capability de thread válida incluso con required=false si XOC
envía el header de operación. Esto permite validar transporte antes del enforcement.
No activa nada por sí mismo. Cambiar required sólo debe considerarse después de
desplegar/verificar ambos transportes y resolver los gates aplicables.

En el transporte seguro:

- XOC no sigue redirects HTTP con la capability.
- Errores de firma/red/proveedor no devuelven JWT, trazas, respuestas crudas ni
  debug diagnostics. No hay reintento con agent:invoke tras un rechazo.
- History valida que el thread de respuesta coincida y proyecta sólo los campos
  públicos existentes; descarta metadata arbitraria y redacta patrones de tokens
  en texto. La redacción de texto no sustituye una política DLP general.
- Un borrado remoto rechazado/fallido mantiene la AgentSession local para permitir
  reintento y conservar ownership. Sólo se elimina localmente al confirmar el
  borrado remoto (200 con thread/deleted válidos o 204), o si no existe thread.
- Con flags OFF se conserva el borrado best effort y transporte anteriores.
- No se loguea la capability; el camino nuevo sólo registra códigos seguros y,
  al completar la operación, operation/request_id.

No se cambia el timeout de Lambda (20 s) ni los timeouts HTTP existentes. La
latencia real sigue siendo un gate de smoke; estos tests no validan tiempos Azure.

## 5. VICTOR, Voice y token-from-user

El handler VICTOR y el verificador compartido no fueron modificados. Se añadieron
regresiones OFF/ON con JWT VICTOR válido y llamadas get/patch a BackendClient
simuladas, comprobando el mismo recorrido y resultado.

`/agents/auth/token-from-user` permanece intacto. Voice es un consumidor real del
token SOPHIA según el diagnóstico del 12-sep; Chat legacy también lo usa como
familia interna de tokens. No se eliminó el endpoint ni se bloquearon esas APIs.
Por eso **los bearer SOPHIA todavía pueden alcanzar endpoints directos de tools**
según sus controles actuales. El flag Azure no es una barrera para esas APIs AWS.

Los tokens sin agent_type permanecen como gate pendiente: el worker actual sí
emite VICTOR tipado, pero no se certificó la transición de todos los emisores
legacy desplegados. No se infiere VICTOR de un claim ausente ni se modifica su
contrato a ciegas en esta entrega parcial.

## 6. Archivos modificados

Backend:

- `src/shared/sophia_thread_operations.py` (nuevo signer/proyección segura).
- `src/handlers/routes/chat.py` (transporte opt-in history/delete).
- `serverless.chat.js` (nuevo flag OFF sólo en Chat).
- `tests/test_sophia_thread_operations.py` (14 pruebas).
- `tests/test_sophia_phase_d_infra.py` (expectativa del flag; mismos IAM/timeouts).
- `docs/sophia-phase-d1-scope-gate.md` (evidencia del gate previo y enlace al cierre).
- `docs/sophia-phase-d1-partial.md` (este reporte).

Runtime:

- `sophia_agent/thread_operations.py` (verificador/entrypoint de operaciones).
- `sophia_agent/handler.py` (gates antes del dispatcher legacy).
- `local.settings.json.example` (flag OFF, no configuración real).
- `tests/test_gateway_enforcement.py` (73 casos de enforcement/regresión).
- `tests/test_thread_xoc_contract.py` (5 casos cruzando ambos repos).
- `PHASE_D.md` (estado parcial y configuración de transición).

## 7. Pruebas ejecutadas

Todo offline: sockets externos bloqueados, credenciales ficticias, metadata EC2
deshabilitada, proveedor/HTTP simulados y AgentSession en SQLite en memoria.
Sin invocar modelos, endpoints productivos, tickets reales ni RDS.

| Suite | Antes de editar | Después |
| --- | --- | --- |
| Backend completa | 222: 220 OK, 2 fallos históricos | 236: 234 OK, mismos 2 fallos |
| Backend focalizada D/D.1/C.3/policy/executor/infra | — | 114 OK |
| Runtime completa, incluido contrato entre repos | 142 OK | 220 OK |

Nuevos casos: 14 backend + 78 runtime = 92. Las cinco pruebas nuevas entre repos
ejecutan XOC ownership + firma real + verificación real Azure + serialización
HTTP, con proveedor simulado: happy path, otro thread, history usado para delete,
otro usuario y otro tenant. También permanecen las seis integraciones D anteriores.

Fallos históricos backend, no corregidos ni ocultados:

- `test_global_url_wins_and_marks_source_global`: URL esperada frente a None.
- `test_normalize_plans_skips_empty_and_keeps_legacy`: [] esperado frente a plan cmd=x.

Comandos lógicos (ejecutados mediante wrapper offline de entorno/socket):

```text
Backend: python -B -m unittest discover -s tests
Runtime: .venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
XOC_API_SOURCE=<ruta local revisada de XOC-API-AWS> para los contratos cruzados
```

Backend usó Moto ya instalado en `.tmp-ticket-sync-tests` mediante sys.path y
lectura autorizada fuera del sandbox. No se instalaron dependencias. La primera
ejecución falló por permisos de lectura; el wrapper se ajustó para no deshabilitar
assertLogs. El baseline válido y el resultado final de la tabla usan la misma
configuración, con captura de logs de los tests habilitada. Runtime conserva seis
warnings históricos de datetime.utcnow. `git diff --check` sin errores de whitespace.

## 8. Evidencia operativa Azure y script

Revalidación sólo lectura el 13-sep:

- Sesión CLI válida para XOC-AZURE, suscripción
  `884feb5e-8492-4d88-8777-6391393372f5`, tenant `50ac7fef-2120-437f-ad31-6beb036a3723`.
- `az storage blob list --account-name rgsophiapoc8952 --container-name scm-releases
  --auth-mode login`: denegado con `You do not have the required permissions needed
  to perform this operation.` No se obtuvieron blob/ETag/bytes ni commit desplegado.
- Pedir **Storage Blob Data Reader** al principal que hace la auditoría sobre el
  contenedor scm-releases, o un artefacto entregado por el responsable por un canal
  autorizado. No se usaron Storage keys, SAS, connection strings ni publish profiles.
- No apareció script de deploy en el runtime ni en `.agents`/`.deployment-audits`
  inspeccionados; las coincidencias fueron documentación. No se inventó ni ejecutó
  un comando sustituto. Sigue pendiente el script y SHA del responsable Azure.

Recurso/slot y base AWS acreditados anteriormente se conservan como evidencia
fechada en `sophia-phase-d-predeploy-gate.md`, no como nueva comparación PROD de hoy.

## 9. Riesgos y decisión

- **HIGH / funcional-seguridad:** APIs directas SOPHIA, identidad compartida Voice
  y transición legacy sin tipo todavía abiertas. No activar un bloqueo global.
- **HIGH / D.2:** material HS256 compartido permite a quien lo posee fabricar
  capabilities. Audiences/scopes no separan criptográficamente a los firmantes.
- **MEDIUM / smoke:** latencia, cold starts y ventana de bearer replay acotada por
  TTL. No hay smoke remoto ni garantías nuevas de single-use.
- **Operativo / NO-GO:** bytes/SHA/script Azure y release aislado Chat pendientes.
  No desplegar main ni incluir multi_planner/cambios ajenos.

¿Lista para commit? **Sí, esta D.1 parcial**, con commits separados y revisión de
estos archivos; no se realizó commit ni push automáticamente.

¿Lista para deploy coordinado con flags OFF? **No todavía**, por gates operativos
de artefacto/script/release. El código está preparado OFF, no es permiso de deploy.

¿Activación coordinada y smoke real ahora? **No**, faltan revisión/deploy autorizado
y resolver/documentar los gates de cierre integral.

¿D.2 antes de MCP? **Sí**, como fase separada; no se inició D.2 ni MCP.

¿Existe bypass legacy conocido todavía? **Sí**: con flags OFF, por transición;
y con enforcement Azure ON, las APIs directas AWS siguen fuera de este cierre.
La regla integral «SOPHIA tools sólo mediante Tool Gateway» aún NO está satisfecha.
