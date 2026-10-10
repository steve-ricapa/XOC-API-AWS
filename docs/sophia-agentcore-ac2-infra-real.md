# Mapa real de infraestructura XOC

Fecha: 10-oct-2026. Informe vigente de AC-2. Prevalece sobre el informe histórico
`sophia-agentcore-ac2-dev.md`: **no falta un ambiente DEV**. El usuario confirmó
una única infraestructura operativa. Los sufijos son convenciones de nombres,
no permiso para duplicar servicios o mover datos.

## Cuenta, identidad y región

- Cuenta: `811776156524`.
- Región: `us-east-1`.
- Única fuente de credenciales AWS utilizada: perfil `xoc-dev`.
- STS: `arn:aws:iam::811776156524:user/santiago.silva`, revalidado al cierre.
- Azure sólo se consultó para acreditar la dependencia existente; no se modificó.

## Stacks encontrados

Inventario inicial sin filtrar por stage: 17 stacks no eliminados, 37 Lambdas,
7 tablas DynamoDB. Después se agregó únicamente el stack AgentCore (una Lambda).

| Stack existente | Estado observado |
|---|---|
| `xoc-api-shared-prod` | UPDATE_COMPLETE |
| `xoc-api-auth-prod` | UPDATE_COMPLETE |
| `xoc-api-chat-prod` | UPDATE_COMPLETE |
| `xoc-api-tickets-prod` | UPDATE_COMPLETE |
| `xoc-api-reports-prod` | UPDATE_COMPLETE |
| `xoc-api-ops-prod` | UPDATE_COMPLETE |
| `xoc-api-tenant-prod` | UPDATE_COMPLETE |
| `xoc-api-admin-prod` | UPDATE_COMPLETE |
| `xoc-api-automation-prod` | UPDATE_COMPLETE |
| `xoc-infra-documents-prod` | REVIEW_IN_PROGRESS; condición preexistente, no intervenida |
| `xoc-infra-storage-prod` | CREATE_COMPLETE |
| `xoc-infra-data-prod` | CREATE_COMPLETE |
| `xoc-infra-network-prod` | CREATE_COMPLETE |
| `xoc-reportes-dev` | UPDATE_COMPLETE; servicio histórico distinto, no Chat alternativo |
| `Crear-mv-control-bd` | CREATE_COMPLETE |
| `crear-mv-desarrollo-xoc3` | CREATE_COMPLETE |
| `crear-mv-desarrollo-xoc2` | ROLLBACK_COMPLETE; no intervenido |

## Correspondencia código → AWS

| Función lógica | Código / IaC | Recurso real acreditado |
|---|---|---|
| Chat/SOPHIA | `serverless.chat.js`, `src/handlers/domains/chat_agents.py`, `src/handlers/routes/chat.py` | Stack `xoc-api-chat-prod`, lógico `ChatAgentsApiLambdaFunction`, Lambda `xoc-api-chat-prod-chatAgentsApi` |
| Rol de Chat | `IamRoleLambdaExecution` | `arn:aws:iam::811776156524:role/xoc-api-chat-prod-us-east-1-lambdaRole` |
| API HTTP compartida | `serverless.shared.js`, exports e integraciones | `xvwg3cvl6b`, nombre `xoc-api-shared-prod`, `https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com` |
| Autenticación | `serverless.auth.js` / shared | `xoc-api-auth-prod-authApi`, `xoc-api-shared-prod-jwtAuthorizer`; secreto JWT existente referenciado por ARN, sin leer su valor |
| Tickets | `src/shared/tickets_store.py`, `ReadOnlyProviders.tickets_list/tickets_get` | `xoc-api-tickets-prod-tickets`; PK/SK `pk/sk`; `StatusIndex` con `gsi1pk/gsi1sk` |
| Documentos/reportes | `src/reports/store.py`, `ReadOnlyProviders.documents_list` | `xoc-api-reports-prod-reports`; `pk/sk`; `StatusIndex` con `gsi2pk/gsi2sk` |
| Inbox | `src/shared/user_notification_inbox.py`, `ReadOnlyProviders.unread_count` | `xoc-api-ops-prod-user-notification-inbox`; `PK/SK`; `UserStatusCreatedAtIndex` con `GSI2PK/GSI2SK` |
| SOPHIA externa | `AGENTS_FUNCTION_BASE_URL` + ruta de Lambda real | `https://prod-a-xoc-cons-g9hvargsfqeafeap.centralus-01.azurewebsites.net/api/agents/SophiaDurableAgent/run` |

Las tres tablas están ACTIVE. Se corroboraron índices por DescribeTable, no sólo
por nombres. Otros índices existentes: tickets `TicketLookupIndex` y
`GlobalCreatedIndex`; reportes `ReportLookupIndex` y `TenantCreatedIndex`; inbox
`UserCreatedAtIndex`. El target no recibió permiso sobre esos índices adicionales.

Otras tablas inventariadas: `xoc-api-automation-prod-cases-v2`,
`xoc-api-ops-prod-device-registry`, `xoc-api-ops-prod-notification-campaigns` y
`xoc-api-ops-prod-notification-event-inbox`. No se tocaron.

También existe REST API `eg6lyrxcyf`, `dev-xoc-reportes`: no se sustituyó por la
API HTTP de XOC. Se inventariaron exports, roles, log groups y variables relevantes
con listas permitidas; no se volcaron secretos ni filas de negocio.

### Recursos asumidos anteriormente que no existen

No existen los stacks `xoc-api-shared-dev`, `xoc-api-chat-dev`,
`xoc-api-tickets-dev`, `xoc-api-reports-dev`, `xoc-api-ops-dev`, ni sus tablas y
Lambda Chat equivalentes. Tampoco existía `xoc-api-agentcore-dev`.
**Eso no es un bloqueo de plataforma: los recursos correctos son los de arriba.**
Se eligió `xoc-api-agentcore-prod` para mantener la convención real. No se crearon
tablas ni otra API/Chat, ni se renombraron recursos existentes.

## Resultado AC-2

```text
AC2_STATUS = BLOCKED
READY_FOR_RELEASE_REVIEW = NO
feature branch = feature/sophia-agentcore-ac2
push status = NO REALIZADO (condicionado a AC-2 PASS)
AgentCore desplegado = SÍ
Chat desplegado/activado en este bloque = NO
rollback de Chat probado = NO
```

Avance real: infraestructura AgentCore desplegada y pruebas directas contra AWS
ejecutadas. **No equivale a completar el circuito Chat → SOPHIA → AgentCore.**
El bloqueo externo actual es la acreditación del runtime desplegado de Azure,
con una incompatibilidad potencial demostrada al revisar el modo `disabled`.
No se declara PASS basándose en mocks o en respuestas vacías.

## Recursos nuevos realmente creados

Despliegue Serverless terminado correctamente; stack `UPDATE_COMPLETE` al cierre.
Sus diez recursos CloudFormation están `CREATE_COMPLETE`:

- Gateway: `xoc-api-agentcore-prod-s6yz3iqnfj`, **READY**.
  ARN: `arn:aws:bedrock-agentcore:us-east-1:811776156524:gateway/xoc-api-agentcore-prod-s6yz3iqnfj`.
  URL: `https://xoc-api-agentcore-prod-s6yz3iqnfj.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp`.
  Protocolo MCP, autenticación `AWS_IAM`.
- GatewayTarget: `xoc-read`, ID `2OJMHZDVKE`, **READY**, tipo LAMBDA.
- Lambda privada: `xoc-api-agentcore-prod-read`, **Active / Successful**,
  Python 3.11, 512 MB, timeout 5 s. Versión publicada `1`; target apunta al ARN
  no cualificado configurado en IaC. Sin Function URL ni API HTTP pública.
- KMS: `99da0530-55f7-40b6-af2b-0166ca9d97de`, **Enabled**, `HMAC_256`,
  `GENERATE_VERIFY_MAC`, origen AWS_KMS. `DeletionPolicy` y
  `UpdateReplacePolicy` conservan `Retain`. No se programó eliminación.
- Rol Gateway: `xoc-api-agentcore-prod-GatewayRole-QCL3uVnYgW6v`.
- Rol target: `xoc-api-agentcore-prod-ReadTargetRole-8ZfZCeFgEd4Q`.
- Log group `/aws/lambda/xoc-api-agentcore-prod-read`, retención 90 días.
- Bucket de artefactos Serverless
  `xoc-api-agentcore-prod-serverlessdeploymentbucket-me3dnqu80iej` y su policy;
  cifrado y bloqueo de acceso público comprobados en el paquete.

Se verificó la policy efectiva del target: exclusivamente VerifyMac sobre la
clave nueva, GetItem sobre tickets, Query sobre las tres tablas/índices indicados
y escritura de logs propios. Sin políticas administradas adjuntas. No tiene
GenerateMac, Scan ni permisos de escritura DynamoDB en esa policy.

El rol Gateway sólo permite InvokeFunction sobre el target exacto. Su trust
policy exige servicio AgentCore, SourceAccount correcto y SourceArn del prefijo
del Gateway de este stack. No tiene autoridad sobre otros targets XOC.
GetPolicy de Lambda devuelve ResourceNotFound y la lista de Function URLs está
vacía; la invocación se realiza con el rol IAM del Gateway, no mediante acceso
público anónimo. Esto no pretende impedir acciones de un administrador AWS.

### Capacidad y alcance

La cuenta reporta sólo **10 ejecuciones concurrentes Lambda, 10 sin reservar**.
Se añadió una opción explícita `SOPHIA_AGENTCORE_RESERVED_CONCURRENCY=unreserved`
para desplegar el target sin quitar capacidad reservada a los servicios existentes.
El default de IaC sigue en 4; no se cambió concurrencia de ninguna Lambda ajena.
Existe riesgo de competencia por capacidad compartida: no habilitar tráfico de
Chat ni hacer pruebas de carga sin revisar este presupuesto.

No hubo rollback de CloudFormation ni claves retenidas huérfanas en este intento.
El primer fallo fue anterior a la creación del stack, por resolución de credenciales.
No se eliminó ni recreó ningún recurso XOC existente.

## Correcciones locales y despliegue reproducible

Commit de implementación: `8ceac11d422df1dd839bfa0585af5a4b36fee512`.
Base previa: `311f3cdfb0f18b53885975ef52e883d370daa069`.

Archivos de implementación modificados:

- `package.json`: scripts canónicos `package:agentcore:prod` y `deploy:agentcore:prod`.
- `serverless.agentcore.js`: opción explícita de concurrencia no reservada y
  aclaración del naming. Ningún provider o política de negocio fue relajado.
- `tests/test_sophia_agentcore_infra.py`: cobertura del override y referencias reales.
- `tests/ac1/run_xoc_session.py`: puente acotado para Serverless 3 / SDK JS v2.
- `tests/test_sophia_agentcore_xoc_session.py`: seis pruebas de aislamiento del puente.
- Este informe y aviso de obsolescencia del informe DEV anterior.

### Compatibilidad del perfil autenticado

AWS CLI acepta el `login_session` de `xoc-dev`, pero Serverless 3.40 usa un SDK
que no lo resuelve. El primer intento dio:

```text
AWS profile "xoc-dev" doesn't seem to be configured
```

El puente obtiene con AWS CLI las credenciales temporales de **ese mismo perfil**,
las conserva sólo en memoria/entorno del proceso hijo, elimina variables AWS
heredadas, bloquea lectura de otros perfiles/metadata y verifica STS también con
el SDK real usado por Serverless. No imprime ni guarda credenciales y no modifica
`~/.aws`. No se usó otra identidad. El hijo no recibe AWS_PROFILE porque ese SDK
no entiende login_session; recibe únicamente la sesión validada de `xoc-dev`.

Comandos ejecutados, desde XOC-API-AWS:

```powershell
$env:AWS_PROFILE='xoc-dev'
$env:AWS_REGION='us-east-1'
$env:AWS_DEFAULT_REGION='us-east-1'
$env:AWS_SDK_LOAD_CONFIG='1'
$env:SOPHIA_AGENTCORE_RESERVED_CONCURRENCY='unreserved'
npm.cmd run package:agentcore:prod -- --aws-profile xoc-dev --package tests/ic1/.build/ac2-real/package-live
python -B tests/ac1/run_xoc_session.py --script deploy:agentcore:prod --package tests/ic1/.build/ac2-real/package-live
```

El puente invoca el script npm existente; no sustituye Serverless por otra IaC.
El paquete inicial `package/` tenía reserva de 4 y **no se desplegó**.
El paquete desplegado es exclusivamente `package-live/`.

## Artefacto y validaciones

- ZIP: 21.963.883 bytes; descomprimido 55.032.729; 1.509 entradas.
- SHA256 ZIP:
  `cc5869f79a36ab2333f8a02581c3ada4bb300d803ba8b85ff48cdd5226a491ed`.
- CodeSha256 real Lambda: `zFhp95o2qyMz+KAlgcOtpLswDYA7qLhf9IzdUiakke0=`.
- SHA256 template:
  `9f331a59ac586a602d37386719439dc12ff35619dc891e98516beadc175c2396`.
- Importación del ZIP real en imagen Linux Lambda Python 3.11: PASS.
- Rutas POSIX, dependencias, grafo CloudFormation, IAM mínimo, ausencia de MCP
  stdio/secretos en el artefacto: PASS según inspector existente.
- ValidateTemplate real AWS: PASS, CAPABILITY_IAM.
- Se conserva la corrección Windows ZIP previamente implementada; no se repitió
  el desarrollo de L6 ni se cambió su baseline.

## Pruebas directas reales

Contextos técnicos sintéticos firmados con KMS para la prueba autorizada; no son
JWT humanos ni pruebas de login. Tenants/usuarios sintéticos 2147483601 y
2147483602; no se crearon registros para ellos ni se escribieron datos XOC.

### Cliente productivo AgentCoreExecution

Tres rondas de cuatro llamadas, sin retries añadidos al cliente:

| Tool | Resultado observado |
|---|---|
| `xoc.tickets.list` | Primera llamada unavailable; dos siguientes allowed, lista vacía |
| `xoc.tickets.get` | denied por ID sintético inexistente; audit_id presente |
| `xoc.documents.list` | allowed, lista vacía |
| `xoc.notifications.unread_count` | allowed, count 0 |

La denegación del ticket inexistente comprueba tránsito/control de no encontrado;
**no es un happy path de ticket real**. Tampoco listas vacías acreditan aislamiento
entre dos tenants con datos reales. Esas pruebas siguen pendientes.

### Gateway y seguridad directa

Se probó sin sustituir el target ni los providers por mocks:

| Caso | Resultado |
|---|---|
| `tools/list` firmado | HTTP 200; exactamente cuatro herramientas READ_ONLY |
| Petición sin firma IAM | HTTP 401, JSON-RPC -32001 |
| Tool inexistente | JSON-RPC -32602, sin resultado de negocio |
| Assertion ausente / MAC alterado / vencida / audiencia distinta / argumentos alterados | denied/unavailable, sin datos |
| Argumento `tenant_id` inyectado | denied/denied; audit_id presente |
| SUPERADMIN | denied/unavailable, sin datos |
| ADMIN_XOC sin delegación | denied/unavailable, sin datos |
| ADMIN_XOC delegado | allowed; lista sintética vacía |
| USER | allowed según policy existente; no se ampliaron permisos |
| Segundo tenant sintético | allowed; lista vacía |

Nombres reales publicados: `xoc-read___tickets-list`, `xoc-read___tickets-get`,
`xoc-read___documents-list`, `xoc-read___notifications-unread-count`.
Dashboard/integrations/write tools no aparecen publicados. Su DENY en la policy
se conserva en pruebas locales; no se afirma un smoke remoto individual para cada
tool disabled ni para una segunda llamada del modelo.

Se contrastaron logs seguros `tool_gateway_decision`, `sophia_agentcore_target`
y reportes Lambda. La combinación de código/tests acredita validación antes del
handler de datos para las denegaciones; **no se afirma disponer de trazabilidad
CloudTrail de cada lectura DynamoDB**. No se habilitaron data events adicionales.

### Incidencias del propio diagnóstico, conservadas en evidencia

1. Primera versión del emisor de seguridad omitía `Mcp-Name`: devolvió -32020
   incluso para controles positivos. Esa ronda no cuenta como PASS.
2. Después aparecieron denegaciones temporales de controles válidos. La fecha
   local estaba adelantada respecto del Date recibido por TLS desde AWS KMS:
   diferencia observada 1.491–2.395 ms, con la resolución de un segundo del header
   y latencia de red. No representa una medición NTP exacta.
3. Sólo el emisor sintético se ajustó para usar ese tiempo AWS. En la ronda final
   pasaron los controles positivos y se rechazaron los negativos. No se cambió
   el reloj del equipo, TTL, check_time ni código productivo para aceptar tokens
   futuros/vencidos. No se ocultaron rondas fallidas.

## Latencia y observabilidad

- Primera llamada del cliente desde laptop: 4.921 ms, unavailable.
- CloudWatch del primer arranque: Init Duration 2.097,23 ms y Duration 677,59 ms;
  respuesta interna unavailable. No hay base para atribuirlo únicamente al
  arranque: también existe el desfase del emisor local.
- Segunda ronda del cliente: 2.233 / 2.344 / 2.149 / 2.111 ms.
- Tercera ronda: 2.145 / 2.226 / 2.095 / 2.249 ms.
- Ronda de seguridad final: aproximadamente 0,8–1,1 s para el POST; esa medición
  **excluye** preparación/firma y no se compara como si fuese la del cliente completo.
- No se anuncian p95/p99 ni capacidad sostenida a partir de esta muestra pequeña.
- No se cambiaron timeout Chat 20 s, presupuesto gobernado 18 s, ventana del
  cliente AgentCore 5 s ni TTL de contexto máximo 5 s.
- Faltan latencias reales Lambda Chat → Azure → Gateway → Azure y arranques fríos
  del circuito completo. La observación local no acredita ese presupuesto.

## Pruebas locales y regresiones

Ejecutado después de las correcciones:

```powershell
python -B tests/ac1/run.py --mcp ../XOC-MCP --dependencies ../.tmp-ticket-sync-tests --output tests/ic1/.build/ac2-real/ac1-post-deploy.json
```

**39/39 PASS**, cero intentos de red y cero intentos de lectura de archivos
secretos dentro del aislamiento. Incluye pruebas nuevas de naming/concurrencia y
del puente: cuenta incorrecta, perfil fijo, eliminación de credenciales/endpoints
heredados, sesión demasiado corta, ruta de fallback existente y errores sin secretos.

No se repitieron innecesariamente suites ya cerradas sin cambios de runtime:
baseline previo 27 Linux, 4 empaquetado, 32 HTTP y 120 focal PASS;
suite completa previa 285/287 con dos fallos históricos de VICTOR:

- `test_global_url_wins_and_marks_source_global`.
- `test_normalize_plans_skips_empty_and_keeps_legacy`.

No se presentan como pruebas nuevas de este bloque ni como regresiones causadas
por AC-2. No se corrigió VICTOR incidentalmente.

## Gate real pendiente: runtime SOPHIA y Chat

### Fuente real de Chat acreditada

Se descargó en memoria el ZIP efectivo de Lambda (sin mostrar URL firmada) y se
comparó cada fuente Python con Git:

- Baseline: `fe3b25f13833fb300ad1049b0e7974b37be8e706`.
- **120/120 archivos `src/**/*.py` idénticos**, cero diferencias contra esa base.
- CodeSha256: `H/uO4Z5cNLVyRGLy/CIBibwN/Gen8BXzvrvW+6niP2E=`.
- LastModified conservado: `2026-09-10T18:08:07.000+0000`.
- `SOPHIA_TOOL_EXECUTION` y `SOPHIA_TOOL_GATEWAY_ENABLED` ausentes al cierre.
- Timeout permanece en 20 s. No cambió rol, código, configuración ni rutas Chat.

HEAD tiene cambios en tickets, cases, workers de automatización, notificaciones,
schemas y stores que no pertenecen a este despliegue. **No desplegar HEAD/main
completo como Chat**. El próximo release debe partir de la base acreditada,
incorporar sólo el protocolo gobernado/AgentCore y su cierre de dependencias,
mantener idénticos Voice/VICTOR y comparar ZIP + template antes del deploy.
No se construyó ni publicó un release Chat que pretendiera saltar este gate.

### Evidencia Azure actual

- Suscripción `XOC-AZURE`, `884feb5e-8492-4d88-8777-6391393372f5`.
- RG `rg-sophia-poc`, Function App `prod-a-xoc-cons`, Running, Linux;
  hostname coincide con la Lambda real de XOC.
- Repo fuente confirmado: `XOC_MICROSERVICIO_AGENTES`, main limpio,
  HEAD local `6249064a9bd69bb388369d03f502b142bac93e1b`.
- Ese HEAD **no acredita** el commit efectivamente desplegado.
- Deployment source: branch/repoUrl null, scmType None; historial de deploy `[]`.
- App setting `SOPHIA_GATEWAY_REQUIRED`: ausente.
- Lectura Entra del contenedor `scm-releases` en `rgsophiapoc8952`: denegada:

```text
You do not have the required permissions needed to perform this operation.
```

- Lectura alternativa de fuente mediante SCM/Entra: Not Found. No se recurrió
  a claves Storage, SAS, perfiles de publicación ni RBAC autoasignado.
- Por tanto, siguen sin acreditarse bytes/revisión desplegada, protocolo actual,
  cierre efectivo del legacy y disponibilidad del segundo turno en Azure.
- La auditoría previa observó que el sitio comparte handlers con VICTOR.
  Sin fuente efectiva acreditada no se puede hacer un redeploy compartido y
  afirmar que preserva lo excluido del alcance.

### Por qué tampoco se desplegó Chat con disabled

`requires_governed_execution()` considera `disabled` un modo gobernado.
`chat.py` rechaza ese modo si `SOPHIA_TOOL_GATEWAY_ENABLED` no está activo, con
`sophia_mcp_requires_governed_chat`. Activar dicho flag envía el protocolo
`xoc-tool-gateway-v1` al runtime Azure. **No es seguro asumir que el runtime
actual lo entiende**, ni quitar este control o volver a direct como alternativa.

No se fabricaron JWT de usuario ni se usaron credenciales de usuarios tomadas de
conversaciones antiguas. Un smoke de Chat/C.3 y dos tenants poblados requerirá
sesiones legítimas y fixtures autorizados, sin compartir tokens en el informe.

## Matriz de cierre

| Gate | Estado al cierre |
|---|---|
| Identidad y discovery sin filtrar stage | PASS |
| Mapping de tablas/índices/Chat/runtime URL | PASS |
| Empaquetado Linux + CloudFormation/IAM | PASS |
| Deploy y recursos AgentCore READY | PASS |
| Direct smoke técnico, listado y denegaciones | Ejecutado; controles finales correctos con las limitaciones indicadas |
| Cuatro happy paths con datos reales | PENDIENTE; get sólo probó inexistencia |
| Chat con disabled y smoke normal/C.3 | BLOQUEADO por runtime acreditado pendiente |
| Activación agentcore y dos turnos reales | NO EJECUTADO |
| Aislamiento real entre tenants poblados | NO EJECUTADO |
| No fallback / segundo tool del modelo en nube | NO EJECUTADO; cobertura local conservada |
| Latencia integral fría/caliente | NO ACREDITADA |
| Rollback Chat a disabled y reactivación | NO EJECUTADO; no se activó inicialmente |
| Push feature / revisión de release | NO-GO hasta completar AC-2 |

## Riesgos pendientes

- **HIGH:** fuente/protocolo/legacy efectivos de Azure no acreditados; se detiene
  la integración Chat para no romper el servicio ni tocar VICTOR.
- **HIGH para el rollout:** un deploy de la rama actual arrastraría cambios ajenos;
  requiere release aislado sobre la base real, no asumir main = desplegado.
- **MEDIUM:** concurrencia total de cuenta 10 y target sin reserva; no hacer carga
  ni activación general sin completar validación de capacidad.
- **MEDIUM:** presupuesto temporal integral pendiente; un arranque inicial falló
  desde laptop y se observó desfase de reloj. No relajar firmas para ocultarlo.
- **MEDIUM:** falta prueba multi-tenant con datos existentes y sesiones legítimas.
- **LOW:** stack nuevo, KMS y logs quedan aprovisionados sin tráfico de Chat;
  pueden generar cargos aunque no se active Chat. No se eliminaron automáticamente.

No se modificaron RDS, schemas, SQL, Step Functions, Voice, VICTOR, web, mobile,
proxy, runtime Azure ni XOC-MCP. No Fargate/ECS/stdio, fallback local, write tools,
campañas, tablas nuevas o migraciones. No se deshabilitó una función existente.

## Evidencia y reanudación exacta

Directorio local ignorado: `tests/ic1/.build/ac2-real/`.

- `inventory.json`: discovery inicial, metadata con cuenta enmascarada.
- `package-live/` y `package-inspection.json`: paquete desplegado e inspección.
- `ac1-post-deploy.json`: 39 pruebas locales.
- `direct-smoke-1791670558060099400.json`: primera ronda, incluido fallo inicial.
- `direct-smoke-1791670592400257700.json` y
  `direct-smoke-1791670951721743900.json`: rondas posteriores.
- `security-smoke-1791671061046783000.json`: ronda final con tiempo AWS.
- Rondas de seguridad anteriores conservadas como diagnóstico, no PASS.
- `source-proof-1791670935102503600.json`: comparación ZIP real Chat y gate Azure.
- Scripts diagnósticos locales: `discover.py`, `direct_smoke.py`,
  `security_smoke.py`, `source_proof.py`. No contienen claves ni datos de negocio;
  no se incluyeron artefactos/builds en Git.

Para desbloquear, el responsable Azure debe entregar el artefacto **efectivamente
publicado** junto con revisión/fecha/comando real de deploy, o permitir lectura
Entra mínima `Storage Blob Data Reader` del contenedor
`rgsophiapoc8952/scm-releases`. No hacen falta contraseñas, claves ni otro ambiente.
Después: comparar bytes con Git, verificar protocolo gobernado y cierre legacy,
preservar handlers compartidos, y sólo entonces preparar el release Chat aislado.

Reanudar desde el gate previo al paso de cablear Chat, **reutilizando el Gateway,
target y KMS existentes**. No crear otro stack ni repetir todo el baseline local.
Validar credenciales/estado y completar: disabled → smoke normal/C.3 → agentcore
→ tools/dos tenants/seguridad/latencias → rollback disabled → reactivar sólo PASS.
Los commits/push de cierre estarán condicionados a ese resultado; MCP adicional
no se inicia automáticamente.
