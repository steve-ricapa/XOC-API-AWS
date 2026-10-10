# XOC / SOPHIA — AC-1 LOCAL: implementación y handoff

Fecha: 2026-10-10. Alcance: preparación local AgentCore; sin push ni deploy.

## RESULTADO

AC-1 implementado y acreditado localmente. READY_FOR_AC2 = **YES**, entendido
exclusivamente como listo para preparar/desplegar y probar DEV en el siguiente
bloque. NO significa que AgentCore ya exista en AWS, que el smoke cloud haya
pasado, ni que PROD esté autorizado.

| Entregable | Estado |
|---|---|
| AGENTCORE_ARCHITECTURE | DECIDED |
| TARGET | Lambda privado, DECIDED |
| TRUSTED_CONTEXT | IMPLEMENTED: KMS MAC dedicado y corto |
| AGENTCORE_CLIENT | IMPLEMENTED: IAM/SigV4, MCP stateless |
| XOC_TARGET | IMPLEMENTED: Policy/Executor canónicos |
| PROVIDERS | WIRED: DynamoDB real por configuración, mocks sólo en tests |
| IAM | DEFINED, recursos acotados |
| SERVERLESS_TEMPLATE | READY, compilación local + validación de esquema |
| TESTS | PASS: 32 AC-1 en Windows; 27 Python en Linux |
| REGRESSIONS | Sin regresiones nuevas; suite global conserva dos fallos históricos |
| COMMITS_LOCAL | Creados en branch AC-1; ver abajo |
| READY_FOR_AC2 | YES, DEV controlado; no iniciar automáticamente |

La suite global NO está totalmente verde: 285/287 pasan. Los dos fallos de
VICTOR ya existían en el baseline y no fueron corregidos por estar fuera de alcance.
No se ocultan como skips ni se redefinieron para hacer pasar la suite.

## ARQUITECTURA

Decisión completa y alternativas descartadas: [ADR AC-1](adr-agentcore-ac1.md).

Chat autenticado -> SOPHIA -> tool_request -> contexto autenticado XOC ->
KMS GenerateMac -> AgentCore Gateway con AWS_IAM -> Lambda privado -> KMS
VerifyMac -> Tool Gateway Policy/Executor -> DynamoDB READ_ONLY -> ToolResult
sanitizado -> segundo turno SOPHIA.

El nuevo target NO es el servidor de laboratorio XOC-MCP. No se publican fixtures,
llaves de prueba, L6, GDB, procesos stdio ni un contenedor de diagnóstico.
XOC-MCP permanece intacto y se conserva como evidencia de aceptación local.

El cliente usa MCP 2026-07-28, documentado por AWS como stateless. No modifica
el protocolo del laboratorio. Version/header/_meta son consistentes, no hay
negociación silenciosa, callbacks, sampling, elicitation ni reintento de tools.

Se exponen exactamente:

- `xoc-read___tickets-list` -> `xoc.tickets.list`.
- `xoc-read___tickets-get` -> `xoc.tickets.get`.
- `xoc-read___documents-list` -> `xoc.documents.list`.
- `xoc-read___notifications-unread-count` -> `xoc.notifications.unread_count`.

Dashboard e integrations siguen disabled. No se agrega tickets.create. C.3 sigue
requiriendo confirmación humana e idempotencia; se ejecutó su cobertura existente.

## CAMBIOS

Sólo se modificó `XOC-API-AWS`.

Código:

- `src/shared/agentcore_context.py`: canonicalización, MAC KMS, expiración, vínculo
  de audiencia/tool/argumentos/request ID e identidad completa.
- `src/shared/sophia_agentcore_client.py`: endpoint fijado por ARN/configuración,
  firma SigV4, transporte sin redirects/proxies heredados/retries, límites de
  respuesta/deadline y validación de ToolResult.
- `src/handlers/agentcore_target.py`: entrada privada de AgentCore; valida metadata
  de Gateway y assertion; no acepta autoridad del event ni del modelo.
- `src/tool_gateway/agentcore_providers.py`: adaptadores de sólo lectura con
  claves/serializadores existentes, paginación y presupuesto acotados.
- `src/shared/sophia_tool_execution.py`: selección explícita del transporte;
  AgentCore prevalece sobre el seam LOCAL. Modo inválido/disabled falla cerrado.
- `src/shared/sophia_tool_chat.py`: usa el selector conservando proyección,
  segundo turno y límite de una herramienta.
- `src/handlers/routes/chat.py`: exige flujo gobernado cuando el transporte lo
  requiere. No se alteró la validación de sesión/thread ni la confirmación C.3.

Infraestructura:

- `serverless.agentcore.js` y `serverless/services/agentcore-resources.js`.
- `serverless.chat.js`: wiring opt-in de outputs/IAM; estado por defecto anterior
  sin cambios. Activar las referencias no activa automáticamente AgentCore.
- `serverless.agentcore.offline.js`: perfil exclusivo `package`, bloquea deploy.
  No contiene dependencias; su ZIP es evidencia de compilación, NO artefacto cloud.
- `package.json`: scripts package DEV, package offline, deploy DEV del nuevo stack.
  El script de deploy fue creado, **no ejecutado**.

Pruebas: `tests/test_sophia_agentcore.py`, `tests/test_sophia_agentcore_infra.py`
y `tests/ac1/` con runners, validación CFN, inspección ZIP y entorno Linux.

No se introdujeron nuevas dependencias productivas ni SDK de agentes. Se reutilizan
requests/boto3/botocore y los requisitos existentes. No se hizo upgrade global ni
se afirma una auditoría de vulnerabilidades de todas las dependencias históricas.

No se tocaron Mobile, Web, proxy, Azure, Voice, VICTOR, Step Functions, Push,
schemas RDS, SQL/migraciones ni los repos de agentes.

## TESTS Y EVIDENCIA

Evidencia local bajo `tests/ic1/.build/ac1/` (ignorada por Git). No se sobrescribió
la evidencia de FT-2R/FT-2R.1. Los intentos preliminares y corregidos están separados.

| Verificación | Resultado | Evidencia |
|---|---|---|
| AC-1 Windows Python 3.13 + Node | 32/32, cero skips | `windows-reviewed.json` |
| AC-1 Linux AL2 x86_64 Python 3.11 | 27/27 | `linux-reviewed.json` |
| Infraestructura en Linux | clase omitida: imagen no incluye Node; sus cinco tests sí pasaron en Windows | mismo JSON |
| Regresión focal Chat/Policy/Executor/C.3 | 120/120 | `focal-final.json` |
| Regresión HTTP IC-1 real contra L6 local | 32 llamadas HTTP, cero fallos/errores | `ic1-http.json` |
| Guard no-stdio | 3 procesos vigilados, 0 query_worker / ProcessOwner / verified_server | `ic1-http.json` |
| Suite global | 285/287, 2 fallos históricos | `backend-all-reviewed.json` |
| Propiedades CFN recursivas | PASS, especificación AWS 267.0.0 | `cfn-release.json` |
| Serverless v3 package offline | PASS | `package-reviewed/` |
| ZIP/CFN inspección | 133 archivos, 8 recursos, grafo sin ciclos, sin fixtures/.env/tests | `inspect_package.py` |
| Git diff check | PASS | comprobado antes de commits |

Las pruebas AC-1 y focales hicieron **0 intentos de red externa y 0 accesos a
archivos de secretos**. La suite global conserva tres intentos externos bloqueados
por el guard, igual que el baseline; no fueron llamadas exitosas a AWS.

Fallos históricos exactos, no nuevos:

1. `test_assess_ticket_automation.AssessTicketAutomationTests.test_global_url_wins_and_marks_source_global`, línea 50.
2. `test_assess_ticket_automation.AssessTicketAutomationTests.test_normalize_plans_skips_empty_and_keeps_legacy`, línea 125.

La prueba integrada AC-1 recorre el finish_turn real, cliente real, firma real
SigV4 con credenciales ficticias, target, Policy, Executor y adaptadores reales;
simula únicamente los límites KMS/HTTP/DynamoDB. Comprueba una ejecución, una
consulta, un segundo turno y cero invocaciones al executor directo de Chat.
No equivale a un smoke contra un Gateway AWS desplegado.

Casos negativos cubiertos: contextos alterados, audiencia/tool/argumentos cambiados,
expiración, exceso de TTL, SUPERADMIN, ADMIN_XOC sin delegación, ownership injection,
target equivocado, tool desconocida/disabled, endpoint distinto, clave mal
configurada, error KMS/transporte, 403/500/redirect, SSE no autorizado, respuesta
sobredimensionada o mal formada, solicitud de segundo tool call y MRTR/sampling.
También aislamiento de dos tenants, ticket lookup con PK del tenant, unread count
con tenant+user, filtrado de secretos, fallo de proveedor y paginación acotada.

Imagen Linux revisada:
`sha256:6e2e037d4ba0db842fdd83b98fbaff67b9a147e2027be35e92c131daf316e94d`.

ZIP de compilación local:
`06f6f31c0519728a8b098e03a78c17b521aba2f159f11b7ddfad38e828ef7552`.

Template CFN compilado localmente:
`2fb0a57b6b1ec7b4f55f34f83bc143b7cb91faea4718d219210f9682ae513270`.

### Repetición local

Usar destinos nuevos para no pisar evidencia:

```powershell
python -B tests/ac1/run.py --mcp ../XOC-MCP --dependencies ../.tmp-ticket-sync-tests --output tests/ic1/.build/ac1/recheck.json
python -B tests/ac1/validate_cfn.py --spec tests/ic1/.build/ac1/aws-cfn-spec.json --output tests/ic1/.build/ac1/cfn-recheck.json
npm.cmd run package:agentcore:offline -- --package tests/ic1/.build/ac1/package-recheck
python -B tests/ac1/inspect_package.py --directory tests/ic1/.build/ac1/package-recheck
```

El runner de regresión ejecuta unittest discover dentro del guard de aislamiento:

```powershell
python -B tests/ic1/backend_regression.py --mcp ../XOC-MCP --dependencies ../.tmp-ticket-sync-tests --suite focal --output tests/ic1/.build/ac1/focal-recheck.json
python -B tests/ic1/backend_regression.py --mcp ../XOC-MCP --dependencies ../.tmp-ticket-sync-tests --suite all --output tests/ic1/.build/ac1/all-recheck.json
```

No ejecutar la suite desnuda heredando credenciales reales. La imagen Linux usa
dependencias de la imagen local verificada de FT-2R.1; la preparación copia sólo
código Python/tests, nunca archivos de credenciales. Es una prueba local, no una
imagen de producción. `prepare_linux.py` y `run_linux.py` registran hashes/resultado.

## COMMITS

Branch: `feature/sophia-agentcore-ac1`.

Base backend: `2166d89ff93f85cfae0eee9c8345c194e4313a6c`.
Implementación + infraestructura + tests:
`8c11578446d7a272b05fe6e38cbd9edb4ac50b8e`
(`feat: prepare IAM AgentCore read-only transport for Sophia Chat`).

Este reporte y el ADR se guardan en un segundo commit de documentación.

XOC-MCP no fue modificado: HEAD
`20fde196364bfcd2e006208cfb279549146c68a0`, branch
`feature/sophia-chat-mcp-ic1`. No se hizo commit en ese repo.

Los untracked ajenos y diagnósticos previos se conservaron y no se incluyeron.
No hubo fetch/pull/push ni edición de cambios desplegados. Estos commits nacen
de una branch de trabajo; **no se asume main = PROD** ni se autoriza desplegar
todo su historial sin revisar el release de AC-2.

## SERVERLESS RESOURCES

Declarados en stack `xoc-api-agentcore-<stage>`:

- `AWS::BedrockAgentCore::Gateway`, authorizer AWS_IAM.
- `AWS::BedrockAgentCore::GatewayTarget`, Lambda y cuatro schemas inline.
- `AWS::KMS::Key`, HMAC_256, GENERATE_VERIFY_MAC, Retain.
- Dos `AWS::IAM::Role`: Gateway y target READ_ONLY.
- Lambda, log group con retención y versión generados por Serverless.

En Chat, sólo con opt-in: una `AWS::IAM::Policy` para InvokeGateway y GenerateMac,
con ARNs exactos tomados de outputs.

Outputs: GatewayArn, GatewayUrl, ContextKeyArn, ReadTargetArn.
No se crean nuevas tablas ni una API pública, Function URL, MCP server propio,
colas, EventBridge, Secrets Manager o recursos RDS. Permisos y excepciones
justificadas de wildcard están detallados en el ADR.

## RIESGOS

- MEDIUM: falta el smoke real de AgentCore/KMS/SigV4/serialización en DEV. Los mocks
  no acreditan permisos efectivos, soporte de protocolo por cuenta ni disponibilidad
  del servicio. Si falla, responder unavailable: nunca relajar validación/fallback.
- MEDIUM: latencia de cold start, KMS y llamadas externas por medir. Se conserva
  Lambda Chat 20 s, deadline gobernado 18 s y presupuesto acotado de herramienta.
- MEDIUM: paginación de tickets/count >20 páginas o presupuesto agotado devuelve
  unavailable; no promete resolver tenants arbitrariamente grandes en cinco segundos.
- MEDIUM: el rol existente de Chat también pertenece al handler compartido con
  Voice. No se aísla código dentro de una Lambda mediante IAM; se agrega autoridad
  al proceso backend de confianza, nunca al runtime externo o al modelo.
- MEDIUM: dependencias existentes de Serverless no están todas fijadas exactamente.
  El empaquetado desplegable de AC-2 debe verificar dependencias resultantes, imports
  Linux, tamaño y escaneo. El package offline de AC-1 no incluye dependencias.
- LOW: reloj entre Lambdas sin tolerancia de extensión; desfase/agotamiento falla
  cerrado. La corta assertion admite replay de lecturas dentro de su validez por
  un invocador autorizado; no se ofrece exactamente una vez a nivel distribuido.
- LOW: KMS Retain conserva la clave tras quitar el stack; habrá que administrarla
  explícitamente después. AC-1 no creó esa clave ni generó gasto de ejecución cloud.
- BASELINE: dos fallos de VICTOR fuera de alcance. No son un cambio de AgentCore.

No se presenta una garantía de funcionamiento productivo ni una certificación
global de seguridad. Sí queda comprobado el cierre por defecto de la ruta nueva.

## READY_FOR_AC2 Y SIGUIENTE PASO

READY_FOR_AC2 = **YES**. No se inició AC-2.

Al comenzar AC-2 DEV mediante su prompt:

1. Verificar cuenta/región/identidad real y release a desplegar; inventariar stacks
   DEV y tablas de tickets/reports/inbox sin asumir existencia o equivalencia PROD.
2. Empaquetar con `npm run package:agentcore:dev` (perfil real, no offline), revisar
   artefacto Linux/dependencias y change set. Conservar respaldo/configuración previa.
3. Desplegar el stack AgentCore DEV con el script Serverless autorizado en AC-2.
4. Cablear Chat usando `SOPHIA_AGENTCORE_PROVISIONED=true` y primero
   `SOPHIA_TOOL_EXECUTION=disabled`; revisar outputs exactos e IAM efectivo.
5. Activación coordinada DEV: SOPHIA gobernada habilitada y transporte `agentcore`.
   No cambiar contrato Azure, Voice, VICTOR ni C.3 como compensación.
6. Smoke: normal Chat, cuatro lecturas, dos tenants, delegación, DENY sin handler,
   contextos inválidos, target inaccesible, C.3 y no segunda herramienta.
7. Medir cliente y target duration_ms, logs canónicos por request_id/audit_id,
   Lambda Duration/InitDuration/Errors/Throttles, latencia total Chat y resultado
   del segundo turno. Comparar cold/warm y concurrentes; no loggear assertion,
   JWT, payload, credenciales o documentos completos.
8. Ante fallo: desactivar transporte a `disabled` y evaluar rollback controlado.
   No ejecutar el mismo pedido por ToolExecutor directo ni por LocalMCPExecution.

PROD permanece separado. No hay autorización implícita para hacer push en AC-1.
