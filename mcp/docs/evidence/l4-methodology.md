# L4 LOCAL: método de aceptación

No es una prueba AWS/Azure, un SLA ni una inferencia sobre cuatro Lambdas.
Es un cliente y un servidor locales, juntos bajo 512 MiB, dos CPU y 128 pids,
en Amazon Linux 2, Python 3.11.16 x86_64, MCP 1.30.0/protocolo 2025-11-25.
No se actualizó el SO fijado ni ninguna dependencia. El host Docker tiene ocho
contenedores ajenos; no se detuvieron ni configuraron. La carga ajena no está
controlada. No se solapan builds/descargas propias con benchmarks de aceptación.

## Referencias y comparación

C1 conserva su imagen, sus tres wheels y sus 212 tests. El nuevo wheel L4 es
aditivo: no sustituye archivos de C1. Se repiten C1Tests, C1Smoke, C1Provenance
y la receta histórica C1Benchmark sobre la imagen C1 exacta.

Además, el benchmark nuevo ejecuta primero 80 consultas reales mediante el
QueryWorker C1 original y después el candidato HTTP. Son 20 por herramienta,
alternando A1/B1/A2/A1, mismos argumentos, fixtures, proyección y presupuesto
completo de 3,5 segundos. El ticket de get se elige según tenant. C1 crea un
proceso MCP stdio por consulta; L4 conserva un servidor HTTP. Ambos utilizan
ClientSession real. No se compara una invocación directa Python con un roundtrip.
La nueva rueda instalada no cambia los bytes usados por QueryWorker C1.

ClientSession puede emitir tools/list al validar el esquema de un resultado de
tools/call. Se cuenta ese HTTP adicional; no es una segunda ejecución de negocio.
Los contadores de workers incluyen list, los de handlers sólo negocio. El estado
canónico `denied` con código `execution_failed` se conserva en `tool_status`, pero
la estadística cliente lo clasifica `error`, no una denegación de autorización.

La receta histórica L3 tenía otras particularidades: secuencial ADMIN A y techo
de 5 s. Se conserva separada, no se mezclan sus poblaciones con las nuevas de
3,5 s. Los resultados erróneos/timeouts se conservan y se clasifican mediante
la evidencia de lifecycle C1; no se borran para mejorar percentiles.
La corrida final también repite cuatro QueryWorkers C1 concurrentes a 3,5 s,
con exactamente los mismos perfiles/tool unread del grupo HTTP concurrente.

## Reloj y fases

Relojes monotónicos del mismo contenedor/boot. No hay cProfile ni importtime en
aceptación. Cada QueryClient nuevo mide entrada -> verificación/preparación de
headers -> cliente HTTP -> initialize/initialized -> una tools/call -> validación
texto/structuredContent -> cierre de recursos del cliente. No hay retry oculto.
La firma de fixture se emite antes de ambos clientes: simula al emisor upstream,
no se cuenta como tiempo interno del worker. Preparar y verificar el transporte
sí se cuenta. Cada POST vuelve a verificar L1 y la llamada real queda ligada.

Cinco muestras parten del servidor apagado: creación del proceso, verificación
de artefactos y dependencias, imports/core/fixtures, readiness y primera consulta.
El cliente padre ya existe; esto NO es un arranque frío de Lambda ni del host.
Se conservan startup, primera consulta, startup-hasta-respuesta y ciclo completo
incluido shutdown. No se suma el shutdown global a cada consulta caliente.

Se registran preparación/initialize/call/cleanup cliente y los spans de servidor
verify_bind, body_receive, dispatch, reset de ContextVar, response_guard y worker.
Dispatch contiene serialización SDK; response_guard mide validación/contención
posterior. NO se pretende aislar todo el tiempo de serialización del SDK.
Los spans anidados se solapan: no sumar sus medianas.

## Escenarios

- A: cinco arranques completos y primera consulta.
- B: 80 secuenciales, veinte por cada tool, identidades alternadas.
- C: cuatro consultas MCP concurrentes.
- D: A llega al proveedor que no coopera; B consulta otra identidad mientras A
  sigue activa. Después se ocupan cuatro slots reales y se observa el rechazo
  del quinto. El servidor de ese escenario es propio y separado; no se reinicia
  para repetir consultas ni convertir errores en éxitos.
- E: denegaciones por rol/delegación/tool y disponibilidad posterior, cero reads
  y handlers de negocio para esas denegaciones.
- F: tres respuestas legítimas grandes, mismas 35 filas/longitudes que C1.
- G: cinco rondas de cuarenta consultas en el MISMO proceso, con identidades y
  errores recuperables. No reiniciar entre rondas.
- Complemento finito: proveedor tarda dos segundos, cliente deja de esperar
  a los 0,3 s, B funciona, se observa el fin real del worker y la recuperación.
  No se informa ese timeout como cleanup del hilo ni como consulta completada.

Los faults se seleccionan sólo con el perfil de arranque de harness `mixed` y
request_id firmado ficticio. No son herramientas públicas, argumentos ni `_meta`.
`handler_entered` se observa dentro de la primera lectura del provider con
handler_calls=1; no es el instante exacto de entrada a la función. Un marcador
`provider_entered` precede inmediatamente al bloqueo.

## Estadística, recursos y límites

Mínimo, mediana, p95 nearest-rank y máximo; número de muestras y conteos >3,5 s
y >5 s. Éxitos, denegaciones, errores y timeouts separados. No p99 ni garantías
de producción. La elección final también considera disponibilidad y cleanup.

Antes/después de rondas se conservan RSS/HWM, CPU ticks, fds, tasks, threads y
cgroup. HWM aporta máximo de RSS del proceso; no es un perfil exacto de todas
las asignaciones simultáneas. fds no equivale exclusivamente a conexiones TCP.
Los eventos de servidor conservan slots, peticiones pendientes y scopes vivos
por weakrefs. El snapshot de request_finished aún puede incluir su propio scope
hasta retornar al SDK; el test de weakref comprueba liberación después del fin
real. Se evalúa tendencia acotada, no retorno byte-exacto del RSS a su inicio.

Un hilo bloqueado conserva slot y contexto. Ni timeout ni desconexión TCP ni
notifications/cancelled entre POST stateless demuestran haberlo detenido.
La recuperación no cooperativa es global (terminar el proceso propio); debe
registrarse ese impacto, no llamarlo aislamiento por petición.

Los límites de body HTTP excluyen headers/framing; stdio C1 incluye newline.
L4 mide 65536/262144 bytes completos. El guard de salida actúa después de la
serialización SDK. El cliente limita bytes del stream, pero el parser SDK y
los chunks del transporte ya tienen asignaciones: no es defensa universal de
memoria contra servidores hostiles. Assertions/headers/datos de negocio no
se vuelcan en logs; sólo resultados ficticios sanitizados necesarios en tests.
