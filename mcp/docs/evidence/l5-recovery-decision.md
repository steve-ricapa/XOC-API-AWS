# MCP-2 · L5 LOCAL — Decisión de recuperación

Fecha: 16-sep-2026. Estado: **diseño terminado; implementación NO autorizada por L5**.

Recomendación principal: **B — recuperar la instancia MCP persistente completa mediante un supervisor local independiente**. Conservar cuatro slots; cerrar admisión, drenar durante un plazo limitado, terminar/recolectar el proceso y verificar una generación nueva. No intentar matar threads Python, multiplicarlos ni repetir solicitudes perdidas.

La recomendación está condicionada a aceptar su radio de impacto: puede interrumpir consultas sanas de cualquier tenant atendido por esa instancia. No propone alta disponibilidad ni selecciona alojamiento cloud. El único siguiente encargo propuesto es **L6 LOCAL**, descrito en §10; no se inicia ahora.

Documento complementario: [contrato de aceptación L5](l5-acceptance-contract.md). Allí se fijan tiempos candidatos, casos, evidencia y STOP. Este informe no cambia la decisión histórica B de L4: experimento terminado, buena latencia, recuperación insuficiente para recomendar integración. La letra B de aquella clasificación y la opción B de este diseño son conceptos diferentes.

## Convenciones de evidencia

- **OBSERVADO EN L4**: medición o resultado conservado del experimento final04, no una ejecución de L5.
- **COMPROBADO POR LECTURA DE CÓDIGO**: comportamiento localizado en fuentes fijadas; no nueva medición.
- **DISEÑO PROPUESTO**: mecanismo o criterio que todavía no existe.
- **SUPUESTO**: condición necesaria para la recomendación, no garantía demostrada.
- **PENDIENTE DE VALIDACIÓN**: dato que deberá resolver el bloque posterior.
- MEDIDO se reserva a L4. ESTIMADO no equivale a medido. Los números de recuperación son **OBJETIVOS POR APROBAR**, no SLA ni estimaciones estadísticas.

## 1. Problema exacto y baseline

**OBSERVADO EN L4.** Un caller puede acabar con timeout mientras su handler síncrono sigue vivo. Cuatro lecturas no cooperativas retienen los cuatro slots indefinidamente. L4 limita admisión y evita una cola ilimitada de trabajos admitidos, pero no detecta ni recupera automáticamente esa pérdida de capacidad.

Fuentes principales disponibles y contrastadas:

- [Decisión L4](l4-decision.md), [metodología](l4-methodology.md), [seguridad y límites](l4-security-and-limits.md), [contexto](l4-context-boundary.md), [empaquetado](l4-packaging.md).
- [Resumen final04](l4-benchmark-summary-final04.json) y [validación final04](l4-validation-final04.json).
- Evidencia original bajo [l4-final04-20260916](../l4-runs/l4-final04-20260916/): results/benchmark/benchmark.json, results/tests/tests.json y comprobantes de proceso.
- Código de [xoc_mcp_lab_l4](../xoc_mcp_lab_l4/), fuentes canónicas exportadas de C1 y SDK instalado fijado. No se consultaron AWS, Azure ni documentación remota.

| Dato | OBSERVADO EN L4; alcance exacto |
| --- | --- |
| Runtime | MCP SDK 1.30.0; protocolo 2025-11-25; Streamable HTTP stateless/JSON, loopback |
| Entorno | Amazon Linux 2, Python 3.11.16, x86_64; 512 MiB / cuota 2 CPU / 128 pids |
| Tests | Linux 304/304 juntos en candidato instalado; Windows 289 passed / 15 skipped documentados |
| Calientes | 80/80 allowed; mediana 26,339 ms; p95 nearest-rank 30,771 ms; máximo 92,877 ms |
| Cuatro normales simultáneas | 4/4 allowed; máximo 72,417 ms |
| A bloqueada, B sana | A entró al proveedor y conservó slot; B allowed en 24,481 ms |
| Cuatro bloqueadas | Quinto intento rechazado con lab_capacity_busy en 18,629 ms |
| Retirada del servidor bloqueado | 0,666309 s para terminar/recolectar; NO incluyó reemplazo ni consulta posterior |
| Arranque de otra población | Cinco arranques; mediana hasta ready 2,193653 s; máximo 2,258778 s |
| A lenta finita | Caller timeout en 0,302425 s; worker terminó realmente en 2,001302 s; B allowed |
| Consulta tras A finita | 24,277 ms; NO es recuperación de instancia bloqueada |
| C1 comparable | 80/80 secuenciales; mediana 2,527577 s; p95 2,786870 s. Los cuatro concurrentes dieron timeout |
| Límites de la evidencia | No RTO global medido, proveedor real, aislamiento fuerte de tenants frente a agotamiento ni estabilidad indefinida |

**No se suman 0,666309 s y 2,193653 s como un RTO.** Tampoco se reutiliza el éxito posterior al caso lento finito como prueba de reinicio. L5 no ejecutó nuevos tests o benchmarks.

Procedencia preservada:

- Core: ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9.
- SHA256 exacto del wheel core: **df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41**.
- SHA256 wheel L4: 764352dc77a7c467f6c8c3162a4f93e4fcca0e21b7933db0e2a4557669dcfb5d.
- Imagen L4: sha256:129efefeecc7549df84604ed2e532fa524aaab716f6cf145ca9e5522e6ab72b9.

## 2. Quién conserva el trabajo y quién devuelve capacidad

Todo este apartado es **COMPROBADO POR LECTURA DE CÓDIGO**, salvo mediciones señaladas.

Recorrido: cliente deja de esperar → request ASGI/ServerSession → awaiter/future → thread del pool → Adapter/Policy/Executor/helper → proveedor → retorno o excepción real → cleanup del contexto → liberación del slot.

| Nivel / propietario | Creación, contexto y plazo conocido | Cancelar significa / qué sobrevive | Evidencia de fin y dueño de capacidad |
| --- | --- | --- | --- |
| Cliente QueryClient | Crea HTTPX, transporte y ClientSession por wrapper. Verifica envelope; objetivo 3,5 s, máximo 5 s y vigencia restante | wait_for cancela el árbol async local y cierra transporte; no interrumpe trabajo remoto | Resultado/error y cleanup del cliente prueban fin de espera. Nunca posee el slot del servidor |
| Request ASGI | L4Application crea RequestScope frozen nuevo, liga contexto y copia scope HTTP | Deadline ASGI 5 s, reducido por firma, con reserva de error dentro del plazo. Finalmente resetea contexto ASGI; el thread puede continuar | request_finished sólo acredita ese ámbito. No devuelve permiso de pool |
| Transporte / ServerSession | Manager persistente, pero transporte y ServerSession nuevos por POST stateless | Cierre cancela tareas async y streams; el trabajo síncrono protegido sobrevive | Fin del task group/session no equivale a fin del handler síncrono |
| Awaiter y future | Handler comprueba scope, vigencia y binding; pool adquiere permiso antes de submit | wait_for(shield(wrapped_future)), hasta 3 s o plazo firmado restante, deja el future subyacente vivo | Fin del awaiter no libera slot. Callback consume excepción tardía sin reenviar resultado |
| Thread / pool | ThreadPoolExecutor de cuatro, contexto copiado y bind_scope explícito; invoca Adapter sin deadline preventivo del thread | No existe interrupción segura del thread; Event.wait no cooperativo puede no retornar | BoundedPool devuelve permiso únicamente tras callable y reset real, o al fallar submit antes de ejecutar |
| Gateway / helper | Adapter por scope, ToolContext frozen; Policy y Executor canónicos | No consulta señal cooperativa universal de cancelación dentro del helper | Handler retorna/lanza. Eso permite llegar al finally del pool; no lo sustituye |
| Proveedor ficticio | ScopedTable resuelve current_scope, referencia explícita a scope y fila fixture | Guardas de 40 lecturas / 5 s se comprueban ANTES de la operación, no interrumpen una espera ya iniciada | provider_finished exige volver/lanza; el proveedor no libera directamente el slot |
| Scope retenido | Contiene VerifiedContext, adapter, wiring, observaciones propias | Resetear ContextVar del caller no revoca referencias del worker vivo | Recolección sólo cuando desaparecen referencias, o termina el proceso completo. El weak registry no es autoridad |

Referencias locales: pool.py:18–91; server.py:72–117 y 129–233; client.py:77–163; scope.py:42–137; wiring.py:25–48. En el SDK fijado: server/streamable_http_manager.py:225–255, server/lowlevel/server.py:664–696 y 759–798, shared/session.py:283–314 y 402–406. Rutas del SDK relativas a .venv/Lib/site-packages/mcp. Los módulos SDK revisados coinciden byte a byte con el wheel 1.30.0 de final04.

### Matices que importan para la recuperación

1. Timeout cliente, timeout de pool, cierre TCP, future.cancel y notifications/cancelled **no son equivalentes**. L4 no llama future.cancel. Cancelar un future ya ejecutándose no mata su thread; cancelar uno aún pendiente necesitaría contabilidad especial porque invoke podría no ejecutar su finally.
2. ClientSession.send_request retira streams al vencer; no envía automáticamente notifications/cancelled.
3. Una notificación de cancelación en otro POST obtiene otra ServerSession; su mapa de requests en vuelo no contiene A. Un 202 no acredita que se canceló A. No se añadirá un mapa de cancelación global por JSON-RPC ID.
4. La ruta JSON del SDK no vigila continuamente disconnect después del dispatch. Puede perderse el destinatario sin detener inmediatamente el handler async; cuando éste acaba, shield sigue preservando el thread.
5. worker_context copia otros ContextVars además del scope propio; el SDK request_ctx puede conservar ServerSession y request ASGI. Esto no demuestra mezcla A/B, pero sí impide afirmar que request_finished elimina todas las referencias.
6. Orden actual: fin del callable → reset del scope → active-- / completed++ → release del semáforo → observer worker_finished → retorno de invoke / Future done. Por tanto worker_finished NO significa join del thread, y el permiso se devuelve antes de terminar el observer.
7. EventFile hace lock, write y flush síncronos, y deja de registrar detalle al llegar a 8192 eventos. Si ese observer se bloqueara, podría seguir ocupando un thread después del release lógico. No se observó ese fallo en L4; es una limitación encontrada por lectura. El diseño posterior debe contar hasta la salida efectiva de la unidad síncrona y no poner I/O de diagnóstico bloqueante después de declarar capacidad libre.
8. pool.shutdown(wait=False, cancel_futures=False) no termina trabajos vivos. La salida defensiva os._exit(124) evita restaurar providers reales con trabajos activos, pero no es un supervisor, detector ni reinicio.
9. tools/list utiliza el mismo pool de cuatro. initialize sólo prueba protocolo/loop; el ready file actual acredita startup/imports/wiring, no una consulta canónica completada.

## 3. Fronteras I/O de las cuatro tools

**COMPROBADO POR LECTURA DE CÓDIGO.** Prefijo C1: l3-runs/l3-c1-final-build/sources-a/core/src. No se inspeccionó otro runtime ni se invocó un proveedor.

| Tool | Operaciones reales del helper y límites existentes |
| --- | --- |
| xoc.tickets.list | tool_gateway/executor.py:36–47 valida limit 1–200. shared/tickets_store.py:137–149 pagina query hasta agotar LastEvaluatedKey, SIN Limit ni cap de páginas/tiempo; :168–179 ordena todas las filas y después corta. Límite de salida no limita el trabajo |
| xoc.tickets.get | executor.py:50–57 y tickets_store.py:111–124: una llamada lógica get_item con tenant/ticket; puede contener varios intentos HTTP internos. Sin paginación |
| xoc.documents.list | executor.py:60–72 y reports/store.py:241–288: query con Limit por página; continúa con cursor hasta acumular limit o agotar páginas. Sin deadline total/page cap explícito. Rama local :17–39, :246–253 lee JSON completo, filtra y ordena |
| xoc.notifications.unread_count | executor.py:75–79 y shared/user_notification_inbox.py:191–207: query Select=COUNT, índice UserStatusCreatedAtIndex, suma todas las páginas. Partición de tenant + user + estado. Sin límite explícito de páginas/tiempo |

Los clientes no pasan Config, connect_timeout, read_timeout, retries, deadline ni cancelación. Tickets/documents mantienen recurso/tabla global al importar; inbox obtiene recurso/tabla en el helper. En L4 esas fronteras se sustituyen por ScopedTable y se bloquean credenciales y Endpoint._send: **no hubo I/O AWS medido**.

Dependencias fijadas: boto3 1.40.18, botocore 1.40.76, urllib3 2.7.0. Por lectura de botocore/config.py:47–55,300–301 y configprovider.py:153–157, defaults condicionales connect/read 60 s y retry_mode legacy. Configuración externa puede cambiarlos; no se consultó configuración de cuentas. El modelo DynamoDB legacy en data/_retry.json:128–134 contempla 10 intentos totales, no diez reintentos. endpoint.py:192–231,350–380 implementa intentos y backoff por cada operación/página.

Separación obligatoria:

- Conexión: socket/connect no cubre necesariamente resolución DNS ni todos los bloqueos de host.
- Lectura: urllib3/util/timeout.py:76–103 distingue espera entre lecturas y duración de respuesta completa.
- Botocore construye Timeout(connect, read), sin total, en httpsession.py:306–309; sus retries son adicionales por operación.
- Páginas: cada una puede consumir nuevos intentos y backoff.
- CPU, acumulación de datos, ordenación, deserialización, filesystem y serialización no quedan acotados por socket timeouts.
- No existe en esos helpers un presupuesto total compartido de tool. No calcular una garantía sumando 60+60 o multiplicando por intentos.

**DISEÑO PROPUESTO, NO seleccionado como solución principal:** un futuro presupuesto monotónico compartido para I/O, retries y páginas reduciría bloqueos cooperativos. No mata threads ni cubre código/kernel no cooperativos. Exigiría otra autorización para cambiar helpers canónicos: un unread_count parcial no puede presentarse como total exacto, ni cortar tickets antes de ordenar conserva automáticamente su semántica. L6 propuesto no modifica esos helpers.

## 4. Modelo de fallos y garantía limitada

**DISEÑO PROPUESTO.** La garantía buscada es recuperar capacidad de esta instancia local o declarar UNAVAILABLE de forma observable, sin mezcla de identidad, ejecución no autorizada ni replay automático.

| Fallo | Contención/resultado esperado |
| --- | --- |
| Normal | Fin real y slot devuelto; sin reinicio |
| Error inmediato de proveedor | Error canónico saneado, cleanup y otras consultas disponibles; no cuenta como gatillo |
| Lenta finita | Caller puede abandonar antes; slot sigue ocupado hasta fin real. Si termina antes del umbral operativo, no se reinicia |
| Una no cooperativa | Quedan como máximo tres permisos utilizables; se permite B mientras gate abierto. Al exceder edad aprobada se recupera TODA la instancia |
| Cuatro no cooperativas | Cero permisos; list/call rechazan, sin cola; detector independiente inicia recuperación |
| Event loop bloqueado | Heartbeat del loop vence; supervisor externo al proceso detecta y termina. No depende de un timer del loop bloqueado |
| Disconnect | Destinatario perdido; no es por sí solo gatillo. Si el trabajo sobrevive al límite de ocupación sí lo es |
| Crash del servidor | Respuestas no confirmadas quedan inciertas; recolectar hijo y reconstruir sin repetirlas |
| Fallo repetido | Máximo dos intentos automáticos por ejecución supervisada; tercer incidente cierra servicio sin otro reemplazo |
| Host/recursos/OS sin progreso | No garantía temporal dura. Si no se acredita muerte/recolección no se crea otro hijo; UNAVAILABLE y evidencia explícita |
| Supervisor/control falla | Lease de admisión vence; hijo deja de admitir al poder ejecutar el guard. No se promete restauración si murió el supervisor |

No se promete interrupción inmediata de una operación no interrumpible del kernel, cero consultas perdidas, ni disponibilidad del conjunto XOC. La capacidad de atender de nuevo sólo se prueba con consultas autorizadas nuevas; retirar un contenedor no implementa recuperación de la aplicación.

## 5. Comparación y selección de UNA estrategia

Todas las ventajas nuevas de esta tabla son **SUPUESTO / PENDIENTE DE VALIDACIÓN**, no métricas de L5.

| Criterio | A: threads + presupuesto de proveedor | B: recuperar instancia completa — ELEGIDA | C: unidad de ejecución separadamente terminable |
| --- | --- | --- | --- |
| Ventaja caliente L4 | Probablemente conservable | Probablemente conservable entre incidentes; supervisión añade overhead por medir | IPC/custodia/arranque adicionales, sin medición |
| Una bloqueada | Puede retener slot indefinidamente si no coopera | B sana hasta cierre; después impacto global | Podría terminar sólo esa unidad |
| Cuatro bloqueadas | No las recupera por sí sola | Cierre/reap de proceso y pool nuevo verificado | Recolectar y reemplazar unidades afectadas |
| Loop bloqueado | No resuelto | Supervisor independiente | Aislar workers no vigila por sí solo proceso de entrada |
| Recursos | Cambios pequeños, garantía incompleta | Un proceso supervisor e IPC acotado; nunca dos servidores vivos por reemplazo | Más procesos/canales y picos; no acreditado en 512 MiB |
| Contexto | Scope actual mientras thread vive | Scope nuevo por solicitud; ninguna autoridad se restaura al reiniciar | Transferencia y custodia entre procesos por diseñar |
| Complejidad | Menor, insuficiente para este fallo | Moderada: supervisor, gate, lease, telemetría, verificación de reemplazo | Mayor: lifecycle, balance de unidades, serialización e integridad |
| Pérdida de sanas | Agotamiento puede negar servicio indefinido | Explícita: pueden perderse todas las llamadas en curso de esa instancia | Radio potencial menor; aún no demostrado |
| Prueba decisiva | Presupuesto real + contraejemplo no cooperativo | Bloqueo → cierre → reap → nueva consulta correcta | Aislamiento individual + costo caliente + recursos + cleanup |

C sería justificable si no se acepta el impacto global de B. Un proceso nuevo por llamada volvería a pagar costos tipo C1; amortizarlo exigiría decidir reutilización y su custodia. **No se decide un prefork ni se incorpora C a B.** Si B fracasa en el contrato posterior, se detiene y presenta nueva decisión, no se implementa C por cuenta propia.

B es la menor extensión que afronta el bloqueo ya observado sin depender de cooperación del thread y sin rehacer el executor canónico. Los timeouts de proveedor son complemento potencial, no requisito oculto ni parte del bloque recomendado.

## 6. Impacto sobre consultas sanas

**DISEÑO PROPUESTO, requiere aceptación expresa antes de implementar:**

- Antes de cerrar admisión, B puede usar los permisos libres. No se le garantiza terminar si coincide con el corte.
- Después del ACK de cierre, no se admite un nuevo trabajo aunque tenga HTTP o sesión ya abierta.
- Trabajos admitidos pueden terminar durante un drenaje de hasta 1 s. Si su caller sigue válido y esperando, pueden recibir su propio resultado dentro de su deadline.
- Al vencer drenaje, se termina la instancia; trabajos sanos restantes pueden quedar sin respuesta o con error/timeout de transporte. No se los etiqueta como éxito ni se reintentan.
- El radio incluye hasta cuatro unidades síncronas, también list, **y otras peticiones HTTP en vuelo**. Cuatro slots no significa sólo cuatro clientes afectados.
- Los tenants comparten disponibilidad de esa instancia. No comparten identidad/datos, pero no hay aislamiento de disponibilidad por tenant.
- Durante ausencia del servidor o loop bloqueado, no hay frontal independiente que asegure un JSON/503 inmediato. El cliente dispone de su propio presupuesto; el operador ve el estado del supervisor.
- Sin ACK de cierre existe una ventana residual de admisión: si el loop vuelve mientras la última lease sigue vigente, aún puede admitir solicitudes autorizadas hasta el corte confirmado, expiración absoluta o muerte. Se limita a como máximo 1 s desde la última emisión de lease bajo los supuestos de progreso; no se promete cero admisiones desde el instante de decisión. Esas solicitudes se contabilizan y pueden perderse con la instancia. Este impacto también requiere aceptación.

La ventana candidata y las pérdidas se miden juntas, no sólo con muestras exitosas. Se propone T_recovery ≤12 s y T_restore_service ≤21 s en el escenario local acotado descrito en el contrato; **ninguno es un RTO medido**.

## 7. Máquina de estados y mecanismo mínimo

Todo §7 es **DISEÑO PROPUESTO**.

### 7.1 Propietario independiente

Un proceso supervisor local es propietario exclusivo de un hijo MCP, su handle de proceso, generación monotónica y contador de recuperaciones. No importa el gateway ni guarda autoridad del usuario para supervisar. No se basa únicamente en PID, request_id, JSON-RPC ID o archivo ready.

Canal IPC privado, heredado/local, acotado y separado de EventFile; no endpoint nuevo. Mensajes de control tipados de tamaño máximo candidato 4096 bytes; snapshots compactos como máximo de cuatro trabajos, secuencia y generación. Emisión no bloqueante; backlog fijo/snapshot reemplazable, no cola creciente. Pérdida, inconsistencia o antigüedad de control no se interpreta como capacidad libre.

Metadatos mínimos: generación, id interno de admisión, instantes monotónicos de admisión/inicio/entrada-salida de proveedor/fin callable/fin future/liberación, número de lecturas, estado del gate, heartbeat y ocupación. El supervisor NO recibe assertions, scopes, objetos ASGI, argumentos, documentos, resultados ni objetos completos de auditoría. La auditoría canónica de actor/tenant sigue donde corresponde, no se convierte en un registro de autoridad del supervisor.

El heartbeat debe ejecutarse en el event loop. Un thread auxiliar que sigue latiendo mientras el loop está detenido no prueba salud. El supervisor tiene su propio reloj y progreso; se comprueba explícitamente el mismo dominio monotónico local. Bloqueo total del host queda fuera de la cota.

### 7.2 Detección y cierre de admisión

- Muestreo/heartbeat candidatos cada 250 ms; heartbeat sin avance durante 2 s dispara fallo de control/liveness.
- Edad máxima candidata de un trabajo todavía vivo: 5 s desde admisión, incluso si tiene progreso parcial. El progreso sirve para explicar dónde se atasca, no renueva eternamente ese límite.
- Un trabajo que sobrevivió al awaiter produce DEGRADED, no automáticamente reinicio. Si termina antes del umbral, recupera capacidad y puede volver a READY.
- Cuatro ocupados durante una concurrencia sana no bastan para reiniciar. Hacen falta edad fuera de presupuesto, falta de loop/control o crash.
- DENY, firma inválida, disconnect y error de proveedor finalizado no incrementan contador de recuperación.
- Gate debe comprobarse de forma atómica con reserva/submit, y también al empezar el worker si hubo carrera/espera técnica. No ejecutar después del cierre trabajo que sólo estaba prevalidado.
- Cierre requiere ACK con generación y secuencia. Sin ACK en 250 ms no afirmar que el gate quedó aplicado: pasar a retirada forzada, sin drenaje confiado.
- Lease privada de admisión candidata de 1 s, renovada cada 250 ms, comprobada al admitir/empezar trabajo. Cada mensaje lleva lease_until monotónico ABSOLUTO emitido por el supervisor, generación y secuencia; nunca se calcula como recepción + 1 s. Ignorar generación ajena, mensajes repetidos/fuera de orden o ya expirados. Al iniciar recuperación el supervisor deja de renovarla. Si el loop se reanuda antes de morir, una lease vencida no permite trabajo nuevo. Una renovación no deshace un cierre ya confirmado: reabrir exige transición explícita del supervisor y condiciones de READY.
- Si no hubo ACK y la última lease aún está vigente, no se acredita cierre: puede existir admisión residual hasta su expiración absoluta o muerte. No prolongarla con mensajes atrasados ni ocultar solicitudes admitidas en esa ventana. Al verificar expiración se cierra el guard; para el supervisor la garantía firme requiere ACK, evidencia de guard cerrado o muerte, no sólo haber enviado una orden.
- No reiniciar lease/edad desde datos MCP. La lease limita admisión, no autoriza negocio ni cancela trabajos vivos.

No se garantiza inmunidad DoS: inválidos que saturan CPU pueden causar una indisponibilidad real. La prueba exigida usa carga inválida acotada; una clave fixture pública no protege un servicio productivo.

### 7.3 Estados y comportamiento

| Estado | initialize / list | tools/call | Capacidad y trabajo vivo | Transición/evidencia; cliente y operador |
| --- | --- | --- | --- | --- |
| STARTING | Público cerrado; no readiness por initialize | Público cerrado; sólo sonda interna explícita | Pool nuevo 4, no disponible al público; una sonda como máximo | Integridad, aislamiento, listener, heartbeat, contexto y sonda; fallo → UNAVAILABLE |
| READY | initialize permitido; list verifica y consume slot real | Verificación normal y permiso libre; quinto rechaza | 4 menos trabajos vivos; no cola por recuperación | Fin real devuelve permiso. Trabajo huérfano conocido → DEGRADED |
| DEGRADED | initialize puede responder; list sólo con slot | Admitir sólo permisos reales restantes | No fingir cuatro libres; 0 libres implica busy | Fin antes del umbral → READY; edad/heartbeat/crash → recuperación |
| DRAINING | Nuevos initialize/list rechazados por gate | Ningún submit nuevo, aunque conexión previa | Sólo admitidos antes del ACK; drenaje hasta 1 s | ACK de cierre obligatorio; operador ve trabajos vivos. Fin de gracia → RECOVERING |
| RECOVERING | Cerrado/ausente cuando el corte está acreditado; sin ACK puede existir ventana de lease | Sin nuevos trabajos DESPUÉS del corte; antes, admisión residual explícita y contabilizada; nunca reencolar/repetir | Capacidad pública no acreditada mientras cierre sea incierto; 0 durante retirada/reemplazo | Terminar/reap; luego STARTING de reemplazo dentro del mismo intento |
| UNAVAILABLE | Rechazo si queda loop responsivo; si no, error/timeout local | No nueva ejecución | Puede quedar hijo antiguo no recolectado: declararlo explícitamente | Fallo de recuperación, límite o host. Sólo intervención local explícita rearma |

Ante loop detenido no se anuncia DRAINING sin ACK: se permanece en RECOVERING con gate no confirmado hasta acreditar su cierre o la muerte. El estado del supervisor por sí solo no revoca una lease en el hijo. No se promete respuesta rápida del proceso que no avanza. Se mantiene URL loopback aprobada; no redirects ni segundo frontal.

### 7.4 Readiness sin bypass ni circularidad

El gate público permanece cerrado mientras se acredita negocio interno. Se propone **una sonda privada ASGI en memoria**, por comando de control del supervisor, procesada por el mismo boundary, SDK MCP, Adapter, Policy, Executor y proveedor fixture.

- La excepción al gate es un objeto/ámbito local de control que no puede derivarse de headers, _meta, argumentos, sesión o IDs. Ningún JSON-RPC público puede pedir ese modo.
- Sobre L1 nuevo, firmado con fixture y actor/tenant ficticios autorizados explícitos; sin heredar contexto del trabajo perdido. No hay scope de usuario por defecto.
- Una ejecución READ_ONLY, xoc.tickets.list, sin fault injection ni retry; comprobar contenido canónico esperado, binding y fin real del slot.
- Integridad instalada, fake wiring y guardas preceden a la sonda; listener realmente ligado y heartbeat progresando también son condiciones de READY.
- Fallo/timeout de la sonda → UNAVAILABLE, sin segunda sonda automática.
- La sonda interna acredita capacidad de negocio, **NO acredita por sí sola el transporte TCP externo**. Al pasar a READY, el arnés debe iniciar una consulta independiente por loopback real y completarla antes de declarar servicio restaurado. No se oculta esta diferencia con el ready file.
- Si esa consulta pública de aceptación falla, el arnés registra FAIL y solicita al supervisor cierre/retirada y transición real a UNAVAILABLE; debe comprobarla, no sólo cambiar una etiqueta del informe. Sin nueva sonda, retry ni reemplazo automático para buscar un éxito.

Esto evita introducir un transporte TCP especial, un header secreto o modificaciones privadas del SDK. **PENDIENTE DE VALIDACIÓN:** compatibilidad del cliente de sonda en memoria con las dependencias fijadas y ausencia de un camino público para activar la excepción. Debe fallar cerrado si no puede implementarse con APIs públicas; no saltarse el gateway para lograr un ready.

### 7.5 Retirada, reinicio y límite

1. Registrar razón, generación e intento ANTES de actuar. Capturar metadatos limitados, no dumps.
2. Cerrar admisión con ACK o registrar fallo de control; detener renovación de lease.
3. Si hay ACK, drenar hasta 1 s; no prolongar por una página de progreso.
4. Solicitar terminación del hijo exacto; tras plazo, kill del mismo hijo si procede. Nunca matar procesos por un PID suelto o comando global.
5. Exigir wait/reap y ausencia de esa generación/hijos propios. Con kill no afirmar que ejecutaron finally Python. Permisos antiguos se retiran con su generación, no se reutilizan.
6. Sólo después iniciar un nuevo hijo. No coexistencia para disimular el fallo; si no se acredita retirada, UNAVAILABLE sin más spawn.
7. Comprobar startup y sonda interna; abrir gate. Consulta nueva de red obligatoria para aceptación de restauración.
8. Máximo **dos intentos automáticos por ejecución del supervisor**. Cada intento cuenta aunque falle; fallo de control de cierre se puede resolver por terminación, pero fallo de reap/startup/integridad/sonda termina el intento en UNAVAILABLE, sin cascada.
9. Un segundo incidente tras una recuperación exitosa puede consumir el segundo intento; tercero → UNAVAILABLE y retirada acotada del hijo, sin tercero nuevo. No rearme por una sonda sana ni ventana móvil que permita reinicios eternos.
10. Muerte del supervisor no crea otro supervisor automáticamente. Hijo cierra admisión por lease cuando puede; restauración exige intervención local explícita.

## 8. Costos, riesgos y límites

**OBSERVADO EN L4:** RSS del servidor principal pasó de 105.824.256 a 111.194.112 bytes. En cinco rondas de 40 consultas: 36 allowed + cuatro errores de proveedor esperados por ronda; RSS final de rondas 109.576.192 / 110.387.200 / 110.927.872 / 111.194.112 / 111.194.112; fd 8, threads OS 4, pool activo y pendientes 0 al cierre de cada ronda. Cgroup de cierre del proceso principal: 157.765.632 bytes; no es el mismo instante que cierre de ronda 5 (153.309.184). No hubo OOM observado.

Weak scopes por ronda 12/44/13/10/15 y pico 55 no prueban liberación instantánea ni ausencia de fugas de largo plazo. Fds no son conteo de TCP. Ticks CPU acumulados tampoco son porcentaje o precio.

**ESTIMADO / SUPUESTO:** B añade un supervisor, dos extremos IPC, snapshots y CPU de comprobación cada 250 ms. No se asigna una cifra de MiB a ese costo sin medir. Mantener como máximo un servidor vivo evita duplicar su RSS por recuperación, pero la importación del supervisor/cliente y picos de startup siguen pendientes. La terminación/reinicialización sólo se paga en incidentes y startup; conservar la latencia caliente es una hipótesis.

Se mantienen **512 MiB, 2 CPU y 128 pids para todo el experimento**, incluyendo arnés/supervisor/servidor, no por proceso. Como máximo cuatro workers de ejecución; no autorizar ocho para ocultar cuatro bloqueos. Si no cabe, STOP y nueva decisión, no ampliar recursos.

| Riesgo | Nivel / respuesta |
| --- | --- |
| Pérdida de consultas sanas por recuperación global | HIGH, conocido e inherente; requiere aceptar interrupción antes de L6 |
| Host/kernel impide reap o supervisor sin CPU | HIGH, fuera de garantía de tiempo; no crear reemplazo ni declarar recuperación |
| Nueva excepción de admisión para sonda mal delimitada | HIGH si falsificable; tests negativos obligatorios; no autoridad alternativa |
| Observer bloqueante / contabilidad incompleta | MEDIUM; L6 debe medir fin real y desacoplar control de logs |
| Falsos positivos de edad/heartbeat bajo carga | MEDIUM; objetivos candidatos y carga acotada, sin SLA |
| Capacidad/overhead del supervisor desconocidos | MEDIUM; comparación instalada dentro del mismo presupuesto |
| Auth fixture confundida con producción | HIGH si se expone; LAB_ONLY y sin integración Chat/cloud |
| Perder resultado después de ejecutar una lectura | Residual explícito; resultado incierto, nunca retry automático |

No hay precios, estimación por usuario, réplicas productivas, alojamiento, HA ni garantía de no interrupción.

## 9. Datos todavía no acreditados

**PENDIENTE DE VALIDACIÓN:**

- Recuperación extremo a extremo con detección, retirada, nueva generación y consulta nueva de red en la misma ejecución.
- Overhead y picos del supervisor, IPC, lease y doble verificación de readiness/restauración.
- Drenaje/corte concurrente, contabilidad hasta retorno real, fallo del observer y carreras de cierre/submit.
- Compatibilidad de sonda privada con APIs públicas del SDK/HTTPX y su inaccesibilidad desde MCP público.
- Reap bajo fallos reales de OS, presión extrema o host detenido; fuera de garantía aunque el ensayo local pase.
- Provider real y su configuración efectiva; no depende de credenciales para terminar este diseño.
- Autenticación productiva, D.2 y exposición multiusuario; no resueltos aquí.

Todos los informes solicitados L4 estaban disponibles. Las dependencias/fuentes canónicas revisadas son las fijadas de laboratorio, no una afirmación sobre PROD. No se necesita repo de VICTOR ni su identidad para L5.

## 10. Único bloque posterior propuesto: L6 LOCAL

**Encargo para aprobación; NO ejecutado: supervisor mínimo de una instancia persistente, con gate y prueba de restauración.**

Objetivo: demostrar el circuito observado pendiente con B, manteniendo resultados/Policy/Executor/core, cuatro slots y recursos. Se recomienda candidato aditivo, para poder ejecutar y comparar L4 intacto:

| Archivos nuevos previstos dentro de XOC-MCP-LAB | Cambio futuro acotado |
| --- | --- |
| xoc_mcp_lab_l6/supervisor.py y control.py | Máquina de estados, IPC/lease, contador finito, ownership/reap |
| xoc_mcp_lab_l6/pool.py y server.py | Adaptación focalizada del lifecycle L4: gate atómico, edades/fin real, heartbeat del loop y sonda privada |
| xoc_mcp_lab_l6/probe.py y harness.py | Sonda ASGI autorizada y nueva consulta real; fallos sólo del arnés; medidas completas |
| xoc_mcp_lab_l6/integrity.py y metadata del paquete | Verificar nuevo candidato, reutilizando íntegros C1/L4 instalados |
| tests/test_l6_recovery.py, test_l6_context.py, test_l6_lifecycle.py | Matriz del contrato y regresiones, sin editar tests históricos |
| packaging/l6_build.py, Dockerfile.l6, scripts/l6.ps1 | Artefacto nuevo reproducible/offline y runner propio si se aprueba la ejecución Linux |
| reports/l6-* y l6-runs/<run-id-nuevo>/ | Evidencia nueva, sin sobrescribir L1–L5 |

Los nombres son propuesta de alcance, no archivos creados. Reusar boundary/client/scopes/fixture de L4 donde sea posible; no duplicar contratos canónicos ni editar el SDK. La adaptación debe eliminar cualquier I/O bloqueante del camino que ya declaró un slot libre, y mantener el cómputo de unidades pendientes hasta su salida efectiva. No hacer refactor general.

Dependencias adicionales previstas: **ninguna**; stdlib + SDK/HTTPX/ASGI fijados. Si se necesitara una, STOP y autorización. Tampoco incorporar timeout real de proveedor a helpers canónicos en este bloque.

No tocar repos API/Mobile/Web/runtime/proxy, Chat, Voice, VICTOR, SQL/RDS, cloud, identidad productiva, llaves, MCP tools nuevas, releases, Git ni benchmarks históricos.

Validación futura: casos concretos del contrato; suites históricas junto al candidato instalado; misma población caliente 80, concurrencia cuatro, cinco arranques y rondas de estabilidad; añadir incidentes medidos completos en poblaciones separadas. No ocultar timeout, rechazo o pérdida detrás del promedio exitoso.

Aceptar B sólo si se verifica aislamiento/una ejecución por solicitud, cierre/reap real, nueva consulta autorizada, límite finito de recuperaciones, recursos y umbrales aprobados. Si falla: NO-GO, conservar evidencia y explicar qué criterio fue inviable. **No implementar C, integrar Chat ni pasar a cloud automáticamente.**

## 11. Respuestas finales A–P

A. Bloquea la recuperación actual el trabajo síncrono no cooperativo, que conserva permisos después del timeout, sin supervisor ni reinicio comprobado.

B. Un timeout efectivo de proveedor limita ciertas esperas cooperativas; no mata threads, ni cubre por sí solo páginas/retries/CPU/filesystem/DNS/kernel o tiempo total.

C. Recomendada B: recuperación supervisada de instancia persistente completa.

D. Unidad recuperada: el proceso servidor y todo su pool/contextos; no una sola consulta. Supervisor queda fuera de esa unidad.

E. Una consulta sana puede terminar durante drenaje o perderse al cortar; no se reejecuta.

F. Detecta un supervisor en proceso independiente mediante heartbeat del loop, edad/estado real de trabajos y estado del hijo.

G. Prueba capacidad nueva: retirada/reap anterior, startup íntegro, sonda autorizada con slot realmente devuelto y NUEVA consulta correcta por loopback real.

H. Observados: 26,339 ms mediana caliente, 0,666309 s retirada bloqueada y 2,193653 s arranque en poblaciones distintas. Propuestos: detección ≤5,25 s de trabajo / ≤2,25 s de loop, recuperación ≤12 s y restauración ≤21 s bajo el contrato. No son RTO medidos.

I. Sí: conservar ventaja caliente es hipótesis a validar comparativamente, no resultado atribuido a L5.

J. Scope verificado por solicitud; generación/id interno sólo correlación; sin registro de autoridad por IDs; eventos viejos y resultados tardíos no completan solicitudes nuevas.

K. Ningún supervisor, cliente o health probe reproduce la consulta perdida. Las sondas y consultas posteriores son solicitudes nuevas expresamente definidas y autorizadas.

L. Se debe aprobar interrupción de toda esa instancia, posible pérdida de consultas sanas de todos sus tenants y ventana candidata de restauración; no sólo aceptar un timeout de A.

M. Único siguiente bloque: L6 LOCAL, supervisor/gate/telemetría/sondas y aceptación de B, sin integración.

N. ¿Se modificó código, tests, dependencias o infraestructura? **NO.** Sólo los dos documentos L5.

O. ¿Se involucró VICTOR? **NO.**

P. ¿L5 autoriza Chat, cloud o producción? **NO.**

## 12. Preservación y cierre documental

La comprobación de cierre y manifiestos antes/después están incorporados al contrato de aceptación (§8). No se creó un tercer informe ni un script. Se conservaron los cambios ajenos preexistentes; no se reparó el .git padre, ni se hizo stage/commit/push/pull/stash/reset/clean.

**No se activó recuperación. La aprobación pendiente es del bloque L6 y su impacto declarado, no de una acción ya realizada.**
