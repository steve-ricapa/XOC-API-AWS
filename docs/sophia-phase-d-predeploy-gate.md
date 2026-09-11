# SOPHIA Fase D — gate pre-deploy

Fecha: 2026-09-11. Resultado actual: **NO-GO para publicación/activación de D**.
Implementación conservada; esta auditoría no cambia código, tests, timeout,
claves ni configuración desplegada. Sólo documentación y commits locales
separados. Sin push, deploy, activación, MCP, invocaciones a modelos o datos RDS.

Este documento prevalece sobre la decisión preliminar de
[sophia-phase-d-local.md](sophia-phase-d-local.md). No confundir una suite verde
del protocolo opt-in con ausencia de accesos legacy.

## 1. Matriz de decisión

| Acción | Gate actual | Motivo / evidencia necesaria para levantarlo |
| --- | --- | --- |
| Deploy runtime Azure a PROD | **NO-GO** | Falta trazabilidad del deployment Azure y auditoría de su configuración; el candidato conserva un bypass legacy HIGH. |
| Deploy Chat con flag false | **NO-GO operativo por ahora** | La base de código PROD está acreditada y el diff aplica, pero aún no existe paquete/release aislado validado ni comparación final CloudFormation/artefacto. Esto no implica que el flag false necesite Azure D para funcionar. |
| Activación coordinada flag=true | **NO-GO** | Legacy sigue disponible y puede saltarse gateway; además faltan evidencia Azure, releases revisados y validación de presupuestos reales. |
| Smoke real de Fase D ahora | **NO-GO** | D no está desplegada; primero resolver gates y autorizar una ventana acotada. Los diagnósticos de sólo lectura sí pueden continuar. |
| Inicio de MCP-1 | **NO-GO** | Requiere D publicada y smoke funcional/de seguridad aprobado. No se inicia automáticamente. |

**El bypass legacy NO es todavía el único ni el último gate.** También faltan
identidad/revisión/configuración verificadas de Azure y el paquete aislado de
Chat. La latencia y la firma compartida tienen riesgos explícitos abajo. Sólo
cuando esos puntos estén acreditados podrá decirse si legacy es el último gate.

## 2. Git y alcance de los commits

Estado inicial, antes de crear commits:

| Repositorio | Rama | HEAD base | Cambios pendientes |
| --- | --- | --- | --- |
| XOC-API-AWS | main | `10e3908f199340253241b6d475277f6dd7e3f0c5` | D: Chat, policy, transporte, IAM/env, tests y documentación. |
| XOC_MICROSERVICIO_AGENTES | main | `cc9e8a8fee9a2adcab54c9a2ebff5897f39f7931` | D: dispatcher, FoundryClient, protocolo/runtime, tests y documentación. |

Se preparan commits separados con estos asuntos, usando la identidad Git local
existente, sin cambiar autor/configuración:

- Backend: `feat: prepare opt-in Sophia phase D with predeploy gate`.
- Runtime: `feat: prepare bounded Sophia gateway protocol with legacy gate`.

Sus hashes finales se entregan en el cierre de la tarea; pueden recuperarse con
`git log -1 --format=fuller` en cada repo. **No son commits desplegados**.
El commit backend en main no debe convertirse en un deploy directo de main.
No se mezclan árboles Git ni se incluyen secretos, entornos virtuales o artefactos.

Fuentes de D que se conservan sin modificaciones durante el gate:

- Backend: `serverless.chat.js`, `src/handlers/routes/chat.py`,
  `src/tool_gateway/policy.py`, `src/shared/sophia_tool_chat.py`,
  `tests/test_sophia_phase_d.py`, `tests/test_sophia_phase_d_infra.py`.
- Runtime: `sophia_agent/handler.py`, `sophia_agent/foundry_client.py`,
  `sophia_agent/gateway_protocol.py`, `sophia_agent/gateway_runtime.py`,
  `tests/test_gateway_runtime.py`, `tests/test_gateway_xoc_contract.py`.

## 3. Azure: fuente confirmada versus deployment acreditado

El responsable confirmó por medio del usuario que
`XOC_MICROSERVICIO_AGENTES/main` es fuente de `prod-a-xoc-cons`, y que su agente
hace pull y despliega con un script propio. Esa confirmación identifica el repo;
**no identifica un SHA publicado ni el comando exacto**.

| Dato | Estado acreditado en este gate |
| --- | --- |
| Repo fuente | `https://github.com/steve-ricapa/XOC_MICROSERVICIO_AGENTES.git`, confirmado por responsable. |
| Branch declarada | main; revisión usada por el último deploy no comprobada. |
| Commit desplegado Azure | **Desconocido**; `cc9e8a8...` es HEAD local base, no evidencia del deployment. |
| Recurso / suscripción / resource group / slot | Nombre declarado `prod-a-xoc-cons`; ARM resource ID y slot exacto **no comprobados**. No deducir slot a partir del hostname. |
| Script/comando | Script propio del responsable, no entregado ni localizado en el repo. No sustituirlo por `func publish`, ZIP deploy u otro comando supuesto. |
| URL global usada por Chat PROD | Verificada en AWS: `https://prod-a-xoc-cons-g9hvargsfqeafeap.centralus-01.azurewebsites.net/api/agents/SophiaDurableAgent/run`. |
| URLs por tenant | Pueden existir overrides en TenantRuntimeSettings; no se consultó RDS ni se acredita inventario por tenant. |
| Agent Foundry / tools remotas | No acreditados: falta SOPHIA_AGENT_ID efectivo, endpoint/proyecto, versión/modelo, instrucciones y tipos/nombres de tools/tool_resources/conexiones. |
| Bytes remotos Azure versus Git | Pendiente de acceso a deployment/artefacto fuente autorizado. No se invocó Azure para crear runs. |

Intento local: `az account show` fuera del sandbox devolvió
`ERROR: Please run 'az login' to setup account.` El usuario informó después que
su compañero habilitará Azure CLI; esa habilitación está pendiente de confirmar.
El intento inicial en sandbox falló al leer azureProfile.json; se repitió con
permiso y se distinguió correctamente falta de sesión de falta de permisos locales.

La información mínima para cerrar este bloque es:

1. Sesión local de lectura del recurso Function y del proyecto/agente Foundry;
   no compartir contraseñas, Function keys, API keys, JWT ni publish profiles.
2. ARM ID/slot y deployment ID/fecha/build SHA; script exacto, hash/revisión del
   script y revisión de Git que realmente publica. Si publica desde un árbol
   sucio, inventariar ese diff: el HEAD por sí solo no prueba los bytes.
3. Si existe ZIP/package vigente, leerlo en memoria o en almacenamiento privado
   autorizado y comparar hashes por archivo con Git. Comparar fuentes de
   `function_app.py`, `sophia_agent/`, `shared/`, `domain/`, configuración de host
   y manifests de dependencias; no publicar settings ni URLs de descarga firmadas.
4. Registrar por separado cambios de build/dependencias generadas. Si sólo hay
   historial de deploy sin artefacto, declarar esa limitación, no afirmar igualdad.
5. Obtener metadata de agente y su configuración mediante lectura, sin crear
   threads/runs ni ejecutar tools. Clasificar herramientas built-in, funciones,
   OpenAPI/MCP/conexiones si aparecen y verificar qué se sobreescribe por run.

El repo local usa Foundry REST, `SOPHIA_AGENT_ID`, endpoint con aliases existentes,
API version default `2025-05-01`, y API key o DefaultAzureCredential. D envía las
cuatro funciones READ_ONLY y `parallel_tool_calls=false` por run; eso no acredita
la configuración actual del agente remoto. El README legado menciona DTS y
ejemplos sin Bearer; no se usa como contrato real ni como script de publicación.

## 4. Base REAL de AWS Chat PROD

Lectura realizada por SSH a `ubuntu@13.223.207.0`, usando la llave ya autorizada;
sin pull, edición ni deploy. La laptop no tenía credenciales AWS (`NoCredentials`).
Identidad de la EC2 comprobada antes de leer recursos:

- Cuenta `811776156524`, región `us-east-1`.
- Rol `XOC-Serverless-DeployRole`, instancia `i-0a8c6992b01b0b7fc`.
- Host `ip-172-31-74-46`; `/home/ubuntu/XOC_AWS` sigue en main `046a0f0...`.
- Se preservaron los cambios remotos previos en `dashboard.py` y `dashboard_store.py`.

Observación de Lambda: `2026-09-11T17:13:06Z`.

| Campo | Valor |
| --- | --- |
| Function | `xoc-api-chat-prod-chatAgentsApi` |
| Estado | Active / LastUpdateStatus Successful |
| LastModified | `2026-09-10T18:08:07.000+0000` |
| Runtime / handler | python3.11 / src/handlers/domains/chat_agents.handler |
| Timeout | **20 segundos**, sin cambios |
| CodeSha256 | `H/uO4Z5cNLVyRGLy/CIBibwN/Gen8BXzvrvW+6niP2E=` |
| Stack | xoc-api-chat-prod, UPDATE_COMPLETE |
| Aliases Lambda | Ninguno |
| Flag D / variable Inbox | Ambos ausentes del entorno desplegado; no hay D activa. |

Se descargó el ZIP únicamente en memoria en EC2. Su SHA256 base64 coincide con
CodeSha256; **120/120 archivos Python bajo src/ coinciden byte a byte con
`fe3b25f13833fb300ad1049b0e7974b37be8e706`**. Ninguno diferente o ausente.
Esto acredita las fuentes Python del artefacto, no que cada byte de las librerías
generadas provenga de Git. La revisión 10e3908 no existía en el Git de EC2 y no se
hizo fetch para introducirla; localmente se comparó la historia correspondiente.

IAM actual de Chat: inline `xoc-api-chat-prod-lambda`, DynamoDB sólo tickets y
sus índices, con permisos de lectura/escritura preexistentes. Adjunta
AWSLambdaVPCAccessExecutionRole. Aún no están desplegados los dos grants Query de
D para reports/Inbox. Se leyeron referencias de secretos, **no sus valores**.

Además se compararon fuentes del autorizador, tickets y scans desplegados:

| Lambda / CodeSha256 | Fuente comprobada |
| --- | --- |
| shared jwtAuthorizer / `D0PHuaR+NI44nZHs8MTcG6C5Wi5zyniBvXdTqyn9ZAM=` | jwt_authorizer.py coincide con fe3b25f/4118235/046a0f0. |
| ticketsDynamoApi / `8oiysc8Q+59Dub0PlDKpT+LYkQADFleC2KImFSqpk4I=` | tickets_dynamo.py coincide con fe3b25f/4118235; dependencies.py también con 046a0f0. |
| ops scansApi / `FE0j8Hf/1nohTxm4GnjGKknWLvwM5Tqq70Ikfd2rLdQ=` | scans.py y dependencies.py coinciden con las tres revisiones. |

No se ejecutaron estos handlers contra producción ni se consultaron tickets/scans.

## 5. HIGH — bypass legacy comprobado en código y simulación offline

Recorrido alternativo:

`POST Azure /SophiaDurableAgent/run sin protocol válido → handler.main legacy →
get_agent_token → build_tool_registry → run_threaded/poll_run → handle_tool_calls
→ shared.tools → BackendClient → APIs XOC`.

No pasa por ToolContext/policy/executor/audit del Tool Gateway. La selección depende
del campo `protocol` de entrada: sólo el valor exacto `xoc-tool-gateway-v1` entra
en D. Omitirlo o usar otro valor deja disponible legacy. `chat_mode=consulta`
suprime tools sólo en esa petición; no es una barrera de autorización general.

### Respuestas explícitas

| Pregunta | Respuesta |
| --- | --- |
| ¿Puede invocar ticket_create, ticket_patch, scans/BackendClient fuera del gateway? | **Sí** en el código candidato, y reproducido offline con JWT ficticio válido y proveedores simulados. No es ejecución anónima ni una prueba de escritura remota. |
| ¿Qué autenticación? | Bearer firmado con `_get_jwt_secret()`, algoritmo configurado (default HS256), exp requerido/vigente, scopes lista con agent:invoke, tenant_id presente, agent_type SOPHIA si viene. agent_type ausente se admite. No exige actor/role/delegación/request_id; no comprueba type=access en el verificador Azure. Issuer/audience sólo si se configuran; configuración remota desconocida. |
| ¿Quién puede obtenerla? | POST /agents/auth/token-from-user en XOC requiere usuario válido y require_tenant_read_access: USER/ADMIN con tenant, ADMIN_XOC y también SUPERADMIN con delegación. Emite SOPHIA por 1 hora, sin actor/role/delegación dentro del JWT. Chat/history/delete emiten internamente tokens SOPHIA de 15 min. Poseedores del token vigente pueden reutilizarlo. |
| ¿Está disponible con flag=true? | **Sí.** El flag selecciona transporte en XOC; no es un cierre del dispatcher legacy en Azure ni del endpoint de intercambio. |
| ¿Sirve una llamada directa a Azure? | El código sí la acepta con ese Bearer y configuración válida. FunctionApp usa AuthLevel.ANONYMOUS a nivel de host, pero el handler exige JWT. No se acreditaron Easy Auth, restricciones de red o slot remoto, por lo que no se declara comprobado el acceso público efectivo. |
| ¿La API-key legacy de XOC sigue activa? | No: /agents/auth/token devuelve 410. Eso NO cierra token-from-user ni el dispatcher Azure. |

Casos de autorización downstream distinguidos:

- `ticket_patch`: PUT /tickets/{id} requiere access claims y tenant, sin comprobar
  actor humano, agent_type SOPHIA o Tool Gateway; llega a update_ticket_fields.
  La función está presente en el artefacto PROD revisado. El mock confirmó una
  llamada al store con tenant autenticado. No se escribió un ticket real.
- Scans: cuatro rutas /scans/agent/* aceptan agent:invoke y tenant; retornan
  actor=agent, sin distinguir SOPHIA de VICTOR ni aplicar gateway. Sus queries
  existentes filtran tenant; no se ejecutaron ni se modificaron aquí.
- `ticket_create`: el runtime sí intenta POST /tickets. Con el token ordinario
  `sub=agent-runtime-7-SOPHIA`, el handler actual intenta int(userId) y falla con
  ValueError antes de persistir. **No afirmar que esa creación funciona hoy**;
  tampoco usar ese error accidental como control de seguridad. Existe además
  /tickets/agent separado con controles amplios; no es la ruta de este handler.
- El mismo Bearer podría usarse directamente contra APIs que acepten ese scope;
  no necesita que Azure vuelva a intermediar. Esto importa al diseñar el cierre.

Prueba offline del dispatcher, ejecutada con flag false y true: cada una devolvió
HTTP 200 simulado y registró POST /tickets, PUT /tickets/ticket-offline y
GET /scans/agent/summaries; **0 llamadas al gateway y 0 conexiones externas**.
Se usó el registry/orquestación/JWT checker reales y se reemplazaron HTTP/Foundry.
No es prueba del SHA ni de las credenciales del deployment Azure desconocido.

## 6. Cambio mínimo propuesto, NO implementado

1. En el entrypoint exclusivo de SOPHIA `/run`, aplicar un modo servidor de
   gateway obligatorio en PROD: rechazar legacy/protocolo ausente o desconocido
   ANTES de crear FoundryClient/BackendClient; admitir sólo capability D válida.
   El modelo/body no puede desactivar ese modo. No basta una instrucción al modelo.
2. No construir ni despachar el registry legacy en SOPHIA productiva. Conservar
   las implementaciones compartidas que VICTOR necesite; no modificar su handler
   ni el verificador compartido indiscriminadamente.
3. Cerrar el suministro y consumo alternativo de agent:invoke SOPHIA para tools:
   revisar consumidores de token-from-user y negar específicamente SOPHIA en las
   rutas de herramientas directas de XOC (tickets/scans correspondientes),
   preservando las identidades operativas legítimas de VICTOR. Esto requiere
   autorización de alcance adicional; no forma parte del diff D actual.
   Sólo dejar de emitir tokens no invalida los emitidos previamente (hasta 1 h).
4. Preservar history/delete que usa XOC después de validar AgentSession/ownership.
   No bloquear todos los tokens SOPHIA globalmente ni toda la Function. Separar
   permisos por operación en una revisión coordinada; esos endpoints NO deben
   convertirse en una vía para obtener autorización de ejecución de tools.
5. Añadir pruebas de cierre: token-from-user emitido antes/después de transición,
   body sin protocol/protocol falso, llamadas directas Azure y XOC, SUPERADMIN con
   delegación, y regresiones VICTOR/history/delete/C.3. Denial con cero handlers.

Nota de history/delete: Azure valida Bearer SOPHIA y forma del thread, pero no
vincula actor/tenant con ese thread. XOC sí comprueba AgentSession al hacer proxy.
Una invocación directa con un thread conocido evita ese control XOC si el recurso
es accesible. Se registra el riesgo HIGH; no se probaron threads reales ni se
eliminaron datos. Preservar funcionalidad no equivale a certificar ese acceso directo.

## 7. HIGH — alcance del material HS256 compartido

Inspección de fuentes y referencias, sin leer/comparar/rotar valores de claves.
AWS firma/verifica con get_jwt_secret_key (prioriza Secrets Manager, luego env,
luego SSM). Azure usa AGENT_JWT_SECRET / JWT_SECRET_KEY / SESSION_SECRET; el mismo
helper es compartido. La igualdad del valor Azure/AWS no está comprobada aquí;
si funciona ese intercambio debe existir material compatible para esos JWT.

HMAC es simétrico: quien conoce la clave de verificación puede firmar. aud/type/
scope/TTL separan usos de tokens intactos, pero no separan firmantes con la clave.

| Componente comprometido con acceso al mismo material | Qué podría fabricar |
| --- | --- |
| Backend signer o un rol/proceso con lectura del secreto JWT | Access/refresh, agent:invoke SOPHIA o VICTOR, delegación/claims, confirmaciones C.3, capability y continuación D con los campos necesarios. |
| Function Azure / código SOPHIA o VICTOR con ese secreto en el entorno compartido | Criptográficamente las mismas familias; no sólo tokens de SOPHIA. Puede elegir otro aud/type/tenant/sub y TTL. La limitación de 120 s puede cumplirse con firmas nuevas. |
| Authorizer o cualquier otro componente que posea la clave simétrica | Puede firmar, aunque su función prevista sea únicamente verificar. |
| Cliente con sólo un access/service/capability válido, sin clave | No puede firmar claims nuevos; sí reutilizar un bearer vigente dentro de los controles que cada ruta realmente aplica. |
| Foundry/modelo sólo con ToolResult | No recibe la clave ni el capability en el contenido del modelo y no puede firmarlos por esa vía. No se acreditan permisos de otras conexiones remotas. |

El authorizer XOC revisado acepta HS256 y proyecta claims; no fija audience,
issuer ni exige exp en `options.require`. Tokens D intactos con otra audiencia
no deben confundirse con access tokens; con la clave robada se puede crear otro
token sin esa audiencia y con type=access. No se fabricó un token real para probarlo.

Las rutas basadas en get_current_user cargan el usuario real: un rol inventado
no reemplaza automáticamente esa fila, pero se puede suplantar un sub existente.
Otras rutas confían directamente en role/tenant del authorizer. Refresh puede
renovar para un usuario existente; C.3 aún exige contexto coincidente y mantiene
idempotencia, pero el compromiso de la misma clave permite falsificar tanto
identidad como firma de propuesta. No equivale a romper una firma sin la clave.

La corrección futura es separar dominios de firma/verificación, idealmente
verificadores con clave pública y firmante restringido, más scopes por operación.
No se rota ni migra nada en este gate; VICTOR no se toca. Este riesgo no desaparece
cerrando únicamente el dispatcher legacy y exige decisión explícita de seguridad.

## 8. Latencia: no existe un hard deadline end-to-end de 18 s

Timeout Lambda comprobado: 20 s. En Chat, `governed_deadline = monotonic()+18`
se establece DESPUÉS de autenticación, resolución RDS de sesión/runtime y firma
inicial. Primer HTTP: min(config, 9 s). Segundo: min(9 s, restante). Entre ambos
corren validación de thread y gateway/DynamoDB; después queda persistencia de sesión.

En Azure cada fase usa 8 s desde la validación inicial; operaciones Foundry
min(4 s, restante), polling 0,2 s y cleanup best-effort hasta 1 s aparte. Adquirir
credenciales DefaultAzureCredential no hereda necesariamente ese timeout HTTP.
Requests usa timeout de conexión/lectura, no límite absoluto de toda la descarga;
por eso 9+9 no demuestra un máximo total de 18. Ver
[Requests: timeouts](https://requests.readthedocs.io/en/latest/user/quickstart/#timeouts)
y [AWS Lambda timeout](https://docs.aws.amazon.com/lambda/latest/dg/configuration-timeout.html).

**Riesgo MEDIUM/HIGH operacional bajo latencia:** cold starts, autenticación Azure,
queries paginadas, reintentos SDK y persistencia pueden agotar Lambda antes de una
respuesta segura. Un getter READ_ONLY no cambia tickets, pero una muerte de Lambda
puede dejar sesión/run pendiente. No se aumentó timeout ni se implementó otro loop.

### Métricas del smoke (diseño, no instrumentación implementada)

| Señal | Fuente / desglose | Criterio propuesto |
| --- | --- | --- |
| Latencia cliente y API | Tiempo cliente, API Gateway Latency/IntegrationLatency, HTTP status | Sin 502/504 inesperados; separar frío/caliente. |
| Lambda | Duration, Errors, Throttles, REPORT/Init Duration/Max Memory Used | Cero timeout; p95 <16 s y máximo observado <18 s como margen conservador, no SLA estadístico con pocas muestras. |
| Preparación/finalización XOC | auth, sesión/runtime, firma, serialización y commit sesión | Medir fuera del tramo HTTP; no asumir que cabe en los 2 s nominales. |
| Runtime fase request/result | request_id, fase, duración HTTP XOC→Azure; Azure request/dependency duration | Cada fase termina y se correlaciona; registrar colds, 429/5xx y errores de auth. |
| Foundry | auth, create_thread/message/run, poll count/tiempo, submit y list_messages | Mismo run/thread autorizado; sin tool adicional ejecutada ni reintentos ocultos de herramientas. |
| Gateway | audit_id/request_id/tool/decision/code + duración handler y cantidad de llamadas | 0 ejecuciones ante DENY; exactamente 1 ante tool permitida; nunca >1. |
| Stores | DynamoDB SuccessfulRequestLatency, throttles/system errors, páginas/tiempo de consulta | Sin throttling; tenant/usuario correctos. Métricas compartidas de tabla no prueban una petición por sí solas. |
| Limpieza y continuidad | Errores de segunda fase, cleanup_unavailable, run/thread persistido | No adoptar hilo ajeno; no crear otro run para disimular fallo; sin sesión bloqueada indefinidamente. |

Logs actuales: gateway ya registra decisión/correlación; algunos errores Azure
no llevan request_id y no hay duraciones por fase/handler explícitas. Las métricas
de plataforma sirven para total/errores, pero no garantizan el desglose de arriba.
Si no hay trazas/dependencias suficientes, proponer instrumentación segura en un
cambio separado antes de certificar ese presupuesto; no inventar mediciones.
No loguear cuerpos, tokens, continuation, credenciales, documentos completos o SQL.
Evitar tenant/user/request_id como dimensiones de alta cardinalidad en métricas;
usarlos sólo en auditoría restringida. Distinguir DENY esperado de error técnico.

Smoke propuesto una vez autorizado: 5 ejecuciones frías observadas y 20 calientes
por consulta crítica, también sin tool, con misma sesión y con nueva sesión;
cuatro tools, denials/ownership/cross-tenant, segunda tool y continuidad ante
fallos simulables sin alterar PROD. No forzar cold start cambiando configuración.
Registrar muestra y no presentar percentiles de una muestra pequeña como SLA.

## 9. Cómo construir el release aislado (definido, NO creado/desplegado)

1. Volver a verificar CodeSha256/120 fuentes antes de empaquetar; si PROD cambia,
   detenerse y recalcular base. Base acreditada ahora: fe3b25f, no main.
2. Crear worktree/rama release nueva desde fe3b25f, sin mover ni limpiar los
   worktrees existentes. Llevar sólo el commit backend de D y los cambios futuros
   de cierre que se aprueben, mediante cherry-pick revisado. No fusionar main.
3. La comprobación `git apply --check` del diff de los tres archivos ya existentes
   de D contra el worktree fe3b25f pasó. Los tres nuevos archivos de código/tests
   no colisionan con esa base. Eso NO sustituye ejecutar tests en el release final.
4. Verificar allowlist de cambios ejecutables: serverless.chat.js, chat.py,
   policy.py y nuevo sophia_tool_chat.py. Tests/docs son acompañantes. Mantener
   requirements, helpers, modelos, rutas ajenas y Step Functions de esa base.
5. Ejecutar suite enfocada y las pruebas entre repos contra ese worktree y el
   SHA runtime acordado; registrar hashes. No introducir multi_planner_v1.
6. Preparar paquete Chat desde esa base, sin desplegar. Comparar cada entrada del
   ZIP y CloudFormation contra PROD: sólo el diff D permitido, dos Query grants,
   variable Inbox, flag false y cambios técnicos de versionado/artefacto Lambda.
   Conservar librerías/binarios o justificar cualquier diferencia de build.
7. Verificar flag false literal tras resolver Serverless; no dejar que una variable
   heredada del shell lo active. Timeout 20, rutas, role base y resto del entorno
   deben mantenerse salvo los ajustes autorizados. No copiar dumps de secretos.
8. Guardar respaldo privado y plan de rollback acotado a Chat antes de pedir
   permiso de deploy. El script real AWS sigue siendo `npm run deploy:chat:prod`
   desde EC2/worktree y paquete aprobados; **no ejecutado en este gate**.

Comparación local main versus fe3b25f: difieren también tickets/automation,
workers/approval, schemas/tickets_store, notificaciones y documentación. Los
archivos D existentes (Chat/policy/config/common/auth/agents) son iguales entre
esas bases antes de aplicar D. Publicar main completo incorporaría cambios ajenos.
El despliegue de Chat comparte Lambda/rol con otras rutas: no basta decir que
el archivo editado se llama Chat para garantizar ausencia de regresiones.

## 10. Pruebas y evidencias de este gate

- Suite completa backend repetida offline: **222 tests, 220 OK, 2 fallos históricos
  de VICTOR, 0 errores/omitidos**. Sin fallos nuevos respecto al cierre anterior.
  `test_global_url_wins_and_marks_source_global` espera URL y recibe None;
  `test_normalize_plans_skips_empty_and_keeps_legacy` espera [] y recibe plan cmd=x.
  No se corrigen incidentalmente ni se declara toda la suite verde.
- Runtime repetido con contrato entre repos: **142 passed**, seis warnings
  históricos de datetime.utcnow del backend.
- Pruebas existentes incluyen 100 checks backend enfocados (96 protocolo/regresión
  más 4 de infraestructura en dev/staging/prod); no se añadieron tests nuevos aquí.
- Pruebas exploratorias inline de legacy con red externa bloqueada y datos/JWT
  ficticios; no se guardaron tokens ni se llamó a stores/proveedores reales.
- Git diff --check, revisión de archivos/staging y comprobación de hashes del
  código antes/después de documentar/commitear. Sin hooks locales activos encontrados.
- Lecturas AWS: STS, get-function/ZIP, CloudFormation e IAM. Sin Lambda invoke,
  put/update/delete, logs de conversaciones, Secrets Manager GetSecretValue ni RDS.

Comandos de referencia para repetir tests en entorno offline:

```text
Backend: python -B -m unittest discover -s tests
Runtime: .venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
```

Se usó wrapper de bloqueo socket.connect externo (loopback permitido), credenciales
AWS ficticias y metadata EC2 deshabilitada. Backend requiere Moto local existente;
se autorizó lectura fuera del sandbox. Runtime usó key Foundry ficticia y
XOC_API_SOURCE apuntando al repo backend local. No usar secretos reales en tests.

## 11. Próximo paso permitido

Continuar sólo la lectura de Azure cuando el responsable habilite CLI y acceso
de datos Foundry. Actualizar esta matriz con evidencia del recurso/slot/deployment,
script y tools. Después presentar para aprobación el cierre focalizado de legacy
y la estrategia de firma/consumidores, sin implementarlos automáticamente.

No hay autorización en esta tarea para desplegar aunque el gate futuro cambie a GO.
