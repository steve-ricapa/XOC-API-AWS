# XOC — Hotfix cross-tenant y gate de diseño D.1c

> Actualización 2026-09-14: el nuevo proxy `93107ac` ya coincide con los archivos
> productivos comparados. Véase [fuente del proxy acreditada](sophia-d1c-proxy-source-verified.md).
> Aclaración posterior del usuario: VICTOR/On-Premise está fuera de alcance y su
> repo, contrato o migración NO son requisito para SOPHIA/MCP. El informe enlazado
> contiene los gates vigentes: identidad Chat/Voice, cierre del bypass SOPHIA,
> separación criptográfica y validación del release/smoke. El texto siguiente
> conserva la auditoría histórica del 13-sep; sus exigencias de acreditar VICTOR
> como prerrequisito quedan sustituidas, no sus advertencias de dependencias
> compartidas. Esta aclaración no implementa ni despliega cambios.

Fecha: 2026-09-13. Alcances separados: hotfix implementado y guardado localmente;
D.1c investigado/diseñado, NO implementado. No push, deploy ni cambios remotos.

## 1. Resultado ejecutivo

- Corregido `GET /cases/ticket/{ticketId}`: un caso de otro tenant ya no se devuelve.
- Commit aislado: `ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9`,
  `fix: enforce tenant ownership for ticket cases`. Contiene sólo ruta y tests.
- Casos: 13/13 OK. D/D.1 backend: 114/114 OK. Backend completo: 249 ejecutados,
  247 OK y los mismos 2 fallos históricos VICTOR. Runtime: 220 OK, 6 warnings.
- Voice tiene una managed identity propia, acreditada en Azure. Es una base
  reutilizable, pero hoy NO autentica el exchange de tools frente a XOC.
- Falta la fuente/build de Voice y acreditar VICTOR global/on-premise/overrides.
  **NO-GO para implementar D.1c o aplicar ahora el DENY general D.1b.**
- D.2 sigue necesaria: los verificadores con el secreto HS256 pueden firmar.
- MCP sigue bloqueado hasta D.1 integral, D.2 y smoke satisfactorio.

## 2. Git y límites de evidencia

| Repo | Rama | HEAD al comenzar | HEAD al terminar / estado |
| --- | --- | --- | --- |
| XOC-API-AWS | main | 289ca5d02261c5988d43395f8fafc3249d2fb40a | ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9 |
| XOC_MICROSERVICIO_AGENTES | main | 6249064a9bd69bb388369d03f502b142bac93e1b | Igual; limpio, sin cambios |

Se preservó el informe previo no versionado
`docs/sophia-phase-d1b-inventory-gate.md`. No se añadió al commit del hotfix.
Este informe D.1c también queda sin commit para revisión; no hay commit funcional
de identidad. No se hizo fetch/pull/push; las referencias remotas locales no
acreditan el HEAD actual remoto. Ningún HEAD local se presenta como PROD.

Evidencia diferenciada:

- Fuente local: repos/HEAD anteriores, router, helpers, authorizer, emisores,
  consumidores y configuración versionada.
- Pruebas: offline, JWT ficticios, I/O simulado, red externa bloqueada.
- Azure actual: consultas de metadata del 13-sep y lectura de `auth.py` del proxy.
- Auditoría anterior del 13-sep: lectura completa de `backend_client.py` y
  `tool_executor.py` en la misma revisión Voice, documentada en D.1b.
- No acreditado: commit/build del proxy, bytes/commit de la Function, destinos
  efectivos AWS y runtimes VICTOR externos. No se consultó RDS.

## A. Corrección cross-tenant

### Causa exacta

`src/shared/cases_store.py::get_case_by_ticket` consulta `TicketIndex` con
`gsi1pk = CASE#TICKET#<ticketId>` y `Limit=1`. El índice es global y proyecta el
caso completo. La ruta devolvía directamente el primer resultado sin comparar
`case.tenant_id` con el tenant autenticado.

Existe `get_case_or_404(tenant_id, case_id)`, pero requiere `case_id`; no sustituye
un lookup por `ticket_id`. No se encontró otro lookup existente por ticket y
tenant. Por eso se conservó la query y se añadió validación antes de devolver
el objeto. El helper global no tenía otro caller en la fuente inspeccionada.

### Archivos del commit

1. `src/handlers/routes/cases.py`: 12 líneas añadidas / 2 retiradas.
2. `tests/test_cases_tenant_isolation.py`: 13 pruebas nuevas.

La ruta reutiliza `get_current_tenant_id` antes del lookup. En API Gateway,
`tenantId` ya contiene el tenant efectivo proyectado por el authorizer. Para
el modo LOCAL_JWT_AUTH, los claims crudos verificados pueden contener
`acting_tenant_id` y delegación explícita: la ruta respeta esa representación
usando el vocabulario de delegación del helper existente. No muta los claims ni
cambia el authorizer o las dependencias globales.

Sólo después de resolver el tenant se hace la query. El `tenant_id` del caso es
Number en DynamoDB (int/Decimal); se compara con el tenant resuelto. No se
coacciona un owner inválido a un tenant autorizado.

### Antes y después

| Petición | Antes | Ahora |
| --- | --- | --- |
| Humano A, caso A | 200, caso A | Igual, sin cambiar payload |
| Agente A, caso A | 200, caso A | Igual |
| Humano A, caso B | 200, exponía B | 404, sin datos B |
| Agente A, caso B | 200, exponía B | 404, sin datos B |
| Ticket inexistente | 404 | Mismo 404 existente |
| Falta tenant autenticado | Podía consultar/devolver un caso | Rechazo antes del store |
| Body/query intenta elegir B | No existía control de pertenencia | No se usa como autoridad |

Caso ajeno e inexistente comparten código HTTP y contenido:

```json
{"error":"No case found for this ticket","code":"not_found"}
```

También se comprobó igualdad de cuerpo y cabeceras, ausencia de contenido del
caso y tenant, owner faltante/inválido, delegación y rechazo de tokens no-access
antes del store. No se añade mensaje «existe en otro tenant» ni logging del caso.
No se afirma tiempo constante: se conserva el índice global autorizado por el
alcance. La lectura interna ocurre, pero nunca se devuelve/serializa B al cliente.

No se modificaron store, índices, schema, SQL/RDS, autorización general de agentes,
otras rutas cases, SOPHIA, Voice, VICTOR, Step Functions, Push/Inbox, C.3 o clientes.
Este hotfix NO cierra los demás bypasses D.1b ni cambia su clasificación.

### Tests y baseline

| Suite | Resultado |
| --- | --- |
| Nueva suite cases antes del fix | Reprodujo 200 cross-tenant; 13 tests, 12 fallos contando subtests |
| Nueva suite cases después del fix | 13/13 OK |
| D/D.1 backend | 114/114 OK |
| Backend completo, baseline | 236 ejecutados, 234 OK, 2 fallos históricos |
| Backend completo, después | 249 ejecutados, 247 OK, mismos 2 fallos |
| Runtime Azure local y contrato cruzado XOC | 220 passed, 6 warnings de datetime.utcnow |
| git diff --check / staged check | OK |

Fallos históricos preservados, sin arreglos incidentales:

- `test_assess_ticket_automation.AssessTicketAutomationTests.test_global_url_wins_and_marks_source_global`:
  esperaba `http://10.0.0.5:8000`, obtuvo `None`.
- `test_assess_ticket_automation.AssessTicketAutomationTests.test_normalize_plans_skips_empty_and_keeps_legacy`:
  esperaba `[]`, obtuvo un plan legacy con `cmd: x`.

D/D.1 comprende `test_sophia_phase_d`, `test_sophia_phase_d_infra`,
`test_chat_access_hardening`, `test_chat_tool_gateway_integration`,
`test_chat_ticket_confirmations`, `test_tool_gateway_policy`,
`test_tool_gateway_executor`, `test_sophia_thread_operations`.

Se ejecutó Python con `-B`, discovery real de unittest y pytest sin cacheprovider.
Las dependencias backend existentes se cargaron desde `../.tmp-ticket-sync-tests`;
runtime usó `.venv/Scripts/python.exe`. El primer intento sandbox no pudo leer
`six.py`; se repitió con acceso local autorizado. Eso no fue un fallo funcional.
Un wrapper en memoria fijó credenciales ficticias, SQLite en memoria, metadata
EC2 desactivada y bloqueó sockets no-loopback. No se invocaron AWS, RDS ni Foundry
para tests. Las consultas de metadata Azure fueron auditoría separada, no smoke.

Discovery empleado: `unittest.defaultTestLoader.discover('tests')` (equivalente a
`python -B -m unittest discover -s tests` bajo ese aislamiento); cases añadió
`pattern='test_cases_tenant_isolation.py'`; la focal añadió los ocho patrones
anteriores; runtime ejecutó `pytest.main(['-q', '-p', 'no:cacheprovider'])` con
`XOC_API_SOURCE` apuntando al backend local.

Conclusión: no hay regresiones adicionales observadas en esas suites. No equivale
a smoke productivo ni a afirmar que toda la plataforma está libre de bugs.

## B. Voice: fuente, despliegue e identidad actual

### Acreditado en Azure el 13-sep

| Dato | Evidencia |
| --- | --- |
| Suscripción / tenant Entra | 884feb5e-8492-4d88-8777-6391393372f5 / 50ac7fef-2120-437f-ad31-6beb036a3723 |
| Resource group | rg-sophia-poc |
| Container App | ca-sophia-voice-proxy |
| Revisión ready | ca-sophia-voice-proxy--0000037 |
| Imagen configurada | xocproxysophiaacr.azurecr.io/proxy-sophia:v15 |
| Tráfico | 100% latest |
| Digest que resuelve actualmente v15 en ACR | sha256:aaca348e69bf054370a05c78f90b8e059296e8b2db8cb975408195ad026e28b2 |
| Fecha creación/actualización de ese manifest | 2026-08-03T18:45:46.7040188Z |
| Managed identity | SystemAssigned |
| Principal/object ID | fc6af5ba-a671-47a5-840c-97a5984f0cac |
| Application/client ID | d686932d-a249-43f9-8e4c-1b6171c2b503 |
| RBAC visible en esta suscripción | AcrPull en xocproxysophiaacr; Foundry User en aoai-sophia-xoc-eus2 |
| App role assignments de ese principal en Graph | [] |
| ACR tasks listadas | [] |

`show-metadata` devolvió annotations=null; NO acredita ausencia de labels en el
config blob. La revisión usa un tag mutable: el digest consultado hoy no demuestra
por sí solo qué digest arrancó la réplica ni qué commit lo construyó. No se
inventó una relación tag→Git ni se descargó/reconstruyó una imagen sustituta.
Una lista vacía de ACR tasks no descarta docker build/push, builds rápidos ni un
pipeline externo o script del responsable.

**Pendiente:** URL del repo del proxy (no está en el workspace), branch, commit de
v15, Dockerfile/contexto, comando/script real de build/push/deploy, digest registrado
en el despliegue. Se requiere source control/evidencia del responsable, no editar
`/app` en la réplica. El repo XOC_MICROSERVICIO_AGENTES no es prueba de fuente del proxy.

### Cómo autentica hoy

Web/Mobile entregan el JWT humano al WebSocket del proxy. La lectura actual de
`/app/app/auth.py::validate_jwt` muestra verificación simétrica con jwt_secret,
algoritmo configurado y verify_exp, y exige sub. Esa función no exige type=access,
role/tenant ni audience/issuer explícitos; no es toda la autorización downstream.

`fetch_agent_context`, acreditado en la auditoría D.1b de la misma revisión,
hace POST a `/agents/auth/token-from-user` con Authorization humano y body
`agentType=SOPHIA`. Recibe `access_token` y lo reutiliza en ToolExecutor. No envía
prueba de la managed identity al exchange XOC. El JWT humano más agentType no
distingue al proxy de Postman u otro cliente autorizado.

Configuración acreditada D.1b: BACKEND_BASE_URL=
`https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com`, agent token path anterior,
AZURE_AGENT_TYPE=SOPHIA, JWT_ALGORITHM=HS256. No se leyeron valores de secretos.

### Operaciones existentes del proxy

| Tool | Método y ruta XOC |
| --- | --- |
| ticket_create | POST /tickets |
| ticket_get | GET /tickets/{id} |
| ticket_patch | PUT /tickets/{id} |
| scan_summaries_list | GET /scans/agent/summaries |
| scan_summary_get | GET /scans/agent/summaries/{id} |
| scan_summary_findings | GET /scans/agent/summaries/{id}/findings |
| scan_findings_list | GET /scans/agent/findings |

Existencia de un caller no acredita éxito ni permiso definitivo: POST /tickets
con sub de servicio tiene el problema de conversión numérica descrito en D.1b.
No se lo arregla ni se abre POST /tickets/agent para compensarlo.
Voice no utiliza en ese dispatcher history/delete de Azure; sus sesiones humanas
`/live-voice-sessions` son un flujo separado que se preserva.

## C. VICTOR: destinos posibles y fuentes

Emisor acreditado: `src/handlers/workers/assess_ticket_automation.py` en el HEAD
backend indicado. `_build_service_token` firma HS256, type=access,
sub=agent-runtime-<tenant>-VICTOR, agent_type=VICTOR, scopes=[agent:invoke],
tenant_id, iat/exp; TTL 900 s, sin audience. La identidad no viene del modelo.

`_resolve_victor_endpoint` establece esta precedencia real:

| Selección | Configuración / destino | Fuente y acreditación |
| --- | --- | --- |
| Global, primero | AGENTS_FUNCTION_BASE_URL_VICTOR + AGENTS_FUNCTION_ROUTE_VICTOR | Código XOC acreditado; prod.yml local fija http://10.20.0.22:8000 + /api/agents/VictorDurableAgent/run. Repo/commit/runtime de ese host NO acreditados |
| Tenant override, sólo sin global | TenantRuntimeSettings activo: function_base_url + function_route_victor | Helper RDS existente acreditado, valores efectivos por tenant no consultados; requieren inventario autorizado |
| Azure | Puede ser destino global/override si se configura | No es una rama fallback separada; runtime disponible en XOC_MICROSERVICIO_AGENTES, main 6249064a9bd69bb388369d03f502b142bac93e1b |
| Sin endpoint | None, route por defecto, source=fallback | No hay llamada externa a VICTOR; no inventar un deployment |

El source string `on_premise` del resolver NO demuestra que el host sea on-premise:
es la etiqueta del branch de runtime settings y puede apuntar a Azure u otro host.
Global gana sobre todos los overrides. `AGENTS_FUNCTION_BASE_URL` genérico de
SOPHIA no es un fallback implícito de este resolver VICTOR.

AWS CLI local, dentro y fuera del sandbox, devolvió:
`An error occurred (NoCredentials): Unable to locate credentials.` No se hizo
login, no se pidieron secretos y no se consultaron Lambda/env efectivos ni RDS.
Por tanto, `prod.yml` NO se presenta como configuración desplegada actual.
El HTTP privado configurado requiere revisar transporte/TLS en la acreditación;
ni IP privada ni URL acreditan identidad de componente.

### Runtime Azure disponible

`victor_agent/handler.py` valida `get_agent_token(..., 'VICTOR')`, reutiliza el
bearer recibido y llama mediante BackendClient a GET /tickets/{id} y PUT
/tickets/{id}. No se halló caller VICTOR de scans, DELETE, POST /tickets/agent,
Push o audit en este handler. No extrapolar a los runtimes no disponibles.

`shared/agent_auth.py` exige firma, exp, scopes lista con agent:invoke y tenant;
compara agent_type sólo si está presente/no vacío. No acepta un tipo explícito
diferente, pero missing/empty siguen admitidos. No se modificó ese comportamiento.

Function actual: prod-a-xoc-cons, recurso raíz Microsoft.Web/sites, sin slot en
el ID consultado, mismo RG/sub anterior. Identidad SystemAssigned:
`131a8f1e-7246-461a-9aac-f0bc08ecfe31`.
`clientCertEnabled=false`: clientCertMode=Required por sí solo NO activa mTLS.
DefaultAzureCredential está presente para Foundry en la fuente; BackendClient
NO usa esa identidad para llamar XOC, reenvía el bearer legado.

El responsable confirmó este repo/main como fuente de la Function; no entregó
commit desplegado ni script. La auditoría previa no pudo leer scm-releases con
Entra por permisos Blob; eso sigue sin acreditarse aquí. No se asumió main=PROD.

**Importante:** SOPHIA y VICTOR comparten esa Function y su identidad de host.
Su managed identity NO separa por sí sola los handlers lógicos. Mapear toda la
Function a VICTOR concedería autoridad también al código SOPHIA del mismo host.

## D. D.1c propuesto — diseño, no implementación

### Capacidades existentes frente a necesidades

| Mecanismo | Encontrado | Uso posible / limitación |
| --- | --- | --- |
| Managed identity exclusiva del proxy | Sí, acreditada | Candidata preferida para prueba de servicio Voice; falta audience y autorización XOC |
| Managed identity de Function | Sí, compartida SOPHIA/VICTOR | Acredita host, no el agente lógico ni su trabajo autorizado |
| JWT HS256 / capabilities D y D.1 | Sí | Contexto y operaciones acotadas; verifier con secreto también puede firmar |
| Emisión worker VICTOR | Sí, interna | Reutilizar punto de emisión para capacidad de trabajo acotada, no identidad elegida por humano |
| AWS IAM | IaC de Lambdas existente | No acredita todavía un exchange de componentes ni separación de signers desplegada |
| mTLS/service assertion/clave dedicada a cada componente | No acreditados para estos enlaces XOC | No reutilizar JWT_SECRET como supuesto secreto exclusivo Voice |
| AgentApiKey | Existe para ingesta | No es identidad Voice ni VICTOR; no reutilizarla para tools |

No se encontró en las rutas/emisores revisados un verificador Entra de componente
ni una credencial exclusiva del exchange Voice ya implementados. Los roles Azure
Foundry User/AcrPull no otorgan por sí solos permiso sobre XOC. App roles=[] no
prueba inexistencia universal de un API Entra; queda pendiente acreditar una
audience/registro aplicable y su autorización. No se propone infraestructura nueva
como si ya existiera.

### Mecanismo mínimo candidato

1. **Voice prueba servicio y usuario, de forma separada.** Reutilizar su managed
   identity para obtener un access token Entra destinado al exchange XOC aprobado.
   El proxy aporta además el JWT humano vigente; XOC verifica ambos por separado.
   El humano no recibe el token de servicio ni la credencial del proxy.
2. La prueba Entra debe validar firma, issuer/tenant Entra permitido, audience
   exacta XOC, vigencia, principal/app autorizado y permiso app-only del servicio.
   La combinación validada se mapea en configuración del servidor a sophia_voice.
   No basta el appid elegido por un cliente ni un token delegado humano. Si se
   autoriza por appid/azp, exigir idtyp=app y el contrato de permisos aprobado.
   No aceptar tokens destinados a ARM, Graph o Foundry como prueba para XOC.
3. El tenant Entra identifica el directorio de la carga, NO el tenant de negocio
   XOC. El tenant/usuario/rol/delegación XOC se derivan del contexto humano
   autenticado y de las policies existentes, nunca del prompt ni del token Entra.
4. El emisor XOC devuelve al proxy una capacidad de tools limitada a componente,
   tenant/actor y operaciones permitidas. Caducidad corta por aprobar (candidata
   <=300 s y nunca superior al contexto humano usado); renovación servidor a
   servidor, sin elevar scope ni mantener privilegios por desconexión del usuario.
5. **SOPHIA Chat:** conservar D y ToolContext. El caller interno XOC ya construye
   la capability de Chat, no un endpoint humano para elegir componente. El runtime
   devuelve tool_request; no necesita bearer de APIs directas. Preservar contratos
   distintos para history/delete y C.3.
6. **VICTOR:** conservar el punto de emisión interno del worker, pero diseñar
   capacidad de trabajo ligada a tenant, ticket, ejecución/operación y destinatario
   acreditado. En Azure, MI sólo puede probar el host; no autoriza por sí sola
   VICTOR. Para callbacks se requiere además esa concesión de trabajo emitida por
   XOC. Para on-premise, acreditar primero identidad/certificado/credencial real;
   no otorgarla por hostname, IP, texto de sub o body.agentType.

La recomendación de reutilizar MI es una propuesta basada en la identidad
observada, no una integración ya probada. Container Apps puede obtener tokens para
recursos protegidos por Entra sin administrar un secreto propio del cliente.
[Microsoft: managed identities en Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity).

La validación debe comprobar audiencia y sujeto/aplicación; los tokens delegados
no equivalen a app-only. El emisor y claims se deben validar con el contrato Entra
elegido, no sólo decodificarlos.
[Microsoft: validación de claims](https://learn.microsoft.com/en-us/entra/identity-platform/claims-validation).

No se añaden endpoint, dependencia, app registration, role assignment, secreto,
KMS, RSA, JWKS, audience o verificador en esta tarea. No se ejecuta una solicitud
de token de la managed identity desde el contenedor para «probar» el diseño.

### Contrato candidato, después de revisar el vocabulario existente

| Campo | Decisión de diseño |
| --- | --- |
| agent_type | Mantener familia SOPHIA/VICTOR; no usarla sola como componente |
| component_id (nuevo, nombre a aprobar) | Valor asignado por emisor tras prueba de servicio/caller interno, nunca copiado de body |
| type | Campo existente: tipo específico para la nueva capacidad; no reutilizar access humano genérico |
| aud | Audiencia exacta de APIs/operación destinataria; las capabilities D/D.1 existentes permanecen distintas |
| scopes | Reutilizar plural/lista donde corresponde al contrato agente actual; permisos concretos, no agent:invoke como wildcard |
| tenant_id / actor_user_id / actor_role / delegation_active | Contexto XOC autenticado; conservar separación actor/tenant efectivo |
| iat / exp / request_id | Requeridos, vigencia acotada y correlación segura |
| ticket_id / ejecución autorizada para VICTOR | Binding de trabajo, no acceso libre a todos los tickets por tener MI |

No se crea otra variante scope/scopes en las capabilities existentes D/D.1,
que ya tienen sus contratos. Los valores exactos del nuevo tipo/aud/scopes se
aprueban con los consumidores; no se fijan como contrato desplegado en este informe.
La proyección del authorizer deberá preservar/verificar los nuevos campos cuando
se implemente, sin convertir un JWT de componente en usuario humano.

## E. Matriz futura para D.1b

Aplicable después de acreditar consumidores y migrar emisión, NO implementada:

| Componente autenticado | Operación | Decisión propuesta |
| --- | --- | --- |
| SOPHIA_CHAT | Ticket write, scans, cases y otras tools por API directa | DENY |
| SOPHIA_CHAT | Tool Gateway D READ_ONLY registrada/habilitada | ALLOW sujeto a ToolContext/Policy; el gateway es interno, no nueva API pública |
| SOPHIA_CHAT | history/delete de thread propio | Sólo capability D.1 de esa operación; no agent:invoke |
| SOPHIA_CHAT | Crear ticket | Propuesta + confirmación humana C.3; nunca tool write automática |
| SOPHIA_VOICE | GET ticket y las cuatro rutas scans acreditadas arriba | ALLOW temporal, explícito y tenant/actor-bound; auditoría de Voice separada |
| SOPHIA_VOICE | POST ticket / PUT ticket del dispatcher actual | Excepción temporal sólo con aprobación de alcance WRITE y revisión de confirmación; no ampliar privilegios ni «arreglar» create por endpoint alternativo |
| SOPHIA_VOICE | Otras APIs, DELETE, snapshots, audit, Push/Inbox | DENY |
| VICTOR acreditado | GET/PUT del ticket de su trabajo | ALLOW con prueba de host + concesión acotada XOC y tenant real |
| VICTOR | Otras tools/APIs | DENY salvo nuevo inventario y autorización explícita |
| unknown | Cualquier tool | DENY |
| missing | Tokens legacy | Ventana de transición inventariada, después DENY; nunca inferir componente |
| Humano | APIs humanas/C.3/sesiones Voice | Mantener policies existentes; no obtiene excepción de componente |

READ_ONLY de D conserva exactamente registry/policy: ADMIN según permisos;
ADMIN_XOC sólo delegado al tenant efectivo; SUPERADMIN denegado para IA; USER según
policy existente. Una excepción Voice NO se debe declarar equivalente a Gateway D
ni utilizarse para evadir sus denegaciones.

WRITE de Voice es una decisión pendiente: desplegar su DENY sin preparar el
consumer rompería el flujo; dejarlo abierto permanentemente no cierra D.1b.
Inventariar la dependencia no equivale a autorizar nueva escritura sin humano.

## F. Migración de token-from-user sin romper clientes

1. Acreditar sources, destinos, signers y consumers; acordar la matriz y ventana.
2. Preparar en fuente controlada un exchange que valide dos pruebas (servicio y
   usuario). Puede evolucionar el endpoint actual manteniendo compatibilidad de
   respuesta para el proxy, o una entrada de servidor separada si el authorizer lo
   requiere. Resolver ese detalle con el source Voice, no improvisarlo aquí.
3. Un POST humano aislado a /agents/auth/token-from-user NO puede emitir identidad
   privilegiada Voice aunque incluya component=voice, agentType, source o headers
   decorativos. La identidad proviene sólo del mapeo de prueba de servicio válida.
4. Desplegar posteriormente el proxy desde su repo para aportar MI, conservar el
   contexto humano y renovar la capacidad. Web/Mobile siguen hablando con el
   proxy; no necesitan almacenar ningún secreto de servicio.
5. Preparar cada VICTOR acreditado para la capacidad de trabajo; no asumir que
   migrar la Function de Azure migra también 10.20.0.22 u otros tenants.
6. Observar sin enforcement los consumidores legacy con logging de metadata
   (emisor, componente autenticado, ruta, código, tenant/actor, TTL), nunca JWT.
   Missing se etiqueta legacy_unknown; no se transforma en VICTOR o Voice.
7. En el corte coordinado, dejar de emitir bearer humano→agent:invoke con permiso
   de tools; el endpoint puede conservar un bootstrap sin privilegios de tool o
   exigir la prueba de servicio. Nunca eliminarlo antes de migrar sus callers.
8. Aplicar matriz después de la ventana y smoke. No basta «esperar una hora»:
   existen signers no inventariados y el authorizer actual no exige exp presente.
   Definir fecha de corte, revocación/aceptación legacy y plan de rollback aprobado.

Una convivencia temporal sigue siendo bypass abierto, no seguridad completada.
Si el smoke falla, detener el corte; no extender silenciosamente una excepción
legacy ni cambiar flags globales para disimularlo. El rollback que reabre legacy
requiere aceptación explícita del riesgo, no se presenta como solución segura.

## G. D.2 y límites de confianza

Una MI propia autentica el workload, pero no impide que un proceso comprometido
use su propia identidad. Tampoco resuelve que el proxy o la Function tengan
material HS256 con el que puedan fabricar JWT/contexto aceptado por otros.
Añadir component_id firmado con el mismo secreto compartido NO basta para cerrar
el escenario de compromiso del verificador.

D.2 debe separar firmante restringido y verificadores sin capacidad de emisión,
con audiences/tipos y derechos de emisión distintos para humano, Chat, Voice y
trabajos VICTOR. No dar permiso de firma global a todos los runtimes. La identidad
compartida de la Function sólo acredita host: si se exige aislamiento frente al
compromiso del proceso común Chat/VICTOR, hará falta evaluar aislamiento adicional
de workloads/credenciales. No se promete que un claim lo proporcione.

Diseño compatible con claves públicas/verificación y permisos de firma mínimos;
ninguna clave, rotación o cambio HS256 se hizo aquí. No se compararon ni imprimieron
secretos. No se modificó ninguna capability existente.

## H. Riesgos, evidencia faltante y próximos permisos

| Nivel | Estado |
| --- | --- |
| HIGH: exposición cross-tenant por cases/ticket | Corregida en commit local; no afirmar corregida en PROD sin release y deploy posteriores |
| HIGH: bypass directo legacy y tokens humanos equivalentes a Voice | Sigue abierto; enforcement D.1b detenido deliberadamente |
| HIGH: HS256 compartido / MI Function compartida | D.2 y binding de trabajo necesarios; no corregidos aquí |
| HIGH: fuente/destinos Voice y VICTOR no acreditados | Impide implementación coordinada segura |
| MEDIUM: tag mutable sin proveniencia de build | Digest actual identificado; falta vínculo deployment/commit |
| MEDIUM: dos fallos históricos VICTOR | Baseline preservado, no regresiones nuevas |

Pedir al responsable, SIN contraseñas, JWT, claves ni dumps de settings:

1. Repo del proxy ca-sophia-voice-proxy; branch y SHA que construyeron v15;
   Dockerfile, contexto y script/pipeline build/push/deploy; digest desplegado.
2. Repo, branch, SHA e imagen/servicio de VICTOR en 10.20.0.22:8000; cómo se
   despliega y qué credencial/identidad de workload tiene disponible (metadata).
3. Inventario autorizado de overrides VICTOR activos por tenant y fuente/commit
   de cada destino, sin secretos; confirmar los env efectivos del worker AWS.
4. Commit y script real de prod-a-xoc-cons; artefacto/proveniencia o acceso de
   lectura al contenedor de releases si debe compararse con Git.
5. Confirmar registro/audience Entra destinado a XOC si existe y permisos app-only
   previstos para la MI del proxy. No enviar tokens ni reutilizar audiencia Foundry.
6. Aprobación del alcance temporal WRITE de Voice y plan de migración/D.2.

### Decisiones explícitas

| Pregunta | Respuesta |
| --- | --- |
| ¿Hotfix cross-tenant listo para commit? | **Sí; ya guardado en ad4ac1a**, separado y sin push |
| ¿Existe información suficiente para implementar D.1c completo? | **No.** Hay diseño candidato y MI Voice acreditada, pero faltan sources/proveniencia y VICTOR efectivo/identidad |
| ¿Puede D.1b reanudarse después de D.1c? | **Sí, condicionado** a implementación acreditada, migración de consumidores y validación; no ahora ni sólo por aprobar este documento |
| ¿D.2 sigue siendo necesaria? | **Sí** |
| ¿MCP sigue bloqueado hasta D.1 integral + D.2 + smoke? | **Sí** |

El hotfix puede revisarse para un release aislado posterior del dominio automation,
sin desplegar main completo ni arrastrar multi_planner/D/D.1. No se creó ese release
ni se desplegó nada en esta tarea. El siguiente paso de identidad es conseguir la
evidencia indicada, no implementar automáticamente un proxy o un DENY general.
