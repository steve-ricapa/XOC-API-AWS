# AC-2R: corrección focalizada del 12 de octubre de 2026

## Evidencia y límites del diagnóstico

Chat respondió HTTP 200 pero devolvió `agentcore_unavailable`. La invocación reciente registró 2874 ms en el cliente, antes de terminar el target. El target autorizó `xoc.tickets.list` y después registró `ValidationError`. Los argumentos no se loguean; un filtro status inválido es la explicación compatible con el código, no un valor concreto demostrado. No se atribuyó el fallo a permisos del usuario ni se ampliaron roles.

## Cambios

- Release aislado Chat, commit `4e8286b`: el timeout HTTP usa el presupuesto restante en lugar de cortarlo por la mitad. Conserva el tope AgentCore 5 s, límite gobernado 18 s, Lambda 20 s y TTL 5 s. Sin reintentos ni fallback.
- Los fallos técnicos se proyectan como `unavailable`, no como una denegación de autorización. Se conserva el código técnico seguro en metadata.
- SOPHIA-only, commit `b4f7520`: filtro status opcional con enum de estados de tickets existentes; instrucciones explícitas para omitirlo al listar todos y no atribuir un fallo técnico a falta de permisos.
- No se cambió el target, IAM, datos, Voice, VICTOR, Step Functions ni XOC-MCP.

## Pruebas

- Chat: 111 pruebas aprobadas, incluidas C.3, contexto, policy, threads y nuevas regresiones de presupuesto y semántica.
- Runtime: 42 pruebas aprobadas, una omitida, en `tests/test_gateway_runtime.py`.
- Inspección de CloudFormation: cambia únicamente Code y versión Lambda; sin diferencias de environment o IAM.
- El paquete conserva dependencias y fuentes ajenas byte a byte. Evidencia en `tests/ic1/.build/ac2r/repair-20261012`.

## Publicación

Azure: build remoto Python 3.12 exitoso, deployment `20622e2a-70c4-472c-927f-24ddf7dd922a`, host Running. Se publicó sólo el directorio allowlisted SOPHIA-only.

AWS: el primer intento falló localmente con ENOENT `.serverless/serverless-state.json`, antes de actualizar el stack. El helper restaura el estado generado del paquete seleccionado para Serverless 3; no se debe empaquetar concurrentemente en el mismo worktree durante un deploy. Reintento exitoso, 47 s; Lambda Active/Successful, SHA `VKixlT8FOnKMHQ44upO2EIPeeaQ6dEltWnbzT5zAy70=`.

## Verificación posterior y parada segura

El smoke directo Azure pasó saludo, continuidad y solicitud de tickets, pero el segundo turno recibió HTTP 401 `invalid_capability`. Una repetición recibió ese mismo error en el saludo. Una capability de servicio enviada después de esperar dos segundos sí recibió HTTP 200 con texto. Esto sugiere sensibilidad temporal en el smoke local, pero no acredita por sí solo la causa ni el estado del circuito AWS→Azure. No se cambió reloj, TTL, iat ni tolerancia de autenticación. No se fabricó una identidad humana.

Se inició retorno a `disabled`, conservando el código corregido, por no estar acreditada la verificación posterior. Las correcciones están implementadas y desplegadas; no hay PASS end-to-end ni reactivación autorizada por resultados completos. Hace falta comprobar Chat normal con la sesión humana legítima y resolver/acreditar esta intermitencia antes de continuar las herramientas.

Retorno confirmado: Serverless exitoso en 53 s; lectura Lambda `SOPHIA_TOOL_EXECUTION=disabled`, `LastUpdateStatus=Successful`, mismo SHA corregido. Esto acredita el rollback de configuración, no el smoke funcional humano posterior, todavía pendiente.

No hay push. La corrección no equivale a PASS end-to-end: falta repetir Chat normal y tickets.list con la sesión legítima, luego el resto de herramientas y el rollback funcional acordado. No se fabricaron JWT humanos ni se reutilizó el confirmation_token expuesto en una captura.

Comandos preparados, desde el backend principal:

```powershell
python -B tests/ac1/chat_ac2r_hotfix.py inspect --mode agentcore
python -B tests/ac1/chat_ac2r_hotfix.py deploy --mode agentcore
# Retorno a disabled con la corrección conservada, sólo cuando corresponda:
python -B tests/ac1/chat_ac2r_hotfix.py inspect --mode disabled
python -B tests/ac1/chat_ac2r_hotfix.py deploy --mode disabled
```

## Diagnostico y transporte gobernado (segunda intervencion, 12 octubre)

- STS revalidado con `xoc-dev`, cuenta `811776156524`. Chat permanece `disabled`, Active/Successful y conserva el SHA indicado arriba. No se desplego Chat ni se cambiaron IAM, TTL o limites de tiempo en esta intervencion.
- Reproduccion local: una capability de servicio recibio 401 al enviarse inmediatamente y 400 `invalid_protocol` al reenviar **el mismo token** tres segundos despues con un mensaje vacio (rechazo de protocolo posterior a autenticacion, sin ejecucion de Foundry). El diagnostico interno de Azure confirmo `not_yet_valid`. No prueba un desfase entre AWS y Azure: acredita el problema del emisor local.
- Runtime `dbc1298`: categorias internas de rechazo de capability, sin tokens, claims ni texto de excepciones; respuesta publica sin cambios. Despliegue aislado `0a2b90b0-0303-427c-b1e5-5e3390fa0d28`.
- Runtime `36996f6`: conexiones HTTP reutilizadas dentro de un turno gobernado, cerradas al terminar; no sesion global ni contexto compartido entre usuarios. Cliente legacy intacto. Sin reintentos, redirects o incremento de timeouts. Despliegue aislado `f835341b-c641-484d-8c53-9fe96a035801`, build remoto satisfactorio y host Running.
- Suite runtime: 225 PASS, 12 omitidos, 8 de VICTOR deseleccionados. Regresiones Chat/C.3: 111 PASS. Docker Linux local no estaba disponible; el paquete se construyo en Azure y su smoke posterior paso.
- `aged-capability-diagnostic.json`: smoke Azure PASS con saludo, continuidad, tool request y segundo turno para allowed/denied/unavailable. Usa fixtures vacios y demora diagnostica de 3 s **antes** de cada envio; no se instalo esa demora en produccion ni se modificaron relojes o validaciones. Los tiempos HTTP registrados excluyen esa demora, por lo que no acreditan el presupuesto end-to-end de Chat.
- `gateway-aged-fixture.json`: tickets.list, documents.list y notifications.unread_count reales devolvieron allowed y audit, usando un tenant/usuario de servicio de prueba sin datos y una demora diagnostica de assertion de 2 s. No es una prueba de usuarios reales ni del rol IAM de Chat. El diagnostico reproducible es `tests/ac1/ac2r_gateway_diagnostic.py`; nunca guarda credenciales o filas devueltas.
- Evidencias JSON en `tests/ic1/.build/ac2r/repair-20261012/`, ignoradas por Git. No confundir estos smokes por componentes con PASS integral. Falta la peticion autenticada desde Postman para acreditar AWS -> Azure sin el reloj local, y despues una activacion controlada y smoke real de herramientas. No se fabricaron JWT humanos.
- Voice, VICTOR, Step Functions, Web, Mobile y autorizacion de datos permanecieron fuera de cambios. No push en esta intervencion.
- `pooled-runtime-smoke.json`: PASS de los nueve pasos contra la version nueva. Segundos turnos allowed/denied/unavailable: 6535/4879/5333 ms frente a 8634/7298/6788 ms en la muestra anterior. Son muestras individuales, no p95 ni garantia de latencia/cold start. La misma demora diagnostica local de 3 s aplica; no se incorpora a Chat.

## Reactivacion autorizada para prueba (12 octubre, 23:22 UTC)

El usuario aporto una prueba autenticada de Chat, sesion 69: HTTP 200, solicitud de tickets con `tool_transport_unavailable`, respuesta natural de indisponibilidad y sin `runtime_unavailable`. Esto acredita el segundo turno AWS -> Azure con herramientas desactivadas, no la lectura de tickets. Luego autorizo explicitamente activar para probar.

Se ejecuto `chat_ac2r_hotfix.py deploy --mode agentcore` con `xoc-dev`, despues de inspeccionar el paquete. Serverless termino correctamente en 52 s; CloudFormation `UPDATE_COMPLETE`, Lambda `Active`/`Successful`, `SOPHIA_TOOL_EXECUTION=agentcore`. SHA conservado: `VKixlT8FOnKMHQ44upO2EIPeeaQ6dEltWnbzT5zAy70=`. Unica diferencia de environment: selector de transporte. Sin cambios IAM o de codigo Voice/VICTOR.

Este es el estado vigente y reemplaza las menciones anteriores a `disabled`. Pendiente repetir la consulta de tickets autenticada y comprobar resultado autorizado/auditoria; aun no se declara PASS integral. Rollback preparado: el mismo helper con `--mode disabled`.

## Validacion posterior: consultas reales y regresion de continuidad

Revision del 12 de octubre, hora local (13 de octubre UTC). No cambios de logica productiva, deploy, push o desactivacion en esta revision. Se conserva la activacion autorizada.

### Evidencia humana y correlacion AWS

Las capturas muestran HTTP 200, `allowed`, auditId y respuestas que el usuario contrasto con sus datos. Se localizaron los cuatro auditId en CloudWatch del target, con decision `allowed`:

| Tool | auditId | Transporte AgentCore observado | Duracion Chat Lambda |
| --- | --- | --- | --- |
| xoc.tickets.list | d8d505a84e80c20e8d2be0f3 | 4303 ms | 18959.21 ms |
| xoc.documents.list | 8c9d6544cea6a083646676b5 | 719 ms | 11633.46 ms |
| xoc.notifications.unread_count | 2bda0497c46b6bdef1d640ce | 656 ms | 12272.34 ms |
| xoc.tickets.get | 7fde04bf549dbf61358a52ef | 4355 ms | 19132.93 ms |

Las duraciones se asociaron por proximidad temporal en los logs; la identidad de tool/decision/request_id si se verifico mediante auditId. No existe aun un desglose distribuido completo por request_id de todos los segmentos. La lectura se pagino hasta agotar nextToken en la ventana revisada (34 eventos target, 49 Chat), sin imprimir conversaciones ni credenciales.

Los casos list/get tuvieron Init Duration de aproximadamente 2 s en target y 2.2 s en Chat. Init Duration se informa por separado de Duration: no se debe sumar dos veces ni atribuir el tiempo total de Postman exclusivamente a MCP. Las ejecuciones Chat de ~19 s dejan menos de 1.1 s respecto al timeout de 20 s. Riesgo de estabilidad pendiente; no es evidencia de perdida de permisos.

Application Insights, muestra de 1 h de `sophia_run`: 24 requests, cero fallos, mediana 4454.0595 ms, maximo 5975.6691 ms. Es una muestra de telemetria, no una garantia de disponibilidad ni prueba de carga. No demuestra por si sola la correccion semantica de la respuesta.

### Pruebas automaticas ejecutadas

- Release Chat `4e8286b`: 116 PASS en phase_d, ticket_confirmations, access_hardening, policy, executor, chat_tool_gateway_integration, thread_operations, ac2r_isolation y failure_semantics.
- Backend feature (contratos AgentCore/IaC/perfil): 39 PASS. La primera invocacion aislada tuvo 1 fallo y 2 errores por faltar `TICKETS_TABLE_NAME` en el entorno de pruebas; al declarar `offline-fixture-tickets`, paso sin cambios de codigo. HTTP/KMS/DynamoDB simulados en esta suite.
- Runtime `36996f6`: 225 PASS, 12 omitidos y 8 de VICTOR deseleccionados.
- Nuevas regresiones en `XOC-API-AWS-CHAT-AC2R/tests/test_chat_continuity_intent_regression.py`: 1 PASS y 2 FAIL, reproducibles offline.
- Peticiones reales sin Authorization: `POST /chat` y `POST /chat/tickets/confirm` devolvieron 401. No se reutilizo el confirmation_token mostrado en la captura, ni se confirmo/creo un ticket real.

Las suites cubren roles, delegacion, SUPERADMIN bloqueado, tools desconocidas/deshabilitadas, inyeccion de ownership, datos de otro tenant descartados, limites temporales, ausencia de fallback y segunda herramienta bloqueada. Esto NO sustituye una prueba autenticada entre dos tenants reales; no se dispuso de esas dos sesiones.

### Hallazgo que impide declarar cierre completo

La segunda captura NO aprueba continuidad: a `Que ticket acabamos de revisar? Responde sin consultar herramientas.` el backend devuelve una propuesta generica NEEDS_CONFIRMATION, no el recuerdo del ticket.

Causa reproducida en el release: `_detect_ticket_creation_intent` acepta cualquier substring de `_TICKET_CREATION_TRIGGERS`, incluyendo `revisar`; `_maybe_create_ticket_from_intent` reemplaza incluso una respuesta de continuidad correcta por una propuesta. Las dos regresiones verifican tanto el falso positivo del detector como la sustitucion de una respuesta correcta. La prueba de creacion explicita sigue pasando.

Clasificacion: bug funcional de Chat, no del transporte MCP. No acredita perdida de memoria del runtime; el postprocesado puede sobrescribirla. La propuesta requiere confirmacion humana y no demuestra auto-creacion. No se corrigio incidentalmente durante una solicitud de pruebas.

Siguiente cambio recomendado: acotar la intencion explicita de crear/proponer ticket, preservar consultas/recuerdo/negaciones y cubrirlas con tests; conservar intacta la confirmacion C.3. Despues revisar el presupuesto de latencia y completar prueba real multi-tenant. Estado: consultas READ_ONLY reales verificadas; cierre integral NO-GO por continuidad y validacion de estabilidad pendiente.

## Correccion local autorizada del detector y mediciones

El detector de respaldo ya no interpreta palabras de incidentes, `revisar` o `analizar` como solicitudes de creacion. Ahora exige una peticion explicita al inicio (crear/abrir/generar/proponer un ticket, con variantes de cortesia en espanol e ingles) y rechaza cancelaciones explicitas. Es deliberadamente conservador, no un clasificador linguistico exhaustivo. El procesamiento de action_plan del modelo y la confirmacion C.3 no se modificaron.

Cambio aplicado tanto al backend feature como al worktree release aislado; se elimino la antigua lista de triggers. La regresion de continuidad pasa, junto con consultas, historia, negaciones, hipoteticos y solicitudes de propuesta/creacion. Suites: 155 pruebas backend feature y 122 release aislado, todas PASS. Son conjuntos parcialmente solapados, no 277 pruebas independientes.

Para latencia se agrego telemetria de `first_turn` y `second_turn` con request_id generado por el servidor y duracion monotonic; sin cuerpos, tokens, identidad, URLs o mensajes de excepcion. Se mide incluso si hay timeout. No se modificaron timeouts, IAM, almacenamiento, eventos, Step Functions, Voice, VICTOR o infraestructura de precalentamiento.

Estado de este ajuste: LOCAL, sin deploy ni push. La reduccion de latencia NO se declara conseguida. Proximo paso operativo: empaquetar el nuevo chat.py sobre el artefacto aislado vigente, revisar el diff CloudFormation y desplegar de forma controlada; luego repetir continuidad y consultas, medir turnos y arranque en frio antes de decidir una optimizacion adicional o costos de precalentamiento. No reutilizar el antiguo chat-isolated.zip esperando que incluya esta correccion.

## Despliegue autorizado de continuidad — 13 octubre UTC

El estado LOCAL anterior queda superado por este despliegue autorizado. Identidad STS revalidada con xoc-dev, cuenta 811776156524. Commit release aislado: cc3c7f4; equivalente backend feature: 6306dee. Sin push.

Helper: tests/ac1/chat_continuity_release.py. Artefactos en tests/ic1/.build/ac2r/continuity-20261013. Se acredito SHA del paquete anterior contra Lambda y se verifico que unicamente src/handlers/routes/chat.py difiere entre ZIPs. Dependencias y restantes archivos conservados byte a byte. CloudFormation: solo Code y version Lambda; environment e IAM sin cambios. Los paquetes anteriores permanecen disponibles para rollback; los helpers anteriores exigen su SHA original, por lo que un rollback debe validar el nuevo SHA antes de reutilizar el paquete previo.

Se repitieron las 122 pruebas del release: PASS. Serverless deploy completo en 47 s; stack xoc-api-chat-prod UPDATE_COMPLETE. Lambda Active/Successful, SOPHIA_TOOL_EXECUTION=agentcore, timeout 20 s conservado. SHA nuevo: dgIc5H3/YFjgdwruzeDWb4xWMnb6XkAfen0UNvOuH4I=.

Pendiente smoke autenticado del usuario: consultar un ticket y, en la misma session_id, preguntar que ticket se acaba de revisar sin herramientas. Debe conservar la respuesta y no generar ticket_proposal. No se declara mejora de velocidad ni cierre integral hasta medir first_turn/second_turn y repetir regresiones reales. Sin cambios a tickets almacenados, eventos, Step Functions, Voice, VICTOR, Mobile o Web.

## AC-2R Closure — evidencia 2026-10-13 01:58 UTC

Continuidad real posterior al deploy: PASS. El usuario envio la pregunta de recuerdo con session_id 75; HTTP 200 en 12.99 s, ticket correcto y ausencia de ticket_proposal. No demuestra por si sola que toda la matriz este cerrada.

Observacion read-only con xoc-dev y STS revalidado. Chat mantiene SHA dgIc5H3/YFjgdwruzeDWb4xWMnb6XkAfen0UNvOuH4I=, Active/Successful, agentcore, timeout 20 s. Target Active/Successful, timeout 5 s. Script reproducible: python -B tests/ac1/closure_observe.py. Pagina CloudWatch y muestra solo agregados, no mensajes originales.

Ventana mixta de 24 h (incluye versiones anteriores, rechazos y diferentes tipos de llamadas; NO constituye benchmark de consultas autenticadas):

- Chat: 108 eventos, 29 REPORT; Duration min 12 ms, mediana 5756.62 ms, max 19835.27 ms. Margen del maximo contra timeout: 164.73 ms.
- Chat Init: 12 muestras, mediana 2266.905 ms, max 2962.64 ms.
- first_turn nuevo: una muestra, 7516 ms. Ninguna muestra second_turn todavia.
- AgentCore: 7 muestras, mediana 3487 ms, max 4355 ms.
- Target: 85 eventos, 16 REPORT; mediana 173.335 ms, max 1099.68 ms. Init 6 muestras, mediana 1922.55 ms, max 2013.3 ms.
- Cero mensajes con el patron Task timed out after; no equivale a cero errores de aplicacion. No se calcularon percentiles de servicio ni correlacion distribuida a partir de esta mezcla.

Regresiones release repetidas: 122 PASS. Sin cambios de arquitectura ni codigo productivo en este bloque.

Operacion: se empaqueto chat-disabled dentro de continuity-20261013 con el MISMO ZIP corregido; inspect PASS. Unica diferencia de environment SOPHIA_TOOL_EXECUTION. Code cambia de referencia S3 pero conserva hash de bytes. No cambia IAM. NO se desplego: rollback/reactivacion real pendientes de ventana coordinada con smoke humano. No volver al ZIP anterior salvo regresion de codigo: reintroduciria el detector defectuoso.

Runbook de ensayo: revalidar STS y hash vivo; seleccionar OUT continuity-20261013 en chat_ac2r_release; inspeccionar disabled; copiar su serverless-state.json al directorio .serverless del worktree release; desplegar paquete disabled con helper y comprobar UPDATE_COMPLETE/hash/modo; probar chat normal y fallo seguro sin fallback; restaurar el estado del paquete chat-agentcore, inspeccionar y desplegar; comprobar UPDATE_COMPLETE/hash/modo y una consulta real allowed. No empaquetar en paralelo con deploy. Detenerse ante drift no explicado. La existencia del paquete NO acredita el ensayo real.

Seguridad real: usuario confirma disponibilidad de cuentas ADMIN, ADMIN_XOC y USER en distintos tenants. Pendiente ejecutar pares positivo/negativo con sesiones legitimas. Primero ADMIN A get ticket A permitido; ADMIN B get mismo ticket A sin datos; luego control positivo B get ticket B. Continuar USER segun policy real, ADMIN_XOC sin delegacion y con delegacion valida. No crear cuentas ni fabricar tokens. No declarar exito por HTTP 200: revisar metadata, contenido y auditoria. Si hay filtracion, parar pruebas y tratar como blocker de seguridad.

Estado Closure: EN CURSO / NO-GO para cierre integral por latencia sin muestra postdeploy suficiente, matriz real pendiente y ensayo operativo pendiente. AgentCore sigue activo. MCP y runtime sin cambios en este bloque; sus estados Git locales registrados fueron 1f09a72 (MCP) y 36996f6 (runtime). No afirmar push de esos repos por este cierre.

## Incidente y ajuste de presupuesto — 12 octubre, hora Ecuador

La nueva prueba humana list fallo HTTP 500 en 25.61 s. La llamada reciente de Chat registro first_turn 7390 ms, transporte AgentCore allowed 4356 ms y REPORT Duration 20000 ms / Status timeout (Init 2282.97 ms). Sin second_turn completo. No es evidencia de rechazo de policy. El observador ahora cuenta tambien Status timeout: buscar solo el texto antiguo Task timed out after omitio este formato.

Se encontro que el deadline se iniciaba despues de consultas/preparacion y no consideraba el tiempo Lambda ya consumido; el timeout escalar requests tampoco reparte un presupuesto unico entre conexion y lectura. Correccion autorizada: deadline al entrar a la ruta, limitado por aws.context.get_remaining_time_in_millis menos 3 s; tope local 17 s; timeouts connect/read repartidos en hasta 9 s o el restante; chequeo del segundo turno despues de firmar capability. Preserva respuesta controlada existente y no reejecuta herramienta. No cambia timeout Lambda, IAM, tools ni infraestructura.

Limites: timeouts requests son de conexion/inactividad de lectura, no una cancelacion dura de todo el proceso (DNS/streaming/DB tambien pueden consumir tiempo). El margen de 3 s para persistencia es una reserva, no garantia de latencia de DB. Pendiente confirmar comportamiento real; esto no acelera el modelo ni acredita rendimiento integral.

127 tests release PASS (122 previos y 5 de presupuesto). Commit release 538d797; backend 487e3de. Paquete aislado budget-20261013: solo chat.py cambia; inspect PASS con environment e IAM intactos. SHA esperado ZNk/VfQvA+3ZzO8jt+QkqyWvCFXJd+Dwb0Cxj3dUA7I=. Paquete anterior continuity-20261013 conservado. No usar el paquete disabled anterior como si incluyera esta correccion nueva; reempaquetarlo desde budget-20261013 antes del ensayo operativo.

Deploy completado en 47 s. Verificado UPDATE_COMPLETE, Active/Successful, SHA esperado coincidente, agentcore activo y timeout 20 s. POST /chat sin credenciales devuelve 401. Pendiente repetir consulta autenticada; no se declara resuelto E2E por el solo despliegue.

## Respaldo de presentacion autorizado — 12 octubre Ecuador

El usuario autorizo explicitamente mostrar los resultados reales autorizados cuando la redaccion final de SOPHIA no completa. La prueba anterior devolvio allowed + runtime_unavailable en 21.89 s: fue degradacion controlada, NO exito funcional completo.

Cambio focalizado en sophia_tool_chat.py: ante error de transporte del segundo turno o runtime_unavailable/runtime_timeout, se renderiza exclusivamente la proyeccion model_result existente con status y code allowed. No consulta stores ni APIs, no reejecuta tools, no evita AgentCore, no convierte DENY en datos. Errores de protocolo/thread y segunda herramienta no se ocultan con este resumen.

Soporta las cuatro READ_ONLY. Campos existentes permitidos, redaccion determinista identificada como resumen directo, escape HTML/Markdown, redaccion de secretos existente, maximo 20 filas y limite de longitud con aviso de resultado parcial. Conserva auditId y completionCode runtime_unavailable; agrega presentation=direct_authorized_summary para distinguir el respaldo del exito del modelo. No agrega propuestas ni confirmaciones.

132 pruebas release PASS: casos de las cuatro tools, vacio, DENY, ownership extranjero, resultado malformado, fallo de protocolo, redaccion, escape y truncamiento; una ejecucion y un intento de segundo turno. Release 321fa07; backend 5262b37. Artefacto presentation-20261013 sobre budget-20261013, unicamente sophia_tool_chat.py distinto; inspeccion Code/version solamente, sin environment/IAM.

Esto NO reduce el tiempo del modelo ni garantiza que Foundry guarde el texto del resumen en su historial: el resumen se devuelve desde XOC. La continuidad tras una respuesta degradada debe probarse por separado. No se amplio el alcance a persistir mensajes en Foundry, reintentar runs o cambiar Voice/VICTOR.

Deploy terminado en 47 s, Lambda Active/Successful, agentcore activo. Hash vivo coincide con paquete: E7LEy4rv75TgEEDB3dVlXFzoXDl2IrFDAQCEyICu9E4=. Pendiente consulta autenticada de aceptacion.

## Diagnostico posterior — 13 octubre Ecuador

Usuario valido resumen directo y casos reales: ADMIN A propio allowed; ADMIN B ticket A denied sin datos, control propio B confirmado; USER list vacio allowed y get ticket de otro tenant denied (usuario confirmo tenants distintos); ADMIN_XOC sin delegacion 403. ADMIN_XOC con delegacion no disponible/no probado. Estas son evidencias humanas, no inspeccion independiente de claims de sus tokens.

Revalidacion live: SHA presentation coincide, Active, agentcore, Lambda 20 s. API Gateway integracion rbq4x2l de esa Lambda tiene timeout 30000 ms. La misma Lambda sirve /chat, /live-voice-sessions y rutas /agents: ampliar su timeout no es un cambio exclusivamente de Chat.

Azure Function aislada, App Insights c854497d-ab9d-4dd7-be21-8ecbb68cb789: en ventana de 3 h los segundos requests temporalmente asociados terminaron 200 en 4687, 4994, 5815, 5058 y 4961 ms; hubo tambien 502 de 9526 y 5498 ms. Correlacion por proximidad temporal, NO trace distribuida completa. Los logs AWS muestran esperas second_turn de 3430, 3296, 3116, 5961, 2067, 4282 y 2581 ms, con ejecuciones terminando cerca de 16.1 s tras el guard. El timeout cliente explica varios resultados degradados aunque Azure termine 200 despues; no explica los 502 propios de Azure.

Script azure_completion_observe.py consulta categorias fijas/metricas de Functions, no contenidos de conversaciones ni credenciales. Azure CLI carece de extension application-insights; se uso REST con token CLI solo en memoria, sin instalarla. No se modifico Azure/Foundry.

Decision requerida antes de ampliar alcance: evaluar Lambda 26 s y presupuesto Chat 23 s (API ya 30 s), preservando margen y respaldo, pero reconociendo impacto en la envolvente temporal de rutas compartidas Voice/agents; alternativa separar Chat/asincronia implica arquitectura nueva. No se ejecuto ninguna de esas opciones. Aumentar el limite no optimiza latencia ni corrige los 502 del runtime; requiere smoke y costos/timeout de clientes revisados. No quitar guard ni resumir un DENY como exito.
