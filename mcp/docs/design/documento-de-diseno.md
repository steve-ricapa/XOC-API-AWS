# Decisión técnica MCP-1 LOCAL — 2026-09-14

## Secuencia y alcance

La autorización nueva permite código MCP local antes de cerrar el hardening
productivo SOPHIA. No se modifican informes históricos ni se declaran superados
D.1b/D.1c/D.2, release, artefacto o smoke cloud. No consume esos servicios.
No investiga ni depende de VICTOR/On-Premise. Tampoco conecta Voice con MCP.

## SDK/protocolo comprobados

mcp==1.30.0 es el SDK oficial Python en línea estable de mantenimiento v1.
Python comprobado: CPython 3.13.1 Windows x64. Cliente y servidor usan el mismo
paquete fijado, sin el paquete externo fastmcp ni un framework MCP propio.

Fuentes oficiales revisadas ANTES de instalar:

- [Release v1.30.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.30.0)
- [Documentación específica v1](https://py.sdk.modelcontextprotocol.io/v1/)
- [Low-level server en el tag elegido](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/docs/low-level-server.md)
- [Cliente en el mismo tag](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/docs/client.md)
- [Lifecycle 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
- [Stdio 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [Tools y resultado estructurado](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)

La negociación real devolvió 2025-11-25. Se usa initialize y
notifications/initialized del SDK. No se mezcla con server/discover de v2/2026.
Las capacidades reales son tools(listChanged=false) y experimental vacío del
SDK; sin resources, prompts, logging MCP, sampling, elicitation o tasks.

Se registra el handler tipado CallToolRequest en Server.request_handlers.
Motivo comprobado en mcp/server/lowlevel/server.py de 1.30.0: el decorador
call_tool normaliza arguments mediante 'or {}'. La entrada tipada y una validación
mínima sobre SessionMessage preservan ausencia frente a null. La serialización,
parseo, JSON-RPC, correlación, initialize, notificaciones, errores y transporte
son del SDK. No hay otro servidor JSON-RPC escrito a mano.

Parámetros mal tipados de tools/call: SDK retorna -32602 sin handler. Null explícito:
guard de transporte retorna -32602 antes del SDK tipado. Método válido no
implementado (resources/list): SDK -32601. Línea ilegible/excesiva: se cierra
el hijo de forma segura, sin copiar excepción ni contenido a la respuesta.

## Mapeo XOC real

Backend revisado: XOC-API-AWS, main,
ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9. Ruta explícita verificada en el lanzador.
No se acepta una ruta de fixture/módulo desde MCP. Una actualización futura del
backend exige repetir tests y revisar el contrato; el HEAD no se equipara a PROD.

| Pieza | Reutilización |
| --- | --- |
| schemas.py | ToolContext, ToolRequest, ToolResult, enums y definición reales |
| registry.py | TOOL_REGISTRY real, intersección con cuatro nombres fijos |
| policy.py | evaluate_tool_request real para catálogo/preflight y de nuevo dentro de Executor |
| executor.py | ToolExecutor.execute original, DEFAULT_HANDLERS originales envueltos sólo para contar |
| audit.py | log_tool_decision original; destino logging local seguro |
| sophia_tool_chat.py | model_result original para proyección; NO se llama finish_turn, capability ni HTTP |

La doble evaluación de Policy en una llamada aceptada (preflight y Executor) no
duplica la ejecución: hay una entrada al Executor y un handler. Preflight permite
que argumentos de autoridad sean denegados por Policy antes del esquema local.
Errores de esquema y de allowlist local no se hacen pasar por decisiones de Policy:
se auditan mediante el mecanismo existente con layer=adapter.

## Persistencia ficticia: punto preciso

Se interceptan boto3.resource/session.resource en el laboratorio, antes de
importar los stores que inicializan tablas al importar. Se conectan sólo nombres
LAB_TICKETS, LAB_DOCUMENTS y LAB_INBOX. Ningún método de negocio se sustituye.

| Handler | Helper real | Simulación |
| --- | --- | --- |
| tickets.list | tickets_store.list_tenant_tickets, normalize_status, _query_all, serialize_ticket | Table.query evalúa Key.eq, índices, orden y paginación |
| tickets.get | get_tenant_ticket_or_404 -> get_tenant_ticket_or_none -> serialize_ticket | Table.get_item compara pk=TICKET#tenant Y sk=TICKET#ticket |
| documents.list | reports.store.list_tenant_document_jobs, serialize_report | Rama DynamoDB real, Table.query; no _LocalReportsTable ni archivos de reportes |
| notifications.unread_count | unread_count_for_user, _status_partition_key | Table.query COUNT por tenant+user+UNREAD y paginación |

No fake que devuelva siempre datos correctos: tablas contienen ambos tenants,
todas las comparaciones son contra la clave recibida. Se comprueban consultas
registradas. Decimal en items se procesa por helpers/proyección originales.

No se prueba semántica completa DynamoDB, IAM, RDS o consistencia cloud.
El fake sólo implementa operaciones/índices requeridos y falla en el resto.
No simula escrituras. SQLAlchemy/modelos se importan por normalize_role; no se
crea engine ni session ni se consulta SQL en el recorrido MCP. Esa frontera
create_engine queda bloqueada en Wiring. La normalización de rol no es un mock.

Los módulos reales de tickets/reportes construyen resource/Table al importar;
se interceptó esa inicialización local. No hubo intento necesario de red para
importarlos ni se obtuvieron credenciales. Helpers con proveedores no cableados
fallan sin fallback. Los tests negativos realizan intentos ficticios que los
guards bloquean antes de cualquier conexión o resolución real.

## Contexto, autorización y salida

Un perfil fija tenant_id, effective_tenant_id, user_id, role, delegation_active.
ToolContext es frozen; request_id nuevo proviene del servidor; source=mcp_lab
explicita el laboratorio. No equivale a un source autenticado de producción.

USER/ADMIN/ADMIN_XOC/SUPERADMIN se resuelven exclusivamente por Policy real.
No se cambia el contexto por arguments, _meta, clientInfo, requestId o prompt.
Procesos separados prueban distintas identidades.

Se publican únicamente cuatro herramientas cuando estén habilitadas y la policy
lo permita. Una llamada directa a una tool oculta no evita esa comprobación.
Schemas locales se derivan del contrato real; su validación es más estricta
respecto a coerción de entero. No añade permisos, operaciones ni parámetros.

ToolResult pasa por model_result con campos públicos y límites originales.
La proyección no es DLP universal para cualquier texto libre. Los canarios se
colocan en campos excluidos, y los campos públicos usan el saneamiento de Fase D.
Un fixture no es una instrucción: ningún texto se ejecuta ni se sigue ningún URL.

## Seguridad local y evidencias

Env limpio, bloqueo de credenciales/archivos sensibles y sockets externos en
servidor y arnés. Loopback sólo se permite para compatibilidad asyncio Windows;
no es un contenedor de seguridad contra código Python local malicioso.
La app no crea endpoints, no usa TLS/auth remota ni sirve múltiples usuarios.
No hay SDK Azure, modelo ni imports del runtime externo en este servidor.

Watchdog limita vida del hijo y tiene un alcance exacto (su propio PID). El
presupuesto de lectura es cooperativo; no cancela un handler bloqueado mágicamente.
El cliente SDK gestiona cierre de stdio/terminación; los tests observan el proceso
original sin sustituir su implementación. No se exponen controles de proceso como tools.

La garantía probada es del recorrido ficticio local. No demuestra cierre del
bypass productivo, Voice gobernado, firma separada o un MCP remoto autenticado.
