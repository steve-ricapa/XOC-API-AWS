# L3 LOCAL — método y límites de interpretación

Baseline original: imagen inmutable L2 be002dede580..., 191 tests y benchmark
repetidos en l3-original-20260915. Ninguna evidencia previa sobrescrita.

Para comparación sin profiler se construyó l3-baseline-probe: sus grupos core y
adapter son BYTE-IDÉNTICOS a L2; sólo cambió el arnés. Cambia el hash del wheel
adapter porque el manifest de versión también liga el arnés. No se presenta como
un cambio de lógica. Se comprobó la igualdad de ambos grupos por archivo.

Separación:

- A: cinco padres nuevos, tiempo externo del proceso y tiempo QueryWorker separado.
- B: 80 consultas, padre reutilizado, nuevo QueryWorker e hijo por cada una.
- C: cuatro workers en un contenedor de 512 MiB/2 CPU, quinto rechazado sin hijo.
  No son cuatro invocaciones Lambda independientes ni un máximo de cuatro usuarios.

Los benchmarks de aceptación conservan ProcessObserver y RSS/cgroup a 20 ms.
Normal no ejecuta measured_child: usa el verified_server original, mismos fixtures.
Large/blocked/late usan el arnés anterior sólo para modificar datos ficticios o
inyectar el bloqueo, con el mismo gateway. Spans inclusivos no se suman.

Diagnóstico separado: tres pares minimal/cProfile+importtime. cProfile registra
código/nombres/tiempos, nunca variables, JWS o resultados. Coroutines pueden tener
varias reanudaciones en cProfile: su callcount NO es un número de tools.
La comparación del coste del profiler es orientativa, no una resta correctora:
hay variación del host y el diagnóstico no usa el sampler de memoria del mínimo.

Reloj correlacionado sólo en Linux: ambos perf_counter usan CLOCK_MONOTONIC en el
mismo contenedor/boot. Del spawn antes del await al primer marcador Python se
observa el arranque programado del intérprete (incluye scheduling), no CPU pura.
No se restan relojes de Windows ni de máquinas diferentes.

Host no exclusivo: Docker tiene otros ocho contenedores ajenos que no se tocaron.
No builds/descargas propios mientras corrían benchmarks de aceptación. No se
alteraron imagen base, locks, SDK, arquitectura, memoria, CPU o procesos.

Único candidato C1: deadline local TOTAL desde entrada, acotado después de verificar
por deadline firmado (el verifier prueba deadline <= exp). Reserva min(0.65 s,
mitad del presupuesto), sin extender vigencia. Margen de spawn 0.05 s y de fase
0.02 s antes de initialize/tools/call. Sin margen: cero trabajo nuevo.

El supervisor conserva el SDK stdio original. Captura su handle mediante un
dispatcher ContextVar sobre la factoría privada fijada; delega la creación real,
sin copiar código/protocolo ni editar site-packages. Contextos ajenos delegan sin
supervisión. Es acoplamiento a una API privada y requiere revisión antes de otro
SDK; NO es una API productiva aprobada.

Al terminar/fallar/cancelar: cierre de stdin, gracia <=0.45 s y fracción del tiempo
restante, terminate y espera <=0.10 s dentro del resto, kill si es necesario,
wait/recolección. No se mata por nombre. Exchange y supervisor son tareas por
request, no pool. Cancelación del caller señala el scope de protocolo o cancela
el spawn; la espera de recolección usa shield acotado.

No se garantiza tiempo real de kernel. Si se alcanza el total aún sin cierre,
se fuerza el handle propio y se observa hasta 0.25 s de rescate: ese intervalo es
SOBREPASO, jamás presupuesto extra de negocio ni éxito. Si sigue sin finalizar,
se reporta LAB_CLEANUP_INCOMPLETE y no se libera el slot como si estuviera sano.
Las limitaciones internas de wait/close del SDK/OS no quedan mágicamente resueltas.

Regresiones durante implementación: primera prueba de error reemplazaba aclose
por una función que fallaba para siempre, impidiendo también al SDK cerrar sus
handles de Windows; produjo dos avisos de transportes. Se acotó la inyección a
un error transitorio y se añadió error de espera del supervisor. El SDK puede
recuperar un cierre transitorio y conservar un resultado válido, únicamente con
hijo recolectado. No se afirma probar un fallo permanente real del kernel.
Un assert inicial exigía error incluso tras esa recuperación; se corrigió esa
expectativa, no autorización/aislamiento ni tests L1/L2. Los 191 anteriores siguen.

No se aplicó segundo candidato/caché ni extracción canónica: las verificaciones
duplicadas cuestan alrededor de 0.10 s BAJO profiler, mientras el import de SDK y
las dependencias canónicas cuesta mucho más. No justifica aquí otra caché/frontera
de invalidación. Todos los hashes/firma/Policy/Audit/proyección siguen ejecutándose.

No hay cloud, Chat, Voice, infraestructura nueva o cambios de autenticación.
VICTOR no se involucró ni se inspeccionó su código/repositorio/runtime/tests.
