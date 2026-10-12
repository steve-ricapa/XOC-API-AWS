# XOC / SOPHIA — FT-2R.1: startup natural y gate HTTP local

Fecha: 10-oct-2026. Alcance exclusivo: XOC-API-AWS / XOC-MCP, laboratorio Linux local.
No cloud, deploy, Voice, VICTOR, stdio/GDB ni IC-2A. Este documento conserva el
resultado rojo de FT-2R; no lo sustituye ni afirma haber corregido su causa.

**Cierre: FT-2 = PASS LOCAL; IC-1 = PASS LOCAL E2E FOR HTTP PATH.**
Se cumplieron los gates nuevos completos con el baseline original, sin subir
4 s ni cambiar código MCP. READY_FOR_IC2A = YES, sin iniciarlo.
Esto acredita la campaña actual; NO demuestra una corrección causal de la
variación histórica de rendimiento. No es autorización ni evidencia de PROD.

## 1. Preservación y procedencia

- Ambas ramas: `feature/sophia-chat-mcp-ic1`.
- Backend inicial: `ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9`.
- MCP inicial: `20fde196364bfcd2e006208cfb279549146c68a0`.
- Staging inicial vacío. Cambios IC-1 y documentos ajenos conservados.
- MCP conserva sus cambios previos de finales de línea y README; no normalización masiva.
- Preflight: `tests/ic1/.build/ft2r1-20261010/preflight.json`;
  SHA256 `ca1687a707e4d9dcf66a93f41afd197bc3b04916e64ffb1936cb90767cecc063`.
- Handoff: 159 entradas de manifest / 160 checksums verificados antes de preparar imágenes.
- Evidencia histórica FT-2R: directorio `.build/ft2r-20261010`, no sobrescrito.
- Evidencia nueva: `tests/ic1/.build/ft2r1-20261010/`.

Artefactos MCP inalterados:

| Elemento | Identidad |
| --- | --- |
| Imagen base | `sha256:ffd2cdc17c30289ca4eaa3787cabe8190c38017f1840ad2020a7baef2b6c8df4` |
| Core wheel | `df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41` |
| L6 wheel | `6fa74cf9fc336d52c7900ab4c35f3c4fd82e6e1e2b60c57ccc63156b2c0908d6` |
| SDK / protocolo | 1.30.0 / 2025-11-25 |
| Runtime | Python 3.11.16, Amazon Linux 2, x86_64 |
| Recursos | 512 MiB, 2 CPU, 128 pids, cuatro slots |
| Aislamiento | sin red externa, filesystem RO, tmpfs 128 MiB, UID 10001, sin mounts/credenciales |

Imagen U1/U2: `sha256:61db3e70b64e6a15ef3cbaefaa8435596245e5a736fd8f8ad55ef4da3bbb42a7`.
Imagen controles: `sha256:ed361f40db4491274ec9c0a698305c506e45183adfba15fe15c20f54e5587f45`.
Son capas de harness/backend sobre la misma imagen MCP. No son wheels nuevas.

## 2. Observer diagnóstico, no cambio de contrato

`PRODUCT_STARTUP_BUDGET = 4.0`; `DIAGNOSTIC_OBSERVATION_CAP = 12.0`.

`ft2r1_observer.py` deriva, únicamente dentro del proceso diagnóstico, la clase
Supervisor instalada. Intercepta el wait cuyo predicado original es
`started_safely` y su anotación de fase startup. No cambia `PHASE_BUDGETS`,
el predicado, integridad, control/hello/heartbeat, clock validation, wiring,
probe, gate OPEN, retiro, ownership, límite global de recovery ni protocolo.
No edita archivos instalados. El ejecutable, entorno del hijo y recursos son
los mismos. El argv sustituye `-m` por un bootstrap de harness para instrumentar;
esta diferencia es explícita y no se presenta como rendimiento productivo.

El journal registra el deadline exacto `start+4.0` y el instante real del primer
poll que lo observa (con su retraso); no inventa una observación exactamente a
4.000000 s. La observación corta a `start+12.0`. El mismo proceso continúa, sin
retry ni un reemplazo extra. Si falla otro control, conserva el fallo.
El resultado se etiqueta `diagnostic_only=true`, `product_acceptance=false`.
La clave genérica `passed` de ese harness sólo indica finalización del ensayo,
NO cumplimiento de L6 bajo 4 s.

U2 usa el comando oficial `test/recover` del perfil `mixed`, la retirada/reap
original y una nueva consulta HTTP después del reemplazo. Conserva 12 s global
y 21 s de restauración; permitir observar startup hasta 12 s NO aumenta esas
cotas. Los 20 cold de preparación de U2 se reportan aparte de U1 y U2.

Instrumentación FT-2R reutilizada: spans importlib, app/wiring/bind/control;
CPU y wall monotónicos; muestreo /proc de hijos propios cada 250 ms; RSS/HWM,
threads/fds, cgroups v1/v2 y schedstat cuando existe. Sin profiler nativo ni red.
Cold significa proceso/contenedor nuevo, no vaciado artificial del page cache.
Las pruebas del observer, contabilidad y gate (incluidos controles negativos):
**16/16 PASS**.

## 3. U1 / U2 completos, sin selección de verdes

Fuente: `natural-analysis.json`, `U1/run-01..30.json`, `U2/run-01..20.json`.
Todos los 50 ensayos y sus 70 startups instrumentados se conservaron.

| Estadística (s) | U1 cold (30) | U2 replacement (20) | Setup U2 (20, separado) |
| --- | ---: | ---: | ---: |
| Completados | 30 | 20 | 20 |
| Incompletos a 12 s | 0 | 0 | 0 |
| min | 2.004606 | 1.941048 | 1.985207 |
| p50 | 2.074726 | 2.012517 | 2.035397 |
| p75 | 2.146091 | 2.069866 | 2.094817 |
| p90 | 2.405109 | 2.084829 | 2.188159 |
| p95 | 2.491330 | 2.104980 | 2.244680 |
| max | 2.640754 | 2.134648 | 2.287911 |
| media | 2.144357 | 2.016708 | 2.062919 |
| desviación poblacional | 0.160570 | 0.057219 | 0.083927 |
| distancia max → 4 s | 1.359246 | 1.865352 | 1.712089 |

Buckets `<4`: 30/20. Todos los buckets 4–4.25, 4.25–4.5, 4.5–5, 5–6,
6–8, 8–12 e incompletos: **0/0**. Ninguno supera 4, 5, 6, 8 ni 12 s.
Percentiles: interpolación lineal `(n-1)*q`. Desviación: poblacional, no muestral.

**D1: NO REPRODUCIDO en esta población natural.** No es el caso hipotético
4.1–4.5 s ni una cola 6–12 s. La evidencia nueva no justifica proponer un nuevo
timeout: conservar **4.0 s**. CONTRACT_MISMATCH no demostrado; PERFORMANCE_DEFECT
no reproducido aquí. La variación respecto de FT-2R sigue sin causa acreditada.
No se atribuye a host, governor, caché, race o arreglo propio sin medición causal.

FT-2R permanece: 6/20 originales y 9/20 guarded dentro de 4 s; 8/10 replacements
dentro del presupuesto. Esas observaciones censuradas no se convierten en tiempos
naturales ni se borran. Esta campaña NO demuestra cuánto habrían durado aquellos
procesos históricos concretos si no hubieran sido terminados.

## 4. Timeline / CPU / recursos

Medianas exclusivas sobre los 70 startups nuevos instrumentados (s):

| Fase | Mediana |
| --- | ---: |
| A_PROCESS_CREATE | 0.000579 |
| B_PYTHON_BOOT | 0.009603 |
| C_IMPORTS | 1.985899 |
| D_APP_CONSTRUCTION | 0.000308 |
| E_GATEWAY_WIRING (sin imports anidados) | 0.000334 |
| F_HTTP_BIND (sin imports) | 0.000602 |
| G_READY_HANDSHAKE | 0.000408 |
| H_SUPERVISOR_OBSERVATION | 0.003052 |
| I_OTHER | 0.054715 |

No hay cohorte `>4` en FT-2R.1: comparación intrapoblación imposible, no rellenada
con datos ficticios. FT-2R ya atribuía sus fallos a imports/CPU; se conserva esa
conclusión histórica limitada. No sumar medianas o tiempos inclusivos anidados.

CPU/wall (70): mediana **0.999878**, mínimo 0.943323, máximo 0.999957.
CPU/wall de `>4` nuevo: no disponible porque n=0.
RSS/HWM pico muestreado **110332 KiB**, threads 4, fds 11.
Espera acumulada máxima de runqueue observada por proceso: **0.0024051 s**.
Son muestras, no garantías de máximos instantáneos. Identidad de integridad
idéntica en todos los ensayos. Las cifras finales de cgroup y cleanup se
contrastan en el cierre, no se infieren de estos picos.

## 5. Grafo de imports: lectura estática + medición dinámica

```text
L6.server → L4.server → mcp.__init__ → mcp.client.session (+ session_group/stdio exports)
                      → mcp.server.lowlevel / streamable_http_manager / types
L6.main → PersistentWiring → boto3 / botocore / sqlalchemy (barreras de proveedor)
                          → tickets_store / reports.store / user_notification_inbox
L6.main → adapter → src.tool_gateway.__init__ → executor → policy
                                                     → src.shared.context
                                                       → sqlalchemy.orm + persistence.models
                 → sophia_tool_chat.model_result → context.normalize_role (ya cargado)
```

Tiempos inclusivos medianos nuevos (70 cargas; NO sumarlos):
L6.server 0.858592; L4.server 0.744636; mcp 0.740869;
mcp.client.session 0.437091; adapter 0.402883; gateway 0.352626;
sqlalchemy 0.373858; context 0.345715 s.

Exclusivos respecto de hijos **instrumentados** (medianas): L6.server 0.032895;
L4.server 0.002746; mcp 0.000504; client.session 0.003732;
adapter 0.002033; gateway 0.000343; sqlalchemy 0.001994; context 0.000951 s.
El tiempo propio incluye imports stdlib/no instrumentados, por lo que no se
vende como un perfil exhaustivo de cada módulo. Datos en `analysis-final.json`.
El inicializador inspeccionado coincide, salvo CRLF, con la wheel MCP 1.30.0
fijada: SHA256 `666edb5009503e1047c9d60346a756f94b261f05cc2625f23d41c728ffc484d0`.

| Nodo | Necesidad, momento y posibilidad de quitarlo |
| --- | --- |
| L6.server | Entry server real; se usa antes de READY. No cliente-only ni type-only. Sacarlo cambia el servicio. Imports concretos actuales ya seleccionan sus clases. |
| L4.server | L6 hereda L4Application y reutiliza boundary/errores/transporte. Necesario antes de READY. Demorarlo sólo mueve el trabajo obligatorio o requiere refactor. |
| mcp | SDK requerido por tipos, servidor, transporte y sonda. El inicializador importa componentes cliente transitivos, aunque L6 no cree un ClientSession durante startup. `from mcp.types ...` también ejecuta `mcp.__init__`; no evita el costo. |
| mcp.client.session | Arrastre de `mcp.__init__`, no decisión de usar un QueryWorker ni lifecycle stdio. Parte de su costo incluye tipos/shared usados también por server. No atribuir todo su tiempo a trabajo eliminable. Evitarlo requiere modificar SDK/import machinery, fuera de este bloque. |
| adapter | Eager import explícito bajo wiring verificado; la sonda anterior a READY construye adapter y ejecuta READ_ONLY. Necesario antes de READY. No quitar la sonda ni mover autorización a después de READY. |
| src.tool_gateway | `__init__` exporta Executor y schemas; adapter usa los mismos Executor/policy/registry/audit. Vaciar el inicializador por sí solo no elimina el grafo, pues se importa inmediatamente de forma explícita. No cambiar semántica ni eliminarlo. |
| sqlalchemy | `PersistentWiring` importa para bloquear create_engine; `context` además carga ORM/modelos. No se abre DB. Quitar la barrera es inadmisible. Una anotación Session no es toda la dependencia: context tiene funciones reales que usan select/modelos. |
| src.shared.context | policy y proyección sólo necesitan normalize_role, pero el módulo carga ORM/modelos para otros consumidores. Es un candidato de separación de helper puro; no un import enteramente type-only. La API pública y el core canónico requieren revisión/pruebas y reempaquetado explícitos. |

No se encontró un import type-only costoso local que se pueda eliminar y que
no se vuelva a necesitar antes de READY. `VerifiedContext`/`Wiring` en scope
participan en verificaciones de tipo y construcción reales, no sólo anotaciones.
No se encontró doble import ejecutado dos veces en el mismo proceso; Python
conserva sys.modules. Las llamadas repetidas a metadata realizan verificación
de procedencia en varias capas; no se quitan/cachean checks para ganar tiempo.

## 6. Candidatos evaluados / decisión antes de cambios productivos

1. **IMPORT_CANDIDATE: extracción de normalize_role.**
   WHY_LOADED: policy y proyección importan context para el helper puro.
   WHY_NOT_REQUIRED_AT_STARTUP: las funciones ORM de context no son llamadas
   por las cuatro tools READ_ONLY; la barrera sqlalchemy sí sigue requerida.
   EXPECTED_EFFECT: potencial ahorro del subgrafo ORM/modelos, menor o igual al
   tiempo inclusivo de context; NO es una ganancia medida ni se suma a sqlalchemy.
   FILES potenciales: helper nuevo, context reexport, policy, sophia_tool_chat,
   selección/manifest/export de core y procedencia de capas posteriores.
   SECURITY_IMPACT / BEHAVIOR_IMPACT: no acreditados todavía; requeriría demostrar
   equivalencia y compatibilidad de imports/reexports, conservando todas las guardas.
   **No aplicado**: no es una edición local independiente del baseline canónico;
   el export actual lee blobs del commit fijado, no working tree. Requeriría
   cambiar explícitamente la cadena de artefactos, no inyectar módulos después
   de integridad. Con el incumplimiento no reproducido y margen observado, no
   se inicia ese cambio de procedencia como un supuesto quick win seguro.
2. **IMPORT_CANDIDATE: evitar client.session por import específico.** Rechazado:
   Python ejecuta igualmente el inicializador del SDK. Parchar SDK o sys.modules
   está fuera de las restricciones; no es una optimización local causal admisible.
3. **IMPORT_CANDIDATE: retrasar adapter / SQL guard / metadata checks.** Rechazado:
   adapter se necesita en la sonda previa a READY; retirar guardas/checks o
   ocultar trabajo detrás de READY incumple el contrato. Sin edición.

Optimizaciones productivas intentadas/conservadas: **0/0**; ganancia atribuible
a código: **ninguna demostrada**. No se hizo un A/B ficticio de 10+10 sin cambio.
No cambió seguridad, API, funcionalidad, SDK, dependencias, OS, recursos ni los
4 s productivos. La validación siguiente es **baseline original revalidado**,
no “post-fix”: no existe un fix productivo en este bloque.

## 7. Gate original de 4 s y cierre

Se completaron las poblaciones fijas sin cargar el observer ni profiler. Ninguna
observación de U1/U2 se reutilizó como aceptación. Fuente: `populations-final.json`.

| Contrato original (s) | Cold original 30 | Cold guarded 30 | Replacement 20 |
| --- | ---: | ---: | ---: |
| Dentro de 4 s | 30/30 | 30/30 | 20/20 |
| min | 1.904690 | 1.915420 | 1.861861 |
| p50 | 2.007034 | 1.949468 | 1.893066 |
| p90 | 2.272725 | 2.062518 | 1.936549 |
| p95 | 2.312738 | 2.074681 | 1.941620 |
| max | 2.529501 | 2.231306 | 1.998217 |
| margen max → 4 | 1.470499 | 1.768694 | 2.001783 |

Otros 20 cold de preparación para los replacements: 20/20; máximo 2.330651 s.
En total: 80 ensayos de aceptación / 100 startups originales, **cero timeout**.
30/30 guardas válidas, 90 procesos guardados y cero ejecuciones de los tres
lifecycles stdio prohibidos. No atribuir la diferencia guarded/original a una
“mejora por el guard”: orden secuencial distinto, no experimento causal pareado.

Los 130 ensayos (50 naturales + 80 originales) terminaron sin hijos residuales
ni cleanup forzado. Cgroup agregado: máximo de memoria 217120768 bytes,
memory.failcnt=0, OOM/under_oom=0, eventos de límite pids=0; pids simultáneos
muestreados hasta 6; nr_throttled=0 y throttled_time=0. No resource pressure
demostrado. Esta ausencia no demuestra cuál causó la variación histórica.

`ft2r1_gate.py` valida esas 80 evidencias, budgets exactamente 4, perfiles
originales sin timeline diagnóstico, isolation/cleanup/guard y consulta de
restauración. Luego ejecuta cada gate restante una vez y para en el primero rojo.
No hace git writes ni deploy. La corrida completa de ese gate terminó con
`gate.json: passed=true`, aceptando únicamente los dos fallos históricos
expresamente permitidos de la suite backend completa.

## 8. Aceptación y regresión: una ejecución de cada gate

| Gate | Resultado | Evidencia nueva |
| --- | --- | --- |
| L6 instalada, Linux | **142/142 PASS**, sin skips | `gate-tests.json` |
| C01–C24 independientes | **24/24 PASS** | `gate-recovery.json` |
| Smoke C04 | PASS | `gate-smoke.json` |
| Provenance | PASS, all_installed=true | `gate-provenance.json` |
| Tripwire negativo | **8/8 PASS**, 7 intentos detectados/bloqueados | `gate-guard.json` |
| IC-1 HTTP E2E | **32/32 PASS**, 0 failure/error, 32 HTTP tools/call | `gate-ic1.json` |
| Backend focal | **120/120 PASS** | `backend-focal.json` |
| Backend completa, UNA vez | **253 PASS / 255**, dos fallos históricos exactos | `backend-all.json` |
| Observer/contabilidad/gate unitarios | **16/16 PASS** | unittest `test_ft2r*.py` |

Suite L6 y C01–C24: 87 procesos guardados en cada corrida; E2E: tres.
En cada una: QueryWorker=0, ProcessOwner stdio=0, verified_server=0;
sin violaciones del tripwire. El control negativo de la guarda es aparte:
sus siete intentos rechazados son intencionales, no ejecuciones de stdio.
Los timeouts **inyectados** en aceptación (por ejemplo C07) deben ser contenidos;
no se confunden con fallos inesperados de los 100 startups originales de §7.

C.3: E2E conserva propuesta NEEDS_CONFIRMATION y cancelación sin create;
la regresión de confirmación/idempotencia pertenece a los tests focales.
No se afirma que una tool WRITE haya sido ejecutada por MCP.
I01–I30, sesiones/continuación propia, rechazo de thread ajeno, dos tenants,
roles/delegación, firma adulterada, ownership, segundo tool bloqueado,
timeout/result inválido y ausencia de fallback quedaron cubiertos. Algunos
Ixx se agrupan en un test; controles adicionales completan 32 métodos E2E.

Los únicos fallos backend completa:

1. `test_assess_ticket_automation.AssessTicketAutomationTests.test_global_url_wins_and_marks_source_global`
   — AssertionError, `test_assess_ticket_automation.py:50`.
2. `test_assess_ticket_automation.AssessTicketAutomationTests.test_normalize_plans_skips_empty_and_keeps_legacy`
   — AssertionError, mismo archivo, línea 125.

Coinciden con FT-2R; no se tocó VICTOR. La suite completa **no está 255/255 verde**.
Su guard bloqueó tres intentos de red, igual al baseline, y cero accesos a
secretos. No se atribuyen esos tres intentos a un test específico porque el
contador es global. Focal y suites HTTP: cero intentos externos/secretos.
E2E terminó con supervisor reaped/absent, sin hijos ni cleanup forzado.

SHA256 de evidencias principales:

| Archivo | SHA256 |
| --- | --- |
| gate-tests.json | `4b350e4d1ce6105bc8b2e8543fa3fbd7b615d043a5dbaf351be3902c14ae852e` |
| gate-recovery.json | `51b440c9a35d01d9c66ee8ace7763b670f771a04bd8781bad931998c284a1694` |
| gate-smoke.json | `2d61d8c3e3cad74bb22009aed59289429647478251ce998a752071cc151d34f3` |
| gate-provenance.json | `01382849e70dd4e73844cdba66472b414ad9609eb93595d695f8bd6ee1148d37` |
| gate-ic1.json | `e5a972f1a73d050c0c172a63b429c052d09b94a8c42092c80b21344c9fba210f` |
| backend-focal.json | `5034388734b441aead45c80d3416001e1b97e3bc1d5b1c3965129561764d0a86` |
| backend-all.json | `6d08506e505240780dfd88b69b6f20558f13a8c56ba0f4dfe1bbf63d75b0d412` |

## 9. Git y seguridad del cierre

Gate completo antes de stage/commit. Revisión de diff y `git diff --check`
limpios. Scan focal de 18 archivos nuevos/mantenibles e informe: cero matches
de claves AWS, PEM/OpenSSH privados o JWT completos. No es certificación de
ausencia universal de secretos; la fixture de firma pública está identificada
como no secreta y sólo laboratorio. No se imprimieron credentials ni tokens.

Commit **LOCAL** backend:

`2166d89ff93f85cfae0eee9c8345c194e4313a6c`

`feat: route Sophia Chat tool execution through MCP`

Autor configurado: Santiago. 19 archivos, 1228 inserciones / 4 eliminaciones:
puerto/cliente y conexión Chat ya existentes de IC-1, unitarios, harness E2E,
guardas, preparación Docker/lock, runners mantenibles y README de reproducción.
No se reescribió la implementación para obtener estos tiempos.

Fuera del commit: `.build`, raw evidence, observadores FT/IC-1R, los informes
diagnósticos, documentos ajenos, AGENTS y cambios CRLF/LF de MCP. Permanecen en
disco; no se eliminaron ni ocultaron. Por eso el working tree no se declara
completamente limpio, aunque el índice y el diff tracked backend sí quedaron vacíos.

MCP: **sin commit nuevo**, porque no hubo optimización productiva. HEAD continúa
`20fde196364bfcd2e006208cfb279549146c68a0`. Su estado/diff y los hashes de historia
comparados con preflight se conservaron. Handoff 159/160 nuevamente verificado.
Snapshot previo al commit: SHA256
`400118c2e59366518201753bc386cb3da155d40c4324005059aed1f2a1669333`.

Verificación posterior: `close-snapshot.json`, SHA256
`838e19a8c0fe17d91cce600249e660d61273b7edc769e81d3c28031e48a20a41`.
Los **75 reportes FT-2R** conservan sus hashes; historia/documentos ajenos y
estado Git completo de MCP coinciden con preflight. Los cuatro archivos de
implementación Chat/puerto/cliente coinciden byte a byte con el contexto de
imagen probado. Backend tracked diff e índice vacíos. Docker: ningún contenedor
en ejecución al cierre. Evidencias e imágenes locales se conservan.

El manifest de la imagen probada conserva HEAD precommit más hashes del working
tree; no fingir que esa imagen se construyó después del commit. El commit recoge
esos mismos archivos fuente; las wheels del core siguen fijadas al commit
canónico anterior. No regenerar el core desde el HEAD nuevo sin revisión de
su selección/procedencia. **NO push, NO pull, NO deploy.**

## 10. Riesgos y siguiente bloque

- **MEDIUM:** diferencia histórica de CPU/imports no explicada causalmente.
  El PASS acredita este entorno y esta campaña, no un SLA para cualquier host.
  Conservar FT-2R rojo y revalidar en el hosting que se diseñe; no llamarlo fix.
- **HIGH si se intentara usar en PROD:** identidades/firma/providers son fixtures;
  no hay acreditación de autenticación ni proveedores reales. No desplegar este lab.
- **LOW en el alcance probado:** guardas, aislamiento, ownership, slots, cleanup,
  deadlines y contrato HTTP pasaron la matriz completa.
- Legacy stdio teardown: **OPEN / KNOWN ISSUE / NON-BLOCKING FOR HTTP PATH**.
  No se investigó ni corrigió aquí; no forma parte de la ruta HTTP acreditada.

**FT-2 = PASS LOCAL. IC-1 = PASS LOCAL E2E FOR HTTP PATH.**
**READY_FOR_IC2A = YES.** No se inicia IC-2A en esta tarea.
Siguiente bloque separado: identity + signing + real READ_ONLY providers +
hosting/runtime design. No decisión contractual pendiente para este cierre:
se conservan startup **4.0 s**, recovery **12 s**, T_restore_service **21 s**.
No se propone una ampliación sin evidencia de la distribución histórica natural.

## 11. Respuestas a los 52 puntos solicitados

1. U1: 30/30 completos; detalle de los 30 en `U1/run-*.json` y §3.
2. U2: 20/20 replacements completos; detalle de los 20 en `U2/run-*.json` y §3.
3. Violaciones de 4 s naturales nuevas: 0.
4. Mayores de 5 s: 0.
5. Mayores de 6 s: 0.
6. Mayores de 8 s: 0.
7. Incompletos a 12 s: 0.
8. Cold min/p50/p75/p90/p95/max: tabla §3; media/desviación incluidas.
9. Recovery min/p50/p75/p90/p95/max: tabla §3; media/desviación incluidas.
10. Fase de los >4 nuevos: no hay muestras. Históricamente C_IMPORTS; §4.
11. CPU/wall lentos >4: no disponible nuevo; todos los nuevos, mediana 0.999878.
12. Presión de recursos: no demostrada; 653 muestras, cifras en §§4/7.
13. Distribución natural observada compacta bajo 4; no long-tail 6–12.
14. CONTRACT_MISMATCH probable: no acreditado por esta campaña; no reproducido.
15. PERFORMANCE_DEFECT probable: no reproducido ahora; causa histórica no resuelta.
16. Grafo relevante y tiempos inclusivos/exclusivos: §5.
17. client.session: export eager de mcp.__init__, no uso de lifecycle XOC stdio.
18. sqlalchemy: barrera create_engine + subgrafo ORM de context; sin DB real.
19. context: helper normalize_role importado por policy/proyección.
20. Quick wins: candidatos §6; ninguno acreditado como cambio mínimo seguro aplicable.
21. Optimizaciones intentadas: 0 (auditoría no es intento de parche).
22. Optimizaciones conservadas: 0.
23. Ganancia por optimización: no aplicable; no atribuir mejora a código.
24. Seguridad afectada: NO, sin retirar checks.
25. Funcionalidad afectada: NO cambio productivo nuevo en este bloque.
26. 4 s productivo cambiado: NO.
27. SDK/deps/Python/OS/resources/protocol cambiados: NO.
28. 30 cold originales: 30/30; baseline revalidado, no post-fix ficticio.
29. 30 guarded originales: 30/30.
30. 20 recovery originales: 20/20, consultas nuevas correctas.
31. L6: 142/142 PASS.
32. C01–C24: 24/24 PASS, corrida independiente.
33. Smoke: PASS.
34. Provenance: PASS.
35. IC-1: 32/32 E2E, 32 HTTP tools/call.
36. I01–I30: cubiertos, con agrupaciones y controles adicionales; §8.
37. No-stdio: QueryWorker=ProcessOwner=verified_server=0; guard PASS.
38. C.3: propuesta/cancelación E2E y confirmación/idempotencia focal conservadas.
39. Thread/session: PASS, propia/ajena/continuación.
40. Cross-tenant: PASS con fixtures A/B; no usuarios reales.
41. Backend focal: 120/120 PASS.
42. Backend completa: 253/255, exactamente los dos fallos históricos, una ejecución.
43. FT-2: PASS LOCAL de esta campaña, sin corrección causal histórica afirmada.
44. IC-1: PASS LOCAL E2E FOR HTTP PATH.
45. Stdio: OPEN / KNOWN ISSUE / NON-BLOCKING FOR HTTP PATH, no fixed.
46. Commit local backend `2166d89`; MCP sin commit nuevo; §9.
47. Push: NO.
48. Voice: NO tocado.
49. VICTOR: NO tocado.
50. Cloud: NO acceso ni deploy.
51. READY_FOR_IC2A: YES, no comenzado.
52. Decisión humana de timeout: ninguna necesaria ahora; conservar 4.0 s.
    La siguiente autorización corresponderá al bloque IC-2A, no a ampliar este alcance.

## 12. Comandos ejecutados / reproducción de evidencia

Desde XOC-API-AWS; los outputs deben ser nuevos, nunca sobrescribir esta evidencia:

```powershell
python -B -m unittest discover -s tests/ic1 -p test_ft2r*.py
python -B tests/ic1/prepare.py --mcp ../XOC-MCP --output tests/ic1/.build/ft2r1-20261010/context-u1
docker build --network none -t xoc-chat-mcp-ft2r1:diagnostic tests/ic1/.build/ft2r1-20261010/context-u1
python -B tests/ic1/ft2r1.py --population U1 --output tests/ic1/.build/ft2r1-20261010/U1
python -B tests/ic1/ft2r1.py --population U2 --output tests/ic1/.build/ft2r1-20261010/U2
python -B tests/ic1/prepare.py --mcp ../XOC-MCP --output tests/ic1/.build/ft2r1-20261010/context-control
docker build --network none -t xoc-chat-mcp-ft2r1:control tests/ic1/.build/ft2r1-20261010/context-control
python -B tests/ic1/ft2r1.py --population original --image xoc-chat-mcp-ft2r1:control --output tests/ic1/.build/ft2r1-20261010/original-30
python -B tests/ic1/ft2r1.py --population guarded --image xoc-chat-mcp-ft2r1:control --output tests/ic1/.build/ft2r1-20261010/guarded-30
python -B tests/ic1/ft2r1.py --population recovery-original --image xoc-chat-mcp-ft2r1:control --output tests/ic1/.build/ft2r1-20261010/recovery-20
python -B tests/ic1/ft2r1_gate.py tests/ic1/.build/ft2r1-20261010 --image xoc-chat-mcp-ft2r1:control --mcp ../XOC-MCP --dependencies ../.tmp-ticket-sync-tests
python -B tests/ic1/ft2r1_analyze.py tests/ic1/.build/ft2r1-20261010 --output tests/ic1/.build/ft2r1-20261010/analysis-final.json
```

La suite completa se ejecutó mediante `backend_regression.py --suite all`, que
usa unittest discovery `tests/test*.py` una vez con el mismo set (255), bajo
guardas offline y dependencias locales. No se corrió además una segunda suite
sin guardas. Backend host Python 3.13.1; los gates L6/E2E fueron Linux 3.11.16.
Guía mantenible incluida en el commit: `tests/ic1/README.md`.

## 13. Las 50 observaciones naturales individuales

U1 y U2 son poblaciones distintas; compartir número de fila no constituye un
experimento pareado. Cada U2 además tiene su cold preparatorio separado en JSON.

| Run | U1 cold (s) | U2 replacement (s) | >4 s / incompleto |
| --- | ---: | ---: | --- |
| 01 | 2.321998 | 1.967554 | No / No |
| 02 | 2.112355 | 2.082763 | No / No |
| 03 | 2.198057 | 2.004665 | No / No |
| 04 | 2.062167 | 2.040622 | No / No |
| 05 | 2.004606 | 1.959383 | No / No |
| 06 | 2.010258 | 1.962756 | No / No |
| 07 | 2.015068 | 2.134648 | No / No |
| 08 | 2.101221 | 2.074408 | No / No |
| 09 | 2.278590 | 2.078098 | No / No |
| 10 | 2.049882 | 1.975379 | No / No |
| 11 | 2.074872 | 1.941048 | No / No |
| 12 | 2.079865 | 1.971663 | No / No |
| 13 | 2.097971 | 1.950258 | No / No |
| 14 | 2.044157 | 1.955325 | No / No |
| 15 | 2.547555 | 1.970424 | No / No |
| 16 | 2.157337 | 2.038360 | No / No |
| 17 | 2.403164 | 2.103418 | No / No |
| 18 | 2.640754 | 2.034674 | No / No |
| 19 | 2.052690 | 2.068353 | No / No |
| 20 | 2.042623 | 2.020370 | No / No |
| 21 | 2.089522 | — | No / No |
| 22 | 2.065648 | — | No / No |
| 23 | 2.059267 | — | No / No |
| 24 | 2.074580 | — | No / No |
| 25 | 2.055724 | — | No / No |
| 26 | 2.101544 | — | No / No |
| 27 | 2.054118 | — | No / No |
| 28 | 2.050037 | — | No / No |
| 29 | 2.062464 | — | No / No |
| 30 | 2.422610 | — | No / No |
