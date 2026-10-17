# L4 LOCAL: contrato de seguridad, límites y límites de la evidencia

Fecha: 2026-09-16. Estado: candidato **LAB_ONLY**, no desplegado y no apto como autenticación productiva. Este documento describe el runtime congelado y distingue mecanismos implementados de observaciones experimentales. No modifica C1, su SDK, sus dependencias ni otro repositorio.

## Evidencia y alcance

La ejecución instalada final `l4-final04-20260916` registró **304 pruebas aprobadas, 0 fallidas, 0 omitidas, 163,31 s**: 212 originales C1 y 92 nuevas L4 ejecutadas juntas, en una misma invocación de pytest sobre la imagen candidata. No es una suma de suites corridas en imágenes distintas. Se puede comprobar en [tests.json](../l4-runs/l4-final04-20260916/results/tests/tests.json) y [runner.log](../l4-runs/l4-final04-20260916/results/tests/runner.log). El [smoke](../l4-runs/l4-final04-20260916/results/smoke/smoke.json) pasó con 25 filas. La [procedencia](../l4-runs/l4-final04-20260916/results/provenance/provenance.json) registra `all_installed=true`, 41 casos de equivalencia canónica, 37 comparaciones HTTP idénticas y cuatro casos adicionales de identidad L1 inválida. Tests, smoke y procedencia registran ningún hijo final; smoke y procedencia registran además el servidor ausente tras cleanup. El entorno observado mantiene SDK 1.30.0, protocolo 2025-11-25, usuario 10001 y cgroup de 536.870.912 bytes.

Esta versión incluye y aprueba las correcciones finales: `test_error_delivery_never_extends_remaining_total_budget` comprueba envío nulo con presupuesto agotado y timeout de error limitado por lo restante; `test_host_origin_validation_precedes_unsupported_method_rejection[GET/DELETE]` comprueba Host/Origin antes del 405; `test_l4_redirects_to_approved_endpoint_never_replay_signed_header[302/307/308]` comprueba que ni siquiera una redirección al mismo endpoint provoca un segundo POST firmado. También aprueba el ensayo HTTP instalado de tamaños exactos y Unicode fragmentado. Los artefactos de `final01/final02/final03` quedan como historial, no como aceptación de la distribución final04. El benchmark de rendimiento es evidencia separada: no se deduce una ventaja de arquitectura del número de tests aprobados ni se anticipan aquí sus resultados finales.

El servidor usa `mcp==1.30.0`, protocolo `2025-11-25`, `Server` y `StreamableHTTPSessionManager(stateless=True, json_response=True)` del SDK público. El cliente usa `streamable_http_client` y `ClientSession`. No se adapta una factory privada de transporte C1 ni se simula HTTP sobre stdio. Los handlers tipados preservan el comportamiento de argumentos sin la normalización `arguments or {}` del decorador de herramientas.

El transporte cliente fija la URL exacta y rechaza cualquier status 300–399 antes de devolver la respuesta al SDK; intenta cerrarla y produce el fallo fijo de transporte `LAB_REDIRECT_BLOCKED`, sin URL ni assertion. Esto es necesario porque el SDK puede seguir redirecciones mediante su propio bucle aunque HTTPX tenga `follow_redirects=False`. Los tres tests citados usan `ClientSession` y Streamable HTTP del SDK real con `MockTransport` como fixture de respuesta HTTP: verifican exactamente un POST con cabecera firmada, respuesta cerrada y ausencia de replay en 302/307/308 hacia la misma URL aprobada. No se presentan como un servidor externo ni como tres ensayos de red adicionales.

Un proceso persistente sirve exclusivamente `http://127.0.0.1:<puerto>/mcp`, sin query. El puerto se fija al arrancar; no lo elige el mensaje MCP. En ese endpoint se validan cabeceras sensibles, Host y Origin antes de rechazar un método: GET, DELETE y OPTIONS con cabeceras de origen válidas reciben 405; un Origin inválido sigue recibiendo 403 aunque el método no se admita. No hay SSE persistente, CORS abierto, sesión como identidad ni puerto publicado por el runner Docker. El SDK puede soportar otros protocolos/transporte; la frontera L4 los restringe expresamente.

## Identidad ficticia y autorización canónica

Cada POST, incluidos `initialize`, `tools/list`, notificaciones y `tools/call`, exige `X-XOC-Lab-Envelope`: sobre L1 completo, JSON canónico, codificado base64url ASCII sin padding. El servidor vuelve a ejecutar el `verify_envelope` L1 original. No toma identidad de query, `_meta`, argumentos, `clientInfo`, cabeceras decorativas o `Mcp-Session-Id`.

L1 fija ES256, `typ=JWT`, el `kid` de fixture y su clave pública; no admite selección de claves aportada por el cliente. La clave corresponde deliberadamente a un escalar de prueba público: **cualquier conocedor del fixture puede firmar identidades ficticias**. Esto no autentica personas ni protege un servicio frente a otros procesos locales. Loopback y Host/Origin son restricciones de exposición/rebinding, no sustitutos de autenticación.

Se verifican las claves exactas del sobre y de claims, `iss=xoc-mcp-lab-fixture-issuer`, `aud=xoc-sophia-query-worker`, `purpose=mcp-read`, `source=sophia_chat`, la firma, el `request_id` y el hash canónico del nombre/argumentos. Los claims son `iss`, `aud`, `purpose`, `source`, `actor_user_id`, `actor_role`, `tenant_id`, `effective_tenant_id`, `delegation_active`, `request_id`, `tool_hash`, `iat`, `exp` y `deadline`.

`iat`, `exp`, `deadline` y usuario deben ser enteros estrictos; delegación es booleano estricto. Se exige `iat <= ahora < deadline <= exp <= iat+30`. Los tenants ficticios admitidos son 91001/91002 o null donde el esquema lo permite; usuarios 92000–92999; el rol conserva el formato L1. Un formato válido no concede permisos: la Policy original decide. Se rechazan claims faltantes/extra, firma/algoritmo/kid incorrectos, identidad real fuera del rango, vigencia inválida y binding alterado.

Para `tools/call`, el nombre y argumentos efectivos se comparan con `VerifiedContext.matches` en ingress y en el handler. Null explícito, parámetros de tarea, `_meta` y parámetros adicionales no constituyen un canal alternativo de contexto. Sólo después de esa comprobación se ejecuta el Adapter de la solicitud. `initialize` y `tools/list` verifican contexto sin ejecutar un handler de negocio ni hacer lecturas de proveedor; el listado aplica la Policy canónica.

La allowlist sigue siendo `xoc.tickets.list`, `xoc.tickets.get`, `xoc.documents.list` y `xoc.notifications.unread_count`. Adapter, preflight de Policy, Executor con su propia Policy, helpers, proyección/saneamiento y auditoría originales se conservan. No se sustituye autorización por una tabla local de roles/tenants ni por anotaciones MCP. La identidad validada crea el `ToolContext` frozen canónico con `source=sophia_chat`.

Una instancia del wrapper cliente admite una llamada; un Adapter verificado admite un intento de negocio. Una nueva solicitud puede reutilizar una assertion todavía vigente. No hay ledger global, replay protection adicional, garantía exactly-once ni revocación distribuida.

## Propiedad del contexto, proveedores y cleanup

Cada POST válido crea un `RequestScope` frozen nuevo. Su autoridad verificada es inmutable; sus filas fixture, presupuesto, contadores, fallos y observaciones mutables pertenecen exclusivamente a esa solicitud. La fachada de tablas se instala una vez por proceso y resuelve el `ContextVar` en cada operación. Una operación de proveedor sin scope falla cerrada.

El handler comprueba que el `starlette.requests.Request` real entregado por el SDK contiene exactamente su scope. El hilo recibe un contexto copiado, con base de scope vacía y binding explícito de su propia solicitud. Tanto ASGI como hilo restauran el `ContextVar` en `finally`. No existe un mapa de autoridad indexado por JSON-RPC ID, `request_id`, sesión o conexión. IDs repetidos entre A y B no fusionan contexto.

`PersistentWiring` y `ScopedIsolation` mantienen una sola instalación de proveedores fake, restricciones de red/archivos de secretos y contadores. No se abren Wiring independientes superpuestos. Se bloquean clientes/proveedores reales y operaciones no admitidas, manteniendo helpers y lógica de negocio originales. Son guardas Python restituibles para el laboratorio, **no un sandbox frente a ejecución arbitraria de código hostil**.

La CLI verifica artefactos C1 y L4 instalados, instala aislamiento/proveedores fake y precarga Adapter/registro canónicos antes de readiness. No acepta `--backend` ni fallback a checkout. `create_app(...)`, usada por integración, exige `PersistentWiring` activo y deja el ownership de aislamiento al caller; no debe confundirse esa API interna con toda la secuencia protegida de la CLI.

El apagado distingue fin de espera de fin real del hilo. Si quedan workers activos al terminar Uvicorn, la CLI registra `server_forced_exit` y usa salida de proceso 124 **antes** de restaurar proveedores/red originales. Un kill externo también termina el proceso completo. No se afirma que este mecanismo cancele solamente una solicitud: afecta a todos los trabajos del servidor.

## Límites aplicados

| Frontera | Límite/acción implementada |
| --- | --- |
| Sobre decodificado | 16.384 bytes UTF-8; representación JSON canónica y base64url canónico obligatorios. |
| Assertion JWS interior | 4.096 caracteres; pasar el tamaño no omite ninguna comprobación criptográfica. |
| Valor de cabecera codificado | Máximo 21.846 bytes; rechaza padding, caracteres extra y codificación no canónica. |
| Cabeceras en ASGI | Suma de nombres/valores/framing calculado de 32.768 bytes; cabeceras sensibles duplicadas rechazadas. No equivale a contar toda la memoria del parser HTTP. |
| Host/Origin | Host exacto `127.0.0.1:puerto`; Origin ausente o exacto `http://127.0.0.1:puerto`. Sin comodines. |
| Body de entrada | 65.536 bytes acumulados, incluidos fragmentos; exceso 413, sin parsearlo como un mensaje admitido. UTF-8 estricto; rechaza claves JSON duplicadas, NaN e Infinity. |
| Recepción de body | Hasta 1 s, reducido por el deadline firmado. Content-Length inválido, discrepante o ambiguo con Transfer-Encoding se rechaza. |
| Respuesta | 262.144 bytes de JSON completo; exceso sustituido por error pequeño `LAB_OUTPUT_LIMIT`, nunca JSON truncado. |
| Trabajos síncronos | Cuatro slots, adquisición no bloqueante antes de submit. Quinto trabajo: `LAB_CAPACITY_BUSY`; no cola ilimitada. |
| Espera del worker | Hasta 3 s, reducido por el deadline firmado; conserva slot si el trabajo sigue vivo. |
| Ciclo ASGI | Deadline nominal de 5 s desde entrada, restando tiempo consumido y limitado por vigencia. Reserva hasta 50 ms para error **dentro** del mismo presupuesto; no envía error si el presupuesto ya se agotó. |
| Cliente | Objetivo total por defecto 3,5 s; parámetro limitado a 5 s y vigencia restante. Incluye initialize/call/cleanup observados; sin retries ni redirects permitidos por su transporte loopback. |
| Observación | Hasta 8.192 eventos de metadatos por proceso; hasta cuatro registros de auditoría por scope. No almacena headers, assertions, argumentos ni resultados en eventos. |

Los límites de body excluyen cabeceras y framing HTTP. Uvicorn aplica además concurrencia de 32 y keep-alive de 2 s; esos valores no son un mecanismo de autorización ni un límite por tenant. El runner usa Linux/amd64, usuario 10001, filesystem read-only salvo tmpfs, `--network none`, sin mounts del backend ni puertos publicados, 512 MiB, 2 CPU y 128 pids compartidos por servidor/cliente. La evidencia del contenedor, no una propiedad de Python por sí sola, sustenta esos límites de recursos.

## Timeout, cancelación y brechas temporales explícitas

Un timeout o disconnect libera la espera de su caller, **no mata un hilo**. El slot se devuelve únicamente en el `finally` del trabajo real. Un A lento mantiene su contexto y slot mientras B usa otro; cuatro A no cooperativos agotan capacidad y el quinto se rechaza. La recuperación de un bloqueo indefinido requiere terminar el proceso completo.

El SDK stateless crea transporte y `ServerSession` nuevos por POST. Una notificación `notifications/cancelled` posterior, incluso con el mismo JSON-RPC ID, no encuentra el mapa de solicitudes en vuelo del POST anterior. Se acepta como notificación 202, sin cancelar A ni atribuirle autoridad de B. No se añade un mapa global para aparentar una cancelación que este diseño no ofrece. El SDK JSON tampoco convierte automáticamente un cierre TCP en interrupción del trabajo síncrono.

Los deadlines usan temporizadores cooperativos. No son garantías hard real-time ni límites absolutos de CPU: planificación, operaciones síncronas y cleanup pueden introducir demora. No hay gracia configurada después de los cinco segundos: dispatch reserva 50 ms dentro del presupuesto restante y el envío de error usa el menor valor entre esa reserva y el presupuesto efectivamente disponible; si éste es cero o negativo, no se intenta enviar. La vigencia se comprueba al verificar/bindear y al entrar en el Adapter; no hay interrupción preventiva de un handler ya ejecutándose cuando vence la assertion. Su resultado tardío no autoriza otra solicitud y su slot no se recicla mientras continúe.

Se distingue `request_finished` de `worker_finished` y apagado global. Que el cliente termine no demuestra que el servidor/hilo terminaron; que el HTTP cierre no demuestra que se liberó el slot. Los tests comprueban esas distinciones con entrada real al proveedor y B atendido durante A.

## Saneamiento y memoria: lo que no se garantiza

Los errores de validación del SDK se sustituyen por códigos/mensajes fijos sin `data` arbitrario. Los logs de dependencias usan el saneamiento existente; los resultados pasan por la proyección canónica y se exige coincidencia texto/structuredContent en el cliente. El perfil de fallos `mixed` sólo se selecciona al arrancar y usa prefijos de request_id firmados de fixture, nunca argumentos o `_meta`. No es una feature de un servicio público.

`BoundedResponse` recibe bytes **después** de que el SDK construyó/serializó su resultado. Acotar esa salida evita enviar una respuesta grande o truncada, pero no limita retroactivamente memoria ya usada por helpers, Adapter, Pydantic, strings duplicados o serialización. El cliente envuelve el stream con `LimitedStream` antes del parseo JSON del SDK y rechaza al superar el presupuesto; un chunk puede existir ya en memoria antes de ser contado. Buffers de HTTP/TLS/kernel, estructuras postparseo y copias internas no equivalen al número de bytes del mensaje. No se afirma que `LimitedStream` o el cap del body sean un sandbox universal de memoria; el cgroup es una defensa adicional, no prueba de memoria constante para entradas arbitrarias.

## Cobertura y observabilidad

El corpus cubre firma/kid/algoritmo/claims/identidad/vigencia/binding inválidos; contexto exigido también en initialize/list; inyección por argumentos/_meta/cabeceras decorativas; Host/Origin/métodos/query; JSON/UTF-8/framing; no eco de detalles sensibles; allowlist y equivalencia con C1; concurrencia A/B y usuarios A1/A2; IDs repetidos; barreras reales de proveedor; error, timeout finito, cierre TCP explícito, notificación de cancelación ajena y cuatro bloqueos no cooperativos.

Las fronteras de salida se prueban en 262.143/262.144/262.145 bytes con ASCII, UTF-8 real y Unicode/escapes serializados. La entrada HTTP real instalada prueba 65.535/65.536/65.537 bytes y un código UTF-8 dividido entre fragmentos. Los sobres de 16.383/16.384 bytes tienen firmas reales válidas; 16.385 se rechaza. Assertions sintéticas de 4.095/4.096/4.097 caracteres permanecen inválidas: la prueba de tamaño no falsifica ni desactiva crypto. También hay respuestas MCP reales mayores de 64 KiB y pruebas de integridad, builds deterministas, paths y extracción de evidencia.

Los eventos distinguen recepción, verificación/binding, dispatch, guard de salida, reset, tiempo real de worker y cleanup. `response_guard_ns` no mide la serialización original del SDK; ésta sigue incluida en dispatch. Los spans son inclusivos y no deben sumarse como fases disjuntas. `handler_entered` se observa en la primera entrada real al proveedor con contador de handler, no en la primera instrucción del handler. Timestamps de proveedor abarcan operaciones observadas, no toda latencia de negocio independiente.

`pending_requests`, pool, tareas/hilos, fd/RSS/cgroup y scopes vivos permiten comparar rondas. El registro de scopes usa referencias débiles y una clave técnica sin función de autoridad; no retiene resultados. El snapshot de `request_finished` todavía puede observar su propio scope hasta el retorno y cleanup del SDK. Un contador débil estable no prueba por sí solo ausencia de todas las fugas; fd tampoco es un conteo exacto de conexiones TCP activas. El tope de eventos implica que un proceso suficientemente largo deja de aportar detalle nuevo. Estos límites deben acompañar cualquier conclusión sobre retención, latencia o estabilidad.

No se extrapolan las pruebas locales a OAuth, acceso multiusuario real, Internet, producción, disponibilidad indefinida, aislamiento por tenant bajo agotamiento de cuatro workers ni migración aprobada de C1.
