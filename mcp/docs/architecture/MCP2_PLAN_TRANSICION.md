# XOC / SOPHIA — MCP-2: plan de transición

Fecha: 14-sep-2026. Estado: propuesta para aprobación, NO release ni autorización de deploy.

## 1. Decisión recomendada

Mantener la orquestación y el contexto humano en XOC. Añadir una **Lambda auxiliar de consultas SOPHIA**, con rol IAM, memoria, timeout y concurrencia propios. Dentro de esa Lambda, un cliente Python del SDK MCP inicia un servidor MCP hijo mediante **stdio estándar**. El servidor adapta las cuatro tools al Tool Gateway original. Chat invoca la Lambda auxiliar mediante AWS Lambda Invoke autenticado por IAM y un sobre de contexto firmado; esa invocación AWS **no se presenta como transporte MCP**.

Esto reutiliza el protocolo efectivamente probado, evita un servidor HTTP/OAuth nuevo para un único consumidor interno y saca la ejecución de herramientas del proceso/Lambda compartido de Chat. No es alojar el laboratorio en la EC2 de deploy, ni publicar perfiles ficticios. La Lambda auxiliar, su cliente y su servidor son software nuevo a implementar; no existen hoy.

La decisión tiene un costo explícito: cliente y servidor MCP se despliegan juntos dentro del worker, arrancar un hijo consume tiempo y no habrá un endpoint MCP para clientes externos. Es adecuado para SOPHIA Chat y una herramienta por turno. Si se necesitara interoperabilidad remota directa, la alternativa de §5 sustituiría esta frontera, previa aprobación; no se construirán ambas ahora.

Para D.2 también recomiendo aislar el runtime SOPHIA Chat en una Function App Azure propia, desde el MISMO repositorio y código revisado, con identidad propia y sin acceso al secreto HS256 compartido. No basta otro handler o slot bajo una identidad compartida. No es una Function sustituta inventada: es un empaquetado restringido del runtime identificado, sujeto a acreditar el artefacto fuente y aprobar el nuevo recurso.

## 2. Evidencia y límites de esta entrega

Las categorías se usan literalmente:

| Categoría | Significado |
| --- | --- |
| Existente y verificado | Código/evidencia local leído el 14-sep-2026, con revisión indicada; no implica cloud actual |
| Implementado sólo localmente | Código disponible sin acreditar activación productiva |
| Propuesto | Diseño de esta entrega; no implementado ni autorizado |
| Pendiente de comprobar | Evidencia necesaria antes del gate correspondiente |

Estado Git comprobado a las 17:09 UTC-5, sin fetch/pull:

| Repo | Rama / HEAD | Estado preservado |
| --- | --- | --- |
| XOC-API-AWS | main / ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9 | Tres informes docs/ sin seguimiento |
| XOC_MICROSERVICIO_AGENTES | main / 6249064a9bd69bb388369d03f502b142bac93e1b | Limpio |
| proxy-sophia-xoc | main / 93107ac2a4ad08b2c28e6242fb0aeda666eaf54f | tests/test_backend_contract.py sin seguimiento |
| XOC-APP-MOBILE | main / 303eac8a441275a5452ca79064c93c41295d82b5 | package.json modificado previamente |
| XOC_PAGE | main / f43d3ed6b2eac389cdf29e9fc9d052137fe188eb | Limpio |

MCP-1 es una carpeta hermana, sin repositorio Git propio inicializado. No se reparó el .git padre. Los worktrees de releases anteriores se conservan, pero sus nombres/HEAD no acreditan la versión actualmente desplegada.

| Elemento | Categoría y evidencia |
| --- | --- |
| SDK MCP 1.30.0, stdio, protocolo 2025-11-25 | Existente y verificado: server.py, cliente, lock y pruebas repetidas en esta tarea |
| Tool Gateway y flujo Fase D | Implementado localmente: backend ad4ac1a; activación cloud no comprobada aquí |
| Runtime externo | Fuente identificada por responsable: XOC_MICROSERVICIO_AGENTES/main; endpoint histórico prod-a-xoc-cons, rg-sophia-poc, Azure Central US; commit/bytes desplegados pendientes |
| Voice | Evidencia anterior del 14-sep: nueve fuentes LF coincidentes con revisión --0000037, imagen v15; no prueba de imagen completa ni revalidación cloud hoy |
| D.2 / identidad independiente | Propuesto; el material de firma compartido y el exchange legacy siguen siendo gates abiertos |
| Chat 20 s | Configuración local confirmada en common.js: default 20, Python 3.11; serverless.chat.js no lo sobrescribe. Timeout efectivo cloud pendiente |
| Backups, PITR, aliases, slots, permisos efectivos | Pendiente de comprobar; no se asume existencia |

Referencias internas reutilizadas: ../XOC-API-AWS/docs/sophia-d1c-proxy-source-verified.md, sophia-phase-d1b-inventory-gate.md, cases-tenant-hotfix-and-d1c-identity-gate.md; ../XOC-MCP-LAB/reports/resultado.md. No son evidencia cloud nueva.

Esta entrega sólo añade los tres Markdown de XOC-MCP-PLAN, fuera de los paquetes Serverless, Functions y Docker. No modifica código, dependencias, flags, cuentas, claves o datos. Se consultó documentación y precios públicos sin login cloud.

Comprobación final local, 17:30–17:32 UTC-5: los diez repos del snapshot conservan exactamente rama, HEAD, status y manifest SHA256 de fuentes/config versionadas; también coinciden con reports/git-final.json de MCP-1. Los 24 archivos visibles de fuentes/tests/documentación/evidencia del laboratorio enumerados fuera de .venv/caches conservaron sus hashes durante la redacción. No se sobrescribieron informes previos. El hash agregado backend sigue siendo ccf551819f629af4ac672e2df6a1fcf8c3ea2ceb8c2537508b7d553aac9da9a5. Esta comparación no acredita estado cloud ni incluye secretos/binarios/dependencias instaladas.

## 3. Aceptación acotada de MCP-1

Se revisaron server.py, adapter.py, wiring.py, isolation.py, los tres archivos de tests, smoke.json, README y documento-de-diseno.md, y el tramo de Chat/Fase D que ejecuta la herramienta.

| Control | Hallazgo comprobado |
| --- | --- |
| Una Policy/Executor | Adapter importa evaluate_tool_request, ToolExecutor y DEFAULT_HANDLERS originales. Preflight y Executor evalúan la misma Policy; sólo se ejecuta un handler autorizado |
| Contexto | Perfil elegido por lanzador, ToolContext frozen. _meta, clientInfo y argumentos no sustituyen actor/tenant. Esto es aislamiento de fixtures, NO autenticación productiva |
| Persistencia | Wiring intercepta resource/Table antes de imports; helpers reales consultan memoria por claves reales. Clientes AWS, credenciales, transporte SDK y engine SQL bloqueados. No hay fallback |
| Aislamiento local | Entorno reducido, archivos de secretos y red externa bloqueados. Loopback para asyncio Windows; no equivale a sandbox contra Python malicioso |
| MCP | SDK real, initialize/initialized/list/call, stdout reservado. Envelope inválido -32602; método no implementado -32601; denegaciones de tools isError=true. Línea ilegible/excesiva termina sólo el hijo |
| Límites | 100 llamadas por instancia, 40 lecturas por llamada y plazo cooperativo 5 s del fake. Watchdog mata el hijo, no cancela mágicamente un handler síncrono. No son límites cloud acreditados |

### Evidencia NUEVA de esta tarea

Ejecutado desde XOC-MCP-LAB, con el venv existente, sin instalar dependencias:

```powershell
.\.venv\Scripts\python.exe -B run_tests.py --backend '..\XOC-API-AWS'
# 85 passed in 19.95s; exit 0
.\.venv\Scripts\python.exe -B smoke.py --backend '..\XOC-API-AWS'
# status=passed; exit 0; 6.51 s de proceso observados
```

Smoke: SDK mcp==1.30.0; revisión 2025-11-25; stdio; model_calls=0; ADMIN A y B y USER A2 listan cuatro tools y ejecutan cuatro consultas cada uno; unread 2/3/5 respectivamente. Cinco rechazos por esos perfiles más uno SUPERADMIN: 12 consultas exitosas y 16 rechazos. La prueba existente verifica salida exitosa de los cuatro procesos hijos. No demuestra IAM, conexión AWS, Azure o latencia productiva.

La evidencia nueva se registra aquí, no sobrescribe reports/smoke.json ni reports/resultado.md. Los 114 tests backend focales y 247/249 completos son **resultados anteriores, no reejecutados**. Los dos fallos históricos son test_global_url_wins_and_marks_source_global y test_normalize_plans_skips_empty_and_keeps_legacy, de test_assess_ticket_automation; no se investigan ni corrigen en este encargo.

### Tamaño: tres medidas diferentes

1. model_result limita `len(json.dumps(projected, ensure_ascii=True)) <= 64000`: sólo el objeto data proyectado, con espacios del serializador por defecto; caracteres ASCII escapados. No mide toda la respuesta ni caracteres Unicode originales.
2. Adapter serializa el payload a texto JSON ASCII y lo repite como structuredContent. El JSON exterior vuelve a escapar el texto. No basta multiplicar por dos el tamaño UTF-8 original.
3. BoundedLines de server.py lee stdin binario con readline(65537): **límite de entrada**, 65536 bytes incluida la terminación de línea. stdout no pasa por esa clase; el escritor SDK no impone ese límite de salida.

Experimento adicional sólo en memoria, con model_result original, objetos CallToolResult/JSONRPCMessage del SDK y su model_dump_json(by_alias=True, exclude_none=True) más newline — el mismo serializador del escritor stdio:

| Fixture permitido | Filas | JSON data ASCII (caracteres) | JSON data Unicode UTF-8 (bytes) | Mensaje MCP completo UTF-8 (bytes) |
| --- | ---: | ---: | ---: | ---: |
| Siete campos string de 240 letras x | 35 | 62696 | 62696 | 125789 |
| Siete campos string de 240 cohetes Unicode | 3 | 60855 | 20535 | 91867 |

Se construyeron ToolContext ficticio, audit_id fixture-audit y request_id sizing-fixture; cada caso aumentó filas hasta la última proyección permitida. No se invocaron proveedores ni modificaron tests. Cambiar IDs cambia algunos bytes del sobre; estos números no son una cota universal.

Conclusión: **no se demostró un bug de truncamiento de salida en el laboratorio**; sí se comprobó que una salida legítima supera 64 KiB y que el README resulta ambiguo si se interpreta bilateralmente. Antes de producción: aclarar README; definir entrada 64 KiB y salida completa propuesta 256 KiB, ambos medidos en bytes; acotar IDs/códigos; validar la salida también del lado cliente. Exceso devuelve error pequeño seguro, jamás JSON cortado. Tests futuros: fronteras exactas, emoji, escaping, texto/structuredContent equivalentes y lectura SDK real de >64 KiB. No corregido ahora.

Otra diferencia relevante: Adapter conserva códigos operativos del Executor, mientras model_result de Fase D expone denied genérico al modelo. El puente futuro debe retener códigos precisos para audit y convertirlos al contrato sanitizado de D al continuar el modelo, sin volver a ejecutar el handler.

## 4. Arquitectura principal detallada

### Recorrido actual observado EN CÓDIGO, no declaración de PROD

```text
Cliente Web/Mobile -> POST /chat (Lambda compartida Chat/Agents)
  -> autentica humano, tenant/delegación, AgentSession
  -> runtime Azure SOPHIA
       flag D false: contrato legacy; persisten caminos alternativos
       flag D true: tool_request + continuación
  -> Fase D: ToolExecutor local -> model_result
  -> runtime, segundo turno -> respuesta + persistencia sesión

Separado: cliente MCP LAB -> stdio -> Adapter -> Gateway original -> fixtures
```

### Recorrido propuesto

```text
Usuario -> POST /chat XOC -> contexto humano y sesión existentes
  -> runtime SOPHIA Chat aislado [NUEVA Function App, código existente seleccionado]
  <- tool_request + continuación opaca (contrato D)
  -> sobre XOC firmado, IAM Invoke síncrono
     [NUEVA Lambda auxiliar de consultas, sin endpoint público]
       verificador de componente/contexto
       cliente MCP Python ClientSession [NUEVO wrapper de producción]
          <--- MCP 2025-11-25 / stdio --->
       servidor hijo MCP [ADAPTACIÓN del lab, sin perfiles ficticios]
          -> registry -> Policy -> Executor -> Audit ORIGINALES
          -> helpers originales -> DynamoDB (sólo tablas/índices autorizados)
       <- CallToolResult proyectado, sin datos crudos
  <- resultado vinculado al request/contexto
  -> runtime, MISMA continuación, segundo turno controlado
  <- respuesta natural -> cliente

Propuesta de escritura -> confirmación humana C.3 -> backend existente, SEPARADO
Voice -> flujo existente con transición de identidad; NO MCP
```

| Decisión | Diseño propuesto |
| --- | --- |
| Cliente concreto | ClientSession del SDK dentro del worker XOC. Chat coordina dos turnos y delega sólo la consulta. El modelo/runtime Azure nunca maneja el cliente MCP |
| Servidor | Proceso hijo por invocación del worker; paquete inmutable, ruta de ejecutable fija, shell=False, sin rutas/argv elegidos por modelo |
| Frontera | Worker AWS separado de Chat con IAM y recursos propios. Padre/hijo comparten la confianza, memoria e IAM del worker; stdio no crea una segunda frontera IAM |
| Lifecycle | Un contexto verificado, initialize/initialized, como máximo un tools/call, cierre y wait del hijo antes de retornar. Listado opcional para comprobar catálogo; sin reutilizar un hijo con otro usuario |
| Estado | Sin sesiones MCP persistentes ni base nueva. Continuación y AgentSession permanecen en D/Chat. Ningún cache de resultados compartido entre usuarios |
| Datos | tickets_store.list_tenant_tickets/get_tenant_ticket_or_404; reports.store.list_tenant_document_jobs en DynamoDB; unread_count_for_user. Sin RDS en herramientas. Chat sigue sus helpers/sesiones actuales, no se inventa SQL |
| Deploy | Chat, worker y runtime Azure son releases separadas, con contrato compatible. Cliente y servidor MCP se actualizan juntos en el worker |
| Recursos nuevos | Lambda worker, rol mínimo, log group, versiones/alias; firma KMS y separación runtime descritas en §7. Sin HTTP API nueva, ALB, EC2 MCP, SQS, EventBridge, AgentCore o DB nueva |
| Red | Worker sin VPC para estas lecturas DynamoDB; no necesita RDS ni internet de negocio. Chat ya está en VPC: conectividad a Lambda Invoke/KMS debe acreditarse, no asumir NAT/endpoints disponibles |

Sin compatibilidad o presupuesto de arranque demostrado en Linux, no se activa. El cliente no mantiene recursos abiertos entre invocaciones; AWS puede congelar/reutilizar el entorno y un proceso sobrante sería riesgo de contexto. La frontera de recursos es la Lambda, no sólo el nombre de un módulo. [Lifecycle AWS](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtime-environment.html).

### Qué se conserva y qué se reemplaza de D

- Conservar parser de alias tool_request/toolRequest/tool_call/toolCall, validación de sesión/thread, continuación opaca y máximo una herramienta por turno.
- Reemplazar en la rama MCP la llamada directa `ToolExecutor().execute(...)` de finish_turn por un puerto de ejecución que recibe el ToolRequest y el contexto ya autenticado. Su implementación invoca el worker; el Executor sólo corre dentro del servidor MCP.
- Mantener ToolResult como contrato interno y la proyección pública existente. El cliente valida tipos, request_id/tool_name, estado, tamaño y coherencia de los dos contenidos; no acepta un resultado atribuido a otro request. No reconstruir objetos crudos perdidos durante la proyección.
- Enviar al segundo turno exactamente protocol, phase=result, request_id, thread_id, continuation y tool_result con request_id/tool_name/status/code/data/audit_id. Mantener codes genéricos para denegación/error ante el modelo; detalle categórico sólo en audit/telemetría.
- El segundo turno con otra tool NO la ejecuta. Resultado MCP fallido no dispara el Executor local, BackendClient ni exchange legacy. Un fallo puede terminar con mensaje seguro sin segundo turno si no queda presupuesto.
- C.3 no se migra: propuesta, confirmación explícita, autorización y recibo idempotente permanecen. MCP no publica create/patch/delete.

Comprobación adicional de fuente: gateway_runtime.py ya envía tools=tool_definitions() y parallel_tool_calls=false al crear CADA run; usa cuatro alias Foundry que mapea a los nombres canónicos XOC. Conservar esa sobrescritura por run, comprobar que la API desplegada la respeta y no ampliar el catálogo. No editar globalmente un agente compartido para implementar MCP. Que un modelo solicite un nombre no demuestra enabled ni permiso: el servidor vuelve a decidir con registry/Policy.

## 5. Única alternativa: MCP remoto por Streamable HTTP

Sólo elegirla si se exige que un cliente fuera del worker se conecte directamente al servidor MCP, o si el benchmark demuestra que crear el hijo por consulta no cumple el presupuesto.

Cliente: SDK en Chat XOC, conservando contexto humano. Servidor: servicio ASGI MCP dedicado en ECS/Fargate en AWS, dos tareas para disponibilidad, IAM task role de lectura, límites CPU/memoria propios; ALB HTTPS y red aprobada. Son recursos adicionales propuestos, NO existentes ni justificados para la primera opción. Independencia de releases cliente/servidor; mismos helpers, Gateway y dos turnos D. No desplegar en la EC2 de trabajo.

Contrato de transporte: SDK v1.30.0, Streamable HTTP 2025-11-25, respuesta JSON stateless para estas cuatro tools; no SSE permanente ni estado de sesión distribuido. Autenticar cada POST y cualquier GET/DELETE soportado; Origin permitido, TLS y límites en proxy y aplicación. 405 para métodos deliberadamente no ofrecidos; no confundir 401/403 HTTP con isError de una tool. Pool HTTP acotado, sin cookie global de usuario. Desconexión = error seguro; no replay automático de tools. Si posteriormente se usan sesiones, su ID sólo correlaciona: enlazar identidad/contexto y reautenticar, 404 implica reinicialización, no cambio de usuario. [Transportes](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).

Esta alternativa requiere un servidor de autorización OAuth compatible aún NO seleccionado/aprobado, descubrimiento Protected Resource Metadata RFC9728 y del emisor RFC8414/OIDC, audiencia/resource del MCP canónico y scopes mínimos. Cliente confidencial preregistrado; para M2M evaluar la extensión oficial client credentials, no disfrazar la capability privada como OAuth. La delegación XOC debe quedar ligada a esa identidad verificada, nunca elegida en _meta. No reenviar el access token MCP a DynamoDB/APIs downstream. La compatibilidad del emisor y del SDK se prueba antes de autorizar esta alternativa. No se afirma que Entra, Cognito o un JWT HS256 existente satisfagan automáticamente ese contrato. [Autorización MCP](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization).

El contrato remoto necesario en la arquitectura PRINCIPAL es Lambda Invoke IAM más sobre XOC firmado, no un endpoint MCP HTTP; por eso no se promete compatibilidad OAuth de ese sobre privado. stdio es ya un transporte MCP estándar.

## 6. Versionado, empaquetado y compatibilidad

Mantener mcp==1.30.0 y negociación 2025-11-25 mientras pase el gate de seguridad/compatibilidad. El paquete declara Python >=3.10; esto permite evaluar Python 3.11 pero no valida todas sus dependencias. [pyproject del tag](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/pyproject.toml), [release fijada](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.30.0).

Objetivo inicial worker: Python 3.11, Linux x86_64/Amazon Linux 2, ZIP como las Lambdas actuales, 512 MiB a medir. No actualizar el runtime de toda XOC ni usar el venv Windows como ZIP. Imagen oficial/SAM de build fijada por digest para pruebas y resolución Linux; usar contenedor de build no significa alojar MCP en un servicio de contenedores. Python 3.11 tiene horizonte de mantenimiento a revisar antes del release; cualquier migración de runtime se separa. [Entornos Python AWS](https://docs.aws.amazon.com/lambda/latest/dg/python-image.html).

Distribución propuesta:

1. Versionar posteriormente XOC-MCP-LAB como repo independiente o artefacto fuente controlado; decisión de propietario antes de commit. Hoy no tiene commit. No incluir .venv, resultados sensibles o reparar .git padre.
2. Producir desde un commit backend APROBADO un wheel/ZIP de librería `xoc-tool-gateway-core`, con manifest de archivos necesarios bajo sus imports originales src.*, sin copiar otra implementación mantenida a mano. El build debe fallar ante imports ausentes o fuentes fuera de allowlist.
3. Adapter, registry schemas derivados y wrapper pertenecen al artefacto MCP; Policy, Executor, audit, handlers, helpers y proyección vienen del artefacto backend canónico. Si extraer la proyección a un módulo puro resulta necesario para empaquetar, será un cambio posterior mecánico con equivalencia byte/contrato, no una segunda proyección.
4. Manifest firmado/verificable: backend commit, árbol/hash por archivo, versión adapter, SDK/revisión, lock Linux completo con hashes, builder digest, versión Python/arquitectura, SBOM y SHA256 de artefacto. Nada se resuelve de main al arrancar.
5. Ejecutar tests actuales con ese artefacto instalado, sin checkout hermano ni sys.path de desarrollo; comprobar source paths y un solo ejemplar de Gateway. Repetir importación fría, STDIO, cierre de hijos, env mínimo, memoria y semántica de helpers.
6. Mantener lock Windows separado. Compatibilidad por tupla `(adapter, gateway artifact, Python, SDK, protocolo)` aprobada, no por rango abierto de commits. Un cambio del registry no publica nuevas tools automáticamente.

No introducir fake Isolation/Wiring en producción: allí habrá IAM real y proveedores sólo de lectura. Mantener los guards y fixtures exclusivamente para tests; ningún flag puede transformar el servidor de perfiles ficticios en un servidor de datos reales.

## 7. Identidades y cierre D.2

### Tres autoridades distintas

| Identidad/credencial PROPUESTA | Emisor / firmante / verificador | Propósito, contexto y vida |
| --- | --- | --- |
| Usuario XOC | Emisor auth existente; Chat aplica autenticación/contexto/delegación actuales | JWT humano sólo hasta XOC. Rol/user/tenant desde contexto backend; no se entrega a Azure/MCP |
| Llamada Chat -> worker | Rol de ejecución Chat autorizado a Invoke sobre alias exacto; AWS verifica SigV4. Además XOC firma JWS ES256 con KMS key Chat; worker verifica pública fijada | iss lógico XOC Chat, aud=xoc-sophia-query-worker, purpose=mcp-read, source=sophia_chat; 30 s máximo; sin refresh. actor/user/role/tenant/effective_tenant/delegación/request, hash tool+args+contexto y deadline |
| Contexto worker -> servidor stdio | Worker entrega sobre firmado en el entorno mínimo exclusivo del hijo; servidor verifica antes de construir ToolContext | Un hijo por request. No credencial en argv, logs, tools/call.arguments o _meta. Variable de arranque acotada y efímera, no .env/archivo permanente ni mutación global de os.environ; no JWT humano. El entorno no es secreto frente a código comprometido dentro del mismo worker |
| Acceso a datos | STS/rol de ejecución del worker, credenciales temporales administradas por AWS | Sólo GetItem/Query en tablas e índices exactos tickets/reports/Inbox y logs. Sin Scan, Write, RDS, Secrets Manager, Invoke ajeno o KMS Sign. IAM no reemplaza Policy multi-tenant |
| Chat -> runtime D y operaciones thread | XOC firma asimétricamente; nueva Function verifica pública y purpose/aud estrictos | Audiencias separadas runtime/worker/thread. Actor/contexto y hash de body/phase/thread; 120 s máximo D, 60 s history/delete, ajustables a la baja. Nunca access ni agent:invoke |
| Continuación runtime | Secreto exclusivo de continuación de SOPHIA, custodiado en Key Vault y accesible sólo a MI del runtime aislado; el mismo runtime verifica | No sirve como access token XOC. 120 s, thread/run/call/tool hash y contexto ligados; XOC lo transporta opaco. No concede ejecución de una segunda herramienta |
| Voice como componente, NO MCP | Entra emite token de aplicación de MI a una audiencia XOC exchange por registrar/aprobar; XOC verifica firma, issuer, aud, tid, oid/appid/azp y permiso de aplicación exactos | MI candidata existente, contrato nuevo. Entra tenant no es tenant XOC. El humano y su delegación se validan aparte por XOC |
| Bearer de APIs para Voice | Emisor backend con key KMS Voice distinta; verificadores XOC públicos | type/purpose voice_api, aud exacta XOC, actor/contexto y allowlist de operaciones actuales; vida propuesta 5 min, renovación sólo exchange con prueba MI+humano. Jamás aceptado como capability Chat/worker |

Los IDs URI/aliases de emisor, audiencias y claves son nombres de contrato PROPUESTOS, no recursos ya registrados. Pin de alg ES256 y kid conocidos; prohibir none, confusión HS/RS/ES y URL jku/x5u enviada por token. KMS firma con clave no exportable; convertir firma DER a formato JOSE correctamente y probarlo. [Claves asimétricas KMS](https://docs.aws.amazon.com/kms/latest/developerguide/symmetric-asymmetric.html).

Permiso por tool: scope read del componente limita el techo, pero siempre evalúa registry/Policy con ToolContext. El worker comprueba la firma y los claims, no sólo que llegó a una red interna. Identity policy/resource policy de Invoke se revisan juntas; un campo callerArn no prueba quién invocó. Administradores cloud siguen siendo parte explícita de la confianza operativa. [Permisos Invoke](https://docs.aws.amazon.com/lambda/latest/dg/access-control-resource-based.html).

### Separación real, no sólo claims

- Runtime Azure nuevo: MI sólo para proyecto Foundry necesario, storage propio y secreto de continuación. No puede leer JWT_SECRET, agent keys, secretos AWS/Voice ni firmar identidades XOC. No importar BackendClient/dispatcher legacy en el paquete de Chat gobernado.
- Key Chat sólo puede ser usada por el emisor XOC aprobado; key Voice sólo por su exchange backend aprobado. El worker y Azure/proxy son verificadores, no firmantes de estas credenciales. Un mismo backend confiable puede emitir ambos propósitos; no se vende eso como aislamiento frente a compromiso TOTAL del backend emisor.
- Proxy Voice no debe conservar acceso de lectura al HS256 global: validar el humano a través del exchange XOC autenticado con MI y recibir contexto verificado. Esto cambia sólo bootstrap/identidad; no audio, WebSocket de negocio ni tools MCP. Contrato/mensajes visibles existentes deben preservarse.
- La Function original contiene componentes compartidos: no quitar su secreto global ni cambiar validadores compartidos. Cerrar específicamente las entradas legacy SOPHIA después de mover Chat; preservar history/delete autorizados hasta drenar/migrar sesiones, mediante capability dedicada, sin reabrir herramientas. Ningún requisito de acceso o cambio a VICTOR/On-Premise.
- Acreditar que el runtime aislado usa el mismo proyecto/agente/thread store autorizado: mover URL no migra mágicamente hilos. Mantener bindings AgentSession; probar history/delete de sesiones anteriores antes de cortar la URL antigua.

Inventario mínimo de infraestructura PROPUESTA para esta frontera:

| Recurso | Justificación / decisión pendiente |
| --- | --- |
| Una Lambda worker + rol + log group + versiones/alias | Limitar herramientas fuera de Chat; único host MCP de la opción principal |
| Dos claves KMS de firma, Chat y Voice | Separar propósitos y acceso real a firmantes; sólo públicas en verificadores; sin key compartida con Azure |
| Una Function App SOPHIA con MI propia y plan Flex Consumption | Separar proceso/permisos del runtime compartido; publicar fuente seleccionada del repo identificado |
| Storage requerido por esa Function y secreto exclusivo de continuación | Reutilizar custodia sólo si se acredita aislamiento de acceso por scope; de lo contrario aprobar cuenta/contenedor/Vault separado. No un almacén nuevo de datos de negocio |
| Audiencia/permiso de aplicación Entra para exchange Voice | Autenticar el componente actual; no convertirlo en cliente MCP ni usar Entra tenant como tenant XOC |

No se crea ninguno aquí. La configuración del agente se restringe por run; no se necesita crear otro agente/modelo por defecto. El runtime y la infraestructura de consumidores ajenos permanecen en su sitio.

### Exchange legacy: condición de cierre

Hoy un humano autorizado obtiene un bearer SOPHIA legacy en POST /agents/auth/token-from-user; la MI de Voice no se prueba allí. Por tanto, bloquear sólo tools legacy en Azure NO cierra llamadas directas a XOC.

Transición propuesta con responsable de Seguridad + backend + Voice:

1. Añadir contrato de exchange Voice con prueba de componente, sin ampliar operaciones. Proxy migra sólo identidad/bootstrap y deja de necesitar HS256. Revalidar sus siete llamadas existentes con mocks y pruebas autorizadas.
2. Dejar de emitir legacy SOPHIA a un humano solo. Chat gobernado nunca usa ese exchange, ni envía JWT humano al runtime.
3. Verificadores XOC rechazan específicamente el bearer SOPHIA legacy en las rutas inventariadas y aceptan el nuevo bearer Voice sólo en su allowlist; no un DENY global de agent:invoke. Mantener consumidores distintos fuera de esta migración.
4. Registrar emisores, verificadores, máximas vidas y versiones con riesgo temporal. Cerrar sólo con pruebas negativas en TODOS los verificadores afectados y sin necesidad de tokens legacy. Esperar la expiración sin quitar la aceptación/emisión no cierra nada.
5. Apagar ruta de tools SOPHIA legacy de la Function original; probar llamada directa Azure con humano, capability de otro propósito y bearer antiguo: cero tools/BackendClient. No eliminar infraestructura compartida.

Si una credencial HS256 ya comprometida puede fabricar un access humano aceptado por auth, retirar accesos actuales al secreto no remedia ese incidente. Inventario D.2 debe declarar esa exposición; rotación/migración del emisor humano requeriría aprobación aparte. No prometer contención frente a una clave comprometida que deliberadamente sigue siendo válida. Sí exigir que los componentes SOPHIA desplegados no puedan obtenerla. No rotar nada en este plan.

### Revocación y frescura

Revalidar usuario/delegación con mecanismos backend existentes antes de emitir cada sobre; no inventar una consulta SQL ni aceptar la delegación que diga el modelo. Envelope expira a 30 s y nunca supera expiración/deadline del contexto padre; sin refresh ni cache de contexto entre requests. Cambio de tenant crea otro sobre/hijo. Rechazar firmas/kid no admitidos; cachear sólo claves públicas/metadata, no decisiones de ownership.

Revocación ordinaria tiene una ventana residual de hasta la vida del sobre ya emitido; no se afirma revocación instantánea de un JWS offline. Incidente: detener admisión/canary y deshabilitar alias/permisos del componente, conservando auditoría. Una petición aceptada puede completar una lectura en su límite de ejecución. Rotación coordinada futura mantiene sólo claves públicas antiguas el tiempo aprobado, no conserva acceso a firmantes viejos indefinidamente.

## 8. Catálogo y negocio sin cambios

| Tool | Resolución autorizada |
| --- | --- |
| xoc.tickets.list | Tenant efectivo; USER/ADMIN según Policy actual, no filtro inventado de creador |
| xoc.tickets.get | Clave tenant+ticket; ajeno e inexistente indistinguibles al consumidor |
| xoc.documents.list | Jobs/metadatos proyectados del tenant, no documentos completos ni URLs secretas |
| xoc.notifications.unread_count | Tenant efectivo + usuario, no count de todo el tenant |

Catálogo = cuatro nombres revisados ∩ enabled ∩ READ_ONLY ∩ Policy. tools/call reautoriza aunque el cliente o modelo ignore el listado. ADMIN_XOC necesita delegación válida; SUPERADMIN denegado. Dashboard/integrations permanecen disabled; no escritura ni shell/SQL libre. Una consulta de ticket ajeno puede ejecutar un handler y una lectura por clave antes de fallar; no confundirla con DENY previo de rol/argumentos que sí exige cero handlers.

## 9. Aislamiento de fallos y presupuesto

Parámetros iniciales PROPUESTOS para pruebas, no capacidad medida:

| Frontera | Límite/mecanismo y verificación |
| --- | --- |
| Admisión Chat de prueba | Allowlist tenant+usuario calculada en servidor. Throttle de ruta POST /chat inicialmente 1 req/s, burst 2 en entorno/canary autorizado; no reducir globalmente API/Voice |
| Worker | ReservedConcurrency=8, 512 MiB, timeout duro 5 s; comprobar cuota y dejar holgura a otras Lambdas. No provisioned concurrency inicialmente |
| Cada consulta | Presupuesto worker desde Chat 3.5 s; watchdog propio y cierre/reap del hijo en finally, sin tareas sobrevivientes. Si no hay tiempo, no arrancar una tool |
| Lecturas | Botocore connect/read timeout y total_max_attempts=1 acotados en proveedor del worker. Paginas/bytes/lecturas con presupuesto real a implementar, sin modificar semántica de helpers ajenos; exceso falla seguro, no devuelve conteo falso |
| Pools | SDK Lambda/DynamoDB y HTTP runtime con pools pequeños acotados; no abrir una conexión por fila ni compartir cookies/identidad entre sesiones |
| Retry | Cero retry automático del tools/call o de Invoke tras timeout ambiguo; sólo el usuario puede iniciar otro turno. READ_ONLY no equivale a exactly-once ni a costo cero |
| Circuit breaker | Contador simple por entorno y dependencia, apertura propuesta 30 s tras 3 fallos; no librería/DB nueva. Es amortiguación local, NO límite global. Los límites duros son admisión/concurrencia/deadline |

Reserved concurrency limita y reserva capacidad; no mantiene caliente la función ni cuesta como provisioned concurrency. [Control AWS](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html).

Los helpers actuales pueden paginar más datos de los que finalmente proyectan. Los 40 reads/5 s del fake NO limitan DynamoDB real. El proveedor del worker necesita un presupuesto medido; no atribuir esta protección al lab o a un asyncio timeout alrededor de código bloqueante.

| Fallo | Respuesta objetivo y test |
| --- | --- |
| Runtime Azure no responde | Final seguro dentro del deadline; cero tools si aún no pidió una. Chat no reinicia otros stacks. Test timeout/5xx antes y después de tool |
| MCP/worker caído | Tools temporalmente no disponibles; nunca fallback a Executor local, APIs legacy o otro tenant. Test worker error/throttle/hijo muerto |
| Documents falla | Error sólo de esa llamada. No abrir breaker global de tickets/Inbox por ese error; solicitudes posteriores a dependencias sanas funcionan. Proceso por consulta evita residuo |
| Saturación | Rechazo rápido, no cola nueva ni acumulación de hilos. Prueba de carga autorizada: límite de hijos y de concurrencia, memoria, throttles y salud de rutas ajenas |
| Auth/KMS/JWKS indisponible | No emitir/aceptar contexto por defecto; sólo claves públicas cacheadas aún válidas, nada de aceptar firma desconocida. Cero datos antes de verificación |
| Pérdida de conexión | No inventar resultado; cero reintentos automáticos. La Lambda invocada puede seguir hasta su timeout aunque Chat cierre el socket; registrar intento ambiguo y acotar ese costo |

Límite residual: POST /chat comparte hoy Lambda con endpoints Agents/Live Voice. Separar worker evita que su CPU/memoria se agoten dentro de Chat, pero esperar al modelo todavía ocupa Chat; no garantiza aislamiento total de esos endpoints. Throttle por ruta y holgura de cuota son gate. Si no bastan en carga, detener activación y proponer separación de ruta en otro cambio, no prometer que nada puede afectar Voice. Tablas y cuenta cloud también siguen siendo dependencias compartidas.

### 20 segundos completos

El código D inicia governed_deadline=ahora+18 DESPUÉS de resolver usuario/sesión/rutas y limita llamadas individuales a 9 s. Eso no cubre el inicio de la petición, persistencia final ni todos los efectos de connect/read de requests. No existe garantía total 9+9. No cambiar timeout Lambda en esta tarea ni como atajo posterior.

Asignación inicial para diseñar y luego medir, en segundos:

| Trabajo | Presupuesto propuesto |
| --- | ---: |
| Autenticación humana | 0.7 |
| Resolución tenant, AgentSession y rutas | 0.8 |
| Runtime/modelo primer turno, red incluida | 5.5 |
| Firma/transporte de capabilities (acumulado) | 0.3 |
| Worker entero | 3.5 |
| Runtime/modelo segundo turno, red incluida | 5.5 |
| Persistencia/serialización final | 0.7 |
| Cierre de recursos/margen interno | 0.3 |
| Reserva hasta los 20 s | 2.7 |
| Total | 20.0 |

Desglose orientativo del worker 3.5: Invoke/red+cold start 0.5, verificación 0.15, arranque SDK/hijo/initialize 0.8, gateway+lecturas 1.5, proyección/cierre/respuesta 0.55. Son hipótesis, no percentiles medidos. El smoke Windows no demuestra ninguno de esos tiempos cloud.

Implementación posterior: deadline monotónico desde entrada a Chat, acotado por get_remaining_time_in_millis menos reserva de respuesta; trasladar remaining/deadline firmado al worker, restar red y clock skew. Limitar realmente lecturas HTTP de runtime, tamaño y espera; requests timeout no es cronómetro total. No arrancar segundo turno si no queda presupuesto y no decir que un read timeout cancela el modelo remoto.

Medir request_id correlacionado, t_auth/session/runtime1/sign/invoke/cold/initialize/policy/handler/data/projection/runtime2/persist/end-to-end; recuento pages/bytes, calls=1, RSS, hijos vivos, throttles, codes y flags/versiones. Sin prompts, tokens, argumentos completos o resultados en logs; evitar tenant/user como dimensión métrica de cardinalidad ilimitada. Audit original conserva su contexto seguro.

Criterios propuestos de STOP: una fuga/bypass/handler tras DENY; segundo handler en un turno; hijo no cerrado; memoria >80% sostenida; regresión de rutas ajenas; p95 e2e >16 s o p99 >18 s en muestra controlada suficiente; timeout >1% o error técnico >2% en 100 solicitudes autorizadas; worker p95 >3 s; cuota sin holgura. En smoke pequeño, cualquier timeout inesperado suspende expansión, sin fingir un p99 fiable. El error deliberadamente inyectado se cuenta separado. Si no encaja, tools no disponibles y nuevo diseño de latencia, no subir a ciegas los 20 s.

## 10. Secuencia pequeña de transición

1. **Contrato y laboratorio verificable:** primer bloque local de la matriz, envelope firmado ficticio, stdio, tamaños, deadlines, manifest. Sin cloud ni tocar Chat.
2. **Integración mínima e identidad:** puerto de ejecución Chat, worker productivo, artefacto canónico y D.2; transición de identidad Voice exclusivamente. Tests offline y diffs separados por repositorio, sin activar.
3. **Entorno controlado aprobado:** validar runtime Linux, IAM real y proyecto/agente de prueba identificado. No asumir DEV. Si no existe, aprobar worker/tablas fixtures y runtime aislado para pruebas; no usar filas ajenas de PROD.
4. **Release aislado:** acreditar bytes PROD, base y diferencias; generar artefactos y respaldos/restauración ensayada. No publicar main completo ni multi_planner. Cerrar firma/bypass antes de activar MCP en usuarios.
5. **Ventana/activación:** server-side allowlist, métricas, smoke y rollback del runbook. Ampliación sólo con aceptación de seguridad/funcionalidad/latencia.
6. **Cierre:** evidencia, versiones definitivas, inventario de verificadores y credenciales retiradas, responsables, retención y seguimiento. MCP para Voice o otros agentes NO comienza automáticamente.

## 11. Gates y riesgos

| Gate | Decisión actual |
| --- | --- |
| Autorizar primer bloque local después de revisar este plan | GO técnico; falta aprobación expresa del usuario para código |
| Implementar toda integración productiva | Condicionado a aprobar arquitectura/identidad/recursos y alcance exacto; se puede trabajar offline sin acceso cloud |
| Deploy runtime Azure | NO-GO hoy: artefacto actual, empaquetado aislado, contrato de claves, rollback y pruebas pendientes |
| Deploy worker o Chat con flags inactivos | NO-GO hoy: no existe implementación aprobada ni release/base/ensayo operativo |
| Activación MCP / smoke real | NO-GO hoy: D.2 y bypass, compatibilidad y presupuesto cloud no cerrados |

HIGH: identidad compartida y exchange legacy; procedencia de Function PROD sin acreditar; contextos/firmas no deben sustituirse por profiles. MEDIUM: latencia/cold start, paginación real, recursos compartidos Chat/Voice, locks Linux y continuidad de threads. LOW en laboratorio: ambigüedad README 64 KiB y falta de versionado Git propio; no bloquean diseñar el primer bloque.

No se dice que el único blocker sea legacy: también faltan runtime/base productiva, identidad, empaquetado, recuperación y mediciones. VICTOR/On-Premise NO es gate.

## 12. Resumen ejecutivo — ocho respuestas

1. **Arquitectura:** worker XOC aislado con MCP stdio por consulta; aprovecha lo probado y contiene recursos sin un servicio HTTP público innecesario.
2. **Cliente/servidor:** cliente SDK en Lambda auxiliar XOC; servidor hijo dentro de esa Lambda. Chat sigue autenticando/orquestando; Azure sólo pide información y presenta resultados.
3. **Reutilización:** SDK, adaptación de catálogo/esquemas, pruebas y proyección; Gateway/Policy/Executor/Audit/helpers originales como artefacto versionado, nunca una copia divergente.
4. **Seguridad faltante:** sobre firmado y ligado al contexto, roles mínimos, D.2, runtime sin HS256 global, exchange Voice probado por componente, cierre selectivo de SOPHIA legacy y aislamiento de firma.
5. **Caídas:** límites worker/hijo/dependencias y admisión Chat; salida segura, sin retries ambiguos ni fallback. Se declara el riesgo residual de Lambda Chat compartida.
6. **Respaldo:** fuentes + artefactos exactos + configuración/identidad y referencias a secretos bajo custodia; recuperación ensayada de versiones compatibles. No restaurar bases por un error de código.
7. **Bloqueos:** código local sólo espera autorización de ese bloque; deploy espera implementación, base/artifacto y rollback; activación además exige identidad/bypass cerrados, smoke/carga y latencia aceptados.
8. **Primer código a autorizar:** el bloque L1 de MCP2_MATRIZ_CAMBIOS_Y_ACEPTACION.md, exclusivamente local en el laboratorio: contexto firmado ficticio, worker/stdio y límites probados; sin cambios en backend/runtime/Voice ni recursos cloud. No ejecutado por este plan.
