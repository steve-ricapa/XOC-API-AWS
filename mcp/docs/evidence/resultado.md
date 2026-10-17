# Resultado MCP-1 LOCAL — 2026-09-14

## Resultado ejecutivo

MCP-1 LOCAL está implementado y listo para revisión de código, NO para producción.
Servidor y cliente del SDK oficial intercambiaron MCP por stdio; las cuatro herramientas
usan Policy, Executor, handlers y helpers originales de XOC sobre tablas ficticias.

Todo lo nuevo está en XOC-MCP-LAB. Ningún repo existente recibió modificaciones,
commit, stage, pull, push, deploy o flags. VICTOR/On-Premise y Voice no se tocaron.
La nueva autorización adelanta el laboratorio; no reescribe decisiones históricas
ni cierra D.1b, D.1c, D.2 o los gates de integración productiva.

## A-J: aceptación explícita

A. ¿Servidor MCP real ejecutable localmente? SÍ.
SDK mcp==1.30.0, Python 3.13.1, servidor xoc-mcp-lab 0.1.0. Desde XOC-MCP-LAB:

```powershell
$backend = (Resolve-Path -LiteralPath '..\XOC-API-AWS').Path
.\.venv\Scripts\python.exe -B server.py --lab --backend "$backend" --profile admin_tenant_a
```

B. ¿Cliente MCP real por stdio? SÍ.
ClientSession oficial mcp==1.30.0, cliente xoc-lab-python-client 0.1.0.
La respuesta initialize confirmó revisión 2025-11-25. El cliente completa la
notificación initialized. Se comprobaron tools/list, tools/call, método no soportado,
parámetros inválidos y cierre de proceso. Evidencia resumida: smoke.json y tests/test_stdio.py.

C. ¿Sólo las cuatro tools autorizadas cuando Policy lo permite? SÍ.
xoc.tickets.list; xoc.tickets.get; xoc.documents.list; xoc.notifications.unread_count.
Intersección explícita con registry, enabled, READ_ONLY y Policy.
El contexto SUPERADMIN, inválido o ADMIN_XOC sin delegación no obtiene catálogo
utilizable y tampoco puede invocar esos nombres directamente.

D. ¿Policy y Executor reales? SÍ.
Se importan del backend original, no se copian ni se reemplazan. Instrumentación
invoca handlers originales exactamente una vez cuando son autorizados.
Sólo se simula:
- boto3 resource/Table: query/get_item con comparación de claves y paginación.
- Proveedores no cableados y credenciales: bloqueados, nunca fallback.
- Destino de logging/auditoría: stderr local; log_tool_decision es original.
- Contexto: perfil de fixture del lanzador, explícitamente no autenticado.
SQLAlchemy/modelos son dependencias de importación de normalize_role; no se abre
engine/session SQL en MCP. No se reemplaza normalize_role ni Policy.

E. ¿Denegaciones y aislamiento tenant/usuario? SÍ.
85 tests nuevos verdes cubren roles, ownership injection/aliases, catálogo, llamadas
directas hidden/disabled/WRITE, schemas/falsy, canarios/proyección/tamaño, fallos de
store sin fallback, helpers/keys reales, mocks restaurados, dos procesos/contextos,
red/credenciales bloqueadas y arranque incompleto.
En el smoke A devuelve tickets/docs A, B sólo B. Unread A1=2, A2=5, B1=3.
Ticket ajeno/inexistente: una lectura autorizada por clave tenant+ticket, respuesta
segura execution_failed. Se distingue de DENY previo con cero handlers/lecturas.

F. ¿Repos y servicios productivos sin cambios? SÍ, no se operó sobre servicios.
Diez repos locales conservan HEAD, rama y status, además de hashes de las fuentes
y configuración versionadas comparadas. git-initial.json == git-final.json.
No se revisó estado cloud durante esta tarea: afirmar ausencia de acciones nuestras
NO implica afirmar que terceros no hayan cambiado producción entretanto.
El directorio raíz contiene .git pero git no lo reconoce como repositorio; no se
inicializó ni reparó. El laboratorio tampoco se inicializó como un nuevo repo Git.

G. ¿Algún modelo, servicio remoto o dato real? NO durante ejecución MCP/tests.
Preparación: sí hubo consultas de documentación oficial y descargas autorizadas de
dependencias públicas desde PyPI. No se usó AWS, Azure, Foundry, APIs XOC, perfiles,
JWT reales, MFA o datos de usuarios. Los intentos negativos usan valores ficticios
y fallan en los guards locales antes de acceder a recursos.

H. ¿MCP-1 LOCAL terminado y listo para revisión? SÍ, con los límites indicados.
No es una promesa de aislamiento frente a ejecución local hostil ni validación de
IAM/cloud/auth productiva. No es un servidor remoto autenticado.

I. ¿MCP productivo autorizado? NO.
No conectar este perfil ficticio a un puerto remoto, SOPHIA real ni credenciales.

J. ¿Próximo paso concreto?
Revisar este diff independiente, ejecutar el smoke y aprobar el contrato del
adaptador. Para conectarlo posteriormente, sustituir la frontera de contexto
ficticio por una frontera de autenticación XOC aprobada en una tarea separada.
No se abre automáticamente otra fase, migración, MCP remoto o deploy.

## Resultados ejecutados

| Comprobación | Resultado |
| --- | --- |
| setup.ps1 con lock completo | OK; entorno exclusivo; pip check sin conflictos |
| Python / instalador | CPython 3.13.1 / pip 24.3.1 |
| run_tests.py | 85 passed, 20.48 s, exit 0 |
| Smoke oficial por stdio, última ejecución | passed, exit 0; 4 procesos/perfiles; sin modelo |
| Regresión gateway Policy/Executor existente | 16 tests, OK |
| Regresión backend D/D.1 existente | 114 tests, OK |
| Backend completo existente | 249 tests: 247 OK, 2 fallos históricos; exit 1 |
| AST fuentes Python laboratorio | 14 archivos parseados, OK |
| Snapshot inicial/final | 10 repos idénticos en las propiedades comparadas |
| Inspección procesos propia | 0 procesos server/smoke/tests de XOC-MCP-LAB restantes |

El smoke hace 12 consultas exitosas y 16 rechazos entre sus cuatro perfiles.
El test completo de smoke observa los cuatro procesos reales del SDK y verifica
returncode=0. Otros tests incluyen procesos paralelos y arranques rechazados.
Los temporales se cierran con context managers; no se borran archivos del usuario.

Fallos históricos del backend, SIN corregir ni ignorar:
1. test_global_url_wins_and_marks_source_global: esperaba URL fixture y obtuvo None.
2. test_normalize_plans_skips_empty_and_keeps_legacy: esperaba lista vacía y obtuvo
   un plan legado con cmd=x.
Ambos de test_assess_ticket_automation.AssessTicketAutomationTests. Se mantienen
como referencia previa; no hay cambios a esa lógica.

Runtime SOPHIA (220 previos) y proxy (21 previos): NO reejecutados, no modificados.
No hubo smoke de audio, modelo, WebSocket, cloud ni Step Functions.

Problemas de entorno encontrados y resueltos localmente:
- Primer pip sin permiso de red falló; descarga posterior explícitamente autorizada.
- PowerShell bloqueaba scripts: se usó Bypass sólo en el proceso del script local,
  sin cambiar ExecutionPolicy global.
- Regresiones existentes: acceso sandbox a six.py y Node fuera del entorno filtrado.
  Se autorizó lectura offline y se añadió sólo la ruta Node al arnés de regresión.
  No se alteraron dependencias/test/configuración de los repos.
- Primer test de envelope malformado usaba el genérico SDK equivocado y perdía params:
  se corrigió la construcción del cliente de test, no el gateway para hacerlo aprobar.

## Backend exacto y preservación

XOC-API-AWS: main / ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9.
SHA256 agregado de sus 398 fuentes/configs versionadas:
ccf551819f629af4ac672e2df6a1fcf8c3ea2ceb8c2537508b7d553aac9da9a5.

Se preservaron tres informes backend no versionados, el test previo no versionado
del proxy, package.json pendiente del mobile y todos los otros cambios existentes.
Los snapshots excluyen secretos y binarios: no son una auditoría de todo el disco
ni del historial. No se leyeron contenidos/historial .env para esta implementación.

## Manifiesto del laboratorio

- .gitignore — excluye venv, cachés, artefactos.
- requirements.in — dependencias directas fijadas.
- requirements.lock.txt — 50 paquetes directos/transitivos, Windows CPython 3.13.1.
- setup.ps1 — preparación sólo del venv local.
- server.py — servidor stdio, modo obligatorio, contexto fijo, límites.
- smoke.py — cliente de humo real sin modelo.
- run_tests.py — arnés aislado del laboratorio.
- run_backend_regressions.py — usa entorno previo para comparar regresiones.
- snapshot.ps1 — HEAD/status/hash local sin secretos.
- xoc_mcp_lab/__init__.py — paquete local.
- xoc_mcp_lab/isolation.py — entorno/red/archivos sensibles/diagnósticos.
- xoc_mcp_lab/fixtures.py — dos tenants, usuarios, tickets/docs/counters ficticios.
- xoc_mcp_lab/wiring.py — proveedor de tablas en memoria con claves estrictas.
- xoc_mcp_lab/adapter.py — ToolRequest/Context, gateway original, schemas/proyección MCP.
- xoc_mcp_lab/client.py — cliente oficial con lanzamiento fijo del hijo.
- xoc_mcp_lab/testing.py — ruta del backend controlada por el arnés.
- tests/conftest.py — fixtures de test y restauración.
- tests/test_gateway.py — contrato, autorización, datos, seguridad, límites.
- tests/test_stdio.py — protocolo real, procesos, metadata, errores y cierre.
- README.md — comandos PowerShell, uso y límites.
- documento-de-diseno.md — decisiones, fuentes oficiales y fronteras simuladas.
- reports/git-initial.json y reports/git-final.json — evidencia Git.
- reports/smoke.json — resultado de la última ejecución.
- reports/resultado.md — este reporte.
- .venv/ — entorno generado localmente, excluido de revisión Git.

## Límites que no deben confundirse con funcionalidad productiva

No autenticación remota; perfiles seleccionables sólo en el lanzamiento del lab.
No exposición HTTP/SSE, AgentCore, registro global, MCP remoto o cambios de RBAC.
No conexión a herramientas WRITE/destructivas. No auto-ticket ni modificación C.3.
No firma separada ni bypass productivo cerrado por esta entrega.
No implementación de todo DynamoDB; sólo fronteras usadas con datos en memoria.
Red guard de proceso con loopback para asyncio; no sandbox de ejecución arbitraria.
No exactamente-una-vez distribuido ni cancelación ficticia de trabajo síncrono.
Los límites y comportamientos detallados están en README y diseño.
