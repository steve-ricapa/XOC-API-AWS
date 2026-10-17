# MCP-2 L3 LOCAL — resultado y decisión

Fecha: 15-sep-2026. Sólo XOC-MCP-LAB. Datos ficticios. Sin cloud ni Git writes.

## Resumen ejecutivo

L3 está cerrado como investigación e implementación LOCAL. Se conservó L2,
se repitió el baseline, se perfiló el arranque y se implementó UN candidato:
C1, presupuesto total con cierre/recolección dentro del tiempo disponible.

Resultado funcional: 212/212 tests Linux y 212/212 Windows; smokes MCP-1 y firmado
aprobados; 41 casos de equivalencia canónica; dos builds finales independientes
idénticos. Los 20 archivos del gateway y el hash del wheel core no cambiaron.

Resultado temporal: C1 mejora el cierre, pero NO acredita el objetivo de 3,5 s.
Las 80 consultas correctas tuvieron mediana 2,873 s, p95 3,076 s y máximo 3,910 s.
El initialize colgado con techo 5 s bajó de 7,108 s a 4,807 s, incluido cleanup.
No quedaron hijos pendientes en los escenarios observados.

**Decisión C: evaluar la alternativa de servidor persistente contemplada en el
plan, sin implementarla aquí.** No es una conclusión sobre capacidad cloud ni
una afirmación de que cuatro usuarios no funcionan. Tampoco descarta toda posible
optimización futura: concluye que este diseño/paquete medido no deja margen fiable
para el presupuesto propuesto, sin iniciar una búsqueda indefinida de ajustes.

GO de regresión funcional local y de cierre en los fallos ensayados.
NO-GO del presupuesto objetivo de 3,5 s / integración Chat / producción.
El techo de 5 s se respetó en las muestras C1, no es garantía de tiempo real duro.

## 1. Costo dominante y magnitud

Domina la carga/inicialización del hijo, no la lectura fixture ni el handshake
MCP aislado. En las 80 consultas C1, esperar initialize desde transport_open tiene
mediana 2,433 s; la consulta completa, 2,873 s. Esa espera incluye intérprete,
imports, hashes, firma, Wiring y protocolo: NO significa 2,433 s de handshake.

Desglose diagnóstico cProfile/importtime, tres muestras por imagen:

| Tramo inclusivo | Baseline, segundos aproximados | Interpretación |
| --- | ---: | --- |
| Carga de imports agregada | 3,108–3,200 | Bajo profiler; mayor parte del arranque diagnosticado |
| Compilar fuentes durante imports | 1,571–1,627 | Incluido en imports, NO sumarlo otra vez |
| Import del adapter | ~2,00 | Incluye SDK y core transitivo |
| Import mcp | 1,096–1,149 | Incluido en adapter |
| Import executor canónico | 0,604–0,640 | Incluye policy → shared.context y dependencias |
| Wiring.__enter__ | 0,931–1,002 | Arnés fixture; incluye imports de boto3/SQLAlchemy |
| Import SQLAlchemy | 0,514–0,545 | Incluido en Wiring; NO es una consulta RDS |
| require_installed, dos llamadas | 0,096–0,101 | Se mantuvo la verificación independiente |
| Verificación del sobre en hijo | ~0,007 | No se omitió |
| Adapter.call | 0,0014–0,0019 | Gateway, fixture y proyección; no proveedor real |

En C1 los imports diagnosticados son 2,815–2,957 s y source_to_code 1,400–1,474 s.
No se modificó ese código/import graph: esas diferencias NO se atribuyen a una
optimización de imports. Hay variación temporal del host.

El primer marcador Python se observa 15,7–17,8 ms después de entrar a la factoría
del proceso. Incluye scheduling/interpreter/runpy, no sólo CPU. Se correlaciona
CLOCK_MONOTONIC únicamente entre procesos del mismo contenedor Linux/boot.

Padre C1, medianas observadas sin profiler invasivo: serializar sobre 0,030 ms;
firma/claims 0,429 ms; admisión 0,007 ms; backend_path 21,804 ms; verificación del
intérprete 21,820 ms; entorno 0,081 ms; retorno de factoría 0,992 ms; roundtrip tool
6,055 ms; validación/serialización final 0,065 ms; cleanup 365,975 ms.
No se suman medianas para reconstruir una mediana total.

Como muestra diagnóstica adicional, la primera corrida C1 registra model_result
canónico en 0,101 ms, transport.write agregado en 0,866 ms y recepción de requests
del servidor en 0,406 ms. Son spans inclusivos CPU/perfilados y pueden incluir
reanudaciones: no equivalen a latencia de red ni aíslan exactamente initialize.
La espera completa de initialize sí está medida en el padre. El teardown de
Wiring/Isolation y la espera/recolección están separados en la evidencia.

Detalle exclusivo/inclusivo y acumulado: [l3-startup-profile.json](l3-startup-profile.json).

## 2. Componente, arnés y costo de observar

Baseline comparable: l3-baseline-probe conserva BYTE-IDÉNTICOS los grupos core y
adapter de L2. Sólo incorpora el arnés L3. El hash/versionado del wheel adapter
también liga el manifest del arnés; un hash distinto no implica lógica distinta.

Baseline original inmutable también se repitió con su instrumentación L2, pero
NO se usa su diferencia contra un benchmark sin profiler como ganancia C1.
Las 80 consultas comparables usan el servidor verificado original y la misma
telemetría mínima: ProcessObserver + muestreo RSS/cgroup cada 20 ms. Large/fallos
usan el arnés previo para datos grandes/bloqueo del proveedor FICTICIO.

Tres pares minimal/diagnóstico por imagen: el diagnóstico añadió de forma
observada 0,740–0,978 s en baseline y 0,679–0,976 s en C1. Es orientativo: hay
variación del host y el diagnóstico no lleva el sampler de memoria del mínimo.
No se resta este costo para declarar PASS. cProfile no vuelca variables, tokens
o resultados; escribe stderr, nunca el stdout MCP.

El padre nuevo externo incluye imports, Isolation, firmante fixture/observador,
worker y teardown. Su diferencia frente al worker NO se atribuye enteramente a
negocio ni a cold start Lambda. Se conservan ambas medidas.

Host compartido: ocho contenedores ajenos ya activos, no tocados. No hubo builds,
descargas ni regresión Windows propios durante el benchmark de aceptación.
Se añadieron 18 sondas alternadas baseline/C1 para limitar sesgo temporal, sin
seleccionar sólo la mejor corrida. Ver [método](l3-methodology.md).

## 3. Cambios del laboratorio

Código ejecutable: query_worker.py modificado y budget.py nuevo en xoc_mcp_lab.
El candidato C1 sólo cambia presupuesto/supervisión, no negocio.

Instrumentación: benchmark.py ampliado sin retirar escenarios L2;
l3_benchmark.py y l3_profile_child.py nuevos en xoc_mcp_lab_harness.

Tests nuevos: test_l3_total_budget.py y test_l3_lifecycle.py. Ninguno de los
archivos de tests L1/L2 fue modificado. Scripts nuevos: scripts/l3.ps1 y
scripts/l3_pair_probe.py. README y compatibility-l3-linux.json documentan L3.
Los JSON/Markdown l3-* contienen metodología, resultados y preservación.

No hubo segundo candidato, caché, pool, import liviano, extracción canónica,
dependencia nueva ni modificación de archivos instalados del SDK.
Inventario con hashes: [l3-source-changes.json](l3-source-changes.json).

## 4. Contratos conservados y comprobación

Se conservaron firma ES256 fixture, claims, binding request/tool/args/contexto,
contexto inmutable, auditoría/proyección, Policy/Executor/handlers originales,
Isolation/Wiring y hashes del paquete. Firma inválida y falta de margen antes de
spawn no crean hijo. Falta de margen después de initialize no inicia la tool.

Una oportunidad por QueryWorker; segunda llamada y concurrencia sobre la misma
instancia denegadas. Instancia nueva con sobre vigente puede hacer otra lectura:
no hay ledger, deduplicación distribuida ni promesa exactly-once. Sin retry,
fallback o segunda herramienta. No se reutiliza un hijo entre identidades.

Las cuatro tools siguen siendo xoc.tickets.list, xoc.tickets.get,
xoc.documents.list y xoc.notifications.unread_count. Tickets/documentos conservan
la Policy por tenant; unread por tenant+usuario. ADMIN_XOC exige delegación;
SUPERADMIN, unknown/disabled y ownership injection siguen denegados. Ticket ajeno
e inexistente no exponen datos. No se cambió a autorización por creador.

Fronteras conservadas y probadas: entrada 65.536 B, salida completa 262.144 B,
sobre 16.384 B y JWS 4.096 caracteres; Unicode/escaping, límites exactos y exceso.
Las tres respuestas grandes válidas de cada imagen dieron 98.017 bytes stdout
por hijo, sin truncamiento, 3 handlers y 54 lecturas ficticias acreditadas.
La recepción del cliente SDK sigue limitada después del parseo; no se convirtió
en defensa preasignación contra un servidor arbitrario hostil.

## 5. Bytes del core

Sí, idénticos a L2. Revisión canónica:
ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9.

SHA256 del wheel core, 127.657 bytes:
df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41.

Igualdad por manifest de los 20 archivos, wheel y origen instalado comprobada.
Ningún sys.path al checkout en Linux. Los imports salen de
/opt/xoc-l2/lib/python3.11/site-packages. Sin fallback ni editable install.

## 6. Tiempos completos antes/después

Mismo AL2/Python 3.11.16/x86_64, MCP 1.30.0, protocolo 2025-11-25, stdio;
512 MiB, cuota 2 CPU y 128 pids. Runtime uid 10001, red none, readonly,
cap-drop ALL, no-new-privileges, tmpfs acotado y montajes mínimos.

| Escenario | Baseline comparable | C1 |
| --- | ---: | ---: |
| 80 secuenciales, éxitos | 80/80 | 80/80 |
| Total mínimo | 2,842 s | 2,620 s |
| Total mediana | 2,957 s | 2,873 s |
| Total p95, nearest rank | 3,189 s | 3,076 s |
| Total máximo | 3,498 s | 3,910 s |
| Trabajo mediana | 2,569 s | 2,487 s |
| Cleanup mediana | 0,384 s | 0,366 s |
| 5 padres nuevos: worker mediana | 2,998 s | 2,702 s |
| 5 padres nuevos: total externo mediana | 4,252 s | 3,841 s |
| Total externo rango | 4,127–4,403 s | 3,831–3,865 s |
| Initialize colgado, techo 5 s | 7,108 s | 4,807 s |
| Bloqueado, presupuesto 4,5 s, 2 muestras | 6,607 s | 4,313–4,325 s |
| Tardío, presupuesto 4,5 s, 2 muestras | 5,596–5,722 s | 4,312–4,314 s |
| Grande, 3 éxitos, total mediana | 3,227 s | 2,703 s |

La mejora secuencial mediana de ~84 ms (~2,8%) es modesta y no se atribuye toda
al cambio: no hubo optimización de imports y el máximo C1 fue peor. La mejora
repetida y causalmente identificada es eliminar la gracia adicional de cierre.

La repetición del L2 original instrumentado dio 80 éxitos, mediana 2,964 s,
máximo 3,470 s y fallo máximo 7,110 s. Se conserva separada.

P95 nearest-rank, sin p99. Los percentiles de n=3/5 describen esas muestras,
no un SLA. Errores/timeouts se incluyen en vistas generales; éxitos por separado.

## 7. Casos sobre 3,5 s y 5 s

| Conjunto | Baseline >3,5 / >5 | C1 >3,5 / >5 |
| --- | ---: | ---: |
| 80 secuenciales, presupuesto 5 s | 0 / 0 | 1 / 0 |
| 5 workers con padre nuevo | 0 / 0 | 0 / 0 |
| 5 tiempos externos de padre nuevo | 5 / 0 | 5 / 0 |
| 11 fallos inyectados | 5 / 5 | 5 / 0 |
| 3 grandes | 1 / 0 | 0 / 0 |
| 4 workers bajo presión compartida | 4 / 4 | 4 / 0 |
| 5 normales alternados a 3,5 s | 0 / 0 | 0 / 0 |
| 4 bloqueados/tardíos alternados a 3,5 s | 4 / 4 | 0 / 0 |

En las 99 muestras individuales de cada benchmark hay seis sobre 3,5 s en
cada imagen; cinco sobre 5 s en baseline y cero en C1. No se mezclan con los
tiempos externos de sus padres ni se borran los fallos deliberados.

Sondas alternadas C1: normales 2,716–2,747 s, 5/5 éxitos; bloqueado/tardío
3,310–3,311 s, error seguro y recolección. Cinco éxitos no borran el outlier de
3,910 s del conjunto mayor. El timeout total se configura explícitamente en
esas sondas; el benchmark principal conserva techo 5 s.

Datos y clasificación de errores: [l3-candidate-comparison.json](l3-candidate-comparison.json).

## 8. Reserva y consumo del cleanup

Un monotonic deadline nace al entrar a QueryWorker, antes de verificar. Se acepta
sólo un timeout local finito, positivo y <=5 s. Tras verificar, el límite es el
mínimo del deadline local y el firmado restante; el verifier prueba deadline<=exp.
No se confía en claims sin firma ni se extiende el sobre.

Reserva interna: min(0,65 s, mitad del presupuesto), ajustada al plazo verificado.
Margen antes de spawn 0,05 s; antes de initialize/tools/call 0,02 s. Verificación,
entorno, spawn, protocolo, resultado/serialización, cierre y wait consumen la
misma cuenta. No hay dos segundos nuevos de gracia después del total.

Supervisor por request: cerrar stdin, esperar hasta 0,45 s/fracción del resto,
terminate sólo el handle propio y esperar hasta 0,10 s/fracción restante,
kill si es necesario y wait con el resto. SDK y framing stdio originales.

El SDK fijado no expone handle público en stdio_client. Se captura mediante un
dispatcher ContextVar que delega su factoría privada original; no se copia el
SDK ni se editan site-packages. El SDK aún posee su context manager; el supervisor
permite que encuentre el hijo recolectado sin consumir su espera adicional de 2 s.
Esta dependencia privada es un riesgo explícito, no API productiva aprobada.

Si el kernel/cierre excede el total: hasta 0,25 s acotados de observación de
rescate, contados como SOBREPASO, nunca éxito o tiempo extra de negocio. Si sigue
incompleto: error y slot no liberado como sano. No hay garantía de tiempo real
duro de Python/OS ni frente a todos los shield internos del SDK/AnyIO.

## 9. Timeout, cancelación y fallos

Timeout de initialize/handler o EOF devuelve error seguro lab_worker_unavailable,
sin retry/fallback, y espera el cierre. Cancelación del caller se propaga después
de recoger su intercambio/supervisor. Arranque, consulta y cleanup tienen tests
específicos, incluida cancelación dentro de la factoría con proceso ya creado.

Los bloqueos/tardíos de 4,5 s y las sondas de 3,5 s acreditan initialize completo,
handler_entered y provider_read_entered. Tras terminar el hijo, el audit final
puede faltar: se informa «handler iniciado, conteo final incompleto», NO cero
lecturas/ejecuciones. No se llamó ningún proveedor real.

Firma inválida/falta de margen previa: cero hijos. Falta de margen después de
initialize: cero handler. Spawn fallido, EOF, cierre transitorio y error sintético
de wait tienen regresión. Si el SDK recupera un error transitorio, sólo se admite
resultado válido con hijo recolectado y returncode 0.

Una inyección inicial permanente de error aclose produjo avisos de transportes
Windows. Se sustituyó por error transitorio y se agregó fallo de wait; se corrigió
un assert nuevo que exigía error incluso tras recuperación válida. No se relajó
ninguna prueba anterior. Esto NO acredita fallo permanente real de kernel.

## 10. Hijos, zombies y recursos propios

En cada benchmark se observaron 103 hijos: 99 consultas individuales y 4 bajo
presión. Todos con returncode, waitpid_not_child=true y sin entrada /proc después
de reaping. Más 18 sondas alternadas, diagnósticos y smokes con comprobaciones
previas a salir. No se usó --rm ni la salida del contenedor como prueba de cleanup.
Las tareas de intercambio/supervisión C1 observadas finalizaron; ningún lifecycle
individual registró overrun o recolección incompleta.

Muestreador propio cerrado/join comprobado por el arnés. No se mata por nombre
ni se terminan procesos/servicios ajenos. Windows comprueba returncode/wait y
tareas propias; no tiene /proc/waitpid POSIX, por eso esos campos no aplican.
Detalle de PIDs/returncodes y límites: [l3-process-cleanup.json](l3-process-cleanup.json).

## 11. Qué demuestra la concurrencia

Se conservaron CUATRO slots. Cuatro workers en el MISMO contenedor no llegaron a
initialize dentro del tiempo de trabajo; los cuatro fallaron de forma segura.
El quinto devolvió lab_busy sin crear un quinto hijo. C1 cierra esos cuatro en
4,809–4,852 s; baseline necesitó 5,862–5,939 s. El código legacy de la medición
devuelve error genérico: agotamiento del plazo se infiere de sus marcas, no de
un código timeout separado. La presión completa C1 duró 4,950 s.

No demuestra «cuatro usuarios incompatibles» ni simula cuatro Lambdas separadas.
512 MiB y 2 CPU Docker tampoco replican exactamente una Lambda de 512 MB.

Memoria secuencial C1: máximo simultáneo padre+hijo 188.014.592 B; cgroup actual
muestreado máximo 143.220.736 B. Bajo presión: 428.175.360 B RSS simultáneo y
314.937.344 B cgroup. Se miden por separado; NO sumar máximos no simultáneos.
RSS cuenta páginas compartidas por proceso y cgroup incluye caché. CPU acumulada
secuencial mediana ~2,985 s/query; presión ~9,704 s CPU frente a ~4,950 s de pared.
OOM/under_oom/oom_kill/failcnt observados en cero. SIGKILL de baseline no se atribuye
a OOM: forma parte del cierre forzado. Muestreo 20 ms puede perder picos.

## 12. Regresiones ejecutadas y exclusiones

Baseline antes de cambios: Windows 191/191 (90,31 s), Linux L2 inmutable 191/191
(111,37 s). Final: Windows 212/212 (84,41 s), Linux 212/212 (127,37 s), cero fallos,
errores o skips. Se agregaron 21 pruebas. No cambió ningún test L1/L2.

MCP-1 independiente: Windows y Linux aprobados, 12 consultas permitidas y 16
denegadas, catálogo de cuatro tools. Smoke firmado independiente: Windows y Linux
aprobados, seis casos y tres hijos recolectados; firmas/binding inválidos sin
hijo y SUPERADMIN sin handler. Equivalencia Linux: 41 casos iguales al snapshot
de referencia, 20 orígenes canónicos acreditados. Fronteras wire/lifecycle van
incluidas en ambas suites completas.

No se ejecutaron suites globales backend, Voice/WebSocket, VICTOR ni tests cloud.
No se consultó producción. [l3-validation.json](l3-validation.json) distingue
comandos/resultados de esta ejecución y limitaciones del sistema operativo.

## 13. Reproducibilidad final

Dos staging limpios y builds offline finales: l3-c1-final-build/sources-a y
sources-b, usuario explícito 10001:10001. Tres wheels y manifests iguales byte
a byte, también iguales a los usados por la imagen C1 ya probada. El builder
inicial heredaba el usuario por defecto de la imagen base; se explicitó el usuario
y se repitió la construcción, sin cambiar fuente ejecutable ni tiempos medidos.

Core: df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41.
Adapter: 0f82b903e2b0bbbc5107a6371a2da95c83c0561c76f5b918a1cb1df1798da2ac.
Harness: f41abe59883f1bc9311ed88f8582b1f31639bc2be65e1163412925c17bd2d736.
Manifest fuente: cdce81cbc01458aa73d12d3709c2608fb58a5742d0970809378bb14e1b1625f8.

Imagen C1: sha256:421858fa81bd95a041aa12e85e6528d48eacae293674789156a6b19aebc3fd40.
Misma base oficial por digest que L2. Locks, wheelhouse y builder reutilizados,
sin descargas ni dependencias nuevas. No se editó el venv; hash del SDK stdio
Windows comprobado igual al inicial.
[l3-reproducibility.json](l3-reproducibility.json) conserva hashes completos/orígenes.

## 14. Preservación externa

Sólo se modificó XOC-MCP-LAB. Se compararon rama, HEAD y status de los cinco repos
relevantes contra el registro inicial; cambios previos ajenos conservados.
Esta comprobación Git no se presenta como inventario byte a byte de todo XOC ni
como inspección del estado cloud. Informes/locks/tests previos del laboratorio
verificados por hash. Artefactos L2 previos no se sobrescribieron.

No se hicieron llamadas AWS/Azure/Foundry ni cambios a Chat, backend, Web, Mobile,
Voice, IAM, recursos, flags, timeouts productivos, RDS o Step Functions. No se
leyeron credenciales. No hubo commit/push/pull/stage ni reparación del .git padre.
Los contenedores ajenos permanecieron fuera de alcance.

## 15. Decisión C y riesgos

Se selecciona **C**, no A: la recolección mejora de forma comprobada, pero p95
secuencial 3,076 s ya supera la referencia worker p95<=3 s del plan; existe un
caso de 3,910 s sin Invoke/KMS/red/DynamoDB. A 3,5 s ese caso tendría que fallar
seguro. Incluso el padre reutilizado deja poco margen; no se basa la decisión
sólo en el startup externo del arnés o la presión de cuatro workers.

No se elige B como ajuste mínimo identificado: el costo está repartido entre
SDK, compilación, dependencias canónicas y Wiring; no se demostró que una única
extracción canónica cierre el presupuesto. No se aplicó ninguna extracción.

Comparación con la alternativa de §5 del plan: un servidor persistente podría
amortizar imports/intérprete y evitar el arranque por lectura. Es expectativa,
NO latencia medida. Añade riesgos de contexto por solicitud, lifecycle, transporte,
backpressure y aislamiento entre identidades. No se eligió servicio cloud, ni
se implementó HTTP/pooling/prefork o reutilización de hijos en este bloque.

Riesgos HIGH para una integración prematura: presupuesto 3,5 s sin margen real;
identidad fixture no productiva; rutas/controles productivos fuera de esta prueba.
Riesgos MEDIUM: acoplamiento privado SDK, cierre ante fallos de kernel no acreditado,
host no exclusivo y muestras pequeñas de cola; limitación post-parseo del SDK.
Riesgo LOW local residual: variabilidad de tiempos; no afecta sistemas externos
porque no se integró el laboratorio a ellos.

Se conservan gateway, Policy, Executor, contratos, paquetes reproducibles y todos
los tests. C1 queda como evidencia local, no como release productivo aprobado.

## 16. Único siguiente bloque propuesto

Autorizar **L4 LOCAL: evaluación acotada de la alternativa persistente del plan**,
primero definiendo identidad/contexto por solicitud y una prueba comparativa
con fixtures y los mismos límites/paquetes/Policy. Debe probar explícitamente
aislamiento, DENY, una tool por request, cancelación y ausencia de estado cruzado.
Sin escoger infraestructura cloud ni integrar Chat como parte de esa evaluación.
No se inició este bloque ni se creó un servidor persistente.

## 17. VICTOR

NO se involucró VICTOR. No se pidió ni inspeccionó su repo/runtime/credenciales,
no se ejecutaron sus tests y no se convirtió en gate de MCP.

## 18. Autorización de cloud, Chat o producción

NO. Esta entrega es LOCAL. No autoriza ni ejecuta deploy, cloud, MCP público,
integración Chat, activación de flags o producción.

## Reproducción y evidencia

Ver [l3-commands.md](l3-commands.md) para comandos PowerShell, exit-code checking,
RunIds efectivamente ejecutados y uso de directorios nuevos. No volver a ejecutar
un RunId existente: los scripts rechazan sobrescribir evidencias.

Evidencia cruda preservada:

- l3-runs/l3-original-20260915: tests y benchmark del L2 inmutable.
- l3-runs/l3-baseline-probe: baseline con arnés comparable y perfil diagnóstico.
- l3-runs/l3-candidate-c1: paquete instalado, tests/smokes/equivalencia/perfil/benchmark.
- l3-runs/l3-budget-pairs-20260915: 18 sondas alternadas a 3,5 s.
- l3-runs/l3-c1-final-build: dos builds finales no privilegiados e idénticos.

El informe no borra los resultados históricos ni declara que main sea PROD.
