# SOPHIA Chat / MCP — IC-1 LOCAL

## Gates antes de implementar

Fuente MCP: `../XOC-MCP`, origin `steve-ricapa/XOC-MCP`, HEAD
`20fde196364bfcd2e006208cfb279549146c68a0`. Backend HEAD
`ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9`. Ambas ramas locales:
`feature/sophia-chat-mcp-ic1`. No push/deploy. Tres documentos preexistentes
sin seguimiento del backend se conservan fuera del cambio IC-1.

Gate 0: 161 archivos, 159 contenidos + manifest + sumas. Con autorización
explícita se restauraron LF y se retiró únicamente el título añadido al README.
Todos los hashes de la entrega coinciden. SDK 1.30.0, MCP 2025-11-25.
Core SHA256 `df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41`.
L6 SHA256 `6fa74cf9fc336d52c7900ab4c35f3c4fd82e6e1e2b60c57ccc63156b2c0908d6`.
Los manifests históricos no se regeneran para encubrir cambios.

Gate 1: Core reconstruido desde blobs Git; L6 reconstruido desde el clone;
ambos hashes exactos. Imagen Linux reconstruida desde el Dockerfile del clone:
`sha256:ffd2cdc17c30289ca4eaa3787cabe8190c38017f1840ad2020a7baef2b6c8df4`.
Smoke stdio, L1, L6 supervisado y provenance pasan. No se monta XOC-MCP-LAB.
Un primer test Windows tuvo 17 errores de permisos en pytest temporal; se
repite con TEMP/TMP propios, sin modificar tests.

## Gate 2 — mapa vigente, leído antes de editar código

`src/handlers/routes/chat.py::proxy_chat` es la frontera inferior a POST /chat.
La dependencia `src/shared/dependencies.py::get_current_user` resuelve actor
desde claims autenticados y registro de usuario; `effective_tenant_id_of` y
`require_tenant_read_access` aplican el contexto/delegación. No es identidad
elegida por el modelo. IC-1 usa claims y usuario ficticios en esa frontera;
no invoca RDS. Las sesiones se prueban en SQLite temporal en memoria.

Recorrido vigente:
1. `proxy_chat`: autorización, tenant efectivo, `_owned_chat_session`.
2. `_resolve_agent_routes`: destino runtime configurado (fixture en IC-1).
3. `_chat_tool_context`: tenant original, efectivo, actor, role, delegación,
   request_id generado por backend, source `sophia_chat`.
4. `sophia_tool_chat.enabled`: flag `SOPHIA_TOOL_GATEWAY_ENABLED`, default false.
5. `capability`: protocolo `xoc-tool-gateway-v1`, fase request; runtime turno 1.
6. `sophia_tool_chat.finish_turn`: valida envelope y thread; `_tool_request`
   reutiliza `_clean_agent_response` y sus cuatro aliases.
7. ANTES de IC-1: `ToolExecutor().execute(context, ToolRequest(...))` aquí.
8. `model_result`: proyección de campos permitidos de ToolResult.
9. `proxy_chat.post_tool_result`: mismo thread/continuation; turno 2.
10. `finish_turn`: ningún segundo tool; respuesta final/metadata segura.
11. Propuesta C.3 y actualización de AgentSession existentes.

Deadline gobernado: monotonic +18 s antes del primer HTTP, máximo 9 s por
turno runtime y restante para el segundo; Lambda compartida default 20 s.
Timeout legacy default 60 s no describe el camino gobernado. No se aumenta
ninguno. Las consultas de sesión previas están fuera de aquel presupuesto
existente; IC-1 no acredita SLA cloud ni cambia esa semántica.

Errores: tool denied/error se proyecta sin datos; timeout/4xx/5xx gobernado
no reintenta legacy; fallo turno 2 no repite herramienta. `_validate_response_thread_binding`
rechaza sustitución/propiedad ajena. C.3 exige confirmación explícita.

Clasificación:
- A CURRENT GOVERNED: Phase D anterior con Gateway local. También el helper
  `_maybe_execute_chat_tool_request` del modo anterior usa Policy/Executor;
  no confundirlo con un bypass sin policy. Su docstring sobre runtime futuro
  es histórica frente a la implementación Phase D actual.
- B MCP CANDIDATE: sustituir sólo la ejecución en `finish_turn` mediante puerto.
- C ALTERNATIVE: transporte runtime legacy cuando flag false y su token
  `agent:invoke`; fuera de esta integración, no eliminado ni usado como fallback.
  No se auditan ni cambian infraestructura remota, Voice o VICTOR.

## Implementación

`src/shared/sophia_tool_execution.py` define el puerto mínimo y un ContextVar
con composición confiable `local_execution(port)`. Sin composición, comportamiento
actual intacto. No existe selector desde body, query, header, prompt o `_meta`.
No se añade flag a Serverless ni dependencia MCP a requirements productivos.
La composición local se rechaza en Lambda/Azure y exige Phase D habilitada;
flag false nunca puede convertir una petición MCP en ejecución legacy.

`src/shared/sophia_mcp_client.py::LocalMCPExecution` usa el SDK oficial instalado
en el harness: initialize, una tools/call, cierre. Conexión nueva por petición,
HTTP `127.0.0.1:<puerto>/mcp`, sin redirects (también los gestionados por SDK),
sin retry, trust_env=false, respuesta acotada. No contiene firmador ni private key.
El issuer inyectado recibe ToolContext y ToolRequest, nunca profile MCP.
La selección no guarda actor/tenant en un pool. El ContextVar se restaura al salir.

`finish_turn` recibe el deadline existente; sólo su ejecución cambia. MCP devuelve
ToolResult validado contra request_id, tool_name, status, code, audit_id y coherencia
text/structuredContent. `model_result` existente vuelve a proyectar campos antes
de entregar al modelo. Fallo MCP => denied/unavailable sin executor alternativo.
No queda margen (<1,25 s) => cero firma/tool. Cada operación MCP usa como máximo
3,5 s y el restante de Chat, deadline entero redondeado hacia abajo. El tiempo de
importación del SDK está dentro del límite monotónico. No se aumenta Chat.

`tests/ic1/fixture_issuer.py` firma exclusivamente con el escalar privado de pruebas
L1/L6 ES256, deliberadamente público (TEST FIXTURE ONLY, no secreto real).
No es una identidad productiva. El verificador original
restringe tenants 91001/91002, usuarios 92000–92999 y request_id `lab-*`.
El test controla el generador UUID del backend para producir ese prefijo; el
cliente no reescribe correlaciones ni acepta un request_id del body/modelo.
Las claims se generan desde `dependencies.get_current_user` con usuario/claims
fake: actor, role, tenant, effective_tenant, delegation, source, request y binding
del nombre/argumentos. MCP verifica firma/binding/deadline y vuelve a evaluar Policy.

El servidor L6, supervisor, Policy, Executor, handlers, locks y wheels no cambian.
Cuatro READ_ONLY: tickets.list/get, documents.list, notifications.unread_count.
Unknown/disabled/WRITE/authority injection se deniegan; ADMIN_XOC requiere
delegación, SUPERADMIN denegado por Policy. GET usa ownership canónico del tenant.

## Reproducción local

No requiere XOC-MCP-LAB ni servicios cloud. Preparar `../XOC-MCP` como indica su
README y reconstruir `xoc-mcp-ic1:baseline` desde `build/ic1-execution/Dockerfile.handoff`.
Para un clone Windows conservar LF (`git config core.autocrlf false` sólo en ese
repo). Se aplicó esa configuración local después de la corrección autorizada.
Git almacena el source original; el único diff versionado MCP es retirar el
título README añadido. `reports/l2-reference-windows.json` es material generado
por prepare, copia verificada del fixture, no nueva evidencia ni cambio de negocio.

Desde XOC-MCP, dependencias adicionales del harness (no del servidor baseline):

```powershell
.\.venv\Scripts\python.exe -m pip --isolated download --index-url https://pypi.org/simple --require-hashes --only-binary=:all: --no-deps --platform manylinux2014_x86_64 --python-version 311 --implementation cp --abi cp311 --abi abi3 --abi none --dest build/ic1-extra -r ../XOC-API-AWS/tests/ic1/requirements-linux.lock.txt
```

Desde XOC-API-AWS, usando directorios/nombres de evidencia NUEVOS por ejecución:

```powershell
python -B tests/ic1/prepare.py --mcp ../XOC-MCP --output tests/ic1/.build/repro01
docker build --pull=false --network none --platform linux/amd64 -t xoc-chat-mcp-ic1:local tests/ic1/.build/repro01
python -B tests/ic1/execute.py --output tests/ic1/.build/evidence/repro01.json
```

El snapshot copia Python real del backend, no .env/credenciales. Los hijos L6
corren `-I` con paquetes canónicos instalados, sin el source backend en su path.
El proceso Chat importa su source separado y usa SQLite en memoria sólo para
AgentSession. La autoridad se resuelve en la dependencia real de auth con store
ficticio. El runtime HTTP se sustituye por respuestas del contrato Phase D;
no se genera tráfico hacia Azure. Red Docker none, sin mounts/puertos publicados,
512 MiB/2 CPU/128 pids, no root, filesystem read-only/tmpfs. Socket MCP únicamente
en loopback interno. Supervisor real y recuperación habilitada.

Todos los artefactos del harness están en `tests/ic1/.build` (ignorado por Git y
excluido por el patrón productivo existente `!tests/**`). No se modifica infra.
No hay paquete con la clave fixture dentro de `src` productivo.

## Pruebas y limitaciones

Primer E2E: 26/28 correctos; dos fixtures intentaban reutilizar thread de otro actor.
El backend los rechazó correctamente. Se corrigió exclusivamente la respuesta del
runtime fake para asignar threads nuevos por sesión, sin debilitar ownership.
Segundo E2E: I01–I30 pasan en 28 tests, 0 errores/fallos, servidor supervisado,
0 accesos externos/secretos y cleanup sin hijos residuales. Evidencia conservada
en `tests/ic1/.build/evidence/attempt02.json`. Corrida final: **32 PASS**, 0 fallos,
0 errores, 5,311 s; incluye cuatro tests adicionales de redirects, flag false,
deadline tras runtime1 y assertion acotada al presupuesto. Evidencia:
`tests/ic1/.build/evidence/final.json`, SHA256
`466f085e875cbfb55f208a9ee5417293aecb8fabac7fa78c0b0cd91d669a0126`.
Imagen E2E final:
`sha256:f904e697b328cf246b8190c0b9bda4558dfa34535736ef43a5d14c601b6eb992`.
No hay accesos externos ni a archivos de secretos. Supervisor reaped/absent,
sin hijos residuales y sin limpieza forzada por el harness. Docker no conserva
contenedores de estas ejecuciones. El script diagnóstico añadido después no
forma parte de esa imagen E2E; sí se ejecutó por separado sobre el baseline.

Backend: 114 focales PASS + 6 nuevas unitarias PASS. Completa: 255 ejecutados,
253 PASS y los mismos dos fallos históricos de `test_assess_ticket_automation`:
`test_global_url_wins_and_marks_source_global` y
`test_normalize_plans_skips_empty_and_keeps_legacy`. El baseline previo de 249/247/2
está en XOC-MCP/docs/evidence/l1-resultado.md. No se tocaron esos tests/funciones.

MCP Windows: 407 PASS / 39 skips POSIX. Linux primer intento: 445 PASS / 1 FAIL
en `test_concurrent_calls_same_instance_only_one_child` (esperaba 1 completed,
observó 0). Segunda suite Linux en serie: también **445 PASS / 1 FAIL**, pero
el primero pasó y falló `test_inherited_secrets_never_used`: esperaba `completed`
y recibió `error`. Las dos evidencias completas permanecen en XOC-MCP:
`build/ic1-validation/tests` y `build/ic1-validation/tests-serial`.
No se modifica test, timeout ni implementación L2/L6.

Diagnóstico focalizado: los dos tests originales, cinco rondas, **10/10 PASS**,
misma imagen inmutable, sin red ni mounts. Un observador del harness registra
sólo status/código/timings/lifecycle, sin cambiar resultados ni assertions.
Consultas completas: 2,404–2,727 s, límite 5 s, todos los hijos reaped,
sin overruns/timeouts observados. El rechazo `lab_call_limit` del segundo intento
concurrente es el resultado esperado. Evidencia:
`tests/ic1/.build/evidence/baseline-focused.json`, SHA256
`ebd7ac4309465821b1414a23c2e907402af809b28804f45d6eac9dac43c31aa0`.

Esto NO reemplaza las dos suites completas ni acredita 446/446. La causa de
los fallos en suite sigue sin aislarse: carga, orden o presupuesto son hipótesis,
no diagnóstico confirmado. El fallo de status no acredita fuga de secretos.
El baseline ejecutado no contiene los cambios IC-1 del backend; tiene los
mismos hashes/imagen reconstruidos del handoff. No atribuirle el fallo a IC-1
sin evidencia, pero tampoco declarar su gate completamente verde.

No se acredita autenticación cloud, entrega exactamente una vez, SLA cloud ni
datos reales. Un fallo/timeout no reintenta una herramienta: puede perder una
consulta sin duplicarla desde Chat. El fixture de timeout bloquea la respuesta
SDK; recuperación bajo fallos de proceso pertenece al baseline L6, no a un cambio
de esta fase. IC-1 no cierra bypass remotos ni habilita productos.

## Cierre

Fecha: 17-sep-2026. **Resultado global: INCONCLUSO** por el gate de regresión Linux
del baseline inmutable. Implementación y E2E IC-1: PASS LOCAL. No se ha observado
un NO-GO de autoridad, fallback, aislamiento o C.3. No hay aprobación de cloud.

Único siguiente bloque recomendado: **IC-1R — aislar la intermitencia de la suite
Linux del baseline congelado y cerrar la evidencia de regresión**. Conservar
los fallos; no aumentar límites, relajar assertions ni cambiar L6 silenciosamente.
Si el diagnóstico exige modificar el baseline, informar incompatibilidad,
impacto y cambio mínimo antes de hacerlo. No se ejecuta otro bloque aquí.

IC-2 queda diferido hasta que ese gate esté cerrado. Entonces corresponderán
identidad asimétrica productiva, providers READ_ONLY reales y una decisión de
hosting/runtime soportado; AL2 no se selecciona automáticamente para producción.

### Presupuesto observado (no SLA)

| Caso final | Runtime1 fixture | MCP real, total cliente | Runtime2 fixture | Handler Chat total |
| --- | ---: | ---: | ---: | ---: |
| I02 tickets.list, primer SDK import | 0,000015 s | 0,721390 s | 0,000020 s | 0,726836 s |
| I04 documents.list, cliente ya importado | 0,000013 s | 0,024658 s | 0,000015 s | 0,032490 s |
| I17 timeout inyectado en SDK | 0,000007 s | 3,230885 s | 0,000017 s | 3,238917 s |

I02: initialize 0,006340 s, tools/call 0,016370 s; el total incluye importación,
configuración y cierre. La métrica tools_call_count cuenta intentos del cliente:
en I17 el SDK se bloquea antes de enviar la llamada y handler_calls=0. No
confundirlo con una ejecución efectiva del servidor. En el happy path coinciden
una llamada, un handler y dos turnos. Runtime Azure no está medido ni invocado.

### Archivos propios del cambio

Backend, modificados:

- `.gitignore`: ignora sólo el directorio generado `tests/ic1/.build/`.
- `src/handlers/routes/chat.py`: pasa deadline y rechaza composición MCP con
  Phase D deshabilitada; no cambia endpoint de confirmación.
- `src/shared/sophia_tool_chat.py`: puerto opcional en el punto de ejecución.

Backend, nuevos:

- `src/shared/sophia_tool_execution.py`: interfaz/composición local.
- `src/shared/sophia_mcp_client.py`: SDK MCP, presupuesto y validación.
- `tests/test_sophia_mcp_execution.py`: seis tests unitarios sin SDK opcional.
- `tests/ic1/fixture_issuer.py`: emisor fixture desde ToolContext.
- `tests/ic1/integration.py`: matriz E2E y defensas adicionales.
- `tests/ic1/run.py`: servidor supervisado, aislamiento y reporte E2E.
- `tests/ic1/prepare.py`: snapshot con hashes desde repos explícitos.
- `tests/ic1/execute.py`: ejecución Docker aislada y evidencia.
- `tests/ic1/Dockerfile`: harness separado del entorno del servidor baseline.
- `tests/ic1/requirements-linux.lock.txt`: dependencias adicionales del harness.
- `tests/ic1/diagnose_baseline.py`: diagnóstico observacional de los dos tests.
- `docs/sophia-mcp-ic1-local-integration.md`: este informe.

MCP: reparación autorizada LF/CRLF y eliminación del título README añadido.
El diff de contenido Git es **README: 0 inserciones, 1 eliminación**. Git status
todavía marca diversos archivos por la normalización/cache del working tree;
se contrastaron sus bytes: 159 contenidos del manifest, cero discrepancias.
No se hizo staging masivo para ocultarlo. `core.autocrlf=false` es configuración
local de ese repo. `reports/l2-reference-windows.json` sigue generado/untracked.
Ningún source MCP, wheel, lock, Policy, Executor ni manifiesto fue alterado.

Los tres documentos backend ajenos siguen sin seguimiento y sin cambios:
`cases-tenant-hotfix-and-d1c-identity-gate.md`,
`sophia-d1c-proxy-source-verified.md`, `sophia-phase-d1b-inventory-gate.md`.
No hubo stash/reset/clean, modificación de remotes ni push.

### Matriz I01–I30

| ID | Comprobación | Resultado |
| --- | --- | --- |
| I01 | Chat sin tool: cero MCP | PASS |
| I02 | Tickets list: MCP, handler=1, runtime=2 | PASS |
| I03 | Ticket propio y rechazo de ticket de otro tenant | PASS |
| I04 | Documents list y proyección | PASS |
| I05 | Unread del usuario | PASS |
| I06 | A no recibe datos de B | PASS |
| I07 | Dos usuarios del mismo tenant: conteos 2 y 5 | PASS |
| I08 | USER según policy vigente | PASS |
| I09 | ADMIN según policy vigente | PASS |
| I10 | ADMIN_XOC delegado, tenant efectivo correcto | PASS |
| I11 | ADMIN_XOC sin delegación rechazado antes de Chat; policy MCP también deniega | PASS |
| I12 | SUPERADMIN, cero handler | PASS |
| I13 | Unknown y disabled, cero handler | PASS |
| I14 | WRITE no habilitada, cero handler | PASS |
| I15 | Authority injection, cero handler | PASS |
| I16 | MCP caído: error seguro | PASS |
| I17 | Timeout y margen insuficiente | PASS |
| I18 | Resultado inválido rechazado | PASS |
| I19 | request_id distinto rechazado | PASS |
| I20 | tool_name distinto rechazado | PASS |
| I21 | Segunda tool solicitada: no se ejecuta | PASS |
| I22 | Segundo runtime falla: no reejecutar tool | PASS |
| I23 | Sesión ajena rechazada | PASS |
| I24 | Sesión nueva válida | PASS |
| I25 | Continuación propia y sustitución de thread rechazada | PASS |
| I26 | Proposal no crea ticket | PASS |
| I27 | Cancelar/no confirmar: cero creación | PASS |
| I28 | Error MCP: cero fallback local/legacy | PASS |
| I29 | Assertion alterada rechazada | PASS |
| I30 | Modelo/body no cambian autoridad ni transport | PASS |

I26/I27 y I16/I28 comparten métodos; las cuatro pruebas extra llevan el total
a 32 métodos, con subcasos adicionales. No se afirma que todos los fallos de
transporte se hayan inyectado modificando el servidor: I17–I20 y redirect usan
dobles acotados en la frontera cliente para mantener intacto L6.

### Respuestas obligatorias 1–31

1. **Clone/handoff:** sí, tras la reparación explícitamente autorizada. 161
   archivos originales acreditados, 159 contenidos más manifest y sumas.
   Core/L6 hashes constan al inicio. No se usa el antiguo LAB como fuente.
2. **MCP Git:** `feature/sophia-chat-mcp-ic1`, HEAD
   `20fde196364bfcd2e006208cfb279549146c68a0`; working tree con reparación descrita,
   sin staged/commits nuevos; archivo generado untracked indicado arriba.
3. **Backend Git:** misma rama, HEAD
   `ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9`; cambios propios listados y tres
   documentos ajenos conservados; nada staged ni committed.
4. **Recorrido encontrado:** proxy_chat → auth/tenant/sesión → runtime Phase D →
   finish_turn → ejecución → model_result → runtime2 → respuesta/AgentSession.
   El mapa Gate 2 anterior detalla funciones, presupuestos y alternativas.
5. **Ejecución anterior:** `src/shared/sophia_tool_chat.py::finish_turn`,
   `ToolExecutor().execute(context, ToolRequest(...))`; ya gobernada por Policy.
6. **Archivos backend:** listado completo en «Archivos propios del cambio».
7. **Servidor MCP cambiado:** NO; adaptación sólo en cliente/backend/harness.
8. **Selección:** composición local confiable `local_execution(LocalMCPExecution(...))`;
   ContextVar restaurado al salir. Default mantiene ejecución actual.
9. **Usuario/modelo selecciona transporte:** NO. Ningún selector de body/header/
   query/prompt/_meta. Composición cloud rechazada; no nuevo flag de deploy.
10. **Contexto:** auth fixture pasa por dependencia backend real; ToolContext
    resuelto por Chat alimenta issuer fixture. Sin perfiles manuales del cliente.
11. **Autoridad del modelo:** NO. Tenant/user/role/delegación/request/source
    provienen del contexto backend; argumentos de ownership se deniegan.
12. **Credencial:** únicamente firma ES256 TEST FIXTURE ONLY en tests; no secreta
    ni productiva. Ninguna clave fixture entra a src o al paquete Lambda.
13. **Policy dentro de MCP:** SÍ; assertion verifica contexto, no concede permiso.
14. **Tools:** exactamente cuatro READ_ONLY: `xoc.tickets.list`,
    `xoc.tickets.get`, `xoc.documents.list`, `xoc.notifications.unread_count`.
15. **WRITE:** NO. No create/patch/delete/scans ni SQL/shell/HTTP arbitrario.
16. **Fallback MCP → local:** NO. Excepción, timeout o resultado inválido producen
    error seguro; mocks del executor local/legacy fallan si son invocados.
17. **Tools por turno:** máximo una tools/call intentada; happy path ejecuta una.
18. **Runtime turns:** máximo dos; una segunda propuesta de tool no se ejecuta.
19. **I01–I30:** todos PASS; matriz y evidencia reproducible arriba.
20. **C.3:** regresión focal PASS, incluyendo confirmación/idempotencia/replay.
    Proposal/cancel E2E no crean tickets. Endpoint, receipts, transaction y
    semántica de eventos permanecen intactos.
21. **Thread/session:** PASS; nueva/propia/ajena, user y tenant, sustitución del
    runtime rechazada. El thread nunca se convierte en autoridad MCP.
22. **MCP tests:** Windows 407 PASS + 39 skips POSIX; smoke/provenance PASS.
    Linux completa dos veces 445/446, con distintos fallos stdio indicados.
    Diagnóstico de ambos: 10/10 PASS, no sustituye el gate completo pendiente.
23. **Backend focales:** 114 PASS + seis unitarios nuevos PASS + 32 E2E PASS.
24. **Backend completa:** 255 ejecutados, 253 PASS, dos fallos históricos de
    test_assess_ticket_automation, documentados arriba. No se corrigió VICTOR.
25. **AWS/Azure/Foundry reales:** NO. Sólo descarga pública de dependencias de
    preparación; ejecución Docker sin red externa. No CLI cloud/SSH/deploy.
26. **Datos reales:** NO. Providers ficticios y AgentSession SQLite en memoria.
27. **Voice:** NO tocado; tampoco proxy, Web o Mobile.
28. **VICTOR:** NO tocado ni migrado. Sus tests históricos sólo aparecen en la
    suite backend completa pedida; no fueron modificados.
29. **Commits/push:** ninguno nuevo en ambos repos, por elección conservadora
    hasta cerrar el gate de regresión. No push, deploy ni activación remota.
30. **IC-1:** INCONCLUSO global; implementación/E2E PASS LOCAL. Falta acreditar
    estabilidad de la suite Linux íntegra, no hay bypass demostrado que exija
    relajar seguridad. No se declara 446/446 ni aprobación productiva.
31. **Un siguiente bloque:** IC-1R, diagnóstico del baseline inmutable y cierre
    del gate Linux. IC-2 sólo después de ese cierre, sin iniciarlo ahora.

### Comandos adicionales ejecutados / reproducibles

Desde la raíz común (dependencias backend locales ya existentes):

```powershell
python -B XOC-MCP/run_backend_regressions.py --backend XOC-API-AWS --dependencies .tmp-ticket-sync-tests --suite focal
python -B XOC-MCP/run_backend_regressions.py --backend XOC-API-AWS --dependencies .tmp-ticket-sync-tests --suite all
```

El runner anterior aplica Isolation antes de importar backend. Las seis pruebas
nuevas están incluidas en la suite completa; también se ejecutaron separadas
con la misma Isolation. No instalar dependencias ajenas ni usar credenciales
reales para reproducirlas. Desde XOC-MCP:

```powershell
.\.venv\Scripts\python.exe -I -B scripts/handoff.py run --image xoc-mcp-ic1:baseline --phase tests --output build/ic1-validation/tests-repro
```

Desde backend, diagnóstico opcional (no reemplaza la suite completa):

```powershell
python -B tests/ic1/diagnose_baseline.py --output tests/ic1/.build/evidence/baseline-focused-repro.json
git diff --check
```

Todos los destinos de evidencia deben ser nuevos. `git diff --check` pasa en
ambos repos. No se empaqueta automáticamente nada para GitHub o producción.

## IC-1R — diagnóstico Linux, 7 de octubre de 2026

### Decisión de salida

**IC-1R: INCONCLUSO. IC-1 conserva PASS LOCAL E2E histórico, pero su cierre
global continúa INCONCLUSO. NO-GO para IC-2 y para commits finales de cierre.**

Se reprodujeron A y B y se identificó un mecanismo común de fallo después de
validar el resultado. No se aisló por qué el proceso hijo tarda ocasionalmente
más que su ventana de salida limpia. No se aplicó un fix especulativo ni se
repitió la suite hasta conseguir cinco verdes. De las cinco suites programadas,
la primera falló y las cuatro siguientes pasaron; eso NO satisface el gate.

El historial anterior queda intacto. Ningún resultado de esta sección sustituye
los dos 445/446 originales ni convierte pruebas históricas en pruebas nuevas.

### Estado y preservación

- Ambos repos siguen en `feature/sophia-chat-mcp-ic1`.
- XOC-MCP HEAD: `20fde196364bfcd2e006208cfb279549146c68a0`.
- XOC-API-AWS HEAD: `ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9`.
- Antes de instrumentar se revisaron status, branch, HEAD, diff y diff-check.
  No hubo pull, reset, stash, clean, rebase, push, stage ni commit.
- MCP conserva la eliminación efectiva de una línea del README ya documentada;
  las demás marcas del working tree no representan cambios de contenido contra
  la entrega. Se revalidaron los **159 hashes de contenido: cero discrepancias**.
- No se editaron MCP, sus tests originales, wheels ni manifests. Se conservaron
  los cambios propios IC-1 del backend, enumerados en el historial anterior.
- Permanecen fuera del trabajo los tres documentos ajenos no versionados:
  `cases-tenant-hotfix-and-d1c-identity-gate.md`,
  `sophia-d1c-proxy-source-verified.md`,
  `sophia-phase-d1b-inventory-gate.md`, todos en `docs/`.
- Archivos propios añadidos por IC-1R:
  `tests/ic1/ic1r_campaign.py` y `tests/ic1/ic1r_exit_probe.py`.
  Además, únicamente se añadió esta sección al informe existente.
- Los diagnósticos y sus fixtures quedan bajo `tests/`, no en `src` ni en el
  paquete productivo. La evidencia nueva está en `.build`, ya ignorado por Git.

### Baseline y controles

Las 41 ejecuciones de R1–R4 usaron exactamente la imagen instalada:

```text
sha256:ffd2cdc17c30289ca4eaa3787cabe8190c38017f1840ad2020a7baef2b6c8df4
```

No se reconstruyó la imagen. Se preservaron SDK `1.30.0`, protocolo
`2025-11-25`, core `df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41`
y L6 `6fa74cf9fc336d52c7900ab4c35f3c4fd82e6e1e2b60c57ccc63156b2c0908d6`.
`require_l6_installed()` valida la instalación antes de cada población.

Entorno: Amazon Linux 2 x86_64, Python 3.11.16, usuario 10001, contenedor
nuevo por repetición, sin mounts del checkout, sin red, raíz read-only,
`/tmp` tmpfs propio de 128 MiB, memoria/swap 512 MiB, 2 CPU, 128 pids,
capabilities eliminadas y `no-new-privileges`. Es el baseline local histórico,
**no una recomendación de plataforma para producción**.

El runner registra un plan antes de ejecutar, con orden, imagen y hash del
script. Conserva TODAS las corridas, incluso las fallidas. Usa la suite instalada
original y sus assertions; añade un observador pytest y wrappers delegantes de
QueryWorker/ProcessOwner, sin sleeps, retries ni cambios de deadlines/resultados.
No se instaló ningún plugin nuevo. **R5 no aplica: la suite ya es serial**;
algunos tests ejercitan concurrencia interna deliberadamente, y se mantuvo.

Observación acotada: monotonic, PID, marcas de lifecycle, resultados categóricos,
slots/ContextVar, identidad de funciones parcheadas y política del event loop,
temporales, contadores técnicos, procesos/hilos y cgroup. El entorno se compara
únicamente dentro del contenedor ya aislado; se retienen nombres cambiados o
booleanos, nunca valores. No se imprimieron JWT, claves privadas, argumentos,
resultados completos, env del host ni mensajes de excepción con datos.

Limitaciones de observación: `handler_entered` se acredita con el contador de
auditoría posterior, no con un timestamp interno nuevo en el handler. El momento
de la assertion queda acotado por el fin de llamada y el reporte pytest; no se
reescribió el test para marcarla. No se inspecciona todo global mutable ni todo
estado aleatorio del intérprete. Los snapshots de recursos no son un profiler
continuo del scheduler del host.

### Matriz completa conservada

Evidencia base, relativa a XOC-API-AWS:
`tests/ic1/.build/ic1r-20261007/`. Cada carpeta R contiene `plan.json`,
`summary.json` y un JSON por ejecución. Los JSON incluyen el orden exacto.

| Población | Ejecuciones | Resultado completo |
|---|---:|---|
| R1 A individual | `R1-a-01` a `R1-a-10` | 10 PASS / 0 FAIL |
| R1 B individual | `R1-b-01` a `R1-b-10` | 10 PASS / 0 FAIL |
| R2 pareja alternada | 10 parejas, 20 tests | 17 PASS / 3 FAIL |
| R3 vecindarios | 6 grupos, 18 tests | 14 PASS / 4 FAIL |
| R4 suite íntegra | 5 suites, 2230 tests | 2229 PASS / 1 FAIL |
| R5 serial adicional | No aplica | La suite original ya es serial |

Total R1–R4: **2280 PASS / 8 FAIL / 0 SKIP**, en 41 contenedores independientes.
El total no se presenta como aceptación: incluye deliberadamente reproducciones
fallidas y poblaciones de tamaños diferentes.

Detalle R2 (A = concurrent calls same instance; B = inherited secrets):

| Run | Orden | PASS / FAIL | Fallo |
|---|---|---|---|
| R2-01 | A,B | 2 / 0 | Ninguno |
| R2-02 | B,A | 1 / 1 | B, primero en contenedor nuevo |
| R2-03 | A,B | 2 / 0 | Ninguno |
| R2-04 | B,A | 2 / 0 | Ninguno |
| R2-05 | A,B | 2 / 0 | Ninguno |
| R2-06 | B,A | 2 / 0 | Ninguno |
| R2-07 | A,B | 1 / 1 | A, primero en contenedor nuevo |
| R2-08 | B,A | 2 / 0 | Ninguno |
| R2-09 | A,B | 2 / 0 | Ninguno |
| R2-10 | B,A | 1 / 1 | A, segundo |

R2 por test: A 8/10 PASS, B 9/10 PASS. El fallo no requiere un predecesor
particular ni haber ejecutado ya otro test. No se acredita dependencia de orden.

Vecindario A, del módulo `test_l2_lifecycle.py`:

```text
test_cleanup_error_after_reap_still_safe_and_slot_released
test_concurrent_calls_same_instance_only_one_child
test_monotonic_total_includes_cleanup
```

Vecindario B, del módulo `test_worker_lifecycle.py`:

```text
test_invalid_child_envelope_never_starts_business
test_inherited_secrets_never_used
test_two_workers_concurrently_isolate_users
```

| Run R3 | PASS / FAIL | Fallos |
|---|---|---|
| neighbors-a-01 | 1 / 2 | Predecesor y sucesor de A |
| neighbors-a-02 | 2 / 1 | Predecesor de A |
| neighbors-a-03 | 3 / 0 | Ninguno |
| neighbors-b-01 | 3 / 0 | Ninguno |
| neighbors-b-02 | 2 / 1 | Sucesor de B |
| neighbors-b-03 | 3 / 0 | Ninguno |

Los propios A y B pasan sus tres vecindarios; el mismo mecanismo aparece en
otros consumidores de QueryWorker, no sólo en los dos tests originales.

| Suite completa | PASS / FAIL / SKIP | Duración s | Peak memoria cgroup bytes |
|---|---|---:|---:|
| R4-01 | 445 / 1 / 0 | 403.548 | 269197312 |
| R4-02 | 446 / 0 / 0 | 404.311 | 273117184 |
| R4-03 | 446 / 0 / 0 | 392.807 | 266641408 |
| R4-04 | 446 / 0 / 0 | 394.233 | 272633856 |
| R4-05 | 446 / 0 / 0 | 398.655 | 272867328 |

R4-01 falla en
`test_worker_lifecycle.py::test_same_request_replay_is_not_distributed_deduplication`:
la primera consulta acaba limpia; la segunda valida su resultado pero termina
con `-15`. A y B pasan en las cinco suites completas. No se ejecutó una sexta
suite para descartar retrospectivamente la primera fallida.

### Mini diagnóstico previo a cualquier fix

```text
ROOT_CAUSE: NOT_ISOLATED
FAILURE_MECHANISM: CONFIRMED

EVIDENCE:
  R2-02 B, R2-07 A, R2-10 A y los demás fallos R3/R4:
  admitted -> spawn -> initialized -> tool_result -> validated
  -> cleanup_started -> terminate -> reap(returncode=-15)
  -> lab_worker_unavailable -> assertion falla.

SECURITY_IMPACT:
  No bypass, uso de secreto heredado ni fuga demostrados en estas ejecuciones.
  El resultado se rechaza de forma cerrada; los datos son fixtures locales.

FUNCTIONAL_IMPACT:
  Una lectura válida puede presentarse como indisponibilidad durante cleanup.
  El gate Linux no es estable. No acredita fallo del supervisor persistente L6.

MINIMAL_FIX:
  Ninguno aplicado: falta aislar la demora interna del hijo en una salida fallida.

WHY_FIX_DOES_NOT_RELAX_CONTRACT:
  No existe todavía un fix acreditado al que aplicar esta comprobación.
  Ampliar la gracia de 450 ms o aceptar SIGTERM como completed no está autorizado.
```

Código responsable del mecanismo, **leído pero no modificado**:
`XOC-MCP/xoc_mcp_lab/budget.py::ProcessOwner.supervise` cierra stdin y espera
como máximo `min(0.45, remaining * 0.7)` antes de TERM. `exchange` exige
`returncode == 0`; el `-15` causa `LAB_EXCHANGE_INTERRUPTED`. QueryWorker
proyecta el error como `lab_worker_unavailable`. Es el worker stdio por consulta
histórico, no el servidor HTTP persistente L6 utilizado por IC-1.

En los tres fallos focales no hubo cancelación, timeout global, overrun ni error
de cleanup registrado. Quedaban respectivamente 1.677, 1.923 y 1.290 segundos
del presupuesto total al retornar. Que sobre presupuesto global NO autoriza
a ampliar el sublímite de salida limpia ni demuestra que el problema sea carga.

Ejemplo A, R2-07, tiempos desde el primer intento:

| Evento | ms |
|---|---:|
| admitted | 0.719 |
| spawn_started / spawn_returned | 42.678 / 84.422 |
| initialized | 2606.654 |
| tool_result / validated | 2616.979 / 2617.094 |
| cleanup_started | 2617.414 |
| reaped | 3077.046 |
| retorno error | 3077.132 |

El segundo intento se rechaza correctamente con `lab_call_limit` en 0.140 ms,
sin admisión ni hijo. El primero sí ejecuta el handler una vez y obtiene una
respuesta válida; no incrementa completed porque falla después. No se trata
de mover artificialmente el contador ni de una assertion lanzada antes del reap.

Para B, R2-02: contador handler=1, executor=1, lectura fixture=1, escrituras=0;
guard=false; las cuatro presencias sensibles observadas del entorno efectivo
del hijo son false. **Otro error se proyecta como status=error**, después de
ejecutar la lectura. No se concluye secret leak por el nombre del test.

Todos los ocho tests fallidos tienen al menos una llamada validada que acaba
en TERM/-15. En R3-neighbors-b-02 otra llamada registra intento TERM pero sale
con código 0; por eso un registro de `terminate` aislado no basta para clasificar
una llamada fallida: se correlacionan returncode, resultado y test.

### Salida del intérprete: sonda adicional, no aceptación

Se programaron diez ejecuciones alternadas, cinco controles y cinco observadas,
en la misma imagen y con los mismos límites. El control usa el comando original.
La variante ejecuta el mismo `verified_server.main()` y sólo añade marcadores
de retorno de main, atexit y GC posterior. No desactiva GC, no intercepta señales,
no alarga plazos ni modifica archivos instalados. La variante puede perturbar
timing, por lo que se conserva separada de la suite de aceptación.

Evidencia: `exit-probe/exit-01.json` a `exit-10.json`.

| Ejecución | Variante | Resultado | Cleanup ms |
|---|---|---|---:|
| 01 | Control | completed, rc=0 | 337.665 |
| 02 | Observada | completed, rc=0 | 387.752 |
| 03 | Control | completed, rc=0 | 369.561 |
| 04 | Observada | completed, rc=0 | 418.523 |
| 05 | Control | completed, rc=0 | 425.546 |
| 06 | Observada | completed, rc=0 | 378.267 |
| 07 | Control | completed, rc=0 | 356.686 |
| 08 | Observada | completed, rc=0 | 351.716 |
| 09 | Control | completed, rc=0 | 386.017 |
| 10 | Observada | completed, rc=0 | 387.367 |

En las cinco observadas se ve retorno de main, atexit y GC generación 2 de
aproximadamente 70–82 ms. **No hubo una salida fallida en esta sonda**: no prueba
que GC sea la causa, ni descarta scheduling/teardown/import graph. Los fallos
históricos sin esta instrumentación también se conservan. No se declara
Heisenbug resuelto ni se usa el 10/10 como prueba de un fix inexistente.

### Aislamiento, residuos, recursos y límites de la conclusión

- Las 41 poblaciones terminan sin hijos directos pendientes y con un solo hilo
  Python. Los cinco finales de suite muestran asimismo un task del proceso.
  Docker no conserva contenedores `xoc-ic1r*` al finalizar la campaña y sonda.
- Las **56 observaciones pre/post de A/B** mantienen ContextVar libre, cuatro
  slots disponibles, IDs de factory/stdio/loop policy, temp cache/owner e hilos
  iguales; cero nombres de entorno cambiados y cero hijos residuales.
- Cada población tuvo su tmpfs nuevo; no se reutilizó un temporal del host ni
  una carpeta entre contenedores. Las consultas stdio A/B no usan puerto TCP.
  No se encontró evidencia de colisión de temporales o IPC en esos caminos.
- En las cinco suites, memory.failcnt=0, OOM=0, rechazo pids=0; peak 254–261 MiB
  aproximadamente, bajo el límite de 512 MiB. La cuota se mantuvo en 2 CPU.
- Se midieron CPU consumida y snapshots antes/después de tests, pero no una
  traza del scheduler ni todos los contadores de throttling v1. No se demuestra
  ni se descarta influencia del scheduling del host por ausencia de OOM.
- No hubo `security_stop` focal. Los tests negativos de la suite que provocan
  guards intencionalmente no se confunden con accesos externos reales.
- El registro de auditoría compartido de algunos tests tiene contadores
  acumulados de más de una llamada; no se atribuyen todos a un único hijo.

Hipótesis pendientes, ordenadas por cercanía al mecanismo observado:

1. Coste variable del teardown/salida del hijo: ubicar la fase interna que
   todavía está activa al agotarse la gracia, en UNA muestra fallida instrumentada.
2. Scheduling padre/hijo durante esa ventana: correlacionar con espera/CPU;
   los snapshots actuales no permiten afirmarlo como causa.
3. Estado retenido entre tests: debilitado por fallos en el primer test de un
   contenedor nuevo y por restauración focal observada; no es la explicación
   demostrada. No se propone reordenar tests.

No hay evidencia suficiente para elegir un cambio mínimo de cleanup que
mantenga todos los contratos. La campaña acotada terminó: no se modifica L6
por si acaso ni se continúa indefinidamente hasta obtener una población verde.

### Evidencia e integridad

SHA256 del runner R1–R4:
`8394d7cc624ec37ad59d4bcababf165791e0ec0e59265824b249c283cd8d1f14`.
SHA256 de la sonda de salida:
`6ebfc8d628efc08c93f49d3cb4722e4f15d2a2f7704129d654e0b4f63935a7f6`.
Los planes y resultados acreditan imagen/script; no se retienen logs crudos,
solamente hashes/tamaños y las proyecciones técnicas permitidas.

Histórico preservado y revalidado:

- `tests/ic1/.build/evidence/final.json`:
  `466f085e875cbfb55f208a9ee5417293aecb8fabac7fa78c0b0cd91d669a0126`.
- `tests/ic1/.build/evidence/baseline-focused.json`:
  `ebd7ac4309465821b1414a23c2e907402af809b28804f45d6eac9dac43c31aa0`.
- MCP `HANDOFF_MANIFEST.json`:
  `f801999c6f627cbc3079c18a89e8d41511a853980655b04a7493c842183928ac`.
- MCP `HANDOFF_SHA256SUMS.txt`:
  `f87763d1d55ca4011146738cb4c2625128a41accbbc51792e27a3ded5eaeab8e`.

Comandos ejecutados desde backend; no repetir sobre destinos existentes:

```powershell
python -B tests/ic1/ic1r_campaign.py --campaign r1 --output tests/ic1/.build/ic1r-20261007/r1
python -B tests/ic1/ic1r_campaign.py --campaign r2 --output tests/ic1/.build/ic1r-20261007/r2
python -B tests/ic1/ic1r_campaign.py --campaign r3 --output tests/ic1/.build/ic1r-20261007/r3
python -B tests/ic1/ic1r_campaign.py --campaign r4 --output tests/ic1/.build/ic1r-20261007/r4
python -B tests/ic1/ic1r_exit_probe.py --output tests/ic1/.build/ic1r-20261007/exit-probe
```

Salidas host: R1=0, R2=1, R3=1, R4=1; el 1 conserva el fallo de la población,
no significa fallo de Docker. Sonda=0, que sólo significa diagnóstico ejecutado,
no aceptación IC-1R. No hubo errores del runner ni parada de seguridad.

### Respuestas obligatorias IC-1R, 1–37

1. **¿Se reprodujo A?** Sí, R2-07 y R2-10.
2. **¿Se reprodujo B?** Sí, R2-02.
3. **Condiciones:** imagen fija, recursos aprobados, contenedores nuevos,
   pareja alternada; ambos pueden fallar como primer test. Otros consumidores
   stdio reproducen el mismo mecanismo en vecindarios y suite completa.
4. **Causa:** ROOT_CAUSE=NOT_ISOLATED; mecanismo de fallo CONFIRMED.
5. **Cuál:** resultado validado seguido de salida no observada dentro de 450 ms,
   TERM/-15 y rechazo como unavailable. La causa de la demora interna no quedó
   aislada; no se afirma que sea carga, GC, orden o secret leak.
6. **Impacto real de seguridad:** no demostrado en las pruebas locales;
   sí indisponibilidad funcional intermitente. No se auditaron sistemas reales.
7. **Fuga de secretos:** no observada; B falla por cleanup, no por uso de secreto.
8. **Fuga tenant/user:** no observada; no se cambiaron autorización ni bindings.
   En el test de dos usuarios, el fallo fue indisponibilidad de una llamada.
9. **Proceso/thread residual:** ninguno observado al final de las 41 poblaciones;
   sonda con hijos recolectados y sin entrada `/proc`; sin contenedores propios.
10. **Dependencia de orden:** no demostrada; primer test nuevo también falla.
11. **Contaminación env:** ninguna en las 56 observaciones pre/post de A/B.
12. **Presión correlacionada:** no se acredita saturación/OOM/pids; los snapshots
    no permiten excluir scheduling ni atribuirle causalidad.
13. **Código corregido:** ninguno del baseline/runtime. Sólo dos diagnósticos
    nuevos y este apéndice; IC-1 previo conservado.
14. **Timeout cambiado:** NO, incluyendo el sublímite de gracia.
15. **Resource budget cambiado:** NO.
16. **Policy/Executor/Gateway cambiado:** NO.
17. **A individual ×10:** 10/10 PASS, R1.
18. **B individual ×10:** 10/10 PASS, R1.
19. **Alternado ×10:** 17 PASS / 3 FAIL en 20 tests; detalle de las diez parejas arriba.
20. **Reproducción mínima ×10 posterior al fix:** no aplica; no hubo fix ni
    reproducción causal determinista. La sonda adicional 5+5 completa NO equivale a F4.
21. **Cinco suites:** 445/446, 446/446, 446/446, 446/446, 446/446; cero skips.
22. **¿Todas 446/446?** NO. No se reemplaza R4-01 por otra corrida verde.
23. **MCP smoke/provenance:** PASS histórico conservado; checks de instalación
    e integridad nuevos pasan. No se repitió el smoke post-fix: no existe fix.
24. **32 E2E IC-1:** PASS histórico, evidencia final intacta; no reejecutados en
    IC-1R porque el gate Linux previo no cerró (sección 20 del encargo).
25. **C.3:** PASS histórico; sin modificaciones ni nueva certificación IC-1R.
26. **Thread/session:** PASS histórico; sin modificaciones ni nueva certificación.
27. **Backend focal:** 114+6 PASS históricos; no reejecutados como cierre posterior.
28. **Backend completa:** histórica 255 tests, 253 PASS y 2 fallos; no reejecutada
    en este cierre inconcluso. No confundir con las 446 pruebas del baseline MCP.
29. **Fallos históricos:** los dos `test_assess_ticket_automation` permanecen
    reportados por su ejecución anterior, no revalidados ni corregidos aquí.
30. **IC-1R:** INCONCLUSO, con mecanismo de fallo reproducido y evidencia conservada.
31. **IC-1 global:** INCONCLUSO; implementación/E2E PASS LOCAL histórico.
32. **Commits locales:** ninguno nuevo en ningún repo; HEADs indicados arriba.
33. **Push:** NO.
34. **Voice:** NO tocado; tampoco Web/Mobile/proxy.
35. **VICTOR:** NO tocado; sin On-Premise ni Step Functions.
36. **AWS/Azure/Foundry real:** NO; sin deploy, SSH, providers o credenciales reales.
37. **¿Puede empezar IC-2?** NO. Falta causa aislada, fix mínimo acreditado si
    corresponde, cinco suites verdes post-fix y regresión posterior de IC-1.

### Siguiente paso limitado

El siguiente trabajo sigue siendo **IC-1R**, no IC-2: una nueva campaña acotada
para localizar dentro del hijo la demora de salida en una muestra fallida, con
los mismos límites. Si la solución requiere variar timeout, SDK/dependencias o
semántica de supervisión, se debe reportar y acordar ese alcance ANTES de editar.
No se propone aceptar resultados después de un cierre anómalo ni usar retries.
Sólo tras un fix causal autorizado corresponden F1–F5, regresión IC-1 y commits.

### IC-1R.2 — child-exit diagnosis

Fecha local: 7-oct-2026. **INCONCLUSO: no hubo muestra fallida instrumentada
en la población acotada. ROOT_CAUSE=NOT_ISOLATED, STALL_PHASE=UNKNOWN.**
No se aplicó fix, no se cambió el baseline y no se ejecutaron suites posteriores
para obtener un verde casual. Los ocho fallos del IC-1R anterior siguen vigentes
como evidencia; esta población sin fallos no los invalida.

#### Preservación y alcance ejecutado

Se leyeron IC-1/IC-1R y las evidencias R2/R3/R4/exit-probe. Se volvieron a
registrar status, rama, HEAD, diff y diff-check de ambos repos antes de editar.
Ramas/HEADs sin cambios respecto al apartado IC-1R anterior. Se conservaron
todos los cambios IC-1, los tres documentos ajenos y los resultados históricos.

Sólo se añadieron:

- `tests/ic1/ic1r2_probe.py`: campaña control/instrumented y observador padre.
- `tests/ic1/ic1r2_child.py`: wrapper diagnóstico del main original.
- Esta subsección del informe. Ningún cambio adicional en `src` ni en XOC-MCP.

Imagen exacta:
`sha256:ffd2cdc17c30289ca4eaa3787cabe8190c38017f1840ad2020a7baef2b6c8df4`.
Se conserva core/L6/SDK/protocolo, usuario no root, 512 MiB/2 CPU/128 pids,
sin red/mounts, raíz read-only, tmpfs nuevo por contenedor. Cero reconstrucciones,
instalaciones, cambios de timeout, assertions, slots o semántica de ProcessOwner.
Los 159 contenidos del handoff se revalidaron: cero discrepancias.

No stage/commit/push/pull, cloud, Voice, VICTOR, Web, Mobile ni IC-2.

#### Diseño y límite de población

El límite del prompt dice **60 procesos totales**, pero el test C crea dos hijos
por invocación. Ejecutar 10 controles y 10 instrumentados de cada test habría
creado hasta 80 hijos stdio. Se adoptó la interpretación conservadora anunciada
antes de ejecutar: **60 hijos stdio**, no 60 invocaciones pytest.

Plan fijado y guardado antes del primer run:

```text
7 ciclos de A-control, A-instrumented, B-control, B-instrumented,
            C-control, C-instrumented = 42 invocaciones / 56 hijos
1 cierre de A-control, A-instrumented, B-control, B-instrumented
                                    =  4 invocaciones /  4 hijos
TOTAL                               = 46 invocaciones / 60 hijos
```

| Caso | Controles | Instrumentados | Hijos totales | Resultado |
|---|---:|---:|---:|---|
| A, mismo QueryWorker concurrente | 8 | 8 | 16 | 16/16 PASS |
| B, secretos heredados | 8 | 8 | 16 | 16/16 PASS |
| C, replay sin deduplicación distribuida | 7 | 7 | 28 | 14/14 PASS |
| Total | 23 | 23 | 60 | 46 PASS / 0 FAIL / 0 SKIP |

Se ejecutó todo el plan, sin parar en el primer PASS y sin agregar procesos
después del límite. No hubo errores del runner. Todos los hijos salieron con
returncode 0 y sin entrada `/proc` al finalizar su test. Docker no dejó
contenedores `xoc-ic1r2*`. Los controles también pasaron: **no hay evidencia
para afirmar que sólo la instrumentación oculta el fallo**.

#### Instrumentación y límites explícitos

Control: comando original `-B -m xoc_mcp_lab.verified_server`; mismo test instalado,
fixture y assertions; sólo reporte pytest y captura delegante del handle de proceso.
No instrumenta main, wait, señales ni callbacks de salida.

Instrumented: se delega en el mismo `verified_server.main()` desde `-c`, sin
reescribir el paquete. Telemetría exclusivamente por stderr controlado del test:

- Primer marcador observable del hijo, before/main/retorno.
- EOF de `BoundedLines`, entrada/retorno de `transport.serve`, flush de
  `BoundedWriter`, entrada/salida del cierre original de `asyncio.Runner`.
- Shutdown de asyncgens/default executor; tareas por ID técnico, coroutine,
  archivo/estado; threads por nombre, daemon y módulo/función del frame superior.
- Al retornar main: GC por generación y perfil selectivo call/return de
  `threading._shutdown`, `logging.shutdown`, weakref, concurrent.futures y
  multiprocessing cuando esos callbacks se ejecutan. No se instancian ejecutores
  ni resource trackers adicionales para observarlos.
- Marcadores atexit observables; no son un inventario exhaustivo de callbacks
  registrados. `atexit_last_observable` NO significa fin de todos los handlers:
  pueden seguir otros registrados antes, como logging, observados por separado.
- `interpreter_shutdown_begin` marca la entrega de control para SystemExit;
  no es una traza nativa de cada fase interna de CPython. El primer marcador
  tampoco sustituye la hora OS de spawn registrada por el padre.

Padre: marcas originales de resultado/validated/cleanup, cierre stdin,
`_wait` start/end, solicitud de TERM/KILL y reap. Se delegan los mismos argumentos
y tiempos a los métodos originales. Muestreo con `loop.call_later(0.04)` durante
la espera, cancelado al salir de ella; no sleeps, retries, cancelaciones nuevas
del trabajo ni espera adicional de salida. La observación tiene overhead y
puede perturbar timing; no se presenta como completamente invisible.

Snapshots `/proc` padre/hijo: estado, CPU user/system ticks, switches voluntarios
e involuntarios, RSS, threads y tipos generales de FD. No se guardan destinos
de symlinks de FDs. Timestamps CLOCK_MONOTONIC correlacionados por test/run,
índice de llamada y PID. Los eventos de cierre stdin llevan PID en `child`,
el resto del padre en `child_pid`; no son identidades de usuario.

No se observan locals, argumentos, datos de tools, claims, env, tokens o keys.
No se altera framing ni se imprime diagnóstico por stdout MCP. La ausencia
de un evento Python no acredita por sí sola ausencia de trabajo nativo de
finalización. No hay marcador ejecutable posterior a la muerte del propio hijo:
la observación final de salida corresponde al padre y al estado `/proc`.

#### Lo nuevo demostrado en las salidas correctas

En los 30 hijos instrumentados:

| Métrica | Mínimo | Máximo | Interpretación |
|---|---:|---:|---|
| validated → main_returned | 1.918 ms | 3.873 ms | main retorna rápidamente en esta población |
| Espera limpia del padre | 259.669 ms | 441.869 ms | salida rc=0, sin TERM |
| GC gen. 2 observado post-main | 57.189 ms | 93.415 ms | trabajo real, no causa acreditada del fallo |
| Ventana cubierta por snapshots vivos | 245.054 ms | 411.629 ms | no incluye necesariamente los últimos ms hasta reap |
| CPU hijo en esa ventana | 230 ms | 400 ms | consume CPU; no está siempre bloqueado |
| CPU padre en esa ventana | 0 ms | 20 ms | incluye observación |
| Switches hijo voluntarios / involuntarios | 2 / 0 | 4 / 2 | no prueba starvation |
| Switches padre voluntarios / involuntarios | 6 / 0 | 10 / 0 | del hilo principal observado |

Resolución CPU: `SC_CLK_TCK=100` (10 ms). Son deltas dentro de los snapshots,
no un profiler continuo ni el total exacto de CPU de todo el proceso.

En todos los `runner_close_begin`, `asyncio.all_tasks(loop)` está vacío. Al EOF
sí se observan tareas normales `TaskHandle._run_coro` (`_tasks.py`) y `serve`
(wrapper); al salir del transporte sólo queda esa coroutine principal, que
termina antes de cerrar Runner. No se atribuye un módulo Python más específico
a `_tasks.py` sin un campo de módulo capturado; no se volcaron frames locales.

Los threads no daemon de AnyIO se observan al EOF/fin de transporte, pero no
quedan threads no daemon adicionales en ninguno de los 30 `main_returned`.
Puede seguir brevemente el Timer daemon propio del watchdog; se conserva su
cancelación original, sin matar threads ni alterar el flag daemon.

El shutdown del default executor y los asyncgens retornan. Se observan también
`threading._shutdown`, `multiprocessing.util._exit_function` y `logging.shutdown`
entrar/salir. Que exista ese callback multiprocessing no demuestra un proceso
resource tracker activo. No hubo una muestra de bloqueo en weakref o un
ThreadPoolExecutor global. No se afirma inventario exhaustivo de finalizers.

#### Ejemplo más lento: run-46, B instrumentado

Tiempos respecto de `validated`:

| Evento | ms |
|---|---:|
| EOF en el hijo | 0.772 |
| Retorno del transporte | 1.551 |
| Runner.close begin / end | 1.987 / 2.580 |
| main_returned | 3.873 |
| Marcador de comienzo shutdown | 3.953 |
| threading._shutdown call / return | 4.098 / 4.154 |
| atexit primero observable | 4.222 |
| multiprocessing exit call / return | 4.334 / 4.443 |
| logging.shutdown call / return | 4.465 / 4.697 |
| GC gen. 2 start / stop | 4.732 / 98.148 |
| Último GC gen. 0 observado, stop | 99.075 |

La espera limpia dura 441.869 ms. En el último snapshot vivo el hijo está `R`,
con un thread; durante los 411.629 ms muestreados consume 400 ms CPU y tiene
2 switches voluntarios / 0 involuntarios. El padre consume 10 ms y tiene
10 switches voluntarios / 0 involuntarios. Después sale rc=0: no hay TERM.

Esto ubica gran parte del tiempo de ESTA SALIDA CORRECTA después del retorno
de main y de los callbacks GC observados. No permite etiquetar los fallos
históricos como GC ni identificar la función nativa responsable del resto.
Hay un tramo de finalización que los marcadores Python no descomponen.

#### Clasificación causal y decisión

```text
FAILED_INSTRUMENTED_SAMPLE: NONE
P1/P2/P3/P4 de una muestra fallida: NO ASIGNABLES
P5 respecto de los fallos históricos: evidencia nueva insuficiente para clasificar
STALL_PHASE: UNKNOWN
ROOT_CAUSE: NOT_ISOLATED
FIX: NONE
IC-1R.2: INCONCLUSO
```

No se demuestra que GC, logging, tasks, threads o scheduling causen los fallos.
No hay muestra fallida con la que contrastar los nuevos marcadores. Tampoco
se demuestra un error de reap: en estas salidas el hijo vive mientras consume
CPU, termina y es recolectado normalmente. No se eleva `ROOT_CAUSE` a CONFIRMED
por tener una muestra correcta cerca del límite.

No se aplicó fix ni hubo before/after causal. F1–F5, cinco suites completas y
regresión IC-1 son condicionales al fix causal: **no ejecutados en IC-1R.2**.
Se conservan, sin recertificarlos, los resultados previos de smoke/provenance,
32 E2E, C.3, sesiones, 114+6 focales y backend completo 253/255 con sus dos
fallos históricos VICTOR. No se usó esta población para cerrar IC-1.

#### Evidencia reproducible

```powershell
python -B tests/ic1/ic1r2_probe.py --output tests/ic1/.build/ic1r2-20261007/probe
```

El destino debe ser nuevo; la campaña rechaza sobrescribirlo. Contiene plan,
`run-01.json` a `run-46.json` y summary. Resultado host=0 significa que pasaron
los tests de la sonda, NO que se haya cumplido el gate causal IC-1R.2.

Hashes del source UTF-8 enviado a Docker, acreditados en el plan:

- Runner: `104dd5ea5d54b96ed9e79f322a7629518526a7fc5a1787854af43b6cc0f7c2e5`.
- Wrapper: `2cb9390093bb388e39dbeefcd56cf8007c182c6f9ab837ec4f7d43a744408f1c`.

AST validado, ejecución real de ambos diagnósticos, diff-check de los repos
sin errores y handoff intacto. Sin staged, commits o contenedores residuales.
Los hashes de `final.json` y `baseline-focused.json` históricos siguen iguales.

#### Respuestas finales IC-1R.2, 1–47

1. **¿Muestra fallida instrumentada?** No: 30 hijos instrumentados rc=0.
2. **¿Qué caso?** Ninguno falló; A/B/C completos según la distribución acotada.
3. **¿Main retornó antes del TERM?** No hubo TERM; retornó antes de la salida
   normal en todos los observados. No se puede contestar para los fallos antiguos.
4. **¿Tasks pendientes?** Durante EOF sí, normales; al comenzar Runner.close,
   cero en las 30 muestras. No hay muestra fallida para extrapolarlo.
5. **¿Cuáles?** `TaskHandle._run_coro` en `_tasks.py` y `serve`; los snapshots
   guardan función/archivo/ID/estado, no un módulo inferido ni argumentos.
6. **¿Threads no daemon adicionales?** AnyIO durante transporte; ninguno al
   retornar main. No se cambiaron threads ni daemon flags.
7. **¿Atexit/finalizer seguía ejecutándose?** Los callbacks observados retornan;
   el trabajo nativo posterior no está descompuesto. No se observó un bloqueo.
8. **¿GC activo?** Se observó GC generación 2 y 0 post-main, con inicio/fin;
   no se observó GC activo al TERM porque no hubo TERM.
9. **¿Stdio seguía cerrando?** EOF/transporte concluyen antes del retorno de main
   en la población correcta. No se atribuye a stdio el fallo histórico.
10. **¿Proceso vivo o fallo de observación?** Vivo en snapshots durante shutdown,
    luego rc=0 y `/proc` ausente. No se demostró error de observación/reap.
11. **¿CPU durante gracia?** Hijo 230–400 ms; padre 0–20 ms en ventanas muestreadas
    de 245.054–411.629 ms. No son medidas de una salida fallida.
12. **¿Context switches?** Hijo voluntarios 2–4, involuntarios 0–2; padre
    voluntarios 6–10, involuntarios 0, durante esas ventanas.
13. **STALL_PHASE:** UNKNOWN para el fallo; no elegir GC/ATEXIT por inferencia.
14. **¿ROOT_CAUSE CONFIRMED?** No, NOT_ISOLATED.
15. **¿Por qué intermitente?** UNKNOWN. Toda esta población pasó; conserva el
    fallo previo. No se demuestra causalidad de carga, orden o instrumentación.
16. **¿Impacto de seguridad?** No observado; el impacto funcional intermitente
    conocido permanece. No se auditaron producción ni servicios externos.
17. **¿Secret leak?** No observado; B pasa su test original de entorno y logs;
    sin credenciales reales ni dumps. No es una prueba universal de inexistencia.
18. **¿Tenant/user leak?** No observado ni cambio de autoridad; estas pruebas
    no sustituyen una nueva regresión E2E cross-tenant.
19. **¿Baseline modificado?** No, 159 hashes intactos y misma imagen.
20. **¿Archivos?** Los dos scripts `ic1r2_probe.py`, `ic1r2_child.py` y este
    apéndice. Resultados nuevos sólo en `.build`, ignorado.
21. **¿Se amplió 450 ms?** No.
22. **¿Cambió timeout total?** No.
23. **¿Cambió SDK/dependencias?** No.
24. **¿Cambió Policy/Executor/Gateway?** No.
25. **¿Fix aplicado?** No.
26. **¿Cuál?** Ninguno; sólo instrumentación diagnóstica temporal del proceso.
27. **¿Before/after causal?** No; no hay muestra fallida nueva ni fix.
28. **F1:** no ejecutado post-fix; diagnóstico A=8 control+8 instrumentados PASS.
29. **F2:** no ejecutado post-fix; diagnóstico B=8 control+8 instrumentados PASS.
30. **F3:** no ejecutado post-fix; diagnóstico C=7 control+7 instrumentados PASS.
31. **F4:** no ejecutado; los ciclos diagnósticos no son aceptación post-fix.
32. **F5:** no ejecutado; no existe reproducción causal mínima acreditada.
33. **Cinco suites:** no se ejecutaron nuevas; históricas R4-01=445/446,
    R4-02=446/446, R4-03=446/446, R4-04=446/446, R4-05=446/446.
34. **MCP smoke/provenance:** PASS histórico; instalación validada en cada run,
    pero sin nueva campaña de smoke posterior a fix.
35. **32 E2E:** PASS histórico; no reejecutado por gate previo inconcluso.
36. **C.3:** PASS histórico, sin modificación ni recertificación en IC-1R.2.
37. **Thread/session:** PASS histórico, sin modificación ni recertificación.
38. **Backend focal:** 114+6 PASS históricos, no reejecutados en esta fase.
39. **Backend completa:** histórica 253 PASS/255, no reejecutada; no se corrigen
    `test_global_url_wins_and_marks_source_global` ni
    `test_normalize_plans_skips_empty_and_keeps_legacy`.
40. **IC-1R.2:** INCONCLUSO, campaña acotada terminada.
41. **IC-1 global:** INCONCLUSO; E2E local histórico PASS.
42. **Commits locales:** ninguno nuevo; HEADs preservados.
43. **Push:** NO.
44. **Voice tocado:** NO.
45. **VICTOR tocado:** NO.
46. **Cloud real:** NO.
47. **¿Puede empezar IC-2?** NO, no hay causa/fix/aceptación acreditados.

#### Siguiente experimento mínimo propuesto, no ejecutado

Focalizar una población nueva y previamente limitada en B, que alcanzó
441.869 ms, conservando controles y límites. Para que no sea repetir a ciegas,
añadir una observación capaz de descomponer el tramo nativo posterior al último
GC Python: por ejemplo, muestreo de stack nativo del hijo propio durante salida.
Antes habría que validar herramienta disponible, overhead y permisos; no se
instaló profiler ni se habilitó ptrace/capabilities en este bloque. Cualquier
cambio de sandbox/permisos requiere revisar y autorizar ese alcance primero.
Sin nueva capacidad de observación, repetir hasta una roja o cinco verdes no
resuelve la causa. No se propone ampliar la gracia ni aceptar rc=-15 como éxito.

### IC-1R.3 — native exit diagnosis

Fecha local: 8-oct-2026. **INCONCLUSO / DIAGNOSTIC_PERMISSION_REQUIRED**.
Se completaron D0 y D1. No se ejecutaron D2/D3: faltan las herramientas en la
imagen congelada y no se autorizó instalarlas ni ampliar permisos. No se
repite otra campaña idéntica. No hubo fix ni modificación del baseline.

#### Baseline, preservación y archivos

Ramas y HEADs preservados: `feature/sophia-chat-mcp-ic1` en ambos repos,
MCP `20fde196364bfcd2e006208cfb279549146c68a0`,
backend `ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9`.
Status/branch/HEAD/diff/diff-check registrados antes del trabajo. Sin stage,
commit, push, pull o cambios destructivos. Se conservan los tres docs ajenos.

Imagen inmutable:
`sha256:ffd2cdc17c30289ca4eaa3787cabe8190c38017f1840ad2020a7baef2b6c8df4`.
Core/L6 y 159 contenidos del manifest revalidados, cero discrepancias.
SDK 1.30.0, protocolo 2025-11-25, Python 3.11.16/AL2 histórico, 512 MiB,
2 CPU, 128 pids, usuario 10001, network none, read-only, tmpfs propio,
cap-drop ALL y no-new-privileges. No se cambia ninguna configuración.

Archivos añadidos sólo para diagnóstico:

- `tests/ic1/ic1r3_probe.py`: D0 e invocación de D1.
- `tests/ic1/ic1r3_analyze.py`: clasificación offline de las observaciones.
- Este apéndice al informe existente.

Se reutilizan `ic1r2_probe.py` y `ic1r2_child.py` SIN editar sus archivos. D1
verifica sus hashes, extiende en memoria el observador padre con métricas `/proc`
y cambia únicamente el intervalo de muestreo de 40 a 25 ms. No modifica el
paquete MCP, el método de espera original ni su argumento de 450 ms.
El wrapper del hijo sigue byte-equivalente al usado en R.2.

Destino nuevo: `tests/ic1/.build/ic1r3-20261008/`. No se sobrescribieron las
carpetas de R/R.2. Los hashes históricos `final.json`, `baseline-focused.json`
y source R.2 coinciden con los documentados anteriormente.

#### D0 — inventario sin instalaciones ni cambios de permisos

Se consultaron herramientas resolubles en PATH dentro de la imagen exacta,
no las disponibles en Windows ni en otra imagen. No hubo profiler pesado.

| Tool | Disponible | Versión | Utilizable con entorno actual |
|---|---|---|---|
| strace | No | N/A | No: falta ejecutable |
| gdb | No | N/A | No: falta ejecutable |
| gstack | No | N/A | No: falta ejecutable |
| pstack | No | N/A | No: falta ejecutable |
| perf | No | N/A | No: falta ejecutable |
| py-spy | No | N/A | No: falta ejecutable |

Se comprobó lectura sobre un hijo Python inocuo propio esperando EOF en una
tubería, sin sleeps ni datos de negocio; luego terminó con rc=0. Ese proceso
de inventario no es una consulta MCP ni se cuenta entre los 45 hijos de D1.

| Nodo `/proc/<hijo>/` | Lectura con permisos actuales |
|---|---|
| stack | Denegada: errno 13 (EACCES) |
| wchan | Sí |
| syscall | Sí |
| stat / status | Sí |
| sched | Sí |
| task/* | Sí |

UID=10001, CapEff=0, Seccomp=2, NoNewPrivs=1. No se tocó ptrace_scope,
perf_event_paranoid, seccomp, capabilities, imágenes ni paquetes.
La denegación de `/proc/.../stack` se documenta, pero NO prueba por sí sola
que gdb necesitaría CAP_SYS_PTRACE: son comprobaciones distintas. Falta comprobar
esa posibilidad con una herramienta real disponible, previa autorización.

#### D1 — observación nueva y población fijada

Se intercalaron diez ciclos, cada uno con B-control, B-observed, B-observed y
A-observed/C-observed alternados. Plan guardado antes de ejecutar:

| Grupo | Invocaciones | Hijos stdio | Resultado |
|---|---:|---:|---|
| B control original | 10 | 10 | 10 PASS |
| B D1 | 20 | 20 | 20 PASS |
| A D1 | 5 | 5 | 5 PASS |
| C D1 | 5 | 10 | 5 PASS |
| Total | 40 | 45 | 40 PASS / 0 FAIL / 0 SKIP |

Todos rc=0, sin residuos `/proc` ni contenedores propios al terminar. No se
amplió la población. No apareció muestra TERM/-15 que justificara detenerla
antes. Que controles y observados pasen no elimina los fallos históricos y no
permite afirmar que la instrumentación los oculta.

Capacidad añadida: `wchan`, syscall actual reducida a número o `running`,
schedstat (CPU ejecutada, espera en runqueue, slices), contadores seleccionados
de sched y IDs técnicos de tasks, además de los snapshots R.2. Se descartan
argumentos/registros/direcciones del archivo syscall antes de retenerlo.
No se leen env/credenciales ni memoria del proceso; no se guardan rutas de FDs.

Snapshots cada 25 ms programados dentro de `_wait`, cancelados al salir. No hay
sleep extra del hijo, cambio de señal, plazo extendido, prioridad, afinidad,
cancelación de GC o atexit ni éxito artificial tras TERM. El overhead observador
existe y queda como limitación; estos resultados no son aceptación post-fix.

Clasificación offline conservadora, en `analysis.json`:

- USER_CPU requiere incremento de utime entre muestras, stime igual, estado R
  y syscall `running` o -1. Describe evidencia de un intervalo, no un stack.
- FUTEX_WAIT/IO_WAIT/KERNEL_WAIT requieren estado de espera y wchan compatible.
- EXITING requiere desaparición o estado Z/X.
- Lo no acreditado se deja UNKNOWN; especialmente la primera muestra sin delta.

366 snapshots vivos clasificados: **331 USER_CPU, 35 UNKNOWN**. No se etiquetó
ninguna muestra como futex/IO/kernel wait. No se infiere un símbolo de userspace
desde `wchan=0`. No se afirma que no existan syscalls entre muestras.

#### FAST / MID / SLOW / FAILED

Los 35 hijos instrumentados tuvieron espera limpia de **252.278–309.968 ms**:
32 FAST (<300 ms), 3 MID (300–<400 ms), **0 SLOW (>=400 ms), 0 FAILED**.
Los controles no llevan las marcas internas para medir esa espera; sus
duraciones pytest no se presentan como tiempos de teardown.

| Ejemplo D1 | Categoría | Espera limpia | main tras validated | CPU user / system muestreada |
|---|---|---:|---:|---:|
| run-20, A | FAST mínimo | 252.278 ms | 2.469 ms | 220 / 0 ms |
| run-35, B | MID máximo | 309.968 ms | 2.161 ms | 280 / 0 ms |
| SLOW >=400 ms | Sin muestra nueva | N/A | N/A | N/A |
| TERM/-15 | Sin muestra nueva | N/A | N/A | N/A |

La muestra 441.869 ms de R.2 se conserva como histórica, pero carece de la
telemetría D1 nueva. **No puede fabricarse una comparación nueva FAST vs SLOW**
usando campos que no se capturaron entonces. Tampoco se equipara MID a SLOW.

Rangos de las 35 salidas observadas:

| Métrica, sólo ventana muestreada | Mínimo | Máximo |
|---|---:|---:|
| validated → main_returned | 1.834 ms | 2.928 ms |
| CPU user hijo | 220 ms | 290 ms |
| CPU system hijo | 0 ms | 0 ms |
| CPU user padre | 0 ms | 20 ms |
| CPU system padre | 0 ms | 10 ms |
| Espera hijo en runqueue según schedstat | 0 ms | 0.0546 ms |
| User CPU hijo después del último marcador Python | 150 ms | 210 ms |
| Switches voluntarios / involuntarios hijo | 2 / 0 | 5 / 4 |

Ticks CPU de resolución 10 ms; 0 ms system significa sin incremento observable
a esa resolución, no ausencia absoluta de syscalls. Los snapshots no cubren
necesariamente el intervalo final hasta reap. schedstat es el contador observado
del task principal; no una garantía global del scheduler de Docker/Windows.

**En los 35 casos, después del último marcador Python:** todos los snapshots
son R, wchan=0, syscall=`running` y task count=1; la CPU user sigue aumentando.
Antes se ven hasta cuatro tasks/threads transitorios propios de runtime/AnyIO;
el tramo final observado queda con uno. El último marcador del hijo es un
`gc_stop`; main, transporte, Runner y callbacks Python observados ya retornaron.

En run-35 queda un tramo de unos 245 ms entre el último marcador Python y reap,
con snapshots de CPU activa en userspace. Eso explica DÓNDE falta observabilidad:
no es una espera de stdin/EOF visible, ni un futex/join visible en esas muestras,
ni una espera larga en runqueue acreditada. No identifica la función nativa.

#### D2/D3 y clasificación causal

D2 no ejecutado: strace no existe y el encargo prohíbe instalarlo sin consulta.
D3 no ejecutado: no hay gdb/gstack/pstack/perf/py-spy disponible; no se cambió
sandbox para probarlos. `/proc/.../stack` además está denegado y, aunque fuese
legible, sería stack del kernel, no solución suficiente al tramo userspace.

```text
OBSERVED_PHASE: CPU de usuario, después de main y del último marcador Python
EXACT_NATIVE_SYMBOL: UNKNOWN
NATIVE_CLASSIFICATION: N10 UNKNOWN
STALL_PHASE: UNKNOWN para el fallo intermitente
ROOT_CAUSE: NOT_ISOLATED
FIX: NONE
IC-1R.3: INCONCLUSO / DIAGNOSTIC_PERMISSION_REQUIRED
```

No se elige N1 (CPython finalization) o N2 (malloc/free/GC nativo) sólo por el
momento del shutdown o por observar CPU. Tampoco se demuestra N8/starvation:
en esta población el incremento observado de espera en runqueue es muy pequeño.
La explicación de por qué otras salidas cruzan 450 ms sigue UNKNOWN.

No se hizo fix y no procede before/after causal ni F1–F5. No hubo nuevas cinco
suites, smoke/regresión IC-1 o commits. Los resultados históricos no se reetiquetan.

#### Evidencias y comandos ejecutados

```powershell
python -B tests/ic1/ic1r3_probe.py --phase d0 --output tests/ic1/.build/ic1r3-20261008/d0
python -B tests/ic1/ic1r3_probe.py --phase d1 --output tests/ic1/.build/ic1r3-20261008/d1
python -B tests/ic1/ic1r3_analyze.py --input tests/ic1/.build/ic1r3-20261008/d1 --output tests/ic1/.build/ic1r3-20261008/analysis.json
```

Sólo destinos nuevos. D0 inventory, D1 plan/run-01..40/summary y analysis conservados.
Hash payload D1: `51f80bec0d3886476048c9c1338f40e4857181d48dc5031d053a77cc0f4e72e5`.
Hash runner al ejecutar: `16dd15431807daff8296477fa350b81ebae17517325012c5e6d3fb20516d203b`.
Source R.2/wrapper histórico verificados antes de derivar payload. Nada se
modifica dentro de la imagen instalada ni en las evidencias anteriores.

#### Único siguiente experimento propuesto — requiere autorización

Herramienta: **gdb**, en una imagen auxiliar de diagnóstico separada, con versión
y digest fijados antes de usarla. No instalar paquetes encima de la imagen MCP
ni actualizar sus dependencias. Observar sólo un hijo Python fixture propio
identificado por container label/PID de esta campaña, jamás procesos del host,
usuarios ajenos o servicios reales. Máximo 2–3 snapshots por hijo, no sampling continuo.

Permiso solicitado primero: preparar la herramienta auxiliar y comprobar si
puede observar al hijo con el UID/permisos actuales. **No se ha demostrado aún
que CAP_SYS_PTRACE sea imprescindible**. Si attach resulta denegado, presentar
el error antes de autorizarla; la candidata mínima sería CAP_SYS_PTRACE sólo
en el observador aislado, nunca `--privileged`, PID namespace host ni cambio
global de ptrace_scope. Un cambio seccomp requeriría aprobación separada y
acotada, no `seccomp=unconfined` automático.

Comando gdb propuesto (NO ejecutado; `<PID_HIJO>` es el PID validado dentro del
namespace diagnóstico, y presupone herramienta ya preparada):

```text
gdb -q -nx -nh -batch
  -iex "set auto-load off"
  -ex "set print frame-arguments none"
  -ex "set print entry-values no"
  -ex "attach <PID_HIJO>"
  -ex "thread apply all bt 12"
  -ex "detach"
```

El futuro harness debe prearrancar el observador para que el lanzamiento de
Docker/gdb no consuma toda la ventana. Disparar snapshots en el tramo post-main
(por ejemplo a 250/350/420 ms sólo si el hijo sigue vivo), validar identidad antes
de attach y retener sólo thread ID/library/symbol/offset. No `bt full`, dumps,
locals, buffers, argumentos o strings; auto-load deshabilitado y red none.
Si faltan símbolos, reportarlo antes de descargar paquetes de debug adicionales.

Riesgo/reproducibilidad: gdb puede pausar brevemente el hijo y provocar por sí
mismo el cruce de 450 ms. La evidencia sólo serviría para localizar trabajo,
no para aceptación ni para probar por sí sola el timing original. Conservar
controles sin debugger; no impedir TERM ni extender la gracia. El auxiliar
también consume recursos, por lo que su overhead se debe registrar por separado.
Mantener intactos los límites del contenedor MCP y toda la evidencia original.

Por qué D1/D2 no bastan: D1 ya acredita actividad userspace, pero `/proc` no
devuelve la función nativa; D2 no está disponible y un tracer de syscalls tampoco
descompone por sí solo trabajo puramente userspace. No se justifica ejecutar
otros 100 procesos con la misma telemetría.

#### Respuestas obligatorias IC-1R.3, 1–54

1. **D0:** ninguno de strace/gdb/gstack/pstack/perf/py-spy disponible en PATH de
   la imagen. `/proc` legible excepto stack (EACCES).
2. **¿D1 útil?** Sí: wchan/syscall/schedstat y tasks localizan actividad userspace
   tras los marcadores Python, sin deducir símbolos.
3. **/proc en salidas lentas:** no hubo SLOW >=400 ms nuevo; en FAST/MID, post-Python R.
4. **CPU:** user hijo 220–290 ms, system 0 ticks adicionales; rangos de ventana
   muestreada, no medida continua ni de un fallo nuevo.
5. **Wchan/syscalls:** post-Python wchan=0 y syscall=running; no nombres nativos
   ni argumentos retenidos. No demuestra ausencia de syscalls entre snapshots.
6. **Threads:** uno durante el tramo post-Python; hasta cuatro al comienzo del cierre.
7. **FAST vs SLOW:** 32 FAST, 3 MID, cero SLOW; no comparación concluyente nueva
   con la salida de 441.869 ms histórica, sin campos D1.
8. **¿TERM/-15 nuevo?** No.
9. **¿Control/observada?** Ninguna falló; 10 controles y 30 tests observados PASS.
10. **¿Main había retornado?** Sí antes del tramo CPU final de los 35 hijos
    observados. No hay muestra fallida nueva para extrapolarlo.
11. **Último marcador Python:** gc_stop; CPU user sigue aumentando después.
12. **¿D2?** No.
13. **Strace:** no instalado; no resultado de tracing.
14. **Últimas syscalls:** no hay secuencia strace; `/proc` final observado dice
    running. No inventar una última syscall como causa.
15. **¿Userspace o kernel?** Evidencia de CPU userspace en los intervalos observados;
    sin espera kernel/futex final visible. No es un stack nativo.
16. **¿D3 necesario?** Es la capacidad siguiente necesaria para identificar la
    función del tramo userspace, no otra campaña idéntica D1.
17. **¿D3 posible sin cambios?** No con herramientas actuales: faltan ejecutables.
    Necesidad de capability extra aún no comprobada con gdb.
18. **Stacks/símbolos:** ninguno obtenido; no gdb ni lectura de memoria/dumps.
19. **STALL_PHASE:** UNKNOWN / N10 para el fallo, aunque el tramo verde observado
    está ubicado después de main en CPU de usuario.
20. **ROOT_CAUSE:** NOT_ISOLATED.
21. **Intermitencia:** UNKNOWN; no atribuir a carga, GC o scheduling sin evidencia.
22. **Impacto funcional:** permanece la indisponibilidad intermitente stdio
    demostrada antes; no se atribuye al servidor HTTP persistente L6 ni a Chat.
23. **Seguridad:** sin bypass o cambio de reglas; observaciones locales fixture.
24. **Secret leak:** no observado; B original pasa, no credenciales reales/env
    dump/tokens. No afirmación universal sobre producción.
25. **Tenant leak:** no observado ni cambio de autoridad; no sustituye E2E cross-tenant.
26. **¿Requiere sandbox nuevo?** Para proseguir falta herramienta auxiliar;
    si attach necesita permisos extra, es un gate aún pendiente de comprobar.
27. **¿Sandbox cambiado?** No.
28. **¿450 ms cambiado?** No.
29. **¿Timeout cambiado?** No.
30. **¿SDK/deps?** No.
31. **¿Policy/Gateway?** No.
32. **¿Fix?** No.
33. **Fix exacto:** ninguno; sólo diagnósticos y análisis offline.
34. **Before/after causal:** no existe; no hubo fix ni fallo nuevo instrumentado.
35. **F1:** no ejecutado post-fix.
36. **F2:** no ejecutado post-fix; B diagnóstico no equivale a F2.
37. **F3:** no ejecutado post-fix.
38. **F4:** no ejecutado post-fix.
39. **F5:** no ejecutado; reproducción causal aún no acreditada.
40. **Cinco suites:** ninguna nueva; históricas 445/446, 446/446, 446/446,
    446/446, 446/446, sin alterar evidencias.
41. **Smoke/provenance:** PASS histórico; checks de instalación e integridad
    actuales, no nueva aceptación posterior al fix.
42. **E2E IC-1:** 32 PASS históricos, no reejecutados por gate abierto.
43. **C.3:** PASS histórico, no tocado ni recertificado aquí.
44. **Thread/session:** PASS histórico, no tocado ni recertificado aquí.
45. **Backend focal:** 114+6 PASS históricos; no reejecutados.
46. **Backend completa:** histórica 253/255 con dos fallos VICTOR conocidos;
    no ejecutada ni corregida en este bloque.
47. **Commits:** ninguno; HEADs preservados.
48. **Push:** NO.
49. **Voice:** NO.
50. **VICTOR:** NO.
51. **Cloud:** NO.
52. **IC-1R.3:** INCONCLUSO; D0/D1 completos, parada en gate de herramientas/permisos.
53. **IC-1 global:** INCONCLUSO; E2E local histórico PASS.
54. **¿IC-2?** NO: falta causa, fix si corresponde y aceptación posterior.

No se instala nada ni se modifica el sandbox hasta recibir autorización para
la herramienta y, si se demuestra necesaria, para la capacidad adicional.

DIAGNOSTIC_PERMISSION_REQUIRED

## IC-1R.4 — GDB auxiliar y diagnóstico nativo (2026-10-08)

### Resultado ejecutivo y parada

**INCONCLUSO / OBSERVER_HARDENING_REQUIRED.** G0 pasó con los permisos actuales,
sin CAP_SYS_PTRACE ni relajación de seccomp. La ausencia de herramienta de R.3
quedó resuelta, pero todavía no se obtuvo un stack nativo utilizable.

Se detuvo la campaña tras cuatro tests B. En el cuarto, el hijo permaneció en
estado de tracing stop durante la observación y recibió TERM seguido de KILL.
Su control pareado ya había pasado. No se lanzaron más tests después de esa
señal. No se reemplazó la muestra fallida por una verde.

El debugger perturbó esta muestra: **N9 DEBUGGER_PERTURBED**. Eso no confirma
la causa del fallo histórico sin debugger, que sigue siendo **N10 UNKNOWN**,
`EXACT_PHASE=UNKNOWN`, `ROOT_CAUSE=NOT_ISOLATED`. No se atribuye el problema a
GC, allocator, CPython, SDK, AnyIO o scheduler sin símbolos nativos.

No hay fix productivo, commits, push, deploy ni inicio de IC-2. Este cierre
documental se guardó después de que el usuario aclarara que había rechazado
por error la primera escritura del informe; no se reanudó la campaña.

### Repositorios y baseline preservados

Se revisaron status, branch, HEAD, diff y diff --check. Ambos repos conservan
la rama `feature/sophia-chat-mcp-ic1` y estos HEAD:

| Repo | HEAD | Cambio productivo de IC-1R.4 |
| --- | --- | --- |
| XOC-API-AWS | `ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9` | Ninguno |
| XOC-MCP | `20fde196364bfcd2e006208cfb279549146c68a0` | Ninguno |

Cambios previos y documentos ajenos conservados. El diff tracked del backend
sigue siendo el previo: 3 archivos, 18 inserciones/4 eliminaciones; el de MCP
sigue siendo una línea eliminada previamente de README. Staging vacío y
diff --check limpio. No se normalizaron las advertencias LF/CRLF.

Verificación del handoff: **160/160 hashes coinciden**. Imagen MCP inmutable:

```text
sha256:ffd2cdc17c30289ca4eaa3787cabe8190c38017f1840ad2020a7baef2b6c8df4
Python 3.11.16 / MCP SDK 1.30.0 / protocol 2025-11-25
Core SHA256:
df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41
L6 SHA256:
6fa74cf9fc336d52c7900ab4c35f3c4fd82e6e1e2b60c57ccc63156b2c0908d6
```

No se reconstruyó ni instaló GDB sobre el baseline. MCP conservó 512 MiB,
2 CPU, 128 pids, UID/GID 10001, network none, read-only, tmpfs propio de
128 MiB, cap-drop ALL, no-new-privileges y seccomp predeterminado. No se
modificaron Policy, Executor, Gateway, protocolo, fixtures ni límites.

### Imagen auxiliar y procedencia

```text
Tag local: xoc-ic1r4-gdb:20261008
Image ID inmutable usado en las ejecuciones:
sha256:f19a9c80f4c0fb7dccb33f27a041ba0503788d400a4f7498bcc467d4aef9a970

Base pública fijada por RepoDigest, distinta del baseline MCP:
public.ecr.aws/lambda/python@sha256:cc940054e77d4a160c611a20c09cb8ab9325c09d72555a164329eb3e46957d80

GDB: GNU gdb (GDB) Red Hat Enterprise Linux 8.0.1-36.amzn2.0.3
RPM añadido: gdb-8.0.1-36.amzn2.0.3.x86_64
Fuente: amzn2-core; descarga RPM informada por yum: 3.1 M
Dockerfile SHA256:
9579a81cf76baa932fc774a7001719a366c0ae064c2c96047895d14c91ab5dfc
```

La imagen auxiliar no fue publicada: `RepoDigests=[]`. Su hash anterior es
el **Image ID local de contenido**, no un manifest digest de registry.
`image.json` conserva versión, inventario completo de RPM, fuentes yum y hashes;
`build.log` conserva la transacción. Solo se añadió GDB: las dependencias ya
estaban en la base. No se instalaron strace, python-dbg o debuginfo.

No se copiaron repos, credenciales, .env, perfiles cloud ni datos reales al
contexto. Solo el build del auxiliar utilizó red pública para paquetes. Todas
las ejecuciones fueron sin red externa y sin root. El sidecar de campaña tuvo
256 MiB, 1 CPU, 64 pids y tmpfs de 32 MiB, separado de los recursos MCP.
Los scripts se suministraron en memoria, sin montar repositorios.

### G0 acreditado y error inicial de captura

El control usó un Python inocuo propio que esperaba EOF por una tubería.
En el primer intento se invocó GDB, pero se perdió el resultado al consultar
el sysctl opcional `/proc/sys/kernel/yama/ptrace_scope`, ausente en este entorno.
No se atribuye PASS ni DENIED a ese intento. Su `g0.json` dejó el contador
`attach_attempts=0` sin actualizar por la excepción: **no acredita cero attaches**.

Una comprobación de metadatos sin GDB confirmó `FileNotFoundError`, errno 2,
`No such file or directory` para ese sysctl. Se corrigió solo esa captura y
se repitió G0 una vez con exactamente los mismos permisos:

```text
G0 = PASS; gdb returncode = 0
Attach/detach total: 34.104 ms
generation: 1efc4e29c69d47b2978e836a41f6a5c2
Target PID 8, PPID 1, start_ticks 2281957
Observer PID 9, start_ticks 2281977
Executable: /var/lang/bin/python3.11
UID/GID real, efectivo, saved y filesystem: 10001
PID namespace: pid:[4026533397]
User namespace: user:[4026531837]
CapEff: 0000000000000000
Seccomp: 2
NoNewPrivs: 1
```

El sidecar compartió solamente el PID namespace del contenedor propio, por ID
y label verificados; nunca el del host. No se creó un user namespace privado.
La ausencia del sysctl no demuestra por sí sola que Yama esté desactivado.
Seccomp=2 acredita filtrado, no una denegación concreta. El G0 exitoso demuestra
attach con el perfil vigente para ese control. Referencias de interpretación:
[Docker seccomp](https://docs.docker.com/engine/security/seccomp/) y
[Linux Yama](https://docs.kernel.org/admin-guide/LSM/Yama.html).

### Campaña y limitaciones del harness

Plan conservado: diez pares B control/observado, cinco A observados y cinco C
observados, máximo 30 tests. Ejecutados solamente cuatro B:

| Test | Modo | Resultado | Observación |
| --- | --- | --- | --- |
| 1 | Control | PASS, rc 0 | Sin GDB |
| 2 | Instrumentado | PASS, rc 0 | Attach omitido por gate de executable |
| 3 | Control | PASS, rc 0 | Control pareado de la muestra 4 |
| 4 | Instrumentado | FAIL, hijo rc -9 | Tracing stop, TERM/KILL, sin stack |

Dos controles PASS; instrumentados uno PASS y uno FAIL; **cero backtraces**.
Cuatro hijos stdio, cero supervivientes. A/C no ejecutados. La muestra 2 no
cuenta como observación GDB exitosa y no fue reemplazada.

GDB se prearrancó antes del test con auto-load deshabilitado, sin argumentos
de frames/entry values, sin paginación y `thread apply all bt 12`. Su sysroot
apuntó al root del contenedor propio para resolver bibliotecas. No se descargaron
símbolos. Se programaron candidatos a 250/350/420 ms, condicionados a identidad
coincidente y marcador main_returned. El supervisor original no esperó a GDB.

El canal diagnóstico fue un socket Unix del tmpfs del experimento, conectado
antes del guard de aislamiento, sin modificar ese guard. Solo transmitió
generation, identidad y timestamp validated. Un marcador técnico post-main
permitió comprobar PID/start_ticks/generation antes de observar; no se transmitió
contenido MCP. Se validaron los IDs/labels de los contenedores al arrancarlos y
PID/start_ticks/PPID/executable/UID antes de cada attach.

Errores del harness, conservados y no contabilizados como tests aprobados:

- El ensamblador inicial falló al localizar el footer, antes de lanzar contenedor.
- Dos arranques fallaron en el handshake antes de pytest por CRLF desde Windows;
  se corrigió únicamente `.strip()` en ese handshake diagnóstico.
- El primer observado esperaba el executable del inocuo de G0. El baseline usa
  `/opt/xoc-l2/bin/python`; el guard evitó attach. Se corrigió la ruta esperada
  usando la identidad verificada y se continuó solo con la población restante.
- En el segundo observado, el lector MI agotó su espera técnica de 2 s durante
  attach y terminó con `_queue.Empty`. Ese límite pertenece al observador, no
  al timeout MCP. No se conservó stack ni duración exacta de attach/detach.

### Correlación con D1

Tiempos en ms después de validated:

| Hito | Muestra 2, sin attach | Muestra 4, observada |
| --- | ---: | ---: |
| main_returned | 2.330 | 2.889 |
| Último gc_stop | 64.520 | 65.581 |
| Primer estado t observado | No | 264.003 |
| Último estado t observado | No | 531.839 |
| Fin de primera espera | 275.843 | 452.376 |
| TERM solicitado | No | 453.174 |
| KILL solicitado | No | 556.227 |
| Código final del hijo | 0 | -9 |

La primera espera comenzó a +0.617 ms en la muestra 4 y conservó el parámetro
de 450 ms. TERM a +453.174 ms refleja scheduling, no una ampliación configurada.
El tracing stop se observó en muestras separadas por 267.836 ms; no se afirma
que sea la duración exacta o completa del attach. `exchange_error` apareció a
+4275.331 ms, categoría PermissionError, sin detalle crudo retenido. **No es
evidencia de un errno de denegación de GDB.** El worker devolvió
`lab_worker_unavailable`.

Prearrancar GDB no garantizó un attach corto. El observador tampoco conservaba
evidencia parcial antes de esperar la respuesta MI ni aseguró una recuperación
robusta en ese fallo. Por eso la muestra está perturbada y no sirve para
demostrar causalidad del fallo histórico. Se paró tras TERM, con su control
pareado ya terminado, sin más observaciones ni fixes especulativos.

### Seguridad, evidencias y limpieza

En ambos observados los contadores auditados del fixture fueron:
`handler_calls=1`, `executor_calls=1`, `reads=1`, `writes=0`.
No se usaron secretos/datos reales ni servicios cloud. No se conservaron env,
locals, argumentos, memoria cruda, dumps, claims o ToolResult. No hay stack
que permita declarar completada la captura nativa sanitizada.

Se retiraron solamente los contenedores efímeros propios por generation/label;
no son recuperables como contenedores. Se conservaron los JSON sanitizados y
la imagen auxiliar local. La comprobación final de contenedores con label
`xoc.ic1r4.generation` no devuelve ninguno. Ocho scripts temporales pasan AST.
El histórico R/R.2/R.3 no se sobrescribió.

Evidencia nueva, ignorada por Git: `tests/ic1/.build/ic1r4-20261008/`:

- `image.json`, `build.log`, `context/Dockerfile`: imagen y procedencia.
- `g0.json`, `g0-metadata-only.json`, `g0-capture-repair/g0.json`: historia G0.
- `campaign-final/run-01.json`, `run-02.json`: primer par.
- `campaign-remaining/run-03.json`, `run-04.json`: segundo par y parada.
- `analysis.json`, `final-checks.json`: correlación, integridad y estados Git.
- Scripts temporales de build/G0/observer/campaign/análisis, sin stage ni commit.

```text
image.json SHA256:
a7bf5988751e7140ac8d57971ca6e776c6aabceece99c61f4bbba51782cdce59
G0 acreditado SHA256:
19886b348366e43063ded4a8397f904ca615dac1f2c7dea2836c2b1aa610e516
run-04.json SHA256:
545dd61a9e75a175ebea6c38d0a4b31a473c42f3cbeb601d6f82a9481e8f2042
analysis.json SHA256:
06c99b0a4f4743415ec768ece4ad1e1262e075a877338fadb39fe56cd911780b
```

### Respuestas de cierre IC-1R.4 — 54 puntos

1. **Imagen auxiliar creada:** sí, separada y solo local.
2. **Base/digest:** completos arriba; auxiliar `f19a9c80…`, sin publicación.
3. **GDB:** 8.0.1-36.amzn2.0.3.
4. **G0:** PASS acreditado; primer intento no evaluable por fallo de captura.
5. **Capability extra necesaria:** no se demostró; G0 pasó sin ella.
6. **Capability habilitada:** NO.
7. **Población:** cuatro tests B, dos controles/dos instrumentados; A/C no ejecutados.
8. **Controls:** 2 PASS / 0 FAIL.
9. **Observed:** 1 PASS sin attach / 1 FAIL perturbado; cero stacks.
10. **TERM/-15:** hubo TERM y después KILL; rc final -9, no -15.
11. **Perturbación GDB:** sí, tracing stop; duración completa desconocida.
12. **Stack ~250 ms:** no obtenido; guard omitió uno y el siguiente attach no completó.
13. **Stack ~350 ms:** no obtenido, sin segundo attach.
14. **Stack ~420 ms:** no obtenido, sin tercer attach.
15. **Símbolos dominantes:** UNKNOWN.
16. **Library/module dominante:** UNKNOWN.
17. **D1/GDB:** D1 acredita tracing stop; sin backtrace no hay correlación de símbolos.
18. **EXACT_PHASE:** UNKNOWN nativo; permanece la observación previa post-main/post-gc.
19. **NATIVE_CLASSIFICATION:** N9 para esta muestra; N10 para la causa histórica.
20. **ROOT_CAUSE:** NOT_ISOLATED.
21. **Intermitencia:** no explicada; el fallo instrumentado no prueba su causa.
22. **Debuginfo necesario:** UNKNOWN; sin stack no puede evaluarse. No instalado.
23. **CAP_SYS_PTRACE necesaria:** no para G0 demostrado; no propuesta para resolver `_queue.Empty`.
24. **Seccomp distinto necesario:** no hay evidencia; no se cambió.
25. **Impacto funcional:** muestra perturbada devuelve lab_worker_unavailable;
    la intermitencia histórica permanece abierta. Nada desplegado.
26. **Seguridad:** sin privilegios nuevos, identidad antes de attach, sin cambios de autoridad.
27. **Secret leak:** no observado; sin secretos reales ni dumps/env conservados.
28. **Tenant leak:** no observado; fixtures únicamente, no recertificación multi-tenant.
29. **Fix aplicado:** NO al producto/baseline.
30. **Fix exacto:** ninguno causal; solo reparaciones temporales del harness descritas arriba.
31. **450 ms cambiado:** NO.
32. **Timeout cambiado:** NO al MCP; TERM/KILL no se aceptan como éxito.
33. **SDK/deps cambiado:** NO en MCP; GDB solo se añadió al auxiliar.
34. **Policy/Gateway cambiado:** NO.
35. **F1:** no ejecutado, sin fix causal.
36. **F2:** no ejecutado; los B diagnósticos no equivalen a F2.
37. **F3:** no ejecutado.
38. **F4:** no ejecutado.
39. **F5:** no ejecutado.
40. **Cinco suites:** no ejecutadas aquí; no se reemplazan fallos históricos.
41. **MCP smoke/provenance:** sin nuevo smoke; baseline intacto y handoff 160/160 verificado.
42. **E2E IC-1:** 32 PASS históricos, no reejecutados aquí.
43. **C.3:** PASS histórico, no reejecutado ni modificado.
44. **Thread/session:** PASS histórico, no reejecutado ni modificado.
45. **Backend focal:** 114+6 PASS históricos, no reejecutados.
46. **Backend completa:** 253/255 históricos con dos fallos VICTOR previos; no reejecutada.
47. **Commits:** ninguno; HEAD preservados y staging vacío.
48. **Push:** NO.
49. **Voice:** NO.
50. **VICTOR:** NO.
51. **Cloud real:** NO; solo descarga pública de paquetes durante build del auxiliar.
52. **IC-1R.4:** INCONCLUSO / OBSERVER_HARDENING_REQUIRED; parada tras TERM.
53. **IC-1:** INCONCLUSO; intermitencia Linux no cerrada.
54. **¿Puede empezar IC-2?:** NO.

### Próximo gate propuesto, no ejecutado

Revisar primero la fiabilidad del observador, no el contrato MCP. En un bloque
posterior se puede evaluar precargar **símbolos ya presentes**, conservar
evidencia parcial sanitizada antes de cada espera MI y asegurar detach/cleanup
ante errores. Validar primero sobre procesos inocuos del mismo intérprete
`/opt/xoc-l2/bin/python`, midiendo el costo real de attach. Prearrancar el proceso
GDB por sí solo no basta.

Es una propuesta diagnóstica, no un fix confirmado ni autorización para ampliar
esta campaña. No se solicita CAP_SYS_PTRACE, privileged, seccomp unconfined,
ptrace_scope o paquetes debug basándose en la evidencia actual. Si un futuro
gate demuestra necesitarlos, vuelve a aplicar STOP.

## IC-1R.5 — Hardening del observador y decisión de parada (2026-10-08)

### Decisión

**OBSERVER_NOT_READY. Condición de salida D: abandonar GDB como observador
para este contrato en la configuración/entorno evaluados.** No se ejecutó
ningún hijo MCP real ni la mini campaña B. No se aplica fix MCP.

Se implementó el harness con MI estructurado, evidencia incremental, límites
por fase, detach de emergencia, verificación del target y terminación exclusiva
del debugger si no acredita detach. La recuperación mejoró: **45/45 targets
inocuos salieron con rc=0; cero tracing stops residuales**.

Sin embargo, no se acreditaron O1–O8 con la versión final. El último piloto
retuvo al target en tracing stop aproximadamente **188.446–190.412 ms**, no
obtuvo stack y necesitó terminar el debugger para liberarlo. Target final:
estado R, TracerPid=0, rc=0. Eso no es un observado PASS y no es un observador
fiable para una captura cerca del final de la gracia MCP de 450 ms.

No se usan los PASS de versiones intermedias para declarar READY. No se
ampliaron permisos ni se siguió deformando el target para conseguir un verde.
La causa histórica MCP sigue **NOT_ISOLATED**, sin nueva clasificación causal.

### Alcance y preservación

Trabajado únicamente en nuevos archivos de `XOC-API-AWS/tests/ic1/` y este
informe; evidencia nueva exclusivamente en `.build/ic1r5-20261008/`.
No se tocó src ni se modificó XOC-MCP. Ambos repos conservan branch
`feature/sophia-chat-mcp-ic1`, staging vacío y HEAD:

```text
XOC-API-AWS: ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9
XOC-MCP:    20fde196364bfcd2e006208cfb279549146c68a0
```

Antes y después se revisaron status, branch, HEAD, diff y diff --check. Los
diffs tracked previos mantienen su hash; cambios ajenos conservados.
Handoff MCP: **160/160 hashes coinciden**. Evidencia R.4 verificada por hash,
sin sobrescribir R/R.2/R.3/R.4. Sin stage, commit, push, pull, reset, stash,
clean ni rebase.

```text
MCP baseline inmutable:
sha256:ffd2cdc17c30289ca4eaa3787cabe8190c38017f1840ad2020a7baef2b6c8df4
Python 3.11.16; MCP SDK 1.30.0; protocol 2025-11-25
Core: df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41
L6:   6fa74cf9fc336d52c7900ab4c35f3c4fd82e6e1e2b60c57ccc63156b2c0908d6

Auxiliar reutilizado sin reconstrucción:
xoc-ic1r4-gdb:20261008
sha256:f19a9c80f4c0fb7dccb33f27a041ba0503788d400a4f7498bcc467d4aef9a970
GDB 8.0.1-36.amzn2.0.3
```

No se instalaron herramientas ni debuginfo. No hubo red externa en los
experimentos, root, CAP_SYS_PTRACE, privileged, host PID namespace ni cambios
de seccomp. Baseline: 512 MiB, 2 CPU, 128 pids; observador separado: 256 MiB,
1 CPU, 64 pids. Ambos UID/GID 10001, read-only, tmpfs propio, cap-drop ALL,
no-new-privileges y network none. El sidecar comparte exclusivamente el PID
namespace del contenedor propio, identificado por ID/generation/label.

### Archivos nuevos

| Archivo en tests/ic1 | Función |
| --- | --- |
| ic1r5_observer.py | Parser MI2, allowlist, journal parcial, límites, detach/fail-safe |
| ic1r5_targets.py | Targets inocuos T1–T4 y manager con reap natural |
| ic1r5_campaign.py | Pares control/observado, identidad y gates inocuos; sin entrada MCP |
| ic1r5_tests.py | 11 tests puros de parser, sanitización y regresión de telemetría |
| ic1r5_analyze.py | Análisis read-only, métricas, O7, O8 e integridad |

Son harnesses diagnósticos **NO acreditados para uso sobre MCP**. Se dejan
locales, sin commit. El runner ahora devuelve exit code no cero si falla un
gate; los artefactos históricos previos a ese ajuste se conservan.

### Diseño del observador y comandos exactos

Se usó **GDB/MI2**, no parsing de backtraces humanos. El parser maneja result,
async, tuples/listas y command IDs. Los stream records de consola/log/target
se descartan sin imprimirlos. De los frames solo salen nivel, símbolo y nombre
de biblioteca; nunca argumentos, locals, direcciones crudas o valores.
Referencia de formato: [GDB/MI Output Syntax](https://sourceware.org/gdb/current/onlinedocs/gdb.html/GDB_002fMI-Output-Syntax.html).

Configuración del último candidato:

```text
/usr/bin/gdb -q -nx -nh --readnever --interpreter=mi2 -iex "set auto-load off"

-gdb-set pagination off
-gdb-set confirm off
-gdb-set print frame-arguments none
-gdb-set print entry-values no
-gdb-set auto-solib-add on
-gdb-set sysroot /proc/1/root
-file-exec-and-symbols /proc/1/root/opt/xoc-l2/bin/python

-target-attach <PID verificado>
-stack-list-frames 0 11
-target-detach
-gdb-exit
```

`--readnever` se verificó en la ayuda del GDB instalado; no implicó instalar
símbolos ni cambiar Python. También se evaluó auto-solib-add off: dio capturas
rápidas en varios casos, pero podía producir frames sin ningún símbolo
identificado. Ese resultado no se aceptó como acreditación general.

Los comandos MI de attach/detach y frames corresponden al contrato documentado:
[Target Manipulation](https://sourceware.org/gdb/current/onlinedocs/gdb.html/GDB_002fMI-Target-Manipulation.html),
[Stack Manipulation](https://sourceware.org/gdb/current/onlinedocs/gdb.html/GDB_002fMI-Stack-Manipulation.html).

Antes de observar se contrastan generation, container ID/label, PID esperado,
start_ticks, PPID, UID, executable y parent start_ticks. No se buscan procesos
por nombre ni se adjunta a un PID distinto para resolver una discrepancia.
El executable inocuo es exactamente `/opt/xoc-l2/bin/python` del baseline.

Límites técnicos del último harness, **no timeouts MCP**:

- Attach: 100 ms.
- Stack: 100 ms; inicialmente 60 ms, ajuste exclusivo del observador inocuo.
- Detach: 75 ms.
- Watchdog global de interacción: 300 ms.
- Preparación de símbolos: hasta 5 s por comando, antes de attach y antes de
  comenzar el trabajo del target; no forma parte de una captura MCP.

Ante error se intenta detach incluso si se envió attach pero falta su ack.
Si no se acredita detach, se termina solo el PID GDB propio, revalidado por
identidad; puede escalarse a KILL **del debugger**, nunca del target. Después
se verifica estado distinto de t/T y TracerPid=0, o que el target ya terminó.
Hay watchdog separado del bucle lector MI; no se espera un único bloque de 2 s.

Cada evento permitido se escribe inmediatamente en un journal del tmpfs propio,
y se reenvía a un JSONL del host que también hace flush por línea. El envío
Docker/Windows/OneDrive no bloquea el lector MI en el último candidato. Los
resultados parciales sobreviven aunque falte el summary final.

### Targets y alcance de lo probado

- **T1:** loop CPU finito de aproximadamente 1 s de tiempo de CPU, sin negocio.
- **T2:** espera EOF; en observados se solicita EOF después de detach/fin del
  observer. El control no espera una duración artificial equivalente.
- **T3:** imports stdlib mínimos, ciclos y callback atexit con GC y trabajo
  sintético de aproximadamente 0.6 s; idéntico entre control y observado.
  No pretende reproducir ni explicar la finalización histórica MCP.
- **T4:** implementado, no ejecutado en esta fase por parada. Para carreras de
  stack/detach, el harness contempla liberar antes al target para que pueda
  salir naturalmente: en all-stop no puede salir por sí solo estando detenido.
  Esas pruebas de frontera tampoco se acreditaron.

### Resultado de gates O1–O8

| Gate | Evidencia | Decisión final |
| --- | --- | --- |
| O1 | Versión intermedia: 10 controles y 10 observados PASS. Versiones posteriores fallaron en captura/plazos/símbolos. Último piloto: control PASS, observado FAIL. | NO acreditado para la versión final |
| O2 | Versión intermedia: 5/5 controles y 5/5 observados PASS; otro par posterior conservó eventos pero falló el summary. | No recertificado en la versión final |
| O3 | 1 control y 1 observado; rc0 en ambos, pero el summary del observado falló. No se completaron 10 pares. | NO acreditado |
| O4 | Implementado el plan de carreras, sin ejecución | NO acreditado |
| O5 | Implementados timeout MI, parser exception y canal cortado. Inyecciones reales no ejecutadas. Hubo recuperación de timeouts naturales del harness. | NO acreditado; no equiparar recuperación natural con los tres tests |
| O6 | Checks implementados para los cinco desajustes. Test unitario prueba rechazo por generation antes de leer proc. Sin cinco intentos reales. | NO acreditado |
| O7 | Escaneo automatizado de 130 JSON/JSONL y revisión focal de serialización/MI | PASS acotado |
| O8 | 45 targets rc0, cero contenedores propios, cero tracing stops residuales; imágenes/handoff intactos | PASS |

Por tanto **OBSERVER_READY=false**. Los PASS intermedios no sustituyen los
gates pendientes o fallidos del candidato final. No se ejecutó mini campaña.

### Problemas del harness conservados, no ocultados

1. Primera variante: timeout de stack y detach; recuperación terminando GDB,
   target continuó y salió rc0. No stack.
2. Auto-solib-add off: obtuvo capturas y permitió O1/O2 intermedios; después
   se observó un stack con todos los símbolos desconocidos. No se suavizó el
   criterio para aceptarlo.
3. Medición inicial usaba la llegada MI stopped como inicio del intervalo: eso
   subestima el stop si la notificación llega tarde. Se agregó muestreo /proc
   de aproximadamente 1 ms y se reportan cotas, no precisión falsa. El analizador
   conserva el raw histórico pero no reutiliza esa cota inicial incorrecta.
4. Al agregar métricas, una variable local sustituyó temporalmente el dict
   target_after por una lista. Corregido y cubierto por test unitario; dos
   observados quedaron sin summary válido, pero sus JSONL permiten reconstruir
   que detach y salida rc0 ocurrieron. No se cuentan como gates PASS.
5. Un control terminó rc0 pero la espera del proceso Docker CLI de Windows
   superó 3 s. Se amplió solo la espera del CLI del host a 10 s **después de
   reap**, no el tiempo de vida del target ni el timeout MCP.
6. El último piloto con --readnever y autocarga activa volvió a agotar stack y
   detach, sin frames. Se aplicó la parada D; no se siguieron probando variantes.

Se preservan todos esos intentos. Totales de investigación/acreditación:
**45 targets, 23 controles, 22 observados; 45/45 rc0**. Esto no significa
45/45 gates PASS. Hay **5 expiraciones de fase** en 3 observaciones (no cinco
targets muertos), **1.787 eventos parciales** y cero tracing stops residuales.

### Latencia y perturbación

Histórico combinado de distintas versiones del harness, **no una distribución
de aceptación del candidato final**. Valores en ms; solo operaciones con ack
aportan duración a su fila, y los timeouts se reportan separados:

| Métrica | N | Mínimo | Mediana | Máximo |
| --- | ---: | ---: | ---: | ---: |
| Attach con ack | 22 | 2.632 | 3.151 | 8.496 |
| Stack con ack | 19 | 22.902 | 25.510 | 68.930 |
| Detach con ack | 20 | 1.052 | 1.436 | 25.888 |
| Interacción completa | 22 | 27.643 | 31.084 | 190.654 |
| Cota superior de tracing stop | 22 | 27.217 | 30.669 | 190.412 |

Para muestras antiguas sin muestreo /proc se usa conservadoramente attach
enviado → estado libre comprobado. Para las nuevas se conserva el intervalo
inferior/superior entre muestras; el sondeo nominal de 1 ms no implica
resolución garantizada ni que se conozca el instante exacto del stop.

Duración de los targets, también histórica entre versiones, en ms:

| Target / modo | N | Mínimo | Mediana | Máximo |
| --- | ---: | ---: | ---: | ---: |
| T1 control | 16 | 1003.968 | 1004.203 | 1010.949 |
| T1 observado | 15 | 1029.688 | 1032.339 | 1198.512 |
| T2 control | 6 | 5.471 | 6.137 | 9.634 |
| T2 observado | 6 | 105.525 | 113.945 | 142.168 |
| T3 control | 1 | 690.856 | 690.856 | 690.856 |
| T3 observado | 1 | 701.765 | 701.765 | 701.765 |
| T4 | 0 | — | — | — |

T2 incluye el tiempo que el harness mantiene abierta la tubería para observar;
no se atribuye toda la diferencia a CPU/finalización del target. T3 solo tiene
un par y no permite inferencias estadísticas.

Último candidato, `pilot-04`, **un solo par**:

```text
Attach con ack: 5.783 ms
Stack: timeout a 100.109 ms, cero frames
Detach: timeout a 75.127 ms, sin ack
Tracing stop muestreado: 188.446–190.412 ms
Interacción completa: 190.654 ms
Target control: 1010.949 ms
Target observado: 1198.512 ms
Diferencia del par: +187.563 ms
Target después del fail-safe: R, TracerPid=0, rc=0
```

En ese candidato no hay una distribución de stack/detach exitosos que reportar.
No se debe inferir latencia fiable de la mediana mezclada de versiones.
La perturbación sigue siendo material frente al contrato MCP, aunque ya no
se abandone al target detenido.

### O7, O8 y comandos de verificación

Escaneo de evidencia por campos prohibidos y patrones de JWT, bearer, access
keys, claves privadas, rutas personales y valores centinela de fixtures:
**130 JSON/JSONL, cero hallazgos**. Revisión focal confirma allowlist de frames,
supresión de streams crudos y ausencia de lectura de env/locals. Esto **no es
una afirmación universal de ausencia de secretos**.

Todos los targets registrados fueron reaped con rc0. Se retiraron únicamente
los contenedores efímeros propios, no recuperables como contenedores; sus
JSONL y summaries disponibles permanecen. No hay contenedores con label
xoc.ic1r5.generation. La imagen auxiliar permanece local, sin cambios.

Comandos usados, sin red cloud ni deploy:

```powershell
python -B tests/ic1/ic1r5_campaign.py --preflight
python -B tests/ic1/ic1r5_tests.py
python -B tests/ic1/ic1r5_campaign.py --gate pilot --name pilot-01
python -B tests/ic1/ic1r5_campaign.py --gate pilot --name pilot-02
python -B tests/ic1/ic1r5_campaign.py --gate O1
python -B tests/ic1/ic1r5_campaign.py --gate O2
python -B tests/ic1/ic1r5_campaign.py --gate O1 --name O1-qualified
python -B tests/ic1/ic1r5_campaign.py --gate O2 --name O2-qualified
python -B tests/ic1/ic1r5_campaign.py --gate O3 --name O3-qualified
python -B tests/ic1/ic1r5_campaign.py --gate O1 --name O1-final
python -B tests/ic1/ic1r5_campaign.py --gate pilot --name pilot-03
python -B tests/ic1/ic1r5_campaign.py --gate pilot --name pilot-04
python -B tests/ic1/ic1r5_analyze.py
```

Son historia de ejecución, **no instrucciones para repetir automáticamente**.
Los directorios de evidencia se crean con exclusividad; repetir con el mismo
nombre se rechaza. Después del STOP solo se hicieron análisis, tests puros,
escaneo y verificaciones de Git/imágenes, sin más targets.

Tests puros finales: **11 PASS**. Syntax/AST de los cinco scripts: PASS.
Evidencia canónica: `tests/ic1/.build/ic1r5-20261008/analysis-final.json`.
`analysis.json` anterior conserva un problema de agrupado de rutas Windows
que dejaba vacío last_candidate; se corrigió en el archivo final sin borrarlo.

### Respuestas obligatorias IC-1R.5 — 36 puntos

1. **O1 10/10:** PASS intermedio, NO acreditado en versión final; último piloto 0/1 observado.
2. **O2 5/5:** PASS intermedio, no recertificado en versión final.
3. **O3 10/10:** NO; un par, observado sin summary válido; incompleto.
4. **O4 10/10:** NO ejecutado.
5. **O5:** timeout MI, parser exception y canal cortado implementados, no acreditados con inyecciones reales. No confundir con timeouts naturales recuperados.
6. **O6:** cinco checks implementados; rechazo por generation probado unitariamente. Campaña real de cinco desajustes no ejecutada.
7. **O7:** PASS acotado, 130 archivos y cero hallazgos; no garantía universal.
8. **O8:** PASS; cero contenedores/residuos propios, 45/45 targets rc0, baseline intacto.
9. **Observer READY:** NO, OBSERVER_NOT_READY.
10. **Attach min/median/max:** 2.632 / 3.151 / 8.496 ms, n=22, histórico mixto.
11. **Stop min/median/max:** cotas superiores 27.217 / 30.669 / 190.412 ms, n=22; no duraciones exactas.
12. **Stack min/median/max:** 22.902 / 25.510 / 68.930 ms, n=19 con ack; excluye timeouts.
13. **Detach min/median/max:** 1.052 / 1.436 / 25.888 ms, n=20 con ack; excluye timeouts.
14. **Tracing stops residuales:** 0 observados al terminar la recuperación; no significa cero pausa durante attach.
15. **Observer timeouts:** 5 expiraciones de fase en 3 observaciones; además un timeout de Docker CLI del host documentado por separado.
16. **Partial evidence:** 1.787 eventos en 22 observaciones, incluidos fallos; dos runs sin summary mantienen sus JSONL.
17. **Target muerto por observer:** NO; todos rc0; se terminó solo GDB cuando fue necesario.
18. **CAP_SYS_PTRACE:** NO añadida ni requerida por un error de permisos observado.
19. **Seccomp cambiado:** NO.
20. **Baseline MCP cambiado:** NO; imagen y 160/160 hashes intactos.
21. **B controls ×5:** NO ejecutados; gate cerrado.
22. **B observed ×5:** NO ejecutados; gate cerrado.
23. **Stacks MCP válidos:** 0; no se observó MCP.
24. **Símbolos MCP:** ninguno. Los inocuos mostraron, entre otros, read/clock_gettime; no son causa MCP.
25. **Overhead MCP:** no medido. Último par inocuo +187.563 ms; interacción 190.654 ms.
26. **TERM/-15 MCP:** no hubo ejecución MCP. Hubo terminación del debugger propio como fail-safe, no del target.
27. **Debugger perturbó alguno:** sí; último piloto retuvo al target ~188–190 ms, aunque pudo continuar.
28. **Nueva clasificación causal:** NO; histórica N10 UNKNOWN / ROOT_CAUSE NOT_ISOLATED.
29. **IC-1R.5:** OBSERVER_NOT_READY, cierre por condición D; hardening parcial, sin acreditación completa.
30. **IC-1:** sigue INCONCLUSO.
31. **¿IC-2?:** NO; tampoco corresponde presentar IC-1R.6 causal/fix como listo.
32. **Voice:** NO.
33. **VICTOR:** NO.
34. **Cloud:** NO.
35. **Commit:** NO.
36. **Push:** NO.

### Siguiente decisión necesaria

No seguir afinando permisos ni aumentar la gracia MCP para acomodar GDB.
Este bloque termina conforme a la condición D del prompt: **descartar este
GDB como observador para el contrato actual** y revisar por separado una
estrategia de diagnóstico que no detenga al hijo de esta manera. No se instaló
ni se autorizó automáticamente otra herramienta. No hay evidencia nueva para
modificar el lifecycle MCP, Python, SDK, dependencias o sus límites.
