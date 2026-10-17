# MCP-2 L2 LOCAL — empaquetado canónico y Linux offline

Fecha: 15-sep-2026. LAB_ONLY / OFFLINE / FIXTURES.

## 1. Resultado y alcance

L2 implementa artefactos y pruebas ejecutables, no otra auditoría general.
El mismo gateway del commit aprobado se empaqueta en un wheel y ejecuta mediante
MCP stdio real en Amazon Linux 2/Python 3.11, sin checkout del backend.
Se mantiene la API Windows anterior y el sobre ES256 ficticio L1.

Sólo se editaron archivos en XOC-MCP-LAB. No stage, commit, Git init, push,
deploy, llamadas a cuentas AWS/Azure, modelos o datos reales. No integración
Chat ni cambios Voice, VICTOR, Mobile, Web, RDS, autenticación o Serverless.

La aceptación funcional del paquete se separa de latencia/capacidad. El benchmark
no acredita AWS y no se aumentan recursos o timeouts para convertirlo en PASS.

Resultado final: **GO funcional LOCAL para empaquetado/equivalencia; NO-GO para
el presupuesto propuesto de 3,5 s completos / 5 s duros y cuatro consultas
simultáneas con 512 MiB/2 CPU. NO autorizado para producción.**

## 2. Baseline, fuente y preservación

Backend: main, HEAD explícito `ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9`.
No se afirma que main sea PROD. Antes de editar se registraron los diez repos
existentes, sus ramas/HEAD/status y hashes de fuentes/configuración no secretas,
y los 39 archivos previos del laboratorio fuera del venv/caches.

- Baseline Windows: 183 passed, 69,49 s, exit 0.
- Smoke MCP-1 previo: 12 consultas autorizadas, 16 denegaciones, exit 0.
- Nuevo smoke firmado independiente, antes del empaquetado: PASS, 6,75 s;
  tres hijos terminaron/recolectados con returncode 0.
- Regresión Windows tras portabilidad: 183 passed, 70,49 s.
- Suite ampliada: 191 passed en Windows; 79,47 s y repetición final 81,31 s.
- Smoke MCP-1 posterior: PASS; smoke firmado posterior: PASS, 7,20 s.

No se volvieron a ejecutar las suites de backend no modificado: los números
114 focal/249 completos y los dos fallos históricos de VICTOR son evidencia de
L1, no una ejecución L2 nueva. No se corrigió VICTOR incidentalmente.

Snapshots: `l2-baseline-repositories.json`, `l2-baseline-lab.json`,
`l2-repository-preservation.json`, `l2-validation-windows.json`.
Estos hashes NO son un backup restaurable ni una comprobación de salud cloud.
La comparación final del laboratorio, archivo por archivo, está en
`l2-source-changes.json`; excluye venv/caches/resultados generados y su propio
archivo para evitar un hash autorreferencial. Ningún archivo previo se borró.

## 3. Qué se empaqueta

Distribuciones independientes:

| Artefacto | Contenido |
| --- | --- |
| xoc-tool-gateway-core | 20 fuentes canónicas `src.*`, sin alterar bytes |
| xoc-mcp-lab | Adapter/worker/stdio, verifier, Isolation y Wiring/fixtures obligatorios |
| xoc-mcp-lab-harness | Tests, mismo firmante fixture, smoke, procedencia y medición |

Los 20 archivos y su motivo individual están en `packaging/gateway-files.json`.
El manifest generado añade por archivo ruta original/destino, blob Git, SHA256
y dependencias `src.*` observadas estáticamente. Se revisan imports fuera de
funciones y también imports diferidos dentro de ellas; la ejecución completa
de las cuatro tools y las denegaciones complementa ese inventario.

No se copia toda la app: no rutas HTTP, workers, deploy scripts, .env, caches,
binarios Windows, .git ni archivos backend untracked. No se creó una Policy,
normalize_role, Executor o helper ficticio para reparar imports.

### Dependencias transitivas importantes

Policy/proyección necesitan `context.normalize_role`; el módulo original importa
`persistence.models` y `shared.http`. Se incluyen los modelos ORM y Werkzeug,
pero no `persistence/db.py`, engine, sesión SQL ni conexión. No hay consultas RDS.

La proyección vive en `sophia_tool_chat.py`, que también define transporte y
capabilities; se incluyen sus bytes completos sin invocar esas funciones.
Necesita config/requests/PyJWT. `config.py` define acceso a Secrets Manager,
pero ese getter NO se llama. Tickets/logging y documentos/Inbox conservan
también funciones de escritura no expuestas por el gateway de este laboratorio.
Wiring bloquea clientes reales, credenciales y engines; sus tablas sólo aceptan
lecturas ficticias concretas. Por eso este core NO debe publicarse como un
worker productivo sin otra revisión de fronteras/autorización.

## 4. Build y procedencia

`export` usa Git en lectura: rev-parse y cat-file del SHA fijado. Rechaza drift
de HEAD y no cambia branch ni mezcla el working tree. Cada staging es nuevo;
se conservan fuentes exactas, incluidos sus finales de línea originales.

`build` usa sólo ese staging y locks. Es un builder pequeño con stdlib zipfile
que produce wheel 1.0: METADATA, WHEEL y RECORD con SHA256/base64. Orden fijo,
epoch ZIP 1980, permisos fijos, ZIP_STORED, sin pyc ni rutas absolutas. Pip instala
los wheels y `pip check` comprueba las dependencias.

Dos staging independientes y builds Linux offline finales (`sources-a/b`,
`build-a/b`) produjeron los mismos tres wheels byte por byte. El core se mantuvo
idéntico durante todas las iteraciones de instrumentación del arnés.

Fuente del adaptador/arnés: manifest
`fcca96f068638532c71f05b4b3cf10f5729b75a59508e92da7e2fa9020a87764`.
No existe un commit Git del lab y no se inventó uno.

SHA256 finales:

- Core: `df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41` (127657 bytes).
- Adapter: `f65c499132c62a955a83c158ec76921d3fc01e61be2ca40b2dc8697caefc9d05` (68732 bytes).
- Harness: `1ed985a09b4f7e7e61341ab63eca2a0f7a7801fa650ebf066d7d2361067b330b` (116529 bytes).

Evidencia: `l2-reproducibility.json` y
`../l2-runs/verified-20260915/build-a/artifact-manifest.json`.
Los manifests aportan integridad/procedencia documentada, NO firma autenticada.
Se acredita reproducibilidad de wheels, no identidad reproducible de imágenes OCI.
La escritura respeta el [formato wheel oficial](https://packaging.python.org/en/latest/specifications/binary-distribution-format/).

## 5. Linux y dependencias

Imagen oficial de base:
`public.ecr.aws/lambda/python@sha256:cc940054e77d4a160c611a20c09cb8ab9325c09d72555a164329eb3e46957d80`.

Entorno observado: Amazon Linux 2, glibc 2.26, x86_64, Python 3.11.16,
GCC de identificación 7.3.1, pip 24.0. No se compiló ninguna dependencia:
todas llegaron como wheels públicos compatibles. No cambio de SDK/protocolo:
MCP 1.30.0, revisión negociada 2025-11-25.

Los [entornos Python oficiales de AWS](https://docs.aws.amazon.com/lambda/latest/dg/python-image.html)
identifican Python 3.11 como AL2. Usar esta imagen local no invoca Lambda ni
certifica un deployment existente. Su metadato OS incluye SUPPORT_END=2026-06-30;
el ciclo de vida del runtime/base deberá revisarse antes de un release cloud,
sin cambiar aquí el objetivo autorizado.

Docker Desktop 4.40.0, engine 28.0.4, host Linux/amd64, 16 CPU visibles al daemon.
Al principio estaba apagado; lo abrió el usuario. WSL Ubuntu 24.04/Python 3.12 se
consultó únicamente para diagnóstico y NO se usó para acreditar el objetivo.
No se instalaron/habilitaron servicios ni se cambió Python global.

43 dependencias runtime y 5 de test, con hashes separados. El inventario local
incluye nombre, versión, wheel, tamaño, SHA256, Requires-Python y Requires-Dist.
No es una certificación de vulnerabilidades ni SBOM firmado. Las versiones
coinciden con Windows salvo exclusión de pywin32/colorama, propios de Windows.
Las wheels nativas son Linux cp311/abi3; no se reutilizaron .pyd ni el venv Windows.

Ubicación: `../l2-runs/20260915/preparation/dependency-inventory.json` y wheelhouse;
locks Linux en `../packaging/`. El lock/venv Windows siguen intactos.

Preparación: sólo descargas públicas PyPI e imagen oficial. Después, instalación
con `--no-index --require-hashes --only-binary=:all: --no-compile`, build/test/smoke/
benchmark sin red externa. La imagen se construyó con `docker build --network none`.

## 6. Demostración sin checkout

La imagen recibe exclusivamente wheels, locks, referencia ficticia e instalador
del arnés. No staging ni checkout. Las ejecuciones montan sólo `/results`.
No HOME, credenciales, .env, docker.sock, host PID/net, privileged o puertos.

Límites: 512 MiB, 2 CPU, 128 procesos; usuario 10001; filesystem read-only,
cap-drop ALL, no-new-privileges, tmpfs 64 MiB y network none. No emulación.
El CWD es `/tmp`, distinto del código instalado. El intérprete arranca con -I/-B.

Origen observado de los 20 módulos canónicos:
`/opt/xoc-l2/lib/python3.11/site-packages/src/...`.
`installed.py` verifica manifest/hash por archivo y versiones, rechaza otro
backend, módulos precargados ajenos, symlinks de fuentes y imports `src.*` no
incluidos. No editable installs ni .pth hacia checkouts. El entorno Python
conserva su stdlib/base normal; no se confunde eso con una dependencia backend.

Se compararon 41 casos contra Windows. Sólo se eliminan request_id y audit_id,
deliberadamente variables. No se normalizaron datos, decisiones, errores,
argumentos, lecturas ni handlers: igualdad completa del resto.

Evidencia final: `../l2-runs/verified-20260915/results/provenance/`:
environment.json, origins.json, equivalence.json y final-process-check.json.

## 7. Smoke, regresiones y seguridad

`smoke_l1.py` funciona separadamente y reutiliza el único firmante público L1,
movido al arnés y reexportado para tests históricos. No genera credenciales
productivas ni imprime el sobre/JWS. Firma válida A/B, firma inválida, cambio
tool/args antes de iniciar hijo, SUPERADMIN firmado denegado, segunda llamada
rechazada y cierre comprobado.

Linux intermedio: 183/183 y después 191/191. Linux final: 191 passed, 0 failed,
0 errors, 0 skipped, 120,88 s. Smoke firmado final: PASS, 10,07 s, tres hijos
recolectados con returncode 0. La suite incluye también el smoke MCP-1 real.

Cobertura conservada: cuatro tools, dos tenants y dos usuarios del mismo tenant,
delegación válida/ausente, SUPERADMIN/unknown/disabled/WRITE, claims/firma/hash/
ownership injection, una llamada, repetición secuencial/concurrente, replay
idéntico en otro worker como nueva lectura, cero handlers en denegación previa,
lookup tenant-scoped de ticket ajeno, errores seguros y bloqueo de proveedores.

Se añadieron ocho tests focalizados de empaquetado/lifecycle: cierre de imports,
RECORD/doble wheel, fallo si ya existe salida, modo instalado sin fallback,
firmante único, fallo de spawn, error inyectado al terminar cleanup ya recolectado,
llamadas concurrentes a una instancia y línea temporal incluyendo cierre
(varios checks se agrupan en un mismo test).

Límites: input 65536 bytes, output 262144 bytes, sobre 16384 bytes, assertion
4096 caracteres. Se conservan fronteras y pruebas Unicode/escaping, exceso de
salida pequeño sin truncar JSON y equivalencia texto/structuredContent.
El SDK comprueba recepción después del parseo: no se afirma protección previa
de memoria ante un servidor arbitrario hostil. Isolation tampoco es sandbox
contra Python local comprometido.

## 8. Medición y aceptación de presupuesto

La evidencia definitiva de esta sección se acompaña en `l2-measurement-summary.json`.
El benchmark completo está en `../l2-runs/verified-20260915/results/benchmark/`.

Se mide desde antes de verificar el sobre hasta después de salir del contexto
SDK y recolectar el hijo. Se conserva timeline monotónico: verificación/admisión,
preparación, arranque, initialize, ToolResult, validación, inicio cleanup y final.
El arnés añade spans inclusivos de Policy, Executor, handler, proveedor ficticio,
proyección y adaptación/serialización. NO se suman spans anidados.

Muestreo de RSS padre/hijos y memoria cgroup cada 20 ms; puede perder picos y RSS
cuenta páginas compartidas por proceso. Se informa máximo simultáneo separado
de los máximos individuales. Contadores cgroup CPU/memoria/OOM se leen sin cambiar
límites. No se suman métricas incompatibles para inventar consumo total.

El primer benchmark conservado (`final-20260915`) tuvo 80 éxitos secuenciales:
mediana total 3,063 s, trabajo 2,650 s y cleanup 0,404 s; máximo total 3,967 s.
Cinco padres nuevos tardaron 4,36–4,83 s incluyendo arranque/recolección del padre.
Un hijo colgado con presupuesto de trabajo 5 s tardó 7,111 s incluyendo cleanup.
Cuatro workers simultáneos agotaron el plazo; el quinto devolvió lab_busy.

Ese primer arnés no encontraba cgroup v1 (su cero de memoria cgroup NO representa
0 bytes) y las pruebas blocked/late de 2,5 s no alcanzaban el handler. No se usan
esas filas para acreditar memoria o bloqueo de tool. Se conservaron como evidencia
intermedia y se corrigió el arnés: cgroup v1/v2, valor ausente=null, y marcador de
entrada real al handler/proveedor en los fallos. No se cambió Policy, SDK, timeout
máximo 5 s ni recursos. La repetición definitiva sustituye esas inferencias.

Conclusión del primer pase: NO-GO para prometer 3,5 s completos / 5 s duros.
Eso no invalida equivalencia/autorización del paquete. No hubo reintentos ocultos,
reuso de hijos, eliminación de validaciones o aumento de memoria/CPU/timeout.

### Resultado FINAL — verified-20260915

80 consultas secuenciales, 20 por herramienta, **80 correctas / 0 errores**.
Tiempos en segundos; percentil nearest-rank. Las estadísticas de cada tramo
se calculan independientemente, no se suman medianas ni spans inclusivos.

| Medida, 80 muestras | Mínimo | Mediana | p95 | Máximo |
| --- | ---: | ---: | ---: | ---: |
| Trabajo | 2,526 | 2,685 | 3,250 | 3,724 |
| Cleanup | 0,382 | 0,406 | 0,469 | 0,513 |
| TOTAL, incluido cierre | 2,910 | 3,095 | 3,730 | 4,189 |

| Total por tool, 20 muestras | Mediana | p95 | Máximo |
| --- | ---: | ---: | ---: |
| xoc.tickets.list | 3,487 | 3,941 | 4,189 |
| xoc.tickets.get | 3,030 | 3,202 | 3,314 |
| xoc.documents.list | 3,030 | 3,208 | 3,383 |
| xoc.notifications.unread_count | 3,089 | 3,313 | 3,594 |

Cinco padres nuevos: total 5,313 / 5,539 / 5,335 / 4,807 / 5,145 s,
incluidos arranque y recolección del padre. El worker dentro de esos padres
tardó 3,516 / 3,911 / 3,768 / 3,346 / 3,653 s, respectivamente.
Esto es arranque local medido, NO cold start Lambda.

Timeline acumulado medio desde entrada del worker: verificación 0,000563 s,
preparado 0,051271 s, transporte abierto 0,053476 s, initialize completo
2,767426 s, ToolResult 2,774527 s y validado 2,774569 s. La evidencia apunta
al arranque/imports/initialize como coste dominante, no a las lecturas ficticias;
no permite inferir latencia de datos reales. El benchmark completo conserva
también los spans inclusivos internos por muestra, sin modificar el gateway.

Concurrencia: **4/4 workers agotan el plazo** (`lab_worker_unavailable`), quinta
solicitud `lab_busy` sin hijo; lote 6,528 s incluido cleanup. No se considera PASS
de capacidad. Contadores cgroup OOM y memory_failcnt permanecen en cero: no se
atribuye un returncode -9 automáticamente a OOM. Todos los hijos se recolectan.

Fallos controlados, con dos repeticiones salvo el plazo máximo:

| Escenario | Trabajo observado | Cleanup | Total |
| --- | ---: | ---: | ---: |
| initialize colgado, plazo corto | 0,402–0,403 | 2,103 | 2,505–2,507 |
| EOF abrupto | 0,061–0,062 | <0,001 | 0,061–0,062 |
| Handler bloqueado, plazo 4,5 s | 4,503 | 2,104 | 6,607 |
| Respuesta tardía, plazo 4,5 s | 4,503–4,504 | 1,328–1,624 | 5,832–6,128 |
| Cancelación | 0,401–0,402 | 2,103 | 2,505–2,506 |
| initialize colgado, trabajo máximo 5 s | 5,006 | 2,103 | **7,110** |

Blocked/late finales sí completaron initialize y registraron entrada al handler
y proveedor. Una auditoría final ausente no se interpreta como cero ejecuciones:
se informa handler iniciado=1 y conteo de lecturas incompleto. No se ejecuta una
segunda tool ni se reintenta. El test de error durante cleanup es una inyección
DESPUÉS de que el SDK ya recolectó el hijo: no prueba todo fallo posible del kernel.

Tres respuestas grandes válidas: total 3,163 / 3,222 / 3,065 s; 1 handler y
18 lecturas ficticias cada una. 98017 bytes recibidos por sesión (incluye
initialize; NO se presenta como tamaño de un único mensaje). Las pruebas de
frontera separadas acreditan un mensaje de resultado >64 KiB completo.

Picos muestreados en bytes, límite cgroup 536870912 bytes (512 MiB):

| Medida | Secuencial | Cuatro simultáneos |
| --- | ---: | ---: |
| RSS padre | 76058624 | 78155776 |
| RSS máximo de un hijo | 110514176 | 108064768 |
| RSS simultáneo padre + hijos | 186572800 | 505122816 |
| Memoria cgroup current | 140922880 | 385241088 |

CPU cgroup consumida durante el benchmark: 331,185 s de CPU acumulada, NO duración
de pared. Cgroup v1 informa cuota 200000/100000 us (2 CPU). Al final del benchmark:
61243392 bytes de memoria current, OOM=0, failcnt=0, children vacío. RSS puede contar
páginas compartidas varias veces; cgroup incluye cache. No se equiparan ni se
deduce una capacidad productiva a partir de una sola máquina local.

**Decisión FINAL de presupuesto: NO-GO**, confirmando el primer pase. El programa
benchmark terminó con exit 0 porque pudo medir y cerrar todos los ensayos; esto
NO significa que las cuatro consultas bajo presión fueran exitosas.

## 9. Repetición operativa y archivos

Desde XOC-MCP-LAB, `scripts/l2.ps1` acepta pasos Prepare, Export, Build, Rebuild,
Image, Tests, Smoke, Provenance, Benchmark, Inspect. Todos comprueban exit codes,
usan argumentos/rutas explícitas y rechazan sobreescribir salidas. RunId nuevos.
Los comandos paso a paso completos están en README, sección L2.

También se ejecutó `Step Prepare -RunId repeat-prep-20260915`: 48 paquetes,
exit 0. Los dos locks obtenidos volvieron a tener exactamente los hashes de la
primera preparación. Las descargas públicas pertenecen a su contenedor separado;
los contenedores de pruebas/benchmark nunca recuperaron conectividad externa.

Ejecución final comprobada: RunId `verified-20260915`, Steps Export → Build →
Rebuild → Image → Tests → Smoke → Provenance → Benchmark. Build/Rebuild offline,
wheelhouse `l2-runs/20260915/preparation`. Image resuelve luego a ID inmutable
`sha256:be002dede580ab1bb0ede1c27caf951813eadd3c2971a600eb05d7a421f77ece`.
No se publica esa imagen.

Step Inspect final también ejecutado con exit 0: mismo ID de imagen y ninguna
fila de contenedores propios pendientes para verified-20260915. La ausencia de
hijos/zombies se comprobó dentro de cada ejecución, antes de --rm.

Archivos nuevos: `packaging/`, `scripts/l2.ps1`, `xoc_mcp_lab/installed.py`,
`xoc_mcp_lab_harness/`, `smoke_l1.py`, dos archivos tests L2,
`compatibility-linux.json` y evidencias L2. Ajustes mecánicos existentes:
server.py, run_tests.py, client.py, isolation.py, verified_server.py,
query_worker.py, reexport tests/l1_fixtures.py y README.

Adapter/Policy/Executor/Audit/helpers canónicos no se editaron. `query_worker.py`
añade selección del intérprete instalado y observación temporal sin cambiar el
contrato de respuesta, los límites o el circuito single-use.

No se sobrescribieron informes L1. Staging, wheels y resultados intermedios
permanecen separados. Contenedores propios usan --rm; antes de su salida los
tests/benchmark comprueban hijos y zombies. No prune, reset ni borrado global.

## 10. Decisiones y siguiente bloque

La matriz A–N definitiva se entrega en `l2-acceptance.json` y el resumen de medidas.
La autorización L2 termina aquí, independientemente del resultado de latencia.

| Gate | Respuesta |
| --- | --- |
| A. Smoke firmado independiente | Sí; Windows y Linux, mismo firmante fixture |
| B. Fuente canónica exacta | Sí; 20 archivos del SHA registrado, sin editar lógica |
| C. MCP real Linux instalado | Sí; 191 tests y smoke |
| D. Sin checkout/symlink/editable/fallback backend | Sí; orígenes y hashes verificados |
| E. Artefactos repetibles | Sí; tres wheels byte-idénticos |
| F. Cuatro tools y aislamiento conservados | Sí; 41 comparaciones equivalentes |
| G. Firma/binding/DENY/límites | Sí; cobertura L1 conservada |
| H. Cierre antes de terminar contenedor | Sí; escenarios y limitación de inyección descritos arriba |
| I. Trabajo/cleanup/total | 2,685 / 0,406 / 3,095 s de mediana secuencial; datos completos arriba |
| J. 3,5 s completos / 5 s duros | No; NO-GO local, no validación AWS |
| K. Pruebas no ejecutadas | Ninguna prueba funcional L2 omitida, 0 skipped; detalles abajo |
| L. Repos y servicios productivos intactos | Sí respecto de nuestras acciones; hashes comparados, sin operaciones cloud |
| M. Siguiente bloque | Únicamente propuesta L3 LOCAL de presupuesto/arranque/cierre |
| N. ¿Publicar en producción? | NO |

K: no se ejecutaron suites backend nuevamente porque no cambió; ni AWS/Invoke/KMS,
Azure/Foundry, Chat o proveedores reales porque están fuera de alcance. Tampoco
Linux ARM64, otras versiones Python/base/SDK o tamaños alternativos de recursos.
Los cuatro timeouts concurrentes SÍ se ejecutaron y se reportan como fallo de
rendimiento, no se esconden como tests omitidos o resultados Windows.

Riesgos: HIGH si se pretendiera usar este presupuesto como garantía productiva
o publicar fixtures/claves públicas de prueba; MEDIUM por acoplamiento de imports
canónicos mixtos y ciclo de vida de la base que requiere revisión antes de cloud;
LOW para interpretar muestras locales pequeñas como percentiles de producción.
No se promete cero riesgo: salida del SDK limitada después del parseo y controles
offline no sustituyen una sandbox frente a código Python comprometido.

Un único siguiente bloque propuesto: **L3 LOCAL — presupuesto extremo a extremo
y perfilado de arranque/cierre**, manteniendo un hijo nuevo por request, fixtures,
misma autorización y sin cloud. Debe revisar primero las hipótesis de 3,5/5 s y
admisión simultánea con evidencia; cualquier extracción de imports backend o
cambio de arquitectura requiere autorización específica, no se aplicó en L2.

Pendiente antes de AWS: presupuesto viable, autorización de integración, identidad
productiva, release fuente acreditado, IAM/keys, proveedores reales acotados,
ensayo de recuperación y smoke consentido. No es requisito obtener VICTOR.

**L2 NO autoriza publicar el paquete en producción.** La clave fixture ES256 es
pública/deliberadamente insegura y nunca sirve como identidad productiva.
