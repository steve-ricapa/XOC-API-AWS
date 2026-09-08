# SOPHIA — C.3: confirmación idempotente de tickets

Fecha: 2026-09-08. Implementación local, sin commit, push, deploy ni cambios AWS.

## 1. Resumen

Una propuesta firmada tiene un `proposal_id` UUID v4 generado por backend. Su primera confirmación guarda un ticket y un recibo de confirmación en una transacción DynamoDB. Un replay autorizado recupera el mismo ticket, sin crear otro ni publicar otro `ticket.created`.

La protección reside en persistencia, no en locks de proceso ni en Web/Mobile. La garantía de unicidad exige conservar el recibo; no se garantiza entrega exactamente una vez del evento. Los fallos de publicación siguen un patrón best effort, con estado observable y reconciliación operativa.

No se modificaron Voice, Function externa, MCP, AgentCore, Push/Inbox, reportes, documentos, RDS/SQL, dependencias Mobile ni infraestructura. No se crearon tickets remotos.

## 2. Git inicial y final

Los tres repositorios están en `main` y conservaron HEAD:

- Backend: `046a0f0b9a7ee7e8d0d24738b4b882859ae38408`. Inicialmente limpio; ahora contiene exclusivamente los archivos C.3 enumerados abajo.
- Web: `4a4d9191961f4e53f8ab93ae4d354fd83af1ddfb`. Sus cambios locales C.2 se conservaron.
- Mobile: `69eb65188e10c76d4951186bcc992d9f2cd36f4d`. Sus cambios locales C.2 se conservaron.

No se hizo pull ni se comprobó qué versión está desplegada. El reporte corresponde al código local.

## 3. Diseño de idempotencia

- `request_id`: trazabilidad de la solicitud, no identidad de la acción.
- `proposal_id`: UUID v4 emitido una sola vez al construir la propuesta; está en la card y firmado en el JWT.
- `ticket_id`: UUID del constructor de tickets, asociado de forma duradera al recibo ganador.
- Cada intento puede preparar un ticket candidato distinto, pero solo una transacción puede insertar el recibo para esa propuesta. Los candidatos perdedores nunca se guardan.
- Un fingerprint SHA256 vincula el recibo con tenant, actor, rol, delegación, proposal_id, asunto, descripción y prioridad. No es un hash del JWT ni guarda el token.
- Lecturas fuertemente consistentes recuperan el recibo y el ticket. Un contenido/contexto distinto para la misma identidad se rechaza con 409.
- Un ticket eliminado no se resucita: el recibo sigue existiendo y se devuelve 409.

Se revisaron los patrones de notifications/inbox, user_notification_inbox e IngestIdempotencyRecord solo como referencia. No se modificaron ni reutilizaron sus stores como ledger de tickets.

## 4. Flujo final

```text
Backend genera ticket_proposal + proposal_id
  -> firma confirmation_token (audiencia específica, 300 segundos)
  -> usuario confirma con Authorization existente
  -> backend valida firma, propósito, expiración y contexto
  -> busca recibo autorizado
     -> existe: recupera el ticket, sin escritura ni publicación
     -> no existe: transacción [recibo + ticket], ambos con condición de no existencia
        -> ganador: publica best effort y devuelve ticket
        -> conflicto: recupera al ganador o devuelve error recuperable
```

Un token expirado se rechaza antes de entrar al store, incluso si ya existe un ticket. Una solicitud que pasó la validación antes de expirar puede terminar después. El token de acceso normal se sigue validando de forma independiente.

No se introduce `delegation_session_id`: el modelo actual solo proporciona actor, rol, tenant efectivo y booleano de delegación. Una nueva delegación con el mismo contexto sigue siendo equivalente.

## 5. Archivos

Modificado:

- `src/handlers/routes/chat.py`: proposal_id, audience, claims requeridas, validación de contexto, nuevo store y flag de replay.

Nuevos:

- `src/shared/chat_ticket_confirmations.py`: transacción, recuperación consistente, publicación del creador y estado del evento.
- `tests/test_chat_ticket_confirmations.py`: 24 pruebas locales C.3.
- `docs/sophia-ticket-confirmation-c3.md`: este reporte.

`src/shared/tickets_store.py` y su constructor compartido permanecen sin cambios de contenido. Los campos C.3 se añaden al item únicamente en el nuevo recorrido. Los otros endpoints de creación no adquieren idempotencia automáticamente.

## 6. DynamoDB e infraestructura

Se utiliza `TICKETS_TABLE_NAME`, configurada actualmente como `xoc-api-tickets-<stage>-tickets`.

Ticket:

```text
pk = TICKET#<tenantId>
sk = TICKET#<ticketId>
índices existentes de tickets, sin cambios
```

Recibo:

```text
pk = SOPHIA_CONFIRMATION#<tenantId>
sk = PROPOSAL#<proposalId>
entity_type = SOPHIA_TICKET_CONFIRMATION
status = CONFIRMED
tenant_id, user_id, proposal_id, ticket_id, fingerprint, created_at
event_status = UNCONFIRMED | PUBLISHED | FAILED | UNKNOWN
```

El recibo se crea sin atributos GSI y usa otra partición, por lo que queda fuera de los índices/consultas/listados/conteos normales de tickets por diseño. Esto no significa que un scan físico de la tabla contenga solo tickets. En C.3.1 se endureció `scripts/backfill_tickets_dynamo_indexes.py`: antes podía indexar recibos por compartir atributos con tickets; ahora exige `pk = TICKET#<tenant_id>` y `sk = TICKET#<ticket_id>` y excluye explícitamente `entity_type = SOPHIA_TICKET_CONFIRMATION`, conservando tickets históricos sin entity_type. El backfill no se ejecutó contra AWS; el filtro no elimina índices que pudieran haberse añadido previamente a recibos.

El recibo no se borra mediante la eliminación normal de un ticket. No se configuró TTL: borrarlo prematuramente podría permitir recreación mientras el token siga vigente. Una política futura de retención/borrado de tenants debe considerar estos recibos.

Operación atómica: `TransactWriteItems` con dos `Put`; ambos usan `attribute_not_exists(pk) AND attribute_not_exists(sk)`. No existe una fase separada de “consumida, pero sin ticket”. La transacción usa un `ClientRequestToken` por invocación, estable durante sus reintentos, sin confundirlo con la identidad permanente de propuesta.

Los conflictos transitorios tienen hasta tres intentos acotados. Se intenta recuperar al ganador antes de reintentar. Sin resultado confirmado, se puede devolver 503 `confirmation_retryable`; el cliente puede repetir el mismo token mientras sea válido.

No hacen falta recursos ni cambios IAM: el stack Chat ya incluye `src/shared/**` y concede PutItem/GetItem/UpdateItem sobre la tabla. Las operaciones Put de una transacción usan los permisos de PutItem según la [documentación IAM de DynamoDB](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis-iam.html). La atomicidad se basa en las [transacciones DynamoDB](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis.html). No se verificaron políticas externas ni recursos desplegados.

## 7. Contrato HTTP y tokens

Solicitud, con Authorization normal/delegado existente:

```json
{"confirmation_token": "<token firmado>"}
```

Primera confirmación:

```json
{
  "message": "Ticket created from confirmed SOPHIA proposal",
  "ticket_created": true,
  "already_confirmed": false,
  "ticket_id": "<ticketId>",
  "ticket": {"id": "<ticketId>"}
}
```

Replay exitoso: mismo contrato y ticket_id, con `already_confirmed: true`. `ticket` contiene el ticket serializado completo, no solo el ID del ejemplo. Se devuelve su estado actual, no un snapshot de la respuesta inicial.

`ticket_created: true` significa que el ticket de la propuesta existe; no que esta solicitud lo haya insertado. Esto conserva las validaciones actuales de C.2. El campo nuevo distingue ambos casos.

JWT: HS256, `aud = xoc-chat-ticket-confirmation`, type/scope existentes, proposal_id, actor, rol, tenant, delegación, iat y exp requeridos. No cambia el JWT general de login. Los IDs/roles adicionales enviados en el body no sobrescriben claims firmadas.

Tokens antiguos sin proposal_id/audience se rechazan con 400: pedir una propuesta nueva. No hay fallback al flujo vulnerable. Por su duración de 300 segundos, la transición tiene una ventana corta, pero debe comunicarse durante el despliegue.

Otros errores: 400 por token/severidad inválidos o expiración; 403 por permisos/contexto; 409 por conflicto o ticket previamente confirmado ya eliminado; 503 ante conflictos transitorios agotados. Fallos no recuperables de infraestructura siguen el manejador de errores existente.

## 8. Metadata y prioridad

Solo los tickets de esta ruta guardan explícitamente:

```text
metadata.source = sophia_chat_confirmed
metadata.proposal_id = UUID firmado
metadata.proposal_request_id = request_id de trazabilidad
priority = critical | high | medium | low | info
severity = CRITICAL | HIGH | MEDIUM | LOW | INFO
```

Los valores corresponden al vocabulario existente de severidad de los clientes; no se inventan niveles P0/P1 ni traducciones de prioridades de notificaciones. Se acepta distinta capitalización; valores desconocidos se rechazan. Ausencia de severidad conserva el default medium de Chat.

No se guardan JWT, confirmation_token, refresh token, secretos ni el payload firmado completo. Los logs nuevos usan identificadores operativos y tipo de error, no cuerpos de respuesta ni tokens.

## 9. Eventos y fallos parciales

Solo el creador que recibe éxito de su transacción intenta `ticket.created`. Se conserva el sobre existente: Source `xoc.ticket`, DetailType `ticket.created`, tenant_id, ticket_id, subject y status. No se modificaron consumers ni Push/Inbox.

La publicación no tiene retry automático del SDK para evitar reenvíos de resultado incierto. Un replay no publica, aunque el recibo indique fallo.

- PUBLISHED: EventBridge devolvió EventId sin fallo por entrada. No prueba entrega final al consumidor.
- FAILED: la respuesta no confirmó aceptación o contiene FailedEntryCount.
- UNKNOWN: excepción/timeout al publicar; podría haber sido aceptado.
- UNCONFIRMED: aún no hay constancia local, por ejemplo caída de Lambda, pérdida de respuesta DynamoDB o fallo al actualizar el recibo del evento.

El ticket no se revierte ante fallo de evento. Si DynamoDB confirmó pero se perdió su respuesta, el store recupera el ticket y no se considera dueño seguro de la publicación: deja UNCONFIRMED. No se promete exactly-once del transporte ni entrega eventual automática; no se implementa outbox en esta fase.

Recuperación operativa, solo con autorización futura:

1. Identificar proposal_id/ticket_id y event_status en el recibo; comprobar que el ticket existe.
2. Revisar logs del publicador y ejecuciones/eventos del flujo de tickets por ese ticket_id.
3. Para UNKNOWN/UNCONFIRMED, no reenviar a ciegas: ausencia de evidencia no prueba que EventBridge rechazó el envío.
4. Si se confirma falta de aceptación/procesamiento, acordar recuperación sobre el MISMO ticket_id mediante el procedimiento operativo vigente; nunca crear otra propuesta para reemplazar automáticamente ese ticket.
5. No borrar el recibo para “desbloquear” ni usar replay como reparación de eventos. Automatizar reconciliación/outbox requeriría otra tarea autorizada.

## 10. Tests y validaciones

Baseline antes de editar, backend limpio en HEAD:

`python -B -m unittest discover -s tests`: 114 tests, 112 aprobados, 2 fallos.

Validación C.3:

`python -B -m unittest tests.test_tool_gateway_policy tests.test_tool_gateway_executor tests.test_chat_tool_gateway_integration tests.test_chat_ticket_confirmations`: 45 tests, OK (21 existentes + 24 nuevos).

Suite completa final:

`python -B -m unittest discover -s tests`: 138 tests, 136 aprobados, los mismos 2 fallos.

Fallos previos demostrados antes de editar, sin corregir:

- `test_assess_ticket_automation.AssessTicketAutomationTests.test_global_url_wins_and_marks_source_global`: esperaba URL global, obtuvo None.
- `test_assess_ticket_automation.AssessTicketAutomationTests.test_normalize_plans_skips_empty_and_keeps_legacy`: esperaba lista vacía, obtuvo un plan legacy.

La prueba de concurrencia utiliza dos threads y una barrera para forzar la carrera, con un fake que verifica condiciones y aplica ambos Put atómicamente. Se prueban también rollback de la segunda condición, conflictos transitorios, pérdida de respuesta DynamoDB/HTTP, expiración, denegación de contexto, metadata, prioridades, eliminación de ticket, respuestas fallidas de EventBridge y compatibilidad del constructor compartido.

No se utilizaron servicios remotos. La atomicidad real de DynamoDB se apoya en su contrato; los tests locales no equivalen a una prueba desplegada. Los nuevos tests restauran sus mocks/imports para no alterar los tests de otros módulos.

Sintaxis: AST parse en memoria de los tres archivos Python C.3, aprobado, sin generar bytecode. `git diff --check`: aprobado. No hay script de lint/test Python definido en package.json ni configuración de lint Python localizada que se haya inventado o ejecutado. No se repitieron build/lint de clientes, que no cambian.

## 11. Escenarios

| Caso | Resultado |
| --- | --- |
| A. Primera confirmación | Ticket + recibo atómicos; un intento lógico de publicación. |
| B. Replay | Mismo ticket, already_confirmed=true, sin publicación. |
| C. Concurrencia | Un ganador; el otro recupera al ganador o recibe error transitorio seguro. |
| D. Timeout/retry | Si el ticket fue confirmado, se recupera; no se crea otro. |
| E. Expiración | 400 antes del store, también si existía ticket. |
| F. Otro usuario | 403 antes del store. |
| G. Otro tenant efectivo | 403 antes del store. |
| H. ADMIN_XOC sin delegación | 403 antes del store. |
| Ticket eliminado tras confirmar | 409, nunca resurrección automática. |

La clasificación de unicidad pasa de D a diseño de idempotencia fuerte persistente, validado localmente. No se certifica aún el entorno desplegado ni entrega de eventos.

## 12. Riesgos restantes

- MEDIUM: evento perdido/incierto requiere reconciliación operativa; no existe outbox ni recuperación automática. La aceptación EventBridge no garantiza una sola entrega física.
- MEDIUM: no se probaron IAM/VPC/endpoints/consumers reales ni la versión desplegada. Los tests completos siguen con dos fallos previos.
- MEDIUM: Mobile conserva TS2724 y el desfase previo entre dependencia declarada 1.22.4 e instalada 1.20.7. No se corrigió ni revalidó Mobile aquí.
- LOW: tokens previos deben regenerarse; el cliente recibe 400 seguro.
- LOW: recibos sin TTL requieren política futura de retención/borrado, preservando la garantía de unicidad. No alterar/borrar recibos vivos manualmente.
- LOW: no hay vínculo a una instancia específica de delegación, solo al contexto existente.

## 13. Compatibilidad C.2

Web/Mobile no requieren cambios para interpretar primera confirmación o replay exitoso: ambos mantienen ticket_created=true y ticket_id válido. No se editó C.2. Los clientes conservan su propia política de no reintentar automáticamente desde una card fallida; el backend sí permite el retry legítimo del mismo token dentro de su vigencia.

## 14. Recomendación

- Revisión: lista, incluyendo revisión de transacciones y límites de eventos.
- Commit: preparado localmente; pendiente autorización y aceptación explícita de los fallos previos.
- Prueba funcional: siguiente paso en entorno controlado autorizado, con propuestas nuevas y usuarios de prueba; verificar concurrencia, recuperación y flujo real del evento.
- Deploy: no realizado. Solo tras autorización/revisión y validación del entorno; el stack afectado es Chat. No se requiere un cambio de tablas/IAM/Serverless ni desplegar ops por esta implementación.
- No avanzar automáticamente a Function externa, Voice, MCP, AgentCore, agentType o thread_id.

Regla final: SOPHIA propone; usuario confirma; backend valida; XOC ejecuta. Una identidad de propuesta confirmada conserva como máximo un ticket y un retry autorizado recupera ese resultado mientras el token sea válido y el ticket siga disponible.
