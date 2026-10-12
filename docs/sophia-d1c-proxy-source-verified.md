# SOPHIA D.1c — Informe actualizado para continuar hacia MCP sin intervenir VICTOR

Fecha: 2026-09-14. **Fuente del proxy acreditada; VICTOR fuera de alcance;
hardening de SOPHIA todavía pendiente. No es una aprobación de deploy.**

El usuario actualizó `proxy-sophia-xoc` y pidió avanzar lo necesario antes de MCP.
Después aclaró expresamente: NO debemos tocar VICTOR ni su repo On-Premise;
nuestro MCP no contempla a VICTOR. Su repo, contrato o migración de identidad
NO son prerrequisitos para continuar el trabajo de SOPHIA/MCP.

Esta decisión sustituye los requisitos anteriores que condicionaban MCP a
acreditar VICTOR. No convierte la autenticación compartida en un aislamiento
seguro ni autoriza cambios globales que puedan romper otros consumidores.

Esta actualización modifica únicamente documentación. Se comprobaron los HEAD
y estados Git locales; la evidencia Azure y los resultados de tests siguientes
proceden de la revisión previa del 14-sep. No se repitieron consultas Azure ni
tests en esta actualización editorial.

## 1. Resultado y alcance real

- En la revisión previa, el HEAD del proxy coincidió con los archivos de aplicación
  comparados con Azure (9/9, con normalización de finales de línea).
- En esa revisión se añadieron 16 pruebas offline del contrato HTTP actual, sin
  modificar runtime, y se revalidaron las suites backend, D/D.1 y runtime SOPHIA.
- En esa revisión y en esta actualización no se implementó identidad de componente,
  DENY general, separación de claves,
  MCP, cambios Voice/VICTOR, Step Functions, RDS, Push, Inbox, Web o Mobile.
- No se ejecutaron push, commit, deploy, cambios de flags, permisos, emisión de
  tokens reales, tools productivas ni escritura remota.

## 2. Estado de repos

| Repo | Rama / HEAD local revalidado | Estado conservado |
| --- | --- | --- |
| proxy-sophia-xoc | main / 93107ac2a4ad08b2c28e6242fb0aeda666eaf54f | Sólo tests/test_backend_contract.py, sin commit |
| XOC-API-AWS | main / ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9 | Tres informes no versionados; se actualizan éste y la nota del gate anterior |
| XOC_MICROSERVICIO_AGENTES | main / 6249064a9bd69bb388369d03f502b142bac93e1b | Ninguno |

El pull lo hizo el usuario. Se preservaron los informes previos no versionados
`docs/cases-tenant-hotfix-and-d1c-identity-gate.md` y
`docs/sophia-phase-d1b-inventory-gate.md`. No se mezcló el hotfix cases con SOPHIA.

Commits recibidos después del HEAD antiguo 87dcfd2:

1. e894f6b1773429f2c576a8075dcbc18c8549babf — feat: add voice tool calling parity.
2. 93107ac2a4ad08b2c28e6242fb0aeda666eaf54f — chore: align proxy config and cleanup tracked artifacts.

En el nuevo HEAD `.env` y los pyc ya no están versionados. Esto no borra su
historial ni demuestra que una credencial histórica haya sido invalidada.
No se leyeron valores ni se reescribió historia o rotó nada.

## 3. Comparación con Azure

Evidencia de la revisión del 14-sep: metadata y `sha256sum` en la réplica,
sólo lectura. No reconsultada durante esta actualización documental:

- Suscripción: 884feb5e-8492-4d88-8777-6391393372f5.
- Resource group: rg-sophia-poc.
- Container App: ca-sophia-voice-proxy.
- Revisión: ca-sophia-voice-proxy--0000037.
- Réplica: ca-sophia-voice-proxy--0000037-6d97645fc5-hm7xk.
- Imagen configurada: xocproxysophiaacr.azurecr.io/proxy-sophia:v15.
- Tráfico: 100% latest; latest ready es la revisión indicada.
- MI: SystemAssigned, principal fc6af5ba-a671-47a5-840c-97a5984f0cac,
  tenant Entra 50ac7fef-2120-437f-ad31-6beb036a3723.

Se compararon blobs Git normalizando CRLF a LF contra los hashes del contenedor.
Coinciden **9/9 archivos** (8 Python, incluyendo el init vacío, y requirements).
No se dice que un checkout Windows CRLF sea byte a byte igual sin normalizar.

| Archivo relativo a /app o raíz Git | SHA256 del contenido LF, coincidente |
| --- | --- |
| app/__init__.py | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 |
| app/auth.py | eb0be1c4df428395aac5b9f7f0a34b53f8fbfa33b41314ba48308e6deb5973f4 |
| app/backend_client.py | aac9c11fdd660464cd6e455bced2d3b86e18986e29fc04c1396e8395aebdcebb |
| app/tool_executor.py | 3158e0fba8201067415c1c62a2b94cc3754fe33b43043d926329b354c23ac430 |
| app/main.py | b1c77f7bb119ea7d835cfbd1fcde3602eabf66999678c915f309b48c8ee4fa51 |
| app/proxy.py | 2b775bd7b27342ef91ef708935c86285416eec19cd34a5e9d647f4bc3530f308 |
| app/azure_client.py | 74f135fde3feb252e1f0c7648f95aeb2b88d1ad47b376cae3c9d0f9ead8802b0 |
| app/config.py | d9696b00954d6699908a29eedb16cac220f3884e90e0966ef626e7ed0a0c6a83 |
| requirements.txt | 2bbe743b89efbd275548f2442aaf46e45780fca3e3ff6f8f96e32f41933a9460 |

El commit intermedio e894f6b sólo coincidía en proxy.py, tool_executor.py y
requirements.txt de los ocho archivos no vacíos cotejados. El cierre lo aporta
93107ac. El código utiliza ahora tenant_id y preserva los cambios de tool calling,
transcripción, keepalive y señalización que estaban en producción.

Límite: esta evidencia acredita la fuente de esos archivos ejecutados, NO un
build reproducible de toda la imagen, el digest arrancado, la imagen base, las
dependencias instaladas o los parámetros históricos del deploy. No se leyó
configuración secreta ni se asumió que todos los otros recursos PROD son main.

## 4. Build y herramientas

Core Tools está instalado: 4.14.0. Se confirmó en su ayuda local que acepta
`--build remote`. No se ejecutó publish, ni siquiera con opciones para listar
archivos que pudieran iniciar un despliegue.

Azure Function SOPHIA y proxy son despliegues diferentes:

- Function: el procedimiento descrito por el responsable es Core Tools con
  build remoto. `func azure functionapp publish ... --build remote` publica el
  proyecto entero; no es una operación de sólo empaquetado.
- Proxy: Dockerfile usa python:3.12-slim y copia requirements.txt y app/.
  deploy/aca-deploy.ps1 contiene az acr build, creación de recursos y asignación
  de identidad: no debe ejecutarse como si fuera una actualización inocua del
  recurso existente. El responsable indicó que desplegaba mediante Azure CLI.

El commit desplegado/artefacto de prod-a-xoc-cons sigue sin acreditarse; instalar
Core Tools no resuelve ese dato. No se volvió a pedir acceso a un repo que ya existe.

## 5. Contratos de Voice que deben preservarse

El flujo real sigue siendo:

1. Web/Mobile entrega JWT humano a /ws.
2. auth.py verifica con JWT_SECRET HS256; no es la identidad del componente.
3. fetch_agent_context hace POST /agents/auth/token-from-user, Authorization
   humano y body únicamente agentType=SOPHIA.
4. El proxy obtiene access_token y metadata tenant/user_name/role/plan_status.
5. ToolExecutor llama directamente XOC usando el bearer del AgentContext.

La MI sólo está utilizada en azure_client para el enlace a Azure/Voice Live;
no aparece una prueba de servicio en el exchange XOC. El bearer SOPHIA actual
continúa obtenible por un cliente humano autorizado. Tener la fuente correcta
NO cerró el bypass.

| Tool | HTTP que debe inventariarse, sin ampliar permisos |
| --- | --- |
| ticket_create | POST /tickets |
| ticket_get | GET /tickets/{id} |
| ticket_patch | PUT /tickets/{id} |
| scan_summaries_list | GET /scans/agent/summaries |
| scan_summary_get | GET /scans/agent/summaries/{id} |
| scan_summary_findings | GET /scans/agent/summaries/{id}/findings |
| scan_findings_list | GET /scans/agent/findings |

Las capabilities de contexto no constituyen una policy backend del dispatcher.
El filtro de información mínima para crear ticket no sustituye confirmación
humana C.3. Los callbacks de tools siguen enviando resultados al modelo y al
cliente; existen mensajes de error con response.text, a revisar en el alcance
Voice futuro, no corregidos incidentalmente aquí. No se declara Voice protegido
por Tool Gateway, ni estos tests como prueba de su seguridad integral.

## 6. Pruebas realizadas y nuevas

Archivo nuevo: proxy-sophia-xoc/tests/test_backend_contract.py.

16 casos nuevos, incluyendo parametrizaciones:

- Request/response real del bootstrap mediante httpx.MockTransport.
- Sin usuario: cero HTTP; sin access_token: no reutiliza JWT humano como fallback.
- 401/403/500 de bootstrap: una llamada, sin retry ni tools alternativas.
- Siete tools: métodos y rutas exactas; usa el bearer de contexto, no un token
  introducido en argumentos por el modelo. GET/PUT conservan ticketId UUID.
- Scans sólo proyecta filtros previstos; tenant/user/role del modelo no se
  convierten en query params. No confundirlo con validación real de ownership.
- Tool desconocida: cero HTTP; inventario de siete tools sin cambios.

| Suite | Último resultado registrado (revisión previa del 14-sep) |
| --- | --- |
| Proxy antes de añadir tests | 5 passed |
| Proxy después | 21 passed |
| Backend D/D.1 | 114 OK |
| Backend completo | 249 ejecutados: 247 OK, mismos 2 fallos históricos VICTOR |
| Runtime SOPHIA y contrato cruzado XOC | 220 passed, 6 warnings datetime.utcnow |

Fallos históricos: test_global_url_wins_and_marks_source_global y
test_normalize_plans_skips_empty_and_keeps_legacy, en
test_assess_ticket_automation.AssessTicketAutomationTests. No se editaron.

Tests offline: claves ficticias, metadata EC2 deshabilitada, SQLite en memoria
para backend y sockets externos bloqueados (connect y connect_ex). No se creó
ningún ticket, sesión Voice real, notificación ni ejecución Step Functions.

El Python global no tenía pytest; se reutilizó el entorno local existente
XOC_MICROSERVICIO_AGENTES/.venv sin instalar paquetes ni tocar requirements.
Proxy se comprobó con Python 3.13.1, pytest 9.1.1 y httpx 0.28.1; no son una réplica
exacta de sus pins productivos (Python 3.12, pytest dev 8.3.2, httpx 0.27.0).
No había websockets en ese entorno: NO se ejecutó smoke de audio/WebSocket.
Estos tests sólo importan el contrato de bootstrap/tool executor.

Se usó `python -B` y pytest `-q -p no:cacheprovider tests`, mediante wrapper offline
en memoria. Backend usó unittest.defaultTestLoader.discover('tests'), equivalente
a unittest discover -s tests con el mismo aislamiento, y las dependencias locales
ya disponibles en ../.tmp-ticket-sync-tests. No se alteraron tests para ocultar
fallos del baseline ni se afirma paridad exacta de dependencias productivas.

## 7. Alcance corregido por decisión del usuario

### Incluido en los próximos trabajos de SOPHIA

- SOPHIA Chat: conservar el circuito Fase D existente, cerrar accesos alternativos
  a sus tools y mantener la autoridad en ToolContext, Policy, Executor y Audit.
- Identidad Chat/Voice: distinguir componentes con evidencia verificable, sin
  permitir que un JWT humano o campos del modelo se conviertan en identidad de
  servicio. Voice se considera aquí para separar autenticación y preservar su
  contrato, NO para conectarlo a MCP ni reescribir sus tools.
- Separación criptográfica correspondiente a SOPHIA, con firma restringida y
  verificadores que no puedan fabricar tokens de otros componentes.
- Tests de aislamiento, compatibilidad y un smoke controlado posterior.
- Después de superar esos gates: MCP-1 READ_ONLY reutilizando el gateway.

### Excluido

- Repo, servidor, identidad o migración de VICTOR On-Premise.
- Integración de VICTOR con MCP, sustitución de su runtime o modificación de su
  lógica, workers o Step Functions.
- MCP para Voice, rediseño de audio/WebSocket o de su ejecución de tools.
- Refactors generales, RDS/SQL/migraciones, Push, Inbox, Web y Mobile.
- Modificaciones de C.3, auto-ticket, nuevas tools WRITE/DESTRUCTIVE o permisos
  ampliados para hacer pasar una prueba.

No se volverá a pedir el repo ni la acreditación contractual de VICTOR como
condición de MCP. Tampoco se declarará VICTOR auditado, migrado o protegido por
estos cambios. Los fallos históricos de sus tests se reportan separados y no
se corrigen incidentalmente.

### Dependencias compartidas: precaución técnica, no nuevo alcance VICTOR

El repo Azure disponible contiene handlers SOPHIA y VICTOR en una misma Function
App, y la auditoría anterior identificó material HS256 compartido. Esto NO
demuestra que el VICTOR On-Premise del usuario dependa de esa Function, pero sí
impide asumir que cambiar un helper, configuración o deploy compartido sólo
afectará SOPHIA.

- No aplicar un DENY global de `agent:invoke`, sustituir el secreto global,
  cambiar el contrato genérico o retirar rutas para resolver SOPHIA.
- No considerar `agent_type`, `component_id`, IP o URL como prueba independiente
  si la autenticación que los sostiene sigue siendo compartida.
- Un componente que conserva acceso al secreto HS256 puede seguir firmando
  tokens aceptados por sus verificadores. Añadir claims no elimina ese riesgo.
- La futura separación debe comprobar también el acceso efectivo al material
  de firma: dos módulos en un proceso con acceso al mismo secreto no constituyen
  por sí solos una frontera criptográfica.
- Si un ajuste concreto no puede aislarse en SOPHIA, reportar archivo/contrato,
  impacto y alternativa acotada antes de tocarlo. Eso detiene ese ajuste, no
  convierte obtener o modificar VICTOR en un requisito general de MCP.

## 8. Qué está implementado y qué falta realmente

### Base local que se conserva

- Fase D: Chat recibe `tool_request`, lo somete al gateway y entrega ToolResult
  sanitizado al segundo turno. Máximo una ejecución y un segundo turno por
  mensaje. El runtime no decide usuario, tenant, rol, delegación ni ownership.
- READ_ONLY del registry: `xoc.tickets.list`, `xoc.tickets.get`,
  `xoc.documents.list`, `xoc.notifications.unread_count`. No activar
  `xoc.dashboard.summary` ni `xoc.integrations.summary` deshabilitadas.
- Ticket proposal sigue requiriendo confirmación humana por C.3; no se introduce
  `xoc.tickets.create` como tool automática.
- D.1 parcial: guard de runtime SOPHIA gobernado y capabilities acotadas para
  history/delete. Tener esos guards en Git no demuestra que estén activos en PROD.
- Hotfix cases independiente: ownership del tenant en `GET /cases/ticket/{id}`,
  commit local `ad4ac1a`; no desplegado en estas tareas.

### Pendientes dentro de SOPHIA

1. Identidad de Voice frente a XOC. Candidato: su managed identity, con audience
   XOC y permisos aprobados, validación app-only y asociación del componente en
   servidor. La existencia de la MI no acredita todavía ese contrato. No usar
   tokens ARM/Foundry como sustituto ni confiar en `body.component=voice`.
2. Separar identidad de servicio del contexto humano. Tenant XOC, usuario, rol
   y delegación deben venir del contexto autenticado XOC, no del tenant Entra
   ni del modelo. Un humano solo no debe obtener credenciales de componente.
3. Acotar el exchange legacy y accesos directos de SOPHIA. Preservar el contrato
   Voice mediante una identidad propia y permisos explícitos; evitar que Chat
   pueda usar esa vía como alternativa al gateway. No cortar globalmente otros
   consumidores para conseguirlo.
4. D.2 para SOPHIA: diseñar e implementar separación de firma/verificación y
   límites de confianza efectivos. No se han creado, rotado ni reemplazado claves.
   Cualquier recurso, permiso o cambio remoto requiere autorización separada.
5. Release: acreditar el artefacto/commit actual de `prod-a-xoc-cons`, revalidar
   la base real de Chat PROD y preparar cambios aislados. NO asumir main=PROD
   ni incluir `multi_planner` o cambios ajenos. Esta acreditación de despliegue
   SOPHIA sigue pendiente; no requiere el repo VICTOR.
6. Pruebas y smoke: aislamiento tenant A/B, ownership, denegaciones, identidad
   humana frente a servicio, tokens incorrectos/expirados, cero ejecución en
   DENY, ausencia de acceso directo alternativo para Chat, máximo una tool y
   un segundo turno, history/delete y confirmación C.3 sin regresiones.
7. Para Voice, smoke de compatibilidad del bootstrap y conversación/tools de su
   contrato existente, sin conectarlo a MCP ni declarar una auditoría integral.
   Registrar tiempos de primer runtime, gateway, segundo runtime y total Chat,
   errores/timeouts y cantidad de ejecuciones, sin tokens ni datos sensibles;
   contrastar con el presupuesto actual de 20 s sin aumentarlo incidentalmente.

Conclusión: el bypass legacy NO puede presentarse todavía como el único ni el
último gate satisfecho. También faltan separación efectiva, evidencia del release
SOPHIA y smoke. Ninguno de estos pendientes exige implementar o migrar VICTOR.

## 9. Decisiones GO / NO-GO actualizadas

| Decisión | Estado y motivo |
| --- | --- |
| Fuente del proxy disponible en Git | CERRADO para los 9 archivos comparados con 93107ac; no es acreditación de toda la imagen |
| Core Tools disponible | Sí, 4.14.0; no equivale a permiso ni preparación completa de deploy |
| Pedir repo/contrato VICTOR para continuar MCP | NO APLICA: expresamente fuera de alcance |
| Continuar trabajo local acotado Chat/Voice | GO de alcance: sin esperar VICTOR; identidad, hardening y tests aún por implementar |
| Declarar D.1c funcional terminado | NO-GO: evidencia y tests de caracterización no son la implementación pendiente |
| Aplicar DENY global D.1b | NO-GO: no identifica de forma segura componentes y puede cortar consumidores legítimos |
| Deploy runtime Azure | NO-GO actual: falta acreditar base/artefacto y revisar release aislado, impacto compartido y rollback |
| Deploy Chat con flag false | NO-GO actual: no hay release aislado revisado listo; flag false reduce exposición pero no demuestra seguridad del deploy |
| Activar coordinadamente flag=true | NO-GO: falta cerrar identidad/bypass/separación y validar compatibilidad |
| Smoke real de cierre | NO-GO para declararlo aprobado; primero preparar entorno/release y ejecución autorizada. Se puede diseñar y preparar localmente |
| D.2 de SOPHIA | PENDIENTE; no se migra VICTOR como parte de esta fase |
| Comenzar implementación/deploy MCP-1 | NO-GO todavía: falta cierre SOPHIA y smoke satisfactorio; NO por ausencia de VICTOR |

El GO local no autoriza push, deploy, cambios de flags, permisos Azure/AWS,
rotación de claves ni creación automática de MCP. Esta tarea sólo actualiza el
informe solicitado.

## 10. Riesgos vigentes

| Nivel | Riesgo | Tratamiento en este alcance |
| --- | --- | --- |
| HIGH | Exchange legacy accesible con autenticación humana y sin identidad independiente del proxy | Separar componente/contexto y cerrar alternativas SOPHIA sin un DENY global |
| HIGH | Material HS256 compartido permite fabricar tokens si se compromete un verificador con esa clave | Separación efectiva de SOPHIA; no afirmar aislamiento por añadir claims ni rotar claves de terceros |
| HIGH | Asumir main=PROD o publicar toda la Function con cambios ajenos | Acreditar release SOPHIA y limitar artefacto/diff antes del deploy |
| MEDIUM | Voice emite errores con response.text y mantiene tools legacy sin garantías C.3 equivalentes | Hallazgo separado; no declarar Voice protegido por MCP/Chat ni refactorizarlo incidentalmente |
| MEDIUM | Tests no replican exactamente dependencias productivas y no hubo smoke WebSocket/audio | Validar compatibilidad en entorno controlado antes de activar cambios |
| MEDIUM | Dos llamadas al runtime dentro del presupuesto Chat de 20 s | Medir latencias y timeouts en smoke; no aumentar timeout para ocultar el problema |

## 11. Siguiente paso para planificar con GPT

Preparar una implementación LOCAL, focalizada y revisable de identidad y cierre
de bypass para SOPHIA Chat/Voice, conservando Fase D y C.3. Reutilizar la fuente
del proxy ya acreditada y sus tests. No pedir ni intervenir VICTOR/On-Premise;
no proponer su migración como requisito de MCP. Toda dependencia compartida debe
identificarse por el cambio concreto y resolverse dentro de SOPHIA, o reportarse
antes de ampliar alcance.

Después: revisión de seguridad, release aislado, autorización de deploy/flags,
smoke y decisión explícita de comenzar MCP-1. MCP-1 expondrá/adaptará sólo tools
READ_ONLY usando el mismo ToolContext, Policy, Executor y Audit; no los reemplaza.
No empezar MCP automáticamente ni integrar Voice o VICTOR en ese circuito.
