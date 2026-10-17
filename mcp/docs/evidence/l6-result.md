# XOC / SOPHIA — MCP-2 L6 LOCAL: informe final

Fecha: 16-sep-2026. Campaña congelada: `l6-final03-20260916`.

## Decisión: INCONCLUSO

Implementación y campaña terminadas. **436 pruebas Linux, 24/24 escenarios y los tres pares de rendimiento pasaron.** El reductor registra **1802 gates PASS, 3 INCONCLUSO y 0 FAIL**. No se declara PASS LOCAL integral porque falta evidencia individual de recursos en tres arranques fríos. No se declara listo para Chat, deploy ni HA.

Únicos gates faltantes:

- `benchmark-1:cold_2:required_process_metrics_present`
- `benchmark-2:cold_2:required_process_metrics_present`
- `benchmark-3:cold_2:required_process_metrics_present`

En el segundo arranque frío de cada población L6, los snapshots periódicos guardaron `server=null`. El arnés conoce el PID al alcanzar READY y la consulta/retirada pueden terminar antes del siguiente muestreo de 250 ms. Faltan RSS/HWM, CPU, threads/tasks y fds individuales del servidor. Sí existen tiempos, fases y mediciones conjuntas del cgroup y de los otros procesos. Esto no demuestra un exceso de recursos, pero tampoco satisface la cobertura requerida. No se fabricaron mediciones retrospectivas ni se relajó el gate.

## Expediente verificable

- [Aceptación y gates](l6-acceptance.json).
- [Detección, recuperación y restauración](l6-recovery-measurements.json).
- [Comparaciones de rendimiento](l6-performance-comparison.json).
- [Procesos y limpieza](l6-process-cleanup.json).
- [Reproducibilidad](l6-reproducibility.json).
- [Preservación al cierre Linux](l6-preservation.json).
- [Preservación posterior a Windows](l6-preservation-post-windows.json).
- [Windows](l6-windows-final.json).
- Evidencia original: `l6-runs/l6-final03-20260916/`.

El reductor documental terminó con exit code 1 y `accepted=false` por esos tres faltantes. No modifica la wheel congelada ni sobrescribe campañas anteriores.

## Arquitectura y alcance

Estrategia B autorizada: un supervisor en proceso independiente recupera la instancia MCP persistente completa. Cuatro slots síncronos compartidos, incluido tools/list; no prefork, proceso por consulta, réplica extra ni replay. Puede perder consultas ficticias sanas en vuelo, incluso de otro tenant; no ofrece disponibilidad por tenant ni exactly-once.

El supervisor posee el handle exacto y generación del hijo. IPC mediante socketpair heredado privado, frames JSON tipados de hasta 4096 bytes, backlog 8. Sólo metadatos técnicos, sin tokens, identidades de negocio, argumentos ni resultados. Heartbeat y comprobación periódica de 250 ms. Lease absoluta de un segundo, renovada cada 250 ms; un cierre no se revierte por una renovación tardía.

Gate y lease se revalidan al reservar y al iniciar. El slot permanece hasta fin real del callable, reset del contexto y Future terminado. Un timeout del cliente no libera trabajo vivo. Observer desacoplado, cola acotada de 128 elementos más uno en proceso; EventFile hasta 8192 filas, con drops visibles. No se promete logging durable ni liberación inmediata del heap.

Detección de edad: antes de 5,25 s desde admisión. Un deadline conocido no espera el siguiente tick periódico. Si falta snapshot actualizado, una petición técnica sin payload solicita uno fresco hasta admisión+5,20 s, plazo no renovable. Sin confirmación se informa incertidumbre de control, no trabajo vivo demostrado. El supervisor detecta loop bloqueado por heartbeat antes de 2,25 s.

Reservas: ACK de cierre 0,25 s; drain 1 s sólo con ACK; terminate 0,65 s; kill/reap 0,35 s; startup 4 s; probe 3,5 s; open 0,25 s; coordinación 2 s. Recuperación ≤12 s y restauración ≤21 s. Reap obligatorio antes de reemplazar. Máximo dos intentos automáticos; tercer incidente → UNAVAILABLE sin tercer reemplazo.

Sonda privada mediante APIs SDK/HTTPX ASGI, boundary/L1/Adapter/Policy/Executor/helper fixture/proyección canónicos. Una operación de negocio tickets.list además de enumeración técnica, sin privilegio HTTP fabricable. Después de READY se exige otra consulta TCP real, despachada antes de 250 ms, con datos correctos. Fallo de ésta provoca retirada/UNAVAILABLE, no fallback.

Sólo archivos aditivos de XOC-MCP-LAB. Sin Git writes, push, pull, commit, deploy, cloud, credenciales ni cambios en API/Mobile/Web/proxy/runtime SOPHIA. No se tocaron VICTOR, Voice, Chat, Step Functions, RDS, core, SDK, L4, locks ni dependencias. Se conservaron los cambios ajenos existentes.

## Identidad, entorno y preservación

Amazon Linux 2, Python 3.11.16 x86_64, SDK MCP 1.30.0, protocolo 2025-11-25. Contenedor conjunto para arnés/clientes/supervisor/servidor: 512 MiB, cuota 2 CPU, 128 pids, usuario 10001, red deshabilitada, readonly y tmpfs 128 MiB. Sin puertos publicados, checkout montado ni descargas durante aceptación.

| Elemento | Identidad |
| --- | --- |
| Core preservado | `ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9` |
| Manifest fuente SHA256 | `645925f7f380014f04dc95fc9bc2a917bd2203295b2b4e12cb82c000a3c03253` |
| Wheel | `xoc_mcp_lab_l6-0.6.0+lab.645925f7f380-py3-none-any.whl` |
| Tamaño / SHA256 wheel | 299001 bytes / `772463131c7dd13d06fd25efd2ad3641c3d4bc06de8560d23626ee2252d3666f` |
| Imagen candidata | `sha256:7303f11b914a918fefdfb2279a701a5f355dfc911561f3910b449427b539feee` |
| Imagen L4 | `sha256:129efefeecc7549df84604ed2e532fa524aaab716f6cf145ca9e5522e6ab72b9` |

Dos builds independientes dieron las cinco wheels y manifests idénticos; cuatro wheels anteriores preservadas. Provenance: 41 casos canónicos, 37 comparaciones HTTP reales (cuatro entradas inválidas L1 no llegan a negocio) y 20 módulos instalados del core con hashes coincidentes.

Preservación final y posterior a Windows: **125/125 archivos, 26/26 grupos históricos y 5/5 estados branch/HEAD/status coincidentes**. Los grupos abarcan 14114 archivos enumerados según baseline. Son manifests, no backups restaurables. Git status no acredita hashes de todos los archivos externos. Las diferencias iniciales de método de enumeración se conservan en checks anteriores; se corrigió el verificador, no el histórico.

## Pruebas y rendimiento

Linux instalado: **436 passed / 0 failed / 0 skipped = 212 C1 + 92 L4 + 132 L6**. Los 304 nodeids históricos pasaron juntos con los nuevos.

Windows: **397 passed / 39 skipped / 0 failed**, 71,49 s del wrapper: 289 históricos + 108 nuevos portables. Skips: 15 históricos y 24 Linux. No acredita recuperación POSIX en Windows.

Smoke independiente correcto. **Todos los escenarios C01–C24: PASS.** Un caso negativo aprobado acredita rechazo/retirada esperados, no restauración de cada incidente.

| Par y orden | Mediana L4/L6 ms | Razón | p95 L4/L6 ms | Razón |
| --- | ---: | ---: | ---: | ---: |
| 1 L4→L6 | 23,539 / 23,420 | 0,99493 | 27,138 / 27,438 | 1,01105 |
| 2 L6→L4 | 23,047 / 23,257 | 1,00911 | 26,118 / 27,479 | 1,05214 |
| 3 L4→L6 | 22,192 / 23,244 | 1,04743 | 32,693 / 29,793 | 0,91129 |

Todas las razones ≤1,25. Misma imagen congelada, población, límites e instrumentación; L4 ejecuta su servidor original instalado. Orden serial alternado, sin seleccionar el mejor par.

Cada una de seis poblaciones pasó 80 consultas calientes (20/tool), cuatro concurrentes, cinco arranques fríos, tres respuestas grandes y cinco rondas de 40: 36 allowed y cuatro errores de proveedor deliberados por ronda. Ninguna consulta normal superó 3,5 s ni 5 s. La corrección/latencia fría pasa; no su cobertura individual completa de recursos.

Medianas frías L4/L6, segundos: 2,045/2,104; 2,026/2,112; 2,008/1,989. Máximos concurrentes L4/L6: 0,0651/0,1023; 0,0676/0,1072; 0,0700/0,1010. Los tiempos de otras campañas no reemplazan estos denominadores.

## Recuperación, pérdidas y recursos

En la matriz final, siete incidentes de edad tuvieron detección desde entrada al proveedor entre 5,006978 y 5,014422 s. Máximo **desde admisión: 5,014957 s**, menor que 5,25. Heartbeat: 2,004952 s. Tiempos desde inyección técnica o desde observación de salida no son tiempo físico universal de detección de averías.

**17 restauraciones** mediante TCP nuevo y correcto; máximo T_restore_service **8,851994 s**, despacho tras READY máximo **0,010485 s**. Las 24 fases de recuperación medidas, incluidas salidas negativas esperadas, tuvieron máximo **5,657568 s**: no son 24 restauraciones exitosas.

Máximos de fases de la matriz, segundos: startup 2,733141; probe 3,080075; open 0,015610; close ACK 0,249252; drain 0,999239; terminate 0,649143; kill/reap 0,010621; coordinación 0,001645. Ningún exceso de reserva en final03. JSON conserva las muestras y orígenes; no se suman máximos de incidentes diferentes para fabricar RTO.

C20 sin ACK: ventana candidata **0,409836124 s**, **seis entradas residuales al proveedor** durante lease válida. Muerte del supervisor: una consulta antes del vencimiento funcionó; después del vencimiento comprobado, cero entradas en tres consultas. El hijo siguió vivo; cleanup explícito del arnés mediante subreaper, **no autorrecuperación del supervisor**.

C05 acredita consulta sana admitida antes del ACK y viva al retiro. C14 distingue ejecución completada de entrega perdida; C19 conserva incertidumbre del trabajo al caer el servidor. No se reejecutan ni se inventa un agregado de pérdidas fuera de los casos observados.

Peak conjunto máximo **277929984 bytes (~265,05 MiB)** en Tests, menor que 512 MiB. CPU quota 2, pids limit 128, OOM/failcnt/eventos de límite cero. Máximo pids muestreado 9; snapshots finales de Tests/Provenance no son máximos de toda su vida. No sumar HWM de instantes diferentes. Faltan los tres servidores fríos antes descritos.

Diez etapas con final_children vacío. Reap interno separado de retirar contenedores; cleanup de fallos separado de recuperación. Inventario Docker final: ningún contenedor etiquetado L6; los ocho contenedores ajenos iniciales seguían activos, sin intervención.

## Historial conservado

- check01: build solamente.
- check02: smoke/provenance correctos, 20/24 escenarios; fallos C01/C15/C21/C23.
- check03: 425 passed, 2 failed. Histórico C1 test_failed_provider_no_retry_or_fallback: KeyError en result, causa no demostrada; assertion intacta. C05 L6: se corrigió contraste de ACK/cierre real y snapshots.
- final01: 23/24; C15 **5,250679084 s desde admisión**, exceso 0,679084 ms. Suite 426/1/0, todos los 304 históricos pasaron. Se corrigió deadline absoluto/confirmación fresca sin ampliar límites. Provenance no se ejecutó tras el fallo.
- final02: 436/0/0, 24/24, smoke y provenance correctos. Sustituida sólo para agregar evidencia fría y nodeids al arnés.
- final03: resultados propios de este informe; **INCONCLUSO por cobertura de recursos**.

No se borraron fallos, builds ni comprobaciones intermedias. Ningún resultado anterior se atribuye al artefacto final.

## Archivos y comandos

Nuevos módulos en xoc_mcp_lab_l6: control, supervisor, pool, server, probe, integrity, harness, acceptance, acceptance_context, acceptance_control, validation e __init__. Ocho módulos de tests: contexto, lifecycle, recovery, pool, supervisor, servidor, validación y packaging. Packaging: l6_build.py y Dockerfile.l6. Scripts: l6.ps1, l6_preservation.ps1, l6_finalize.py (éste documental, fuera de la wheel).

Pasos ejecutados serialmente: Build, Rebuild, Image, Smoke, Recovery, Tests, Provenance, BenchmarkL4 1, Benchmark 1, Benchmark 2, BenchmarkL4 2, BenchmarkL4 3, Benchmark 3. Ejemplo para otra campaña, siempre con RunId NUEVO:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/l6.ps1 -Step Smoke -RunId NUEVO_RUN_ID
# Benchmark/BenchmarkL4 usan -Sample 1, 2 o 3.
# Parada acotada exclusivamente a contenedores propios:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/l6.ps1 -Step Stop -RunId NUEVO_RUN_ID
```

Cierre ejecutado:

```powershell
.venv\Scripts\python.exe -I -B scripts/l4_windows.py --output reports/l6-windows-final.json
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/l6_preservation.ps1 -IncludeHistoricalGroups
.venv\Scripts\python.exe -I -B scripts/l6_finalize.py --run l6-final03-20260916 --output-prefix reports/l6
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/l6_preservation.ps1 -IncludeHistoricalGroups -Output reports/l6-preservation-post-windows.json
```

Outputs existentes: no sobrescribir. No prune ni limpieza global. Stop no sustituye reap interno. Cota externa del arnés 1800 s no amplía budgets internos.

## 25 respuestas obligatorias

1. **Mecanismo:** B, recuperación finita de instancia persistente, cuatro slots, sin replay.
2. **Independencia:** supervisor en otro proceso, propiedad por handle/generación e IPC heredado acreditados.
3. **Detección:** fuera del loop; máximo 5,014957 s desde admisión y 2,004952 s por heartbeat.
4. **Consulta sana:** puede perderse/ser incierta al retirar instancia; C05 acredita el radio de impacto.
5. **Ventana residual:** C20, 0,409836124 s y seis entradas válidas; no revocación instantánea.
6. **Trabajo después de cierre:** reserva/arranque revalidan gate/lease; escenarios ACK/expiración/cierre pasaron.
7. **Contabilidad:** callable/reset/Future/release real, observer acotado separado; timeout no cancela trabajo vivo.
8. **Reap:** obligatorio antes de spawn; fallo impide reemplazo, cleanup acreditado.
9. **Sonda:** privada, SDK/gateway canónicos, una operación de negocio, sin bypass HTTP.
10. **Nueva consulta:** 17 restauraciones TCP correctas, despacho máximo 10,485 ms.
11. **Tiempos:** recuperación máxima 5,657568 s; restauración 8,851994 s; muestras/orígenes en JSON.
12. **Excesos:** ninguno medido final03; exceso final01 conservado y no convertido en PASS.
13. **Intentos:** máximo dos; tercer incidente UNAVAILABLE sin tercero.
14. **Fallos de supervisor/control/reap/startup/probe:** fail-closed o UNAVAILABLE según caso, sin fallback. Supervisor muerto requiere intervención explícita.
15. **Pérdida/incertidumbre:** C05 sana interrumpida, C14 entrega perdida, C19 caída con trabajo vivo. No zero-loss/exactly-once.
16. **Replay/fallback:** no reejecución del usuario ni segunda operación de negocio oculta.
17. **A/B/A2:** aislamiento/context reset pasaron; supervisor sin autoridad de negocio.
18. **Tres pares:** razones mediana 0,99493/1,00911/1,04743; p95 1,01105/1,05214/0,91129; todas ≤1,25.
19. **Limpieza:** diez etapas sin hijos al cierre, sin contenedores L6, ocho ajenos intactos.
20. **Preservación:** 125 archivos/26 grupos/cinco estados Git; no equivale a backup ni hashes de todo repo externo.
21. **Faltante/no probado:** tres mediciones individuales frías; no proveedores/cloud/datos reales, host adversarial, HA ni fugas indefinidas. Inyecciones no prueban kernel realmente ininterrumpible.
22. **Decisión contractual:** INCONCLUSO; 1802 PASS, tres faltantes, cero FAIL. No PASS LOCAL integral.
23. **Único siguiente paso:** muestreo determinista de recursos del servidor en READY y antes del retiro, comparable L4/L6; congelar otro artefacto y repetir campaña completa. No implementado/ejecutado en este cierre.
24. **VICTOR/Voice:** no tocados ni validados/protegidos por L6.
25. **Chat/deploy/HA:** NO autorizados ni acreditados; no comenzar otro bloque automáticamente.

El faltante es de observabilidad del ensayo, no prueba de imposibilidad de B. Pruebas funcionales verdes no sustituyen aceptación contractual completa.
