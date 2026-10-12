# SOPHIA / MCP — FT-2R, diagnóstico HTTP L6

Fecha: 2026-10-10. Ejecución local, sin cloud. Este documento no reemplaza ninguna
evidencia anterior: añade el diagnóstico solicitado al fast-track.

## Decisión

**FT-2 = NO PASS. IC-1 no se cierra. READY_FOR_IC2A = NO.**

El baseline original reproduce el agotamiento del presupuesto de startup de
4 segundos sin guard. También ocurre durante el arranque de reemplazos.
La fase dominante está localizada en imports/inicialización con consumo de CPU.
No se ha demostrado una race, doble inicialización accidental o defecto propio
de L6 cuya corrección mínima cumpla las restricciones del prompt.

ROOT_CAUSE, entendida como defecto corregible y explicación de la variabilidad:
**NOT_ISOLATED**. El tiempo consumido por imports sí está medido; no equivale a
una prueba de que exista un import prescindible o un bug del supervisor.

Por la condición de parada del prompt: no aplicar fix especulativo, no ampliar
timeout/recursos y no avanzar a las aceptaciones finales dependientes de L6.

## Integridad y alcance

Repos en `feature/sophia-chat-mcp-ic1`:

- XOC-API-AWS: `ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9`.
- XOC-MCP: `20fde196364bfcd2e006208cfb279549146c68a0`.

Preflight: `tests/ic1/.build/ft2r-20261010/preflight.json`:
159/159 archivos del manifest y 160/160 checksums válidos antes de ejecutar.
Se registraron status, rama, HEAD, diff, staging y diff --check de ambos repos.
Los cambios previos y el ruido de finales de línea de MCP no se normalizaron.

Baseline instalado, sin reconstruir core ni L6:

| Elemento | Valor |
|---|---|
| Imagen base | `sha256:ffd2cdc17c30289ca4eaa3787cabe8190c38017f1840ad2020a7baef2b6c8df4` |
| Wheel core SHA256 | `df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41` |
| Wheel L6 SHA256 | `6fa74cf9fc336d52c7900ab4c35f3c4fd82e6e1e2b60c57ccc63156b2c0908d6` |
| Python | 3.11.16 |
| OS | Amazon Linux 2, linux/amd64 |
| SDK / protocolo | 1.30.0 / 2025-11-25 |
| Recursos | 512 MiB, swap total 512 MiB, 2 CPU, 128 pids |
| Slots | 4, sin cambio |
| Aislamiento | network none, read-only, tmpfs /tmp 128 MiB, UID 10001, capacidades eliminadas, no-new-privileges |

Las imágenes derivadas sólo añaden el harness/backend de pruebas. No tienen
mounts del checkout, perfiles cloud ni credenciales reales. No hubo otros
contenedores de campaña simultáneos; cada contenedor termina antes del siguiente.

## Qué significa el presupuesto

En `Supervisor.start`, la fase `startup` cubre preparación/spawn del servidor,
imports, wiring, aplicación, bind y observación de hello + primer heartbeat
con verificación del reloj. Su presupuesto sigue siendo **4.0 s**.

Después se ejecutan la sonda privada (3.5 s) y la apertura (0.25 s).
READY sólo se emite después de ambas. No confundimos `ready.elapsed` con el
presupuesto de la fase `startup`. Tampoco movimos imports a la sonda para
hacer pasar artificialmente una fase.

Los procesos nuevos son cold starts de proceso, no de caché de disco del host.
Se conserva la verificación canónica original; no se añade warm-up. Los relojes
monotónicos Windows/host y Linux/contenedor se guardan en dominios separados.

## Controles originales: todos los resultados

Los 20 originales y los 20 guarded usan la misma imagen
`sha256:9bc97cb82c47570dbef4c349a904062f129f05df5ef248fbc51d195ad8272b67`.
El proceso original ejecuta los módulos instalados sin el bootstrap diagnóstico.
Guarded sólo añade el guard final ya existente, no un guard alternativo.

| Población | READY | Agotan startup | <3.5 s | 3.5–3.8 s | 3.8–4.0 s | >4.0 s observado |
|---|---:|---:|---:|---:|---:|---:|
| Original, 20 | 6 | 14 | 0 | 1 | 5 | 14 |
| Guarded, 20 | 9 | 11 | 0 | 0 | 9 | 11 |

Estadística de la duración **observada hasta éxito o aborto**:

| Población | min | p50 | p90 | p95 | max |
|---|---:|---:|---:|---:|---:|
| Original | 3.775970 | 4.000186 | 4.000293 | 4.000300 | 4.000374 |
| Guarded | 3.832264 | 4.000142 | 4.000238 | 4.000252 | 4.000333 |

Los timeouts están censurados por la derecha. 4.0002 s NO significa que el
servidor haya terminado de arrancar en ese tiempo: significa que el supervisor
agotó el presupuesto. La distribución real completa de finalización y sus
p90/p95/max no son identificables con tantos abortos. Los cuantiles se calculan
por interpolación lineal, no seleccionando únicamente resultados verdes.

Finalizaciones exitosas, complemento sesgado que NO representa la población:

| Población | n | min | p50 | p90 | p95 | max |
|---|---:|---:|---:|---:|---:|---:|
| Original | 6 | 3.775970 | 3.930437 | 3.984968 | 3.991288 | 3.997609 |
| Guarded | 9 | 3.832264 | 3.905426 | 3.944188 | 3.944742 | 3.945296 |

Overhead causal del guard: **no identificable** con este diseño secuencial y
censura. Las diferencias guarded−original de los cuantiles observados son
aproximadamente +56.294 ms (min), −0.044 ms (p50), −0.056 ms (p90),
−0.048 ms (p95), −0.041 ms (max). No son una estimación del coste del guard:
los máximos miden mayormente el despertar del timeout. Entre los verdes el
delta de mediana es −25.011 ms y el de máximo −52.312 ms, también con sesgo de
selección. No hay evidencia aquí de que el guard explique el fallo original.

## Initial y recovery, separados

Población diagnóstica oficial: `timeline-official/run-01..18.json`.
Se hicieron 18 iniciales para obtener 10 oportunidades reales de recovery;
se retuvieron los 8 iniciales fallidos. El mecanismo es el oficial
`test/action=recover` bajo el perfil confiado `mixed`, sin reintentar un
reemplazo fallido. Son experimentos independientes, no nuevos retries de L6.

| Etapa | n | startup dentro de 4 s | agotó 4 s | min observado | p50 observado | p90 observado | p95 observado | max observado |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Inicial | 18 | 10 | 8 | 3.872401 | 3.961233 | 4.000221 | 4.000251 | 4.000288 |
| Reemplazo | 10 | 8 | 2 | 3.870218 | 3.950070 | 4.000140 | 4.000172 | 4.000204 |

También se ejecutó la consulta HTTP real de validación y, tras recovery exitoso,
la consulta de restauración oficial. Los dos reemplazos fallidos no se llaman
recuperaciones exitosas ni se reemplazan por corridas verdes.

## Instrumentación y límites de la evidencia

`ft2r_probe.py` es un bootstrap exclusivo de diagnóstico. Importa el módulo L6
instalado y llama a su `main`, en lugar de ejecutarlo como `__main__` mediante
`-m`. No modifica archivos canónicos ni sus presupuestos. No se utiliza para
afirmar equivalencia de performance con los controles puros.

| Marcador | Punto observado |
|---|---|
| S00 | petición host Docker / creación del dueño en harness, dominios separados |
| S01 | inmediatamente antes de Popen del hijo |
| S02 | retorno de Popen con PID, no significa que Python ya ejecute código |
| S03 | primer timestamp tras importar time en el bootstrap; punto más cercano disponible, no inicio nativo de CPython |
| S04 | import del módulo L6 y spans de imports relevantes |
| S05 | entrada/salida de create_app |
| S06 | entrada/salida de PersistentWiring.__enter__ |
| S07 | uvicorn.Server.startup y asyncio.create_server |
| S08 | envío de hello y primer heartbeat por el canal de control existente; no se inventó endpoint health |
| S09 | emisión de READY por supervisor tras sonda y open |
| S10 | retorno de primera consulta MCP HTTP canónica válida; timestamp posterior a recepción/procesamiento, no timestamp de paquete TCP |

La correlación usa PID, PPID, generation y dueño. `startup` se descompone en
A_PROCESS_CREATE, B_PYTHON_BOOT, C_IMPORTS, D_APP_CONSTRUCTION,
E_GATEWAY_WIRING, F_HTTP_BIND, G_READY_HANDSHAKE,
H_SUPERVISOR_OBSERVATION e I_OTHER. La suma de intervalos exclusivos no duplica
imports anidados dentro de wiring. Imports del SDK y dependencias son inclusivos
en el ranking: sumar ese ranking sería incorrecto.

La versión inicial del observador no cerraba spans de imports opcionales que
lanzaban ImportError capturado por sus llamadores. Se conserva esa evidencia;
el analizador corregido NO extiende spans sin cierre hasta el deadline. Los
marca incompletos y deja el tiempo no medido en I_OTHER. La versión focal añade
el cierre en finally con categoría de error, sin mensajes ni argumentos.
Los summaries intermedios no son el resultado final; `analysis-final.json`
es la síntesis corregida. Ningún valor intermedio acredita aceptación.

La primera medición `timeline-first/run-01.json` alcanzó READY inicial, pero el
harness pidió recovery con perfil `none`: el supervisor lo rechazó correctamente
como `harness_command_invalid`. Se corrigió únicamente el harness a `mixed`.
La corrida queda identificada como error del experimento y NO cuenta entre los
10 recoveries oficiales. Tampoco se atribuye como defecto del producto.

## Mini diagnóstico antes de cualquier posible fix

- ROOT_CAUSE: NOT_ISOLATED como defecto mínimo corregible propio de L6.
- SLOW_PHASE: imports/inicialización CPU del servidor; confirmado por spans y
  tiempo de CPU del proceso. No es la demora de salida del lifecycle stdio.
- EVIDENCE: 14/20 abortos originales; 11/20 guarded; 2/10 startups de reemplazo
  abortados. Ver desglose y muestras por generación en los JSON.
- WHY_VARIABLE: el trabajo normal ya roza el presupuesto; variación del tiempo
  de CPU consumido basta para cruzarlo. No está demostrada la causa última de
  esa variación ni corresponde atribuirla a Windows, OneDrive o Docker Desktop.
- IMPACT: UNAVAILABLE fail-closed cuando no se acredita startup; no READY, no
  apertura de una generación incompleta y no fallback para ocultar el fallo.
- MINIMAL_FIX: ninguno demostrado/autorizado. No quitar imports de seguridad,
  mover trabajo a otra fase, cambiar dependencias o ampliar el timeout por intuición.
- CONTRACTS_PRESERVED: startup 4 s; memoria/CPU/pids; Python/SDK; protocolo;
  Policy/Executor/Gateway; cuatro slots; recovery, no retry/fallback nuevos.

No se modificó código del servidor/supervisor ni del Tool Gateway.
No se reabre investigación stdio/GDB. No se inicia IC-2A.

## Desglose observado y recursos

`analysis-final.json` indexa **75 experimentos** con SHA256 de cada JSON bruto.
Contiene 1.084 muestras de recursos; no se borró ni reemplazó ninguna corrida.
Hay 57 entradas de servidor instrumentadas, 56 fases con correlación de control
y 55 intervalos de CPU utilizables. La diferencia procede del subservidor de
C11 en la primera versión del harness: conservaba su journal, pero no todo su
canal de control. Se añadió correlación por dueño y se hizo una tercera
observación explícita de C11, no una sustitución de sus resultados anteriores.

Medianas por componente exclusivo de las 56 fases correlacionadas:

| Componente | Segundos | Porcentaje mediano |
|---|---:|---:|
| A_PROCESS_CREATE | 0.000880 | 0.0222 % |
| B_PYTHON_BOOT | 0.017888 | 0.4521 % |
| C_IMPORTS | 3.828536 | 96.6312 % |
| D_APP_CONSTRUCTION | 0.000387 | 0.0098 % |
| E_GATEWAY_WIRING, excluyendo imports | 0.000570 | 0.0143 % |
| F_HTTP_BIND, excluyendo imports | 0.000910 | 0.0233 % |
| G_READY_HANDSHAKE | 0.000768 | 0.0195 % |
| H_SUPERVISOR_OBSERVATION | 0.001091 | 0.0275 % |
| I_OTHER | 0.108914 | 2.7412 % |

Las medianas independientes no se suman para fabricar un run. El JSON sí
conserva la suma y porcentaje de cada run. Wiring **inclusivo** consume una
mediana de 1.260434 s; su parte dominante ya figura en C_IMPORTS.

Imports principales, medianas inclusivas (se anidan, no sumar):

| Módulo | Segundos |
|---|---:|
| xoc_mcp_lab_l6.server | 1.664752 |
| xoc_mcp_lab_l4.server | 1.448481 |
| mcp | 1.441005 |
| mcp.client.session | 0.844992 |
| xoc_mcp_lab.adapter | 0.792629 |
| src.tool_gateway | 0.690429 |
| sqlalchemy | 0.688385 |
| src.shared.context | 0.676898 |

`src.shared.context` importa modelos/ORM; el wiring instala límites de providers
antes de cargar helpers y el adapter importa el gateway canónico. No se quitó
SQLAlchemy ni se alteró ese gateway para obtener una corrida verde. Los imports
transitivos de módulos cliente del SDK no significan ejecutar QueryWorker o
activar un transporte stdio.

En las 22 fases fallidas con control correlacionado, el marcador de envío de
hello queda después del presupuesto. Veinte también registran heartbeat tardío;
dos no registran primer heartbeat. No hay evidencia aquí de un hello + heartbeat
puntuales que el supervisor haya ignorado durante una espera larga defectuosa.

CPU del proceso / tiempo monotónico observado: mediana **0.999922**, mínimo
0.999470. Es consistente con inicialización CPU-bound, no con una espera larga
de red o locks. No demuestra la causa de la variación de velocidad de ejecución.

- Máximo RSS/HWM del hijo muestreado: **110.824 KiB**.
- Máximo de memoria del contenedor: **206.016.512 bytes**, por debajo de 512 MiB.
- Máximos muestreados del hijo: 6 threads, 12 fds.
- Eventos de pids `max`: 0. Failcnt/OOM: 0.
- En los casos focales con cgroup v1 CPU ampliado: `nr_throttled=0`,
  `throttled_time=0`. Los controles originales no recogían `cpu/cpu.stat`;
  allí no se inventa un cero ni se extrapola este resultado como medición directa.
- No se observó presión de memoria/pids ni throttling en las muestras disponibles.
  No se atribuye el fallo a Windows, OneDrive, Docker Desktop ni carga externa.

Red y filesystem durante startup diagnóstico:

- Cero eventos observados de DNS/connect externos; cero intentos externos
  bloqueados en las instancias de aislamiento del servidor con snapshot disponible.
  El contenedor permaneció sin red externa en todos los casos.
- No se registró ejecución de `getproxies` ni `create_default_context` bajo los
  hooks disponibles. No se alteró `trust_env` ni la configuración de proxy.
- Máximo por snapshot: 207 scans de directorio, 121 aperturas clasificadas como
  metadata de paquetes, 2.106 otras aperturas, 1 apertura temporal propia del
  journal y 6 rutas clasificadas heurísticamente como relacionadas con certificados.
  Esa última categoría también incluye módulos con `/cert` en la ruta: **no**
  demuestra que se haya recorrido un almacén de certificados.
- 3.078 mediciones de lookup `distribution`: mediana 0.192 ms, máximo 11.918 ms
  por llamada. Son llamadas reales, no 3.078 distribuciones diferentes.
- Integridad L6 inclusiva: mediana 85.817 ms, máximo 154.554 ms. No se eliminó
  ninguna verificación por considerarla duplicada sin prueba causal.
- 57 llamadas observadas a urandom: máximo **0.014538 ms**. No indica espera
  relevante de entropía en esas llamadas; no es trazado nativo de toda syscall.
- No se instaló un tracer nativo de locks. No puede acreditarse ausencia absoluta
  de locks de filesystem; no aparece una espera dominante en wall-vs-CPU.

## Los nueve casos, sin agrupar errores diferentes

Primera pasada de los nueve: 3 PASS / 6 FAIL. Se repitieron una vez los seis
fallidos: 0 PASS / 6 FAIL. C11 tuvo una observación adicional para acreditar el
dueño y perfil del subcaso: FAIL. Total focal: **3 PASS / 13 FAIL / 16**.

| Caso | Primera ejecución | Repetición / observación adicional | Evento exacto |
|---|---|---|---|
| C06 | FAIL | FAIL | READY inicial, generación 1, en ambas |
| C08 | FAIL | FAIL | replacement READY, generación 2, en ambas |
| C10 | FAIL | FAIL | READY inicial g1; después replacement READY g2 |
| C11 | FAIL | FAIL; FAIL adicional | inicial g1 del subcaso; luego inicial g1 principal; adicional confirma inicial g1 de `reap_fail` |
| C12 | FAIL | FAIL | replacement READY generación 3, segunda recuperación, en ambas |
| C14 | PASS | No repetir por pasar | No faltó un evento en esta corrida; fallo histórico no resuelto |
| C15 | PASS | No repetir por pasar | No faltó un evento en esta corrida; fallo histórico no resuelto |
| C18 | FAIL | FAIL | replacement READY g2; después READY inicial g1 |
| C19 | PASS | No repetir por pasar | No faltó un evento en esta corrida; fallo histórico no resuelto |

En C11 adicional, supervisor 7/perfil mixed: startup 3.983896 s, READY. Después
supervisor 11/perfil reap_fail: startup 4.000154 s, no READY g1. El caso no alcanzó
la inyección de fallo de reap; no debe informarse como reap roto.

Los fallos actuales conservan `TimeoutError / LAB_L6_EXPECTED_CONTROL_EVENT`
y ubicación canónica `harness.py:182`, ahora acompañados por predicado esperado,
generación y timeline. Para C14/C15/C19 el output histórico no retuvo el predicado
exacto, sólo esa ubicación común: no es posible asignar retroactivamente un evento
con certeza. Una corrida nueva verde no resuelve ni borra esos fallos históricos.

## Regresiones independientes del gate de startup

Ejecutadas después de terminar los contenedores, sin carga concurrente:

- Contabilidad del observador: **6/6 PASS**. Comprueba imports anidados,
  intervalos sin cierre, suma de componentes, separación wiring/imports,
  censura de timeouts, timeline ausente y cuantiles.
- Backend focal: **120/120 PASS**, intentos externos 0, intentos de secretos 0.
- Backend completo, **una sola ejecución**: **253 PASS / 2 FAIL / 255**.
  Son exactamente los dos casos históricos de `test_assess_ticket_automation`:
  - `test_global_url_wins_and_marks_source_global`, assertion de la línea 50.
  - `test_normalize_plans_skips_empty_and_keeps_legacy`, assertion de la línea 125.
- No se corrigió VICTOR. El harness usa las dependencias offline existentes y
  Python host 3.13.1, como las regresiones locales previas. Los procesos L6
  continuaron ejecutando Python 3.11.16 del baseline, sin upgrade.
- La suite completa contabilizó **3 intentos de red bloqueados**, cero accesos
  a archivos secretos. No fueron tráfico externo ejecutado. El contador es
  global y no permite asignarlos con certeza a un test individual; no se repite
  la suite ni se afirma que ese contador sea cero. No modifica el resultado de
  las mediciones de startup aisladas.

La cobertura independiente de backend no autoriza a marcar FT-2 verde.

## Respuesta a los 46 puntos del entregable

1. Unguarded: 20 corridas, 6 READY / 14 abortadas; distribución arriba.
2. Guarded: 20 corridas, 9 READY / 11 abortadas; distribución arriba.
3. Overhead: no estimación causal identificable por censura/orden secuencial;
   deltas de distribución completos documentados, no sólo medianas.
4. Initial/recovery: 18 iniciales y 10 reemplazos oficiales, sin mezclarlos.
5. Fase lenta: imports, ~96.63 % mediano exclusivo.
6. Imports principales: tabla y ranking inclusivo en JSON, sin doble suma.
7. C06: READY inicial g1.
8. C08: replacement READY g2.
9. C10: inicial g1 y replacement g2 en observaciones diferentes.
10. C11: inicial g1, incluido el subcaso reap_fail identificado por dueño.
11. C12: replacement READY g3.
12. C14: no reproducido en la nueva corrida; no cerrado.
13. C15: no reproducido en la nueva corrida; no cerrado.
14. C18: replacement READY g2 e inicial g1 en observaciones diferentes.
15. C19: no reproducido en la nueva corrida; no cerrado.
16. Presión de recursos: no observada en memoria/pids/CPU accounting disponible;
    no conclusión absoluta sobre el host.
17. External lookup: no observado en startup; red externa bloqueada. Los 3
    intentos de la suite backend completa son un experimento distinto.
18. ROOT_CAUSE: NOT_ISOLATED como defecto causal mínimo/variabilidad; fase lenta sí localizada.
19. Fix de producto aplicado: NO.
20. Fix exacto: ninguno; sólo observabilidad y correcciones del harness diagnóstico.
21. 4 s cambiado: NO.
22. Recursos cambiados: NO.
23. SDK/deps/Python del baseline cambiados: NO.
24. 30+30 post-fix cold starts: no aplica, no hubo fix autorizado.
25. 20 post-fix recoveries: no aplica, no hubo fix.
26. L6 142: no nueva aceptación por STOP; sigue preservado el anterior 133/142.
27. C01–C24: no nueva matriz completa; focal actual no equivale a 24/24.
28. Smoke final: bloqueado por gate; no se sustituye por cold starts parciales.
29. Provenance: hashes/integridad instalados verificados; equivalencia funcional
    final no repetida. PASS histórico de provenance se conserva como histórico.
30. IC-1 32 E2E: no nueva ejecución, exige L6 PASS; 32/32 histórico no cierra FT-2.
31. I01–I30: misma condición; no nueva acreditación.
32. No-stdio: **20/20 guards válidos**, 60 instalaciones de guard entre procesos,
    QueryWorker=0, ProcessOwner stdio=0, verified_server launches=0. Sin nuevo 32 E2E.
33. C.3: focal backend PASS; no nuevo E2E tras aceptación L6.
34. Thread/session: focal backend PASS; no nuevo E2E final.
35. Cross-tenant: focal backend PASS; no nuevo E2E final ni cambio de policy.
36. Backend focal: 120/120 PASS nuevo.
37. Backend completo: 253/255; únicamente los 2 fallos históricos indicados.
38. Issue stdio: sigue OPEN, no fixed. FT-1 estructural ya lo separó de HTTP;
    no se usa esa separación para cerrar formalmente IC-1 sin FT-2.
39. IC-1 final: implementación conservada, cierre bloqueado por FT-2.
40. Commits locales: NO, gate final no pasó. Staging no alterado.
41. Push: NO.
42. IC-2A iniciado: NO.
43. READY_FOR_IC2A: NO.
44. Voice: NO cambios, pruebas ni deploy.
45. VICTOR: NO cambios ni conexión on-premise; sólo regresiones backend existentes.
46. Cloud: NO consultas/deploy/cambios AWS o Azure.

## Archivos y reproducibilidad

Cambios de esta tarea, exclusivamente backend/harness/documentación:

- `tests/ic1/ft2r.py`: driver secuencial, poblaciones, control waits, recursos.
- `tests/ic1/ft2r_probe.py`: bootstrap diagnóstico de procesos instalados.
- `tests/ic1/ft2r_analyze.py`: inventario, censura, distribución e intervalos.
- `tests/ic1/test_ft2r_diagnostics.py`: seis tests del análisis.
- `tests/ic1/backend_regression.py`: opción focal/all y ubicación segura de fallos.
- `docs/sophia-mcp-ft2r-20261010.md`: este informe.

No se editaron archivos de XOC-MCP en FT-2R. Sus modificaciones previas permanecen.
Los contextos de Docker conservan exactamente los hashes del harness usado en
cada experimento; las últimas mejoras del analizador no reescriben los JSON brutos.

Evidencia nueva: `tests/ic1/.build/ft2r-20261010/` (ignorada por Git).
Los archivos clave son `preflight.json`, `analysis-final.json`,
`backend-focal.json`, `backend-full.json` y `close-snapshot.json`.
El cierre preserva también hashes de evidencias históricas y docs ajenos.

Comandos, desde XOC-API-AWS (usar directorios NUEVOS para no sobrescribir):

```powershell
python -B tests/ic1/ft_snapshot.py --mcp ../XOC-MCP --output tests/ic1/.build/<run>/preflight.json
python -B tests/ic1/prepare.py --mcp ../XOC-MCP --output tests/ic1/.build/<run>/context
docker build --network none -t xoc-chat-mcp-ft2r:diagnostic tests/ic1/.build/<run>/context
python -B tests/ic1/ft2r.py --population original --output tests/ic1/.build/<run>/original-20
python -B tests/ic1/ft2r.py --population guarded --output tests/ic1/.build/<run>/guarded-20
python -B tests/ic1/ft2r.py --population timeline --output tests/ic1/.build/<run>/timeline
python -B tests/ic1/ft2r.py --population focal --focal-repeats 1 --output tests/ic1/.build/<run>/focal
python -B -m unittest discover -s tests/ic1 -p test_ft2r_diagnostics.py
python -B tests/ic1/backend_regression.py --mcp ../XOC-MCP --dependencies ../.tmp-ticket-sync-tests --suite focal --output tests/ic1/.build/<run>/backend-focal.json
python -B tests/ic1/backend_regression.py --mcp ../XOC-MCP --dependencies ../.tmp-ticket-sync-tests --suite all --output tests/ic1/.build/<run>/backend-full.json
python -B tests/ic1/ft2r_analyze.py tests/ic1/.build/<run> --output tests/ic1/.build/<run>/analysis.json
```

## Decisión humana siguiente

No autorizar deploy/IC-2A con estos datos. Elegir un nuevo alcance explícito:

1. Auditar y reducir el grafo de imports/inicialización manteniendo todas las
   verificaciones y contratos, con autorización específica si alcanza al gateway
   o al empaquetado/dependencias que FT-2R prohibía cambiar; o
2. Revisar el contrato temporal con nuevas mediciones autorizadas. No se propone
   un nuevo timeout numérico basándose en una distribución censurada a 4 s.

No convertir esas opciones en un cambio automático: este bloque exige STOP.
Para cerrar después: corrección causal demostrada, 30+30 cold starts y 20
recoveries si hubo fix, aceptación 142/142, matriz 24/24, smoke/provenance,
32/32 IC-1 con guard y regresiones. Sólo entonces commits locales y evaluación
READY_FOR_IC2A. No push ni cloud implícitos.

## Verificación de cierre

- HEAD de ambos repos sin cambio; staging vacío.
- Status y diff de XOC-MCP idénticos al preflight.
- Diff tracked de backend idéntico al preflight; los nuevos archivos del harness
  y reporte siguen locales, sin stage.
- Hashes históricos y de docs ajenos incluidos en el inventario: sin cambio.
- Handoff: 159/159; checksums: 160/160. `git diff --check` limpio en ambos repos.
- Patrones de access keys, claves privadas y JWT completos en los seis archivos
  de esta tarea: cero coincidencias. Es un escaneo acotado, no una certificación
  de ausencia de secretos en todo el workspace.
- Cero contenedores FT-2R residuales. Imagen baseline con el mismo ID.
- `close-snapshot.json` SHA256:
  `d192f4bfd8ee190245de0a11741a334659b9bbc370aa820da4c5f3e8c359f304`.
