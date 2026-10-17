# MCP-2 L4 LOCAL — resultado y decisión

Fecha: 16-sep-2026. Experimento terminado. Sólo XOC-MCP-LAB.
Fixtures ficticios, sin cloud, sin Git writes, sin Voice y sin VICTOR.

## Resumen ejecutivo

Se implementó y probó UN servidor MCP persistente real, con el SDK oficial
1.30.0, Streamable HTTP stateless/JSON y contexto ES256 verificado por solicitud.
Se conservaron C1 y el gateway canónico sin cambios.

La mejora de latencia es real en este laboratorio: 80/80 consultas calientes
correctas, mediana **26,339 ms**, p95 **30,771 ms**, máximo **92,877 ms**.
El C1 comparable a 3,5 s tuvo 80/80 éxitos: mediana
**2,528 s**, p95 **2,787 s**, máximo **3,036 s**.
Con cuatro clientes simultáneos, HTTP obtuvo cuatro respuestas correctas en
71,655–72,417 ms; C1 obtuvo cuatro timeouts con cierre/recolección comprobados.
No se descartan las muestras fallidas de la comparación.

El aislamiento de identidad/datos pasó las pruebas de intercalado, roles,
binding y IDs repetidos. La disponibilidad tiene una limitación distinta:
un proveedor A bloqueado no impidió atender B, pero cuatro proveedores no
cooperativos ocuparon todos los slots. Liberarlos exigió terminar el servidor
completo. El timeout del cliente NO interrumpe esos hilos.

**Decisión B: mejora de latencia, pero recuperación/contención de fallos
insuficiente para recomendar integración todavía.** No se descarta el candidato:
se conserva toda su implementación y evidencia. El gate concreto pendiente es
una estrategia de recuperación acotada que explicite y controle el impacto de
cuatro trabajos no cooperativos sobre solicitudes saludables.

Resultados funcionales: **212/212 tests originales Linux en C1**, **304/304
Linux en el candidato (212 originales + 92 nuevos)**, y **289 passed / 15 skipped en Windows** para la suite
completa del laboratorio. Los 15 skips son pruebas que requieren el artefacto
Linux instalado; no se presentan como HTTP validado en Windows.
Smokes y procedencia aprobados; siete archivos finales idénticos entre dos builds.

GO del experimento y regresión local. **NO-GO de integración real/producción.**
No se eligió alojamiento definitivo ni se inició otro bloque.

## Evidencia principal y cómo leerla

- [Método, relojes y comparación](l4-methodology.md).
- [Resumen numérico final](l4-benchmark-summary-final04.json).
- [Validación, etapas cliente y hashes](l4-validation-final04.json).
- [Seguridad, contexto, límites y cancelación](l4-security-and-limits.md).
- [Empaquetado y procedencia](l4-packaging.md).
- [Comandos PowerShell reproducibles y cierre](l4-commands.md).
- [Archivos nuevos y SHA256](l4-source-changes-final04.json).
- [Preservación final](l4-preservation-final04.json).
- [Regresión Windows capturada](l4-windows-final04.json).

Evidencia primaria del candidato: `../l4-runs/l4-final04-20260916/`.
Evidencia fresca del baseline C1: `../l4-runs/l4-final02-20260916/results/c1*/`.
Las iteraciones `dev01`, `final01`, `final02` y `final03` se conservan, pero sus
cifras L4 no sustituyen las del paquete final04. Los informes L1/L2/L3 no se sobrescribieron.

## 1. ¿Existe un servidor persistente MCP real por HTTP local?

Sí. `xoc_mcp_lab_l4/server.py` usa las APIs públicas `Server` y
`StreamableHTTPSessionManager(stateless=True, json_response=True)` del SDK
oficial. Cliente `ClientSession` real, protocolo **2025-11-25**, negociación
initialize/initialized, catálogo y tools/call. No son cuatro endpoints REST.
No se copió al servidor HTTP el parche de factoría privada de stdio C1.

Cliente y servidor viven en el mismo contenedor AL2/Python 3.11.16/x86_64,
con red `none`, sólo `127.0.0.1`, sin puertos publicados, sin bind mounts,
usuario 10001:10001, filesystem read-only salvo tmpfs, cap-drop ALL y
no-new-privileges. Presupuesto conjunto: **512 MiB, 2 CPU, 128 pids**.
No se montaron HOME, credenciales, checkout backend ni docker.sock.

El proceso no anuncia readiness hasta verificar paquetes, cargar core e
instalar la frontera ficticia. No incorpora modelo, sampling, chaining,
servidor SSE permanente, resumibilidad o sesiones distribuidas.
GET/DELETE no ofrecidos devuelven 405; Host/Origin se validan antes.
Cliente fijado al endpoint numérico aprobado, sin redirects, proxy de entorno
ni URLs elegidas por el modelo.

## 2. ¿Cuántas solicitudes procesa sin reiniciarse?

El proceso principal final, PID 430 dentro de su contenedor, atendió
**1.152 POST MCP**, con **294 intentos tools/call**, sin reinicios entre grupos
ni entre las cinco rondas de estabilidad. Hubo **291 entradas a negocio** y
**361 lecturas ficticias**; tres llamadas fueron denegadas antes de negocio.
Una operación de negocio puede tener más de una lectura; no implica chaining.
Hubo cero escrituras y cero intentos de proveedores externos en esos contadores.

Desglose de los 294 intentos: 80 secuenciales + 4 concurrentes + 3 DENY +
1 consulta posterior a DENY + 3 del caso lento finito + 3 grandes + 200 de
estabilidad. El caso no cooperativo usa otro servidor propio, para observar
su saturación y cierre global sin esconderlo con reinicios entre consultas.

El SDK puede solicitar tools/list automáticamente para validar el esquema
de una respuesta tools/call. Por eso hay más POST y trabajos técnicos que
llamadas de negocio: no es una segunda ejecución de la herramienta.
Cada wrapper cliente admite sólo un intento; otra instancia es otra solicitud.
No se implementó ledger ni se promete exactly-once.

## 3. ¿Cómo se verifica y elimina el contexto de cada solicitud?

La decisión previa está en [l4-context-boundary.md](l4-context-boundary.md).
Cada POST lleva el sobre L1 completo en la cabecera PRIVADA
`X-XOC-Lab-Envelope`, base64url canónica sin padding. Se reutiliza
`verify_envelope` L1, sin otra criptografía ni cambios en su contrato.
Se verifica ES256, clave pública fixture fijada, issuer/audience/purpose/source,
actor, tenant, rol, delegación, request_id, iat/exp/deadline y hash de llamada.
No se cachea que una conexión ya se autenticó.

Cada POST autenticado obtiene un `RequestScope` nuevo. Su autoridad verificada
y el ToolContext canónico son inmutables. Los contadores/fallos/filas fake/audit
pertenecen a ese scope. `ContextVar` se establece/restaura en `finally` tanto
en ASGI como en el worker; el hilo recibe el contexto explícitamente copiado.
La fachada ficticia se instala una sola vez por proceso, no por usuario.
Sin scope, un proveedor falla cerrado.

No hay current_user/current_tenant global ni lookup de autoridad por IDs.
El registro débil de scopes sirve exclusivamente para observar GC, no autoriza.
Si un hilo sigue ejecutándose, conserva SU scope hasta el fin real: no se
declara liberado porque el cliente haya abandonado la espera.

## 4. ¿Se comprobó aislamiento con intercalado/concurrencia real?

Sí: A1 → B1 → A2 → A1 con un servidor, barreras que fuerzan A/B concurrentes,
dos usuarios del mismo tenant, dos tenants, roles y delegación cambiados,
JSON-RPC IDs iguales, request_id reutilizado en contextos distintos,
cancelación ajena y consulta B después de error/timeout de A.
Una petición sin assertion después de otra válida no hereda identidad.

Se prueban tablas fake detrás de los handlers/helpers reales, no Policy
permisiva ni Executor sustituto. Un ticket ajeno/inexistente no expone datos.
Tickets/documentos conservan alcance por tenant y unread por tenant+usuario:
no se inventó filtrado por creador ni se ampliaron permisos.
Las cuatro herramientas siguen siendo READ_ONLY:

```text
xoc.tickets.list
xoc.tickets.get
xoc.documents.list
xoc.notifications.unread_count
```

USER/ADMIN siguen la Policy existente; ADMIN_XOC requiere delegación y
SUPERADMIN se deniega. Dashboard/integrations disabled no se habilitaron.

## 5. ¿La llamada efectiva sigue ligada a la assertion?

Sí. Ingress y el handler comprueban el nombre y argumentos MCP efectivos
contra `VerifiedContext.matches`. No basta una firma válida para cualquier tool.
Initialize/list verifican identidad y vigencia sin acceder a datos de negocio.
Ni `_meta`, clientInfo, headers decorativos, tenant/rol en argumentos o session ID
otorgan autoridad. Cambiar tool/argumentos/contexto/request_id falla cerrado.

Una nueva solicitud independiente puede reutilizar el mismo sobre vigente;
no se presenta esto como protección anti-replay productiva.

## 6. ¿Policy, Executor y helpers son los originales?

Sí. Core del commit `ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9`.
Wheel core SHA256:

```text
df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41
```

Los 20 orígenes canónicos se verificaron bajo site-packages, sin checkout
backend montado ni fallback a fuentes locales en Linux. Los tres wheels C1
se copiaron intactos; sólo se agregó la distribución `xoc-mcp-lab-l4`.
Los locks/dependencias permanecieron iguales.

Equivalencia: **41 casos canónicos** contra el reference fijado; **37 casos HTTP
idénticos** normalizando sólo request_id/audit_id. Los otros cuatro contienen
identidad nula inválida para L1 y se rechazan antes de negocio. No se afirma
falsamente que esos cuatro alcanzaron el mismo resultado HTTP canónico.

Dos builds independientes finales, siete archivos idénticos byte a byte:
cuatro wheels y tres manifests. Wheel L4:
`xoc_mcp_lab_l4-0.4.0+lab.27a62d14058a-py3-none-any.whl`, 150.887 bytes,
SHA256 `764352dc77a7c467f6c8c3162a4f93e4fcca0e21b7933db0e2a4557669dcfb5d`.
Imagen final: `sha256:129efefeecc7549df84604ed2e532fa524aaab716f6cf145ca9e5522e6ab72b9`.

## 7. ¿Qué tiempo se paga al arrancar y por consulta?

Segundos; p95 nearest-rank. Cinco arranques desde proceso servidor apagado,
no desde un host/Lambda apagado. El cliente padre ya existe.

| Población | n | mínimo | mediana | p95 | máximo | >3,5 / >5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Arranque → ready | 5 | 2,143003 | 2,193653 | 2,258778 | 2,258778 | 0 / 0 |
| Primera consulta después de ready | 5 | 0,032834 | 0,035184 | 0,088728 | 0,088728 | 0 / 0 |
| Arranque → primera respuesta | 5 | 2,178589 | 2,228166 | 2,349829 | 2,349829 | 0 / 0 |
| Ciclo frío completo, incluido cierre global | 5 | 2,393575 | 2,443124 | 2,464509 | 2,464509 | 0 / 0 |
| Cierre global de los cinco procesos | 5 | 0,114589 | 0,214885 | 0,215057 | 0,215057 | 0 / 0 |
| HTTP caliente correcto | 80 | 0,022775 | 0,026339 | 0,030771 | 0,092877 | 0 / 0 |
| HTTP concurrente correcto | 4 | 0,071655 | 0,072143 | 0,072417 | 0,072417 | 0 / 0 |
| Resultado grande correcto | 3 | 0,029336 | 0,029897 | 0,030044 | 0,030044 | 0 / 0 |
| DENY esperado | 3 | 0,020278 | 0,020798 | 0,025591 | 0,025591 | 0 / 0 |

Cliente caliente, 80 muestras, milisegundos; etapas inclusivas, no sumar medianas:

| Etapa | mínimo | mediana | p95 | máximo |
| --- | ---: | ---: | ---: | ---: |
| Preparación/verificación cliente | 0,246 | 0,263 | 0,341 | 0,392 |
| initialize | 4,787 | 5,360 | 6,509 | 12,820 |
| tools/call y validación SDK | 13,153 | 15,969 | 19,951 | 83,349 |
| Cierre recursos del cliente después del resultado | 0,386 | 0,452 | 0,585 | 0,601 |

Servidor, 1.152 POST de todas las poblaciones: verificación/binding mediana
0,757 ms/p95 0,999 ms; recepción body 0,136/0,228 ms; dispatch 1,620/2,967 ms;
reset 0,002405/0,003918 ms; guard de salida 0,024958/0,056357 ms.
Trabajo real de los 564 trabajos del pool, incluidos list: mediana 0,521 ms/p95 0,993 ms.
El máximo dispatch (~2,003 s) incluye deliberadamente el proveedor lento.

Serialización original del SDK está dentro de dispatch; el guard de salida no
la mide por separado. No se afirma un desglose exacto exclusivo de Policy vs
Executor vs serialización. Hay marcadores de primera/última lectura de proveedor.
No se añadió profiling invasivo a aceptación.

## 8. ¿Cuál es la comparación justa con C1?

Mismos fixtures, argumentos/proyección, perfiles A1/B1/A2/A1, 20 lecturas por
tool, imagen base/Python/SDK/core y recursos conjuntos. MCP real en ambos casos.
C1 crea un hijo stdio por consulta; L4 conserva el servidor HTTP y crea un
cliente por consulta. Ambos presupuestos completos son 3,5 s.

| C1 comparable, segundos | resultado | n | mínimo | mediana | p95 | máximo |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Secuencial, todas correctas | allowed | 80 | 2,434784 | 2,527577 | 2,786870 | 3,036410 |
| Cuatro simultáneas | 4 timeouts | 4 | 3,309072 | 3,323791 | 3,372470 | 3,372470 |

Todas las muestras anteriores acabaron antes de 3,5/5 s. Eso NO convierte
un timeout en éxito: C1 reserva 0,65 s para cerrar el hijo y puede agotar su
presupuesto de trabajo antes de 3,5 s. En final04 no hubo timeouts secuenciales;
los cuatro concurrentes terminaron con sus hijos recolectados.

Como antecedente, [final03](l4-benchmark-summary.json) tuvo 78 éxitos y dos
timeouts secuenciales seguros antes de negocio (0 handlers/reads),
`lifecycle.timeout=true`, con hijos recolectados. Su [validación](l4-validation.json) conserva
`worker_status=error`/`lab_worker_unavailable` junto a la clasificación temporal.
Esa evidencia no se elimina ni se mezcla con las 80 muestras finales; C1 no
se alteró para cambiar la cifra.

Separadamente se repitió la receta histórica instalada C1, techo 5 s, ADMIN A:
80/80 correctas, mediana 2,549074 s, p95 2,776961 s, máximo 2,902659 s;
11 escenarios de fallo y 3 resultados grandes. Su initialize colgado terminó
en 4,807395 s incluido cierre. Cuatro workers simultáneos acabaron en timeout
y el quinto fue busy. Esa población NO se mezcla con la comparación a 3,5 s.

L3 histórico reportó mediana 2,873 s; queda citado como histórico, no como
ejecución nueva. El host es compartido y conservó ocho contenedores ajenos;
no hubo builds/descargas propias simultáneas a aceptación. Loopback/fixtures
no permiten extrapolar estas cifras a AWS, Azure, DynamoDB real o un SLA.

## 9. ¿Qué pasa con B cuando A se bloquea?

Hay evidencia de `handler_entered` y `provider_entered` ANTES del bloqueo:
el marcador de handler se observa en la primera lectura, no se inventa por
haber empezado una coroutine. A alcanzó realmente el proveedor ficticio.

En el caso no cooperativo, A dejó de esperar a los **1,003859 s**. Mientras
A seguía ocupando su slot, B terminó correctamente en **24,481 ms**.
Después se ocuparon cuatro slots reales; el siguiente tools/call devolvió
`lab_capacity_busy` en **18,629 ms**, sin iniciar negocio adicional.
No hay cola ilimitada ni liberación ficticia de capacidad.

En el caso lento finito, A dejó de esperar en **0,302425 s**, B funcionó en
**24,991 ms** y el worker A acabó de verdad a los **2,001302 s**.
Sólo entonces liberó el slot, con contexto restaurado y pool active=0.
La lectura de recuperación posterior fue correcta en **24,277 ms**.

## 10. ¿El timeout detuvo el trabajo o sólo dejó de esperar el cliente?

Sólo dejó de esperar el cliente. `asyncio.wait_for` no mata un handler
síncrono que sigue en un thread; `shield` conserva el future y el slot hasta
el `finally` del trabajo real. La prueba de desconexión TCP muestra la misma
distinción. No se promete cancelación preventiva de lecturas ya comenzadas.

En el SDK stateless fijado, cada POST tiene su ServerSession. Una notificación
cancelled en otro POST no encuentra el trabajo anterior, aunque coincida el
JSON-RPC ID. No se creó un mapa global por ID para simular esa capacidad ni
permitir que A cancele B.

Presupuestos nominales: cliente objetivo 3,5 s/techo 5 s; recepción hasta 1 s;
espera worker hasta 3 s; ASGI 5 s limitado además por vigencia. Los 50 ms de
respuesta de error se reservan DENTRO de los 5 s, no después. Son temporizadores
cooperativos, no una garantía hard real-time ante bloqueo del event loop.

## 11. ¿Quedan slots, tareas, threads o conexiones retenidas?

Caso normal/errores recuperables: en el último snapshot HTTP, antes del cierre,
había
**pending_requests=0**, **pool active=0**, **564 submitted = 564 completed**.
Se restauraron todos los contextos ASGI y todos los workers observados.
Quedaban cuatro threads de infraestructura/pool ociosos, no cuatro trabajos
abandonados. El snapshot `/proc` tenía cuatro tareas de SO y ocho fds.
Las seis tareas asyncio del evento final se observan aún dentro del request;
no equivalen a seis llamadas de negocio pendientes.
El registro débil contaba 15 scopes vivos; no equivale a 15 trabajos activos.

Caso no cooperativo: persistieron cuatro workers activos y cinco threads de
SO con el principal. Tras 0,65 s de gracia se terminó el proceso propio con
SIGKILL. Cierre/recolección global medidos: **0,666309 s**, returncode -9,
process_absent=true. Se pierde TODO trabajo del proceso, no sólo A.
El proceso principal sano cerró por SIGTERM en 0,214799 s, sin kill forzado.

Al terminar tests, smokes, procedencia y benchmark: `final_children` vacío;
los contenedores de cada fase se retiraron por ID exacto después de exportar
evidencia. El baseline manual detenido `55e125237974...` se verificó con exit 0,
OOM false y se retiró también. Comprobación final `docker ps -a --filter
name=xoc-l4` sin resultados. Los ocho contenedores ajenos permanecieron activos.
Imágenes, wheels y evidencias locales se conservan para reproducir.

Limitación de observabilidad: se contaron fds, NO estados/conexiones TCP
individuales; no se deduce de ocho fds una garantía de ausencia de socket leaks.

## 12. ¿La memoria se estabiliza durante las rondas?

En cinco rondas de cuarenta lecturas en el MISMO proceso: 36 correctas y
4 errores de proveedor controlados por ronda. Total 180 correctas/20 errores,
cero timeouts y cero muestras >3,5 o >5 s. Los errores `execution_failed`
siguen teniendo el contrato canónico `tool_status=denied`, pero se clasifican
como error técnico, no como denegación de autorización, en las estadísticas.

| Ronda | RSS final bytes | cgroup bytes | CPU proceso ticks | fds / threads | scopes débiles | active / pending |
| --- | ---: | ---: | ---: | --- | ---: | --- |
| 1 | 109.576.192 | 151.130.112 | 411 | 8 / 4 | 12 | 0 / 0 |
| 2 | 110.387.200 | 151.998.464 | 464 | 8 / 4 | 44 | 0 / 0 |
| 3 | 110.927.872 | 153.075.712 | 517 | 8 / 4 | 13 | 0 / 0 |
| 4 | 111.194.112 | 153.493.504 | 576 | 8 / 4 | 10 | 0 / 0 |
| 5 | 111.194.112 | 153.309.184 | 628 | 8 / 4 | 15 | 0 / 0 |

RSS aumentó ~1,54 MiB entre finales de rondas 1 y 5 y tuvo meseta en 4–5.
Cgroup incluye cliente, tmpfs de eventos y cachés, no sólo objetos del servidor.
No hubo OOM ni memory_failcnt. No se exige retorno byte exacto al RSS inicial.
Esto acredita una ventana acotada, NO memoria constante indefinida ni ausencia
de fugas. Hay objetos aún vivos, compatibles con retención transitoria y GC;
su causa no se perfiló. El conteo weakref no es cero ni monotónico (pico 55),
y no se usa como autoridad.
El test específico comprueba que el scope del worker se libera después de su
fin real y GC, no inmediatamente al expirar la espera.

Mínimo/mediana/p95/máximo por ronda y por estado, antes/después de recursos,
CPU cgroup y muestras sin filtrar están en `l4-benchmark-summary-final04.json` y raw.

## 13. ¿Qué autorización HTTP sigue siendo fixture privada?

TODA la emisión/confianza de assertions es del arnés privado L1. La clave
fixture es conocida para pruebas; no demuestra identidad productiva.
El servidor contiene la clave pública fixture fijada y recibe assertions
firmadas, no claves de confianza elegidas por el cliente ni un secreto de firma
como credencial de tool.
No se emitieron JWT reales ni se consultó un tenant/productor real.

No hay OAuth MCP productivo, Entra, IAM, JWKS remoto, KMS, TLS público,
revocación distribuida ni autenticación de SOPHIA real. El header no se llama
access token OAuth ni se envía a frontend. L4 no sustituye esos gates.

Logs/eventos conservan IDs ficticios, códigos, tiempos y auditoría whitelist;
no assertions/JWT, headers completos, argumentos/documentos, claves ni dumps
de contexto. Los informes de equivalencia contienen sólo fixtures proyectados.

## 14. ¿Cuáles fueron los fallos, limitaciones y tests no ejecutados?

### Matriz final ejecutada

| Grupo | Resultado y evidencia |
| --- | --- |
| Originales Linux C1 | 212 passed, 0 failed/skipped; 125,492 s; imagen C1 intacta |
| Suite completa Linux candidata | 304 passed (212 C1 + 92 L4), 0 failed/skipped; pytest 163,31 s; imagen final04 |
| Suite completa Windows | 289 passed, 15 skipped; detalle temporal en l4-windows-final04.json |
| Smoke C1 MCP-1/firmado | 6 casos; SDK/model_calls=0, firmas/binding y DENY |
| Smoke persistente | status=passed, 25 filas; protocolo/list/call/roles/single-use |
| Procedencia/equivalencia | 20 fuentes instaladas, 41 canónicos, 37 HTTP iguales, 4 rechazados L1 |
| Contexto/concurrencia | Scope frozen, barreras reales, A/B/A2, IDs repetidos, no herencia |
| Roles/binding/metadata | Firma/claims/alg/kid/vigencia, ownership injection, delegación, SUPERADMIN |
| Fallos/recuperación | Error finito, timeout finito, desconexión, cancelación ajena, 4 bloqueos + quinto |
| Límites | Entrada 65.535/65.536/65.537; salida 262.143/262.144/262.145; Unicode/escaping |
| Sobre/JWS | 16.383/16.384/16.385 bytes; JWS sintéticos 4.095/4.096/4.097 siguen sujetos a crypto |
| Payload grande SDK | 3/3 completos; body máximo observado 95.726 bytes; texto = structuredContent |
| Empaquetado | Dos builds independientes: siete archivos iguales; locks/core preservados |

Las 212 Linux se corrieron primero en el baseline C1; además, la corrida final04
ejecutó JUNTAS las 212 anteriores y las 92 nuevas en la imagen candidata:
304/304. Windows descubre también ambas poblaciones juntas. No se ejecutó suite global backend, Voice,
VICTOR, cloud ni proveedores reales.

### Hallazgos y correcciones exclusivamente locales

- Primera exportación `dev01` por docker cp falló; no quedó detalle suficiente
  para atribuir causa. El baseline manual también perdió JSON temporales al
  acabar el contenedor. No se inventó evidencia: se repitieron los escenarios,
  se implementó exportación binaria acotada antes de cerrar tmpfs y se guardaron
  resultados nuevos. La extracción valida paths, tamaños y no sobreescritura.
- El emisor fixture conservaba un request_id externo diferente al claim
  parcheado para inyección de fallos. Se corrigió sólo el arnés L4, sin modificar
  L1; los faults finales acreditan entrada real al proveedor.
- El margen de respuesta de error se movió dentro del techo nominal de 5 s;
  Host/Origin se validan también antes de rechazar GET/DELETE, y el transporte
  cliente se fijó al endpoint exacto. La build final incluye sus tests.
- La revisión final detectó que el SDK puede seguir por su cuenta redirects
  a la misma URL aunque HTTPX tenga follow_redirects=false. Se cerró ese camino
  en el transporte L4: todo 3xx se cierra y rechaza ANTES de devolverlo al SDK.
  Tests con ClientSession real y transporte HTTP ficticio verifican 302/307/308,
  exactamente un POST, sin replay firmado. El servidor del benchmark no emite
  redirects; aun así se reconstruyó y repitió la aceptación con esta corrección.
- La clasificación de `execution_failed` distingue error de DENY manteniendo
  intacto el ToolResult canónico. Los timeouts C1 se reconocen por lifecycle;
  no se silencian bajo una categoría de error genérica.
- La cancelación stateless/disconnect no interrumpe hilos. Cuatro bloqueos
  producen indisponibilidad global hasta terminar el proceso: **gate HIGH**
  para recomendar integración, no supuesto resuelto por un timeout HTTP.
- Límites de salida actúan después de serialización SDK. El stream cliente
  limita bytes pero ya pueden existir chunks/copias. No es sandbox universal
  de memoria frente a servidor hostil. El cgroup agrega otra frontera.
- Sin conteo de estados TCP, sin desglose exacto de serialización/policy,
  sin carga prolongada, sin alta disponibilidad, sin cancelación preventiva.
  Eventos acotados a 8.192 por proceso; no se promete observación eterna.
- AL2 fijado conserva `SUPPORT_END=2026-06-30` en os-release. Se respetó el
  entorno solicitado; usarlo aquí no constituye aprobación de base productiva.

## 15. ¿A, B o C y por qué?

**B.** Mantener imports/core cargados elimina gran parte del costo de C1;
los resultados y el aislamiento de datos no sugieren abandonar esa mejora.
Pero el mismo proceso comparte capacidad y destino de recuperación: cuatro
operaciones no cooperativas impiden servir otras, incluso de otro tenant.
Matar el proceso soluciona el experimento, NO brinda aislamiento por solicitud.

L4 queda cerrado como implementación/evaluación local, no como arquitectura
aceptada para integración. El gate de disponibilidad es concreto y observable;
no se responde sólo “falta seguir probando” ni se optimiza indefinidamente.

## 16. UN siguiente bloque acotado propuesto

**L5 LOCAL — decisión de recuperación e impacto de bloqueo, sólo diseño.**
Sobre estos resultados y lectura focalizada de contratos síncronos existentes,
definir un contrato verificable de deadline, fin real del trabajo, agotamiento
de cuatro slots y tratamiento de las solicitudes sanas. Distinguir qué puede
garantizar un timeout cooperativo del proveedor y qué exige una frontera de
aislamiento adicional o recuperación global explícita. Cerrar con UNA opción
y criterios de aceptación/rechazo, coste y limitaciones, sin implementarla.

No decidir alojamiento cloud, no cambiar core, no añadir pool prefork, no
conectar Chat/SOPHIA real, no desplegar ni ampliar recursos en ese bloque de
diseño. La implementación de la opción requeriría autorización aparte.
**L5 no se inició.**

## 17. ¿Se involucró VICTOR?

**NO.** No se solicitó/accedió su repo, runtime On-Premise, tokens, contratos,
tests, planificación, aprobaciones ni Step Functions. No es un requisito L4.
Voice, su proxy/audio/WebSocket y los cambios locales previos se dejaron intactos.

## 18. ¿Se autoriza producción con L4?

**NO.** Sin commit/stage/push/pull, sin deploy, sin activación de flags,
sin OAuth productivo ni integración con SOPHIA. No se modificó otro repo.

## Archivos y preservación final

Todos los cambios son adiciones dentro de XOC-MCP-LAB:

```text
xoc_mcp_lab_l4/
  __init__.py, integrity.py, boundary.py, scope.py, wiring.py,
  pool.py, server.py, client.py, harness.py
tests/
  test_l4_scope.py, test_l4_cancellation.py, test_l4_http_boundary.py,
  test_l4_sdk.py, test_l4_packaging.py
packaging/
  Dockerfile.l4, l4_build.py
scripts/
  l4.ps1, l4_preservation.py, l4_summarize.py, l4_windows.py, l4_finalize.py
reports/l4-*
l4-runs/ (artefactos y evidencia, sin sustituir L1/L2/L3)
```

Baseline/final acreditan hashes de archivos originales del laboratorio
idénticos y branch/HEAD/status de los cinco repos externos iguales. Esa
comprobación Git es de estado, no un hash completo de cada archivo ajeno.
No se hicieron operaciones Git de escritura ni se reparó el .git padre.

| Repo externo intacto | HEAD |
| --- | --- |
| XOC-API-AWS | ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9 |
| XOC-APP-MOBILE | df151a73551ed5a437c2f1e40b51896cee9dd24c |
| XOC_PAGE | f43d3ed6b2eac389cdf29e9fc9d052137fe188eb |
| proxy-sophia-xoc | 93107ac2a4ad08b2c28e6242fb0aeda666eaf54f |
| XOC_MICROSERVICIO_AGENTES | 6249064a9bd69bb388369d03f502b142bac93e1b |

Comandos exactos de build/tests/smoke/benchmark/cierre están en
[l4-commands.md](l4-commands.md); requieren RunIds nuevos para no sobrescribir.
La comparación final procede de wheels instalados y archivos retenidos, no
de una estimación ni de un despliegue.
