# AC-2R — SOPHIA aislada desplegada; Chat en disabled

Fecha: 10 de octubre de 2026, Guayaquil; despliegue Chat finalizado el 11 de octubre UTC.

## Resultado y punto exacto de reanudación

**No es un cierre end-to-end.** Azure SOPHIA-only funciona con el protocolo gobernado y Chat ya está desplegado en modo `disabled`. Falta el smoke autenticado de Chat y C.3 con una sesión humana legítima. El usuario dispone de la web y pidió hacer las pruebas con Postman al final. No se fabricaron JWT humanos ni se activó AgentCore saltando ese gate.

```text
AZURE_SOURCE = ACCREDITED / DERIVED
SOPHIA_PROTOCOL = GOVERNED_PASS_DIRECT_AZURE
CHAT_ISOLATED_RELEASE = PASS
CHAT_DISABLED_DEPLOY = PASS
CHAT_DISABLED_SMOKE = PENDING_AUTHENTICATED_POSTMAN
ACTIVATION_PACKAGE = VALIDATED_NOT_DEPLOYED
DISABLED_ROLLBACK_PACKAGE = VALIDATED_NOT_EXECUTED
AGENTCORE_ACTIVATED = NO
FOUR_TOOLS_E2E = PENDING
C3 = LOCAL_PASS / LIVE_PENDING
ROLLBACK = NOT_EXECUTED
REACTIVATION = NOT_EXECUTED
AC2_STATUS = PENDING_AUTHENTICATED_SMOKE
READY_FOR_RELEASE_REVIEW = NO
PUSH_STATUS = NOT_PUSHED
```

Siguiente operación: probar conversación normal, continuidad e intención/propuesta C.3 en el Chat desplegado. Sólo con esos resultados satisfactorios se permite `SOPHIA_TOOL_EXECUTION=agentcore`. Después faltan las cuatro herramientas end-to-end, negativos, observación de latencia/logs, rollback real a disabled y reactivación condicionada a PASS. No repetir el despliegue del stack AgentCore ni las pruebas directas ya acreditadas sin un cambio que lo justifique.

## 1. Identidad y origen Azure

Suscripción XOC-AZURE `884feb5e-8492-4d88-8777-6391393372f5`; tenant `50ac7fef-2120-437f-ad31-6beb036a3723`; principal `steve.ricapa@hotmail.com`, objeto `5d3a1ba8-5e67-4b77-b649-0358298c57d6`. Se acreditó su asignación Owner preexistente; no se creó otra.

La Function compartida `prod-a-xoc-cons`, Linux/Python 3.12, no tenía `SOPHIA_GATEWAY_REQUIRED` ni una referencia Git acreditada en metadatos de despliegue. Su paquete `scm-latest-prod-a-xoc-cons.zip` es realmente SquashFS:

- SHA256: `b2909553bc9fe729e0a6543b758cd060cee85854d964d0002502004f7e04dbbf`.
- ETag: `0x8DF15976BD1A2B1`; última modificación: `2026-09-18T15:13:40Z`.
- Comparación: 22 de 25 archivos fuente coinciden con `cc9e8a8fee9a2adcab54c9a2ebff5897f39f7931`.
- Diferencias exactas: `function_app.py`, `sophia_agent/handler.py`, `sophia_agent/tools.py`.
- Clasificación: **DERIVED**, no EXACT_COMMIT. `main` no se equiparó con producción.

Se leyó usando Entra y permiso temporal Storage Blob Data Reader sólo en el contenedor `rgsophiapoc8952/scm-releases`. Asignación temporal `93490ead-2fa8-4d8b-87a5-653106ec1525`, **retirada y ausencia verificada**. No se usaron storage keys, SAS ni contraseñas de publicación. Se inspeccionó el sistema de archivos sin ejecutar el código extraído.

### Auditoría del protocolo anterior

El artefacto anterior no contiene `gateway_protocol.py`, `gateway_runtime.py` ni `thread_operations.py`. No acredita capability gobernada, `ToolResult` de vuelta, segundo turno controlado ni binding gobernado. Su handler utiliza el circuito legacy/BackendClient y el token antiguo. No hay límite gobernado acreditado para una segunda herramienta. No tener habilitado Gateway conserva el camino legacy, no lo cierra.

El repositorio sí contiene el protocolo en la base `6249064a9bd69bb388369d03f502b142bac93e1b`. Se reutilizó, sin inventar otro protocolo.

## 2. Aislamiento Azure y recursos creados

La unidad anterior incluye SOPHIA y VICTOR: no se volvió a publicar ni se cambió su configuración. El plan ARM dejó los recursos anteriores en `Ignore`. La operación sobre sus appsettings fue exclusivamente `list` para reutilizar material dentro de Azure, no una actualización.

Nueva unidad **SOPHIA-only**, en `rg-sophia-poc`, centralus:

| Recurso | Nombre / alcance |
|---|---|
| Function App | `prod-a-xoc-sophia` |
| Plan Flex Consumption | `ASP-prod-a-xoc-sophia`, FC1, Python 3.12, 2048 MB |
| Almacenamiento LRS | `stxocsophiaac2r`, shared keys y blobs públicos deshabilitados |
| Contenedor de paquetes | `sophia-releases` |
| Key Vault | `kv-xoc-sophia-ac2r`, RBAC, soft delete y protección de purga |
| Secreto | `sophia-capability-signing` |
| Application Insights | `appi-prod-a-xoc-sophia`, autenticación local deshabilitada |
| Identidad administrada | `87be58ba-751b-4368-a2b3-3b642a9c43b8` |

Se reutiliza el workspace de Central US existente y el proyecto/agente Foundry existente. No se crea otro modelo, agente ni ambiente DEV. El límite de escala 40 es un máximo de Flex, **no 40 instancias reservadas**; no hay always-ready configurado. Los costos son variables por ejecución/memoria, almacenamiento/operaciones, Key Vault y telemetría, además del consumo del modelo existente. No se ofrece una cifra mensual sin volumen medido.

Permisos comprobados de la nueva identidad:

- Storage Blob Data Owner sólo en su almacenamiento de host.
- Key Vault Secrets User sólo en el secreto de capability.
- Monitoring Metrics Publisher sólo en su Application Insights.
- Foundry User sólo en `aoai-sophia-xoc-eus2/projects/sophia-project` para la API clásica de threads/runs. Este rol es más amplio que mera inferencia; no se concedió a toda la suscripción.

La clave existente se reutilizó mediante una expresión ARM evaluada en Azure y referencia Key Vault. No se copió manualmente, no se persistió localmente ni se rotó. Se comprobó en memoria que el material de firma de XOC y SOPHIA coincide; la evidencia sólo registra PASS.

Publicación desde el directorio allowlisted, mediante Core Tools y build remoto. Endpoints nuevos: `health`, `run`, `history`, `delete`. No se publican handlers VICTOR, BackendClient, herramientas legacy, SQL, herramientas XOC locales ni MCP server.

```powershell
.\.venv\Scripts\python.exe -B deploy/sophia_only/build.py build/<directorio-nuevo>
# Dentro del directorio generado:
func azure functionapp publish prod-a-xoc-sophia --subscription 884feb5e-8492-4d88-8777-6391393372f5 --python --build remote
```

No ejecutar ese comando en la raíz del repositorio compartido.

## 3. Implementación y pruebas SOPHIA

Rama `feature/sophia-only-ac2r`. Commits de código:

- `dc9fb42b287c51c25e0b700d89426e6a2bc8e156`: paquete gobernado aislado, infraestructura, helpers sin imports legacy, pruebas.
- `217edfaf0078c1c85e28abeed0e37630027c2a87`: dependencia compatible con Python 3.12 y prueba Linux reproducible. Es el commit fuente del paquete desplegado.
- `ec1a1ac0dfe241264869994eaf7655439cac4efb`: documentación y script del smoke directo; HEAD local, sin push.

Primer build remoto rechazó `azure-functions==2.3.0` porque requiere Python 3.13. Se corrigió a `1.25.0` y se acreditó con Python 3.12 en Linux antes de republicar. No se cambió el runtime compartido.

Pruebas Linux, sin red: **215 PASS, 12 omitidas por integración cross-repo no configurada y 8 deseleccionadas relacionadas con VICTOR**. Las nuevas cubren S01–S12, import aislado sin legacy, helpers de mensajes equivalentes por AST, capability, resultado permitido/denegado/no disponible, hilo ajeno, malformados y cierre aun con flag false. También se corrigió un mock faltante de autenticación en tests legacy para que no dependieran de credenciales personales.

Smoke real Azure, con **capability de servicio**, no token de usuario ni acceso a datos XOC:

| Caso | HTTP | Tiempo desde laptop |
|---|---:|---:|
| Sin capability | 401 | 1521 ms |
| Conversación normal | 200 | 8825 ms |
| Continuidad normal | 200 | 7600 ms |
| Primera herramienta, fixture permitido | 200 | 5381 ms |
| Segundo turno permitido | 200 | 4651 ms |
| Primera herramienta, fixture denegado | 200 | 5843 ms |
| Segundo turno denegado | 200 | 4995 ms |
| Primera herramienta, fixture no disponible | 200 | 4689 ms |
| Segundo turno no disponible | 200 | 8174 ms |

Se usaron resultados vacíos de prueba, no se crearon tickets/documentos ni se consultaron datos de negocio. No se publicaron JWT, continuations, prompts ni respuestas completas en evidencia. Los tiempos incluyen red desde la laptop; **no son medición Chat end-to-end, ni acreditan cold/warm**. El margen del turno de 9 s requiere observación real; no se aumentó ningún timeout.

## 4. Release aislado Chat y evidencia de no afectación

Worktree `XOC-API-AWS-CHAT-AC2R`, rama `release/chat-agentcore-ac2r-20261010`, base real `fe3b25f13833fb300ad1049b0e7974b37be8e706`.

Commit de código: `a4751c1`.

HEAD del release: `890b4a88ad77423c6b425b957634ddb9789aba7b`, que añade el manifiesto durable y cobertura de regresión de threads. No se ha pusheado.

ZIP previo: `H/uO4Z5cNLVyRGLy/CIBibwN/Gen8BXzvrvW+6niP2E=`; 120/120 fuentes Python revalidadas contra la base. Se conservó el ZIP de dependencias ya desplegado; **1675 entradas no modificadas**. Sólo estos siete archivos fuente se añadieron/modificaron:

1. `src/handlers/routes/chat.py`.
2. `src/shared/sophia_tool_chat.py`.
3. `src/shared/sophia_thread_operations.py`.
4. `src/shared/sophia_tool_execution.py`.
5. `src/shared/sophia_agentcore_client.py`.
6. `src/shared/agentcore_context.py`.
7. `src/tool_gateway/policy.py`.

Origen del overlay: `b3544afa4dd12e39f0aa90c26016a2049b5dd337`; Chat añade el routing gobernado específico del release. Manifiesto durable: `docs/ac2r-chat-release-manifest.json` en el worktree del release.

El routing mantiene metadata/extra_json del tenant y no modifica los settings compartidos ni VICTOR. Se excluyeron modificaciones actuales ajenas de schemas, stores, tickets, cases, workers, notificaciones y multi_planner. No se incluyó el cliente stdio/local MCP.

Ocho funciones C.3/propuesta/confirmación mantienen **AST idéntico** contra la base. Los proveedores, documentos, persistencia y código Voice permanecen con los bytes de producción. El paquete importó correctamente en la imagen Lambda Python 3.11 sin red. Las 108 pruebas focalizadas de Chat, C.3, ownership, policy, executor, segundo turno, history/delete y transporte disabled pasaron.

CloudFormation modificó sólo código/environment de la Lambda Chat y su versión, más una policy adicional. Timeout 20 s, VPC, rutas y permisos previos permanecen iguales. IAM nuevo: `bedrock-agentcore:InvokeGateway` sólo para el Gateway existente y `kms:GenerateMac` sólo para la clave existente. No se agregaron grants directos DynamoDB a Chat.

Deploy Serverless `xoc-api-chat-prod`: **UPDATE_COMPLETE**, 53 s. SHA256 desplegado: `u+j2IEXmhHNjFVJDKv60PFvnJDdaVF5pBcpYNiQmNDw=`. Estado Lambda Active / LastUpdateStatus Successful.

Flags efectivos:

```text
SOPHIA_TOOL_GATEWAY_ENABLED=true
SOPHIA_THREAD_CAPABILITIES_ENABLED=true
SOPHIA_GOVERNED_BASE_URL=https://prod-a-xoc-sophia.azurewebsites.net
SOPHIA_TOOL_EXECUTION=disabled
```

`SOPHIA_AGENTCORE_PROVISIONED=true` se usa al empaquetar IaC para incluir referencias al stack existente; no es una variable requerida del runtime. No se rebajaron los límites: Chat 18 s gobernados / Lambda 20 s / cliente AgentCore 5 s / contexto 5 s máximo. La observación anterior de reloj era del orden de 1–2 **segundos**, no milisegundos; no se alteró tolerancia ni TTL.

## 5. Matriz obligatoria de 58 puntos

| N.º | Verificación | Resultado |
|---:|---|---|
| 1 | Principal/suscripción Azure | Acreditados arriba |
| 2 | Origen del artefacto anterior | DERIVED, acreditado por hashes |
| 3 | Commit/revisión | Anterior derivado cc9e8a8; SOPHIA-only desplegado 217edfa |
| 4 | Diferencias anterior/Git | Tres archivos listados arriba |
| 5 | Protocolo en runtime anterior | NO |
| 6 | Cambios SOPHIA | Reutilizar protocolo, aislar imports y paquete |
| 7 | Aislamiento | PASS; no redeploy de unidad compartida |
| 8 | App usada | Nueva SOPHIA-only |
| 9 | Recursos Azure | Inventario arriba |
| 10 | VICTOR modificado | NO |
| 11 | Tests SOPHIA | 215 PASS, 12 omitidos, 8 deseleccionados |
| 12 | Smoke directo SOPHIA | PASS con fixtures sin AgentCore |
| 13 | Base Chat | fe3b25f, 120/120 fuentes |
| 14 | Manifiesto Chat | SHA del ZIP, overlay, template, environment, IAM |
| 15 | Archivos overlay | Siete fuentes listadas |
| 16 | Cambios ajenos HEAD | Excluidos |
| 17 | Deploy Chat disabled | PASS, UPDATE_COMPLETE |
| 18 | Chat normal disabled real | Pendiente Postman legítimo |
| 19 | C.3 disabled real | Pendiente; AST y tests locales PASS |
| 20 | Activación AgentCore | NO, gate pendiente |
| 21 | tickets.list end-to-end | Pendiente; directo AgentCore previo PASS |
| 22 | tickets.get end-to-end | Pendiente; previo not-found controlado, no lectura existente acreditada |
| 23 | documents.list end-to-end | Pendiente; directo previo PASS |
| 24 | unread_count end-to-end | Pendiente; directo previo PASS |
| 25 | USER | Directo previo ALLOW; LIVE_CHAT_ROLE_TEST=NOT_AVAILABLE |
| 26 | ADMIN | Contexto de servicio Azure PASS; rol humano Chat pendiente |
| 27 | ADMIN_XOC delegado | Directo previo ALLOW; humano Chat no disponible |
| 28 | ADMIN_XOC sin delegación | Directo previo DENY; humano Chat no disponible |
| 29 | SUPERADMIN | Directo previo DENY; humano Chat no disponible |
| 30 | Cross-tenant | Unit tests PASS; no aislamiento con datos poblados reales acreditado |
| 31 | Contexto adulterado | Directo AgentCore previo DENY; no repetido |
| 32 | Contexto expirado | Cobertura local previa; circuito Chat real pendiente |
| 33 | Audience incorrecta | Cobertura local previa; circuito Chat real pendiente |
| 34 | Herramienta incorrecta | Cobertura local previa; circuito Chat real pendiente |
| 35 | Intento WRITE | Policy local DENY; circuito real pendiente |
| 36 | Herramienta desconocida | AgentCore previo rechazo; circuito real pendiente |
| 37 | Sin fallback | Tests release PASS; falla real AgentCore desde Chat pendiente |
| 38 | Segunda herramienta | Bloqueada en unit tests; prueba de circuito completo pendiente |
| 39 | Latencia total Chat | Pendiente |
| 40 | SOPHIA turno 1 | 4.689–5.843 s para herramientas, desde laptop |
| 41 | AgentCore | Evidencia directa previa conservada; no medición end-to-end nueva |
| 42 | Target Lambda | Sin ejecución nueva en este bloque |
| 43 | SOPHIA turno 2 | 4.651–8.174 s desde laptop |
| 44 | Cold/warm | No acreditado en circuito completo |
| 45 | Logs AWS | Muestra reciente sin registros; no declarar PASS de seguridad E2E |
| 46 | Logs Azure | 53 trazas revisadas, cero patrones de credenciales/payloads buscados |
| 47 | Rollback a disabled | NO ejecutado; iniciar desactivado no prueba rollback |
| 48 | Chat después de rollback | Pendiente |
| 49 | Reactivación | NO |
| 50 | Stack AgentCore reutilizado | SÍ, xoc-api-agentcore-prod |
| 51 | KMS reutilizada | SÍ, 99da0530-55f7-40b6-af2b-0166ca9d97de Enabled |
| 52 | Voice modificado | NO; bytes/config conservados, no se probó Voice |
| 53 | VICTOR modificado | NO; no se probó su comportamiento |
| 54 | XOC-MCP modificado | NO |
| 55 | Commits | Código Azure dc9fb42/217edfa; Chat a4751c1; documentación separada |
| 56 | Branches pusheadas | Ninguna: falta PASS integral |
| 57 | AC2_STATUS | PENDING_AUTHENTICATED_SMOKE |
| 58 | READY_FOR_RELEASE_REVIEW | NO |

## 6. Riesgos y límites

- HIGH: no está acreditado el flujo real de un usuario Chat/C.3. No activar herramientas ni anunciar AC-2R PASS antes del smoke.
- HIGH, preexistente: material HS256 compartido entre componentes. Aislar el paquete no elimina su alcance criptográfico si esa clave se comprometiera. No hubo rotación en este bloque.
- La Function antigua conserva código SOPHIA legacy porque se prohibió redeployar su unidad compartida. El nuevo Chat apunta exclusivamente al runtime aislado, pero eso no equivale a retirar todos los endpoints antiguos.
- MEDIUM: el turno Azure más lento observado consumió 8.825 s desde laptop; el límite HTTP de Chat es 9 s por turno dentro de 18 s. Medir desde AWS antes de aceptar el rendimiento; ante incumplimiento declarar PERFORMANCE_BLOCKER, no ampliar límites.
- MEDIUM: Foundry User tiene permisos mayores que sólo inferencia dentro de ese proyecto. La identidad nueva no tiene IAM AWS, acceso DynamoDB/RDS ni claves XOC de agentes.
- MEDIUM: la revisión de logs es una muestra acotada por patrones, no una certificación universal DLP. El smoke AWS todavía no generó evidencia.
- LOW: 12 pruebas cross-repo omitidas y datos de negocio no poblados en fixtures. No confundir listas vacías con aislamiento real entre tenants.

## 7. Pruebas Postman, sin compartir credenciales

Usar una sesión legítima de una cuenta de pruebas, su access token base como Bearer, y la URL existente:

```text
POST https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com/chat
Content-Type: application/json
Authorization: Bearer <access token sólo en Postman>
```

1. Body `{"message":"Hola Sophia, responde brevemente sin usar herramientas","new_session":true}`. Esperado: 200, texto y session_id/thread_id propios.
2. Con el `session_id` retornado, enviar `{"message":"¿Qué te dije antes? Responde sin herramientas","session_id":123}` sustituyendo 123. Esperado: continuidad del mismo hilo. No usar IDs ajenos.
3. Pedir una propuesta de ticket de sólo lectura: `{"message":"Propón un ticket de prueba AC2R para revisar la disponibilidad de las integraciones usando información existente. No ejecutes acciones. Muéstrame la propuesta para confirmarla.","session_id":123}`. Esperado: propuesta con confirmación humana; **no confirmar ni crear un ticket sólo por el smoke**.
4. Cancelar/descartar la propuesta en el cliente. No hay que inventar un endpoint de cancelación. La confirmación/idempotencia real sólo se prueba con fixture segura y autorización correspondiente; tests locales ya pasan.

Compartir únicamente HTTP status y respuesta sanitizada. No publicar JWT, confirmation_token, continuation, nombres/documentos sensibles ni credenciales. Cuando pase el gate, documentar `authenticated-disabled-smoke.json` con evidencia real (no fabricarlo para desbloquear el script), revalidar el paquete activo contra el estado cloud y desplegar. Luego ejecutar las pruebas pendientes de la matriz y rollback.

### Activación y retorno preparados, no ejecutados

El usuario reiteró que las pruebas Postman se harán al final. Se completaron los preparativos sin pedir otra prueba ni saltar la condición explícita de activación.

Los dos paquetes Serverless están generados en `tests/ic1/.build/ac2r/chat-agentcore/` y `tests/ic1/.build/ac2r/chat-disabled/`. Ambos pasaron `aws cloudformation validate-template` usando exclusivamente `xoc-dev`, cuenta `811776156524`, región `us-east-1`. La inspección del activo contra el stack desplegado pasó: sólo cambia `SOPHIA_TOOL_EXECUTION` en environment, la referencia del artefacto y la versión Lambda. No añade nuevos permisos respecto del deploy disabled; conserva el ZIP revisado, las dependencias y el timeout.

Hashes SHA256 de las plantillas:

- Disabled: `a0873e32f524bb8de30fd2ed2757761332b654dabbe00f420ab5d0c6f7b4c6e0`.
- AgentCore: `714afe67842aaa9ac5155e2b74ed62a7cc5791c647740bd28d5bbe1a3c49384f`.

Comandos de continuación desde `XOC-API-AWS`, **sólo después del smoke disabled acreditado**:

```powershell
python -B tests/ac1/chat_ac2r_release.py inspect --mode agentcore
python -B tests/ac1/chat_ac2r_release.py deploy --mode agentcore
```

Para el ensayo posterior de retorno a disabled:

```powershell
python -B tests/ac1/chat_ac2r_release.py inspect --mode disabled
python -B tests/ac1/chat_ac2r_release.py deploy --mode disabled
```

La preparación no sustituye la prueba de rollback. El script rechaza activación sin evidencia del smoke autenticado y detiene despliegues si el SHA del código vivo difiere del release/base acreditados. Se debe revisar cualquier cambio concurrente antes de reusar los paquetes. Última lectura cloud: Lambda Active/Successful, mismo SHA revisado, ejecución `disabled`, URL SOPHIA-only. No se ejecutaron estos comandos de activación o retorno.

## 8. Evidencias y herramientas

Backend: `tests/ic1/.build/ac2r/` contiene evidencia local ignorada: source proof Azure, metadata del artefacto, retiro RBAC temporal, origen overlay, manifiesto ZIP, inspección CloudFormation y cloud-evidence. Paquetes y fuentes extraídas no se pushean.

Runtime: `build/ac2r-release-02/release-manifest.json`, `build/ac2r-direct-smoke-01.json`; ambos ignorados. `deploy/sophia_only/` contiene IaC, builder allowlisted, smoke sin impresión de secretos y prueba Linux.

La skill Foundry se utilizó sólo para orientar el RBAC de su identidad administrada; su preparación instaló extensiones locales de azd. No desplegó agentes/modelos ni cambió Foundry por su cuenta. El build remoto se hizo con Azure Functions Core Tools.

Referencias oficiales consultadas: [IaC de Azure Functions](https://learn.microsoft.com/en-us/azure/azure-functions/functions-infrastructure-as-code), [Flex Consumption](https://learn.microsoft.com/en-us/azure/azure-functions/flex-consumption-how-to), [referencias Key Vault](https://learn.microsoft.com/en-us/azure/app-service/app-service-key-vault-references), [RBAC de Foundry](https://learn.microsoft.com/en-us/azure/foundry/concepts/rbac-foundry).
