# XOC / SOPHIA — Fast-track IC-1 / gate IC-2A

Fecha: 2026-10-10. Trabajo exclusivamente LOCAL. No push, deploy, cloud,
Voice, VICTOR, cambios de negocio ni modificación del baseline MCP.

## Decisión

**NO-GO para cerrar/versionar IC-1 e iniciar IC-2A.** La separación de los
lifecycles HTTP/stdio está sustentada por código y una nueva corrida E2E
exitosa, pero FT-2 no queda acreditado: se reproduce inestabilidad de arranque
del servidor HTTP L6 frente a su presupuesto actual de 4 segundos.

No se atribuye este nuevo bloqueo al worker stdio. No se ha aislado la causa
de la variación del startup. Tampoco se puede atribuir todo al observador:
un control original sin observador falló durante otra corrida, y un control
secuencial posterior pasó con muy poco margen.

La autorización de commits IC-1 e implementación IC-2A estaba condicionada a
FT-1 + FT-2 + no-stdio PASS. No se salta esa condición. La implementación
IC-1 previa permanece local; no hay un commit de aceptación engañoso.

## Preservación inicial

Ambos repos en `feature/sophia-chat-mcp-ic1`:

| Repo | HEAD inicial |
|---|---|
| XOC-API-AWS | `ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9` |
| XOC-MCP | `20fde196364bfcd2e006208cfb279549146c68a0` |

Se revisaron status, branch, HEAD, diff y diff --check antes de editar.
Backend: cambios previos en `.gitignore`, `chat.py`, `sophia_tool_chat.py`,
cliente/puerto MCP y harness IC-1 sin versionar. Tres documentos ajenos
untracked preservados: cases-tenant-hotfix-and-d1c-identity-gate,
sophia-d1c-proxy-source-verified, sophia-phase-d1b-inventory-gate.
MCP: numerosos paths marcados M, pero el diff de contenido únicamente
eliminaba una línea redundante `# XOC-MCP` del README. No se normalizaron
archivos ni se creó commit por LF/CRLF. `reports/` previo se conservó.

No reset, stash, clean, rebase, pull ni push. No eliminación de evidencia.
Los resultados previos R.1–R.5 y el informe acumulativo no se reescribieron.

## FT-1: mapa exacto

Paths relativos a cada repo; no se equipara importar el SDK con ejecutar stdio.

P: `src/handlers/routes/chat.py::proxy_chat` → contexto autenticado
`_chat_tool_context` → `src/shared/sophia_tool_chat.py::finish_turn` →
`src/shared/sophia_tool_execution.py::selected_local_port` →
`src/shared/sophia_mcp_client.py::LocalMCPExecution.execute/_call` →
SDK `streamable_http_client` + `ClientSession.call_tool` → TCP loopback →
`xoc_mcp_lab_l6.server::L6Application` → clase base L4 y su callback
`call_tool` → `xoc_mcp_lab_l6.pool::BoundedPool.run` →
`RequestScope.adapter` → `Adapter.call` → gateway/policy/executor → helpers.

H: `xoc_mcp_lab/query_worker.py::QueryWorker.run/_run` →
`budget.py::TotalBudget/ProcessOwner` → SDK `stdio_client` →
`verified_server.py::main` → adaptador/gateway → EOF →
`ProcessOwner.supervise/exchange` → espera clean exit hasta 450 ms →
TERM/KILL/reap según el contrato histórico.

| Componente / archivo y función | HTTP P | stdio H | Compartido |
|---|---|---|---|
| Backend `LocalMCPExecution.execute/_call` | Sí; HTTP exclusivamente | No | No |
| `xoc_mcp_lab_l6/server.py::L6Application/main` | Sí | No | No |
| `xoc_mcp_lab_l6/supervisor.py::Supervisor._startup/_retire` | Sí; dueño de proceso persistente por generación | No | No |
| `xoc_mcp_lab_l6/pool.py::BoundedPool.run/_finish` | Sí; 4 hilos y reservas | No | No |
| `xoc_mcp_lab/query_worker.py::QueryWorker` | No | Sí | No |
| `xoc_mcp_lab/budget.py::ProcessOwner` | No | Sí | No |
| `xoc_mcp_lab/budget.py::TotalBudget/_capture` | No | Sí | No |
| `xoc_mcp_lab/verified_server.py::main` | No | Sí | No |
| `xoc_mcp_lab/adapter.py::Adapter.call` | Sí vía RequestScope | Sí vía verified_server | Sí, despacho/negocio |
| `src/tool_gateway/policy.py::evaluate_tool_request` | Sí | Sí | Sí, autorización |
| `src/tool_gateway/executor.py::ToolExecutor.execute` | Sí | Sí | Sí, ejecución |
| `src/shared/sophia_tool_chat.py::model_result` | Sí | Sí | Sí, proyección |
| `xoc_mcp_lab/request_envelope.py::verify_envelope/VerifiedContext` | Sí vía boundary L4 | Sí | Sí, identidad ficticia de laboratorio, no lifecycle |
| `xoc_mcp_lab/wiring.py::Wiring/MemoryTable` | Sí vía scope/wiring L4 | Sí | Sí, providers ficticios, no lifecycle |
| `xoc_mcp_lab/isolation.py` | Sí | Sí | Sí, guardas del laboratorio, no lifecycle |

Aristas importantes:

- L6 server importa L4 server; L4 server importa boundary, scope y pool,
  no `QueryWorker` ni `budget.py`.
- L4 boundary importa `request_envelope`; scope importa `VerifiedContext`
  y `Wiring`; wiring persistente importa `MemoryTable` e `Isolation`.
- El supervisor importa stdlib y `control.py`; crea `-m xoc_mcp_lab_l6.server`
  una vez por generación, no por tool/request. Tiene protocolos de leases,
  heartbeat, drain, retire y replacement distintos del clean exit stdio.
- `xoc_mcp_lab_l4/harness.py` contiene una población benchmark comparativa
  que importa QueryWorker **dentro de esa función**. Importar utilidades del
  harness desde L6 no ejecuta esa población. No forma parte del dispatch HTTP.
- El SDK importa clases stdio al importar `mcp`; esto tampoco abre un child.
  La instrumentación bloquea módulos del lifecycle propio y spawns ajenos.
- Los módulos compartidos incluyen negocio, seguridad del laboratorio y
  fixtures; no se oculta esta distinción diciendo que comparten sólo nombres
  de tools. Ninguno de esos helpers inicia el lifecycle stdio problemático.

`LocalMCPExecution` acepta sólo URL `http://127.0.0.1:puerto/mcp`, bloquea
redirects, usa HTTP retries=0 y entrega DENY ante fallo; no importa un cliente
stdio. `local_execution` es composición confiable mediante ContextVar, no
selección por body/modelo, y rechaza runtimes cloud. No existe todavía un
selector productivo configurado; no se certifica una implementación futura.
El contrato futuro debe admitir únicamente disabled/HTTP, nunca stdio.

## Instrumentación de no-stdio

Sólo en `tests/ic1/no_stdio.py`, no en código productivo ni wheels del MCP.
El harness instrumenta su propio arranque, supervisor y server. Registra
sólo PID, evento fijo y nombre de módulo. Rechaza importar los tres módulos
`query_worker`, `budget`, `verified_server`; cubre import normal e importlib.
Rechaza spawns que no sean los dos módulos HTTP L6 admitidos y propaga el
guard al proceso hijo. Conserva clase/subscriptabilidad de Popen para el SDK.
No cambia presupuestos, providers, resultados ni funciones de cleanup.

El control negativo `guard_selftest.py` verifica 8 condiciones, incluyendo
6 intentos de import y 1 spawn rechazados, más compatibilidad de Popen.
PASS: 8 checks, 7 intentos detectados. Esos intentos son exclusivamente el
control negativo; no se mezclan con contadores de tráfico IC-1.

La corrida `ic1-solo-04.json` ejecutó 32 E2E, 32 calls HTTP, tres procesos
instrumentados, cero entradas al lifecycle stdio y cleanup correcto.
La instrumentación final añadió un MetaPathFinder para imports dinámicos;
su control negativo pasó, pero la corrida final IC-1 no alcanzó READY.
Por tanto **no se declara el guard final + FT-2 completo como PASS**.

## Known issue — legacy stdio worker lifecycle

Estado: OPEN; root cause no aislado; sin fix. No se reclasifica formalmente
como NON_BLOCKING_FOR_HTTP todavía porque falla el requisito 9 (FT-2 nuevo).
La evidencia estructural sí permite separar este issue del startup HTTP.

Síntoma: un hijo stdio puede haber producido resultado válido pero no salir
con rc=0 durante la ventana de clean exit de hasta 450 ms. ProcessOwner
aplica TERM; `exchange` exige salida limpia y termina en
`LAB_EXCHANGE_INTERRUPTED` / `lab_worker_unavailable`.

Tests afectados históricamente:

- `test_l2_lifecycle.py::test_concurrent_calls_same_instance_only_one_child`.
- `test_worker_lifecycle.py::test_inherited_secrets_never_used`.
- `test_worker_lifecycle.py::test_same_request_replay_is_not_distributed_deduplication`.

IC-1R.1 conservó fallos de campañas y suites completas; R.2 observó cierre
Python/GC sin reproducir el fallo; R.3 inspeccionó estado nativo limitado;
R.4 intentó GDB y observó perturbación; R.5 endureció el observador pero
concluyó OBSERVER_NOT_READY. GDB descartado para este contrato: perturbaba
materialmente el target. No afirmar fixed, resolved ni false positive.
No secret leak ni tenant leak observados en aquellas campañas; esto no es
una demostración universal de ausencia. No se borró ni relajó ningún test.

## Evidencia nueva / cronología

Custodia: `tests/ic1/.build/ft-20261010/`, ignorada por Git. El JSON de
inventario final incluye hashes, HEADs, diff y preservación del handoff.

- Preparación inicial en sandbox: Git rechazó propiedad del clon. Se repitió
  con usuario autorizado y directorio nuevo; no se alteró safe.directory.
- Harness inicial: Popen convertido a función rompía un type annotation del
  SDK (`TypeError: function object is not subscriptable`). Se corrigió **sólo
  el harness**, preservando los intentos `ic1-01/02`, `l6-tests-01/02`.
- `ic1-03`: startup superó 4.0 s, no READY, cleanup correcto, cero stdio.
- `l6-tests-03`: 142 tests; 125 PASS, 17 FAIL; 57 procesos instrumentados,
  cero entradas stdio. Durante una parte de esta corrida se ejecutaron
  controles adicionales: no se usa como población de performance aislada.
- Smoke original durante esa carga: C04 FAIL, sin hijos residuales. No se
  guardó el JSON interno de ese control; sólo consta la salida de terminal.
- `control-solo-01`: C04 PASS sin guard, ejecución secuencial. Startup
  generación 1 = 3.902057 s, generación 2 = 3.872541 s, contra 4.0 s.
- `ic1-solo-04`: 32/32 PASS, I01–I30 y casos extra; 32 calls HTTP.
- `provenance-01`: PASS, módulos canónicos instalados y resultados equivalentes.
- `guard-01`: 8/8 PASS, 7 intentos negativos correctamente rechazados.
- `ic1-final-05`: guard final, falla startup/READY antes de los E2E; cero
  HTTP calls, cero stdio, no se presenta como 32 tests aprobados.
- Backend original focal: 114/114 PASS. Primer wrapper propio suprimió
  globalmente logging y provocó un falso fallo de assertLogs (119/120).
  Se corrigió el wrapper para preservar la capacidad de captura del test;
  no se modificó el test ni el logger productivo.

Resultados finales:

- `l6-tests-solo-04.json`: **133 PASS / 9 FAIL / 142 tests**, 344.70 s.
  Sin otro contenedor de pruebas simultáneo. 68 procesos instrumentados,
  cero QueryWorker, cero ProcessOwner stdio, cero verified_server, cero
  violaciones del guard. El fallo HTTP persiste aun con esa separación.
- Aceptación oficial C01–C24 incluida en la suite: **15 PASS / 9 FAIL**.
  Fallan C06, C08, C10, C11, C12, C14, C15, C18, C19, todos con
  `TimeoutError / LAB_L6_EXPECTED_CONTROL_EVENT` (`harness.py:182`).
  Esta salida por sí sola no distingue cada espera inicial de cada espera
  de recuperación: no se atribuyen los nueve casos automáticamente al mismo
  punto. En `ic1-final-05` sí está acreditado el vencimiento del startup.
- `backend-final-02.json`: **120/120 PASS**, cero intentos de red externa y
  de lectura de archivos de secretos. Incluye 114 focales y los 6 unitarios
  de `test_sophia_mcp_execution.py`.
- Docker no mostró contenedores activos al terminar. Cleanup IC-1 registrado
  sin supervisor/hijos propios residuales y sin cleanup forzado del harness.
- `git diff --check` pasó en ambos repos; staging vacío; HEADs conservados.
- El informe histórico conserva SHA256
  `9f0e51b5234ef43d58b8a77dbb595cb97289315163a82867cf64235d21ed8efc`;
  `analysis-final.json` R.5 conserva
  `2fc9115253cb86a738932815046c038a69e7b6f0396278542df6346209b93f55`.

Hashes de evidencia clave:

| Archivo bajo .build/ft-20261010 | SHA256 |
|---|---|
| ic1-solo-04.json | `686dd49d30c9ffd08e05995955e1e8eb06bd86b4f82ee884c0b13ee9044e8768` |
| ic1-final-05.json | `ffdbe964c1a408faa21f075a051ae7760139be1451fa76095c713246144b49d9` |
| l6-tests-solo-04.json | `70035ed69f538f77739c490412309a5f91635d504feb7df3f44c33d63d53b05f` |
| backend-final-02.json | `5b2c78c2b3af93c6f61b0515ef18012bd3f2108cdc333d0f0ae915a33aec696f` |
| provenance-01.json | `ca84719ca8a7464f332458bba87734fb60093258d35715099d45e38014727f89` |
| guard-01.json | `765a250182f376b8b9e2c0d0af031a8a47394e04f77f2655dc594f55dcc4ef34` |

`inventory-final.json` conserva el inventario al terminar las pruebas;
`inventory-close.json` agrega verificación completa de HANDOFF_SHA256SUMS y
hashes de los harnesses. No se sobrescriben inventarios ni resultados previos.
Verificación final: **159/159 entradas de HANDOFF_MANIFEST y 160/160 de
HANDOFF_SHA256SUMS coinciden**. SHA256 de `inventory-close.json`:
`8a950a2f46158c7a451b9851fa2966d6c8575331f985bdb496181f6bfdca5e5b`.

## Diff propio exacto de este bloque

XOC-API-AWS, todos bajo `tests/ic1/` excepto el documento:

- `run.py` (existía untracked): activa guard sólo de test, captura fallo de
  arranque con control técnico, añade evidencia no-stdio/calls HTTP al gate.
- `no_stdio.py` (nuevo): guard propagado a supervisor y server.
- `guard_selftest.py` (nuevo): control negativo del guard.
- `http_regression.py` (nuevo): suite L6, smoke/control, recovery y provenance
  sobre artefactos instalados, sin reemplazar tests oficiales.
- `fast_track.py` (nuevo): ejecución local Docker y evidencia nueva por corrida.
- `backend_regression.py` (nuevo): 120 tests offline con reporte de outcomes.
- `ft_snapshot.py` (nuevo): inventario Git, preservación y hashes.
- `docs/sophia-mcp-fast-track-20261010.md` (nuevo): este informe.

XOC-MCP: **cero modificaciones nuevas**, cero commits nuevos. No se editó
su README, no se normalizaron los archivos M ni se regeneraron wheels.
El diff tracked del backend continúa siendo el previo: 3 archivos, +18/-4.
No se modificó ningún archivo de `src/` en este bloque.

Custodia: los harnesses nuevos quedan locales sin stage, igual que el harness
IC-1 y las investigaciones R.1–R.5 previas. Los contextos Docker `context-02`
a `context-07` contienen snapshots de source/harness; `.build` sigue ignorado.
El primer contexto incompleto por error de propiedad Git también se preserva.
No se incluyen estas carpetas, raw diagnostics ni documentos ajenos en commits.

Comandos principales (ejecutar secuencialmente, desde XOC-API-AWS):

```powershell
python -B tests/ic1/prepare.py --mcp ../XOC-MCP --output tests/ic1/.build/NUEVO-CONTEXTO
docker build --network none -t xoc-chat-mcp-ic1:local tests/ic1/.build/NUEVO-CONTEXTO
python -B tests/ic1/fast_track.py guard --output tests/ic1/.build/NUEVA-EVIDENCIA/guard.json
python -B tests/ic1/fast_track.py ic1 --output tests/ic1/.build/NUEVA-EVIDENCIA/ic1.json
python -B tests/ic1/fast_track.py tests --output tests/ic1/.build/NUEVA-EVIDENCIA/l6.json
python -B tests/ic1/fast_track.py control --output tests/ic1/.build/NUEVA-EVIDENCIA/control.json
python -B tests/ic1/fast_track.py provenance --output tests/ic1/.build/NUEVA-EVIDENCIA/provenance.json
python -B tests/ic1/backend_regression.py --mcp ../XOC-MCP --dependencies ../.tmp-ticket-sync-tests --output tests/ic1/.build/NUEVA-EVIDENCIA/backend.json
```

Las rutas NUEVO-* son ejemplos para no sobrescribir evidencia. El control
negativo tiene intentos deliberados de stdio **bloqueados**, nunca ejecutados.
No repetir hasta seleccionar solamente runs verdes: todos los resultados
deben formar parte de la decisión. El overhead del guard también debe
cuantificarse en el siguiente bloque; no está demostrado nulo.

## Alcance y condición de parada

No se cambia el timeout de startup (4 s), el timeout stdio (450 ms), los
presupuestos HTTP ni la asignación Docker (512 MiB / 2 CPU / 128 PIDs).
El margen de arranque reducido es evidencia; atribuirlo a Docker, antivirus,
hardware o un import específico sigue siendo una hipótesis, no root cause.
Resolverlo requiere un bloque focalizado del lifecycle **HTTP**, no seguir
persiguiendo el stdio ni abrir IC-2A eludiendo su precondición.

## Respuestas requeridas: A. Fast-track IC-1

1. HTTP y stdio no comparten el lifecycle problemático; sí helpers de
   identidad ficticia, proyección, gateway y providers de laboratorio.
2. QueryWorker no aparece en el dispatch IC-1 HTTP.
3. ProcessOwner stdio no aparece en ese dispatch.
4. La ventana stdio de 450 ms no controla HTTP. El bloqueo observado es el
   startup HTTP de 4 s, contrato distinto.
5. No se encontró fallback HTTP→stdio ni HTTP→ToolExecutor local ante error.
6. No reclasificar formalmente todavía: FT-2 no PASS.
7. Evidencia: mapa anterior, código señalado y JSONs nuevos con hashes.
8. L6 regression: 133 PASS / 9 FAIL en última corrida; no acreditada globalmente.
9. C01–C24: 15 PASS / 9 FAIL; C04 control PASS no sustituye aceptación completa.
10. Una nueva corrida 32 E2E PASS; corrida final detenida antes del arranque.
11. I01–I30 PASS en ic1-solo-04; no estabilidad final acreditada.
12. C.3 PASS en suite focal; lógica intacta.
13. Thread/session PASS en suite focal y E2E exitosa.
14. Cross-tenant PASS en suite focal/E2E exitosa; sin señal de fuga observada.
15. No-stdio: cero en corrida HTTP exitosa y controles; guard negativo PASS.
    No equivale a aceptación HTTP completa con guard final.
16. IC-1: NO-GO de cierre formal; implementación local preservada.
17. Known issue legacy documentado arriba, OPEN, sin fix.
18. Commits locales: ninguno nuevo; estaban condicionados a PASS.
19. Push: NO.

## Respuestas requeridas: B. IC-2A

20. Identity architecture: no implementada en este bloque por gate FT-2.
21. Signer/verifier: no implementados; no se reutilizó fixture privada.
22. Claims productivos: pendientes; no se cambió autoridad autenticada.
23. Algoritmo productivo: sin seleccionar/implementar en esta fase detenida.
24. Key ownership: no se crearon, copiaron ni consultaron claves productivas.
25. Providers reales: sólo lectura preliminar confirmó handlers canónicos
    `tickets_store`, `reports.store`, `user_notification_inbox`; sin wiring nuevo.
26. IAM mínimo: pendiente de IC-2A; no se creó policy ni ARN inventado.
27. Riesgo preliminar: tickets.list recoge todas las páginas antes de truncar;
    unread_count recorre hasta agotar páginas; documents.list limita elementos.
    No se cambiaron semántica, retries ni timeouts de helpers.
28. Hosting: no comparado ni decidido antes de pasar el gate.
29. Hosting recomendado: pendiente, no extrapolar laboratorio a producción.
30. Runtime productivo: pendiente. AL2/Python 3.11.16/SDK 1.30.0 siguen siendo
    sólo el baseline de esta prueba, no una recomendación productiva.
31. Config schema productiva: no implementada ni activada.
32. Observabilidad productiva: pendiente; se agregó sólo instrumentación de test.
33. Rollback productivo: pendiente de diseño; se mantiene no-fallback del
    cliente actual. No se reabrió legacy ni se cambió configuración cloud.
34. P01–P20: NO EJECUTADOS, IC-2A no iniciada.
35. Archivos cambiados: ocho paths propios, enumerados arriba; únicamente
    harnesses/documentación del backend; ningún cambio nuevo en XOC-MCP.
36. Cloud calls: NO. Docker local y tests offline únicamente.
37. Voice: NO.
38. VICTOR: NO.
39. IC-2A: NO INICIADA por precondición no cumplida, no PASS parcial ficticio.
40. Un siguiente bloque: **FT-2R — estabilizar y acreditar startup/recovery
    HTTP L6 dentro del presupuesto vigente**, con control no instrumentado,
    guard probado y ejecución secuencial. No GDB stdio, no cloud. IC-2B aún no
    corresponde; después del gate se retoma el IC-2A autorizado.
