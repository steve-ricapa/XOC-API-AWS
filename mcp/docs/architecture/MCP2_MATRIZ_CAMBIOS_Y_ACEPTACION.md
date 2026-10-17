# MCP-2 — Matriz de cambios, aceptación y costos

Fecha: 14-sep-2026. Plan, no implementación. Leer junto con MCP2_PLAN_TRANSICION.md y MCP2_RUNBOOK_RESPALDO_DESPLIEGUE_ROLLBACK.md.

## 1. Cambios mínimos posteriores, separados por componente

Todo cambio de esta tabla es PROPUESTO y requiere autorización posterior. Los nombres de archivos nuevos son destinos previstos, no código ya presente. Mantener commits separados por repo cuando se autoricen; esta tarea no hace commits.

| Componente / repo | Cambio previsto | Motivo / reutilización | Impacto y límite | Prueba | Responsable / aprobación |
| --- | --- | --- | --- | --- | --- |
| XOC-MCP-LAB | L1 del §2: envelope fixture, arranque verificado, límites de bytes/deadline, pruebas | Evolucionar lo probado sin otra Policy | Sólo lab, sin credenciales reales/puertos/modelos ni modificar backend | 85 actuales + nuevas L1 | Implementador + QA; usuario autoriza sólo L1 |
| Gateway canónico / XOC-API-AWS | Manifest/build de subset de src/tool_gateway, helpers y proyección como artefacto versionado; archivo packaging por definir con inventario de imports | Eliminar sys.path de checkout como dependencia productiva, NO copiar otra Policy | Sin cambios de negocio/SQL, hash de originales; no empaquetar app completa por comodidad | Artefacto sin repo hermano, imports/fixtures/gateway iguales | BA; aprobación de empaquetado y pins Linux |
| Chat / XOC-API-AWS | src/shared/sophia_tool_chat.py: puerto de ejecución; src/shared/sophia_mcp_client.py NUEVO: Invoke firmado, validación respuesta; src/handlers/routes/chat.py: deadline de entrada/selector seguro | Reemplazar sólo ejecución directa de tools en rama MCP | Un modelo request, una tool, una continuación; C.3 intacto; flag off seguro no legacy fallback | Happy path, DENY, doble tool, timeout, sesiones/C.3 | BA/SEG; aprobación código backend |
| Worker / XOC-API-AWS + artefacto adapter | src/handlers/internal/sophia_mcp_query.py NUEVO y servidor empaquetado | Frontera IAM/recursos fuera de Chat; SDK stdio real | Sin HTTP público, sin perfiles lab ni reuso de contexto; READ_ONLY | SDK real, IAM/denial, lifecycle, bytes y carga | BA/OPS; código + recurso nuevo aprobados |
| Infra worker / XOC-API-AWS | Config Serverless independiente prevista serverless.mcp.js y script package.json por implementar/revisar; alias/versiones/IAM/logs/concurrency | Deploy independiente del worker | Rol mínimo; no heredar permisos RDS/Secrets/Write de common.js por defecto; no provisionar toda plataforma | Template estático, policy tests, diff recursos, cloud controlado | BA/SEG/OPS; aprobación costos/permisos explícita |
| Firma XOC / XOC-API-AWS | Emisor capability asimétrico específico; verifier y propósito por token; configuración de pública/kid | Cerrar D.2, separar secreto humano de componentes | No cambio del JWT humano, no rotación global; worker no firma | Alg/iss/aud/purpose/hash/exp negativos; IAM signer | BA/SEG; diseño identidad y claves nuevas autorizado |
| Runtime SOPHIA / XOC_MICROSERVICIO_AGENTES | gateway_protocol.py, gateway_runtime.py y entrypoint de paquete SOPHIA aislado; nuevas públicas y secreto exclusivo continuación | Reutilizar D, no crear otra Function con lógica inventada | Sin tools legacy/BackendClient; conservar cuatro tools por run y autorización final de registry; no editar globalmente agentes compartidos | D/D.1, continuidad, llamada directa denegada, dos turnos | RA/SEG; source/artefacto y nuevo recurso Azure aprobados |
| Function original / mismo repo | Guard exclusivo de rutas SOPHIA legacy; history/delete sólo capacidades verificadas durante transición | Quitar acceso alternativo donde antes existía | NO quitar auth/secret/handlers compartidos globalmente; revisar paquete completo | Direct calls legacy DENY; historia legítima preservada; contratos ajenos sin cambios | RA/SEG; diff acotado + ventana específica |
| Voice / proxy-sophia-xoc | app/auth.py, backend_client.py, config.py: bootstrap humano vía XOC + prueba MI; quitar dependencia efectiva HS256 compartida | Impedir que Chat se haga pasar por Voice; frontera identidad D.2 | NO MCP, NO cambios audio/tool planning; conservar siete operaciones, mensajes y errores públicos compatibles | test_backend_contract + auth positivos/negativos y compatibilidad acordada | Responsable Voice + BA/SEG; aprobación sólo identidad |
| Exchange y API verifier / XOC-API-AWS | routes/agents.py y helpers auth/authorizer concretos que el inventario identifique: Voice-bound exchange, reject selectivo SOPHIA legacy | Cierre bypass directo XOC | NO DENY global agent:invoke ni migración VICTOR; no borrar endpoints de consumidores legítimos | Humano solo/Chat token/Voice MI falsa rechazados; Voice autorizado conservado | BA/SEG; inventario de verificadores y retiro aprobado |
| IAM/Entra/Vault/Foundry | Rol MI aislado, permiso de app Voice, keys/refs mínimas, catálogo controlado del agente | Aislamiento real de firma/datos | Recursos/config PROPUESTOS; sin agente con herramientas arbitrarias; no tocar proyectos ajenos | Acceso efectivo, impossibilidad firmar desde runtime/proxy, no sólo nombres de claims | SEG/RA/BA; autoridad cloud posterior |
| Observabilidad / config | Métricas seguras por etapa, cohort selector y tools unavailable | Detectar fallo y recuperar sin bypass | No tokens/prompts/PII como labels; no dependencia por cada mecanismo | Correlación, alarmas, kill switch y ensayo | OPS/SEG; retención/costos aprobados |
| Web / Mobile / Push / Inbox / reportes / Step Functions | NINGÚN cambio de implementación en MCP-2 previsto | Consumidores/servicios existentes | Pruebas de regresión relevantes, no redeploy de todos por defecto | Chat/C.3 siguen contratos; otras rutas no tocadas | QA; fuera de cambios salvo autorización aparte |

VICTOR/On-Premise: fuera de alcance, sin solicitudes de repo, scripts o cambios. Si empaquetado comparte archivos, demostrar ausencia de diff funcional en ellos; no utilizar esa situación como requisito para migrarlo.

## 2. L1: primer bloque exacto que recomiendo autorizar

**Objetivo:** comprobar localmente el contrato de una invocación XOC verificada que inicia un cliente/servidor MCP stdio con contexto inmutable, una sola tool y límites de proceso/mensaje. No conectar Chat real todavía. Esto entrega la pieza que permite implementar luego el worker sin tener que inventar autenticación al desplegar.

### Archivos previstos exclusivamente en XOC-MCP-LAB

| Archivo | Trabajo delimitado |
| --- | --- |
| xoc_mcp_lab/request_envelope.py NUEVO | Schema/version/hash y verificación JWS ES256 de fixture: emisor, audiencia, purpose, actor/contexto, exp/iat, request_id, hash de tool+args; pública de prueba fijada, nunca jku del token |
| xoc_mcp_lab/transport.py NUEVO | Extraer/reutilizar el serve SDK de server.py; entrada acotada, salida serializada <=256 KiB, stdout limpio, errores estables. No escribir otro JSON-RPC |
| server.py | Delegar transporte común, preservar CLI --lab/--backend/--profile, sus tests y guards. No aceptar JWT productivo en la CLI de perfiles |
| xoc_mcp_lab/adapter.py | Permitir construcción interna con contexto previamente verificado, conservando constructor de perfiles para pruebas; misma Policy/Executor/handlers/proyección; request_id correlacionado del sobre |
| xoc_mcp_lab/verified_server.py NUEVO | Lanzador de prueba que acepta envelope fixture del entorno efímero, valida antes de proveedor, limpia entorno y aplica Wiring/Isolation existentes; nunca un modo prod oculto |
| xoc_mcp_lab/query_worker.py NUEVO | Función local invocable con sobre fixture: verifica, inicia hijo con SDK, initialize/initialized/call una vez, valida resultado, cierre/reap con plazo. Sin boto3 Lambda Invoke ni KMS real |
| tests/test_verified_context.py NUEVO | Firmas/contexto/roles/inyección/replay restringido a request, doble usuario/tenant y cero handlers ante auth inválida |
| tests/test_worker_lifecycle.py NUEVO | Deadline, hijo muerto/lento, EOF, saturación local, sin retry/fallback, sin residuos |
| tests/test_wire_limits.py NUEVO | Unicode, escaping, límites completos entrada/salida, text=structuredContent y lectura >64 KiB por cliente SDK real |
| compatibility.json NUEVO y README.md | Tupla backend HEAD/hash, SDK/revisión/entorno; aclarar 64 KiB entrada y estado estrictamente fixture. No inicializar Git |

Si la extracción de transport.py exige ajustar tests/cliente existentes del laboratorio, mantener ese diff mecánico y sus contratos; no refactorizar otros módulos. Las dependencias ya incluyen PyJWT y cryptography: no hace falta instalar, actualizar o cambiar locks para probar ES256 local. Claves de prueba inequívocamente ficticias, generadas en memoria por tests o fixture público de test; no usar ni emitir credenciales reales. No persistir privadas de prueba fuera del laboratorio.

Sobre conceptual, NO request de cliente público ni MCP tools/call.arguments:

```text
schema_version = xoc-mcp-query-v1
request_id = correlación generada por XOC
tool = nombre + argumentos de negocio
context_assertion = JWS
  iss, aud=xoc-sophia-query-worker, purpose=mcp-read, source=sophia_chat
  actor_user_id, actor_role, tenant_id, effective_tenant_id, delegation_active
  request_id, tool_hash, iat, exp, deadline
```

En producción el emisor se alimentaría del contexto autenticado backend; en L1 sólo firma fixtures. No permitir que request_id/hash/contexto o kid vengan de instrucciones del modelo sin autenticación. El esquema privado AWS no es un nuevo protocolo MCP: la sesión stdio sigue el SDK estándar.

### Criterio de aceptación L1

1. Mantener 85 tests y smoke existentes verdes, sin cambiar persistencia/Policy original ni dependencias.
2. Un envelope válido => una sesión MCP, una ejecución y un resultado proyectado; mismo request_id en audit/respuesta. Segunda tool en mismo worker denegada, no más handlers.
3. Firma inválida, alg equivocado, exp/iat fuera de ventana, aud/purpose/issuer distinto, key de Voice/runtime ficticia, hash/contexto alterado => cero hijos de negocio/handlers/lecturas; error seguro sin token.
4. tools/call.arguments y _meta no cambian identidad; campos ownership siguen DENY por Policy. USER tenant-read e Inbox tenant+user conservados.
5. Caso ASCII y Unicode mayor de 64 KiB de salida llega correctamente si <=256 KiB; exceso exacto produce error pequeño, no truncación. Entrada >64 KiB rechazada; límites cuentan newline y sobre completo.
6. Hijo colgado/muerto, timeout de cliente y EOF dejan cero procesos del worker; no se afirma cancelar cloud, porque no existe nube en L1. Repetir en Windows; Linux queda como gate posterior, no resultado inventado.
7. No callbacks a APIs XOC/Foundry, no sockets externos, no .env o credenciales heredadas, no AWS/Azure real, no modelo, no archivos de secretos/logs completos.
8. Presentar diff exclusivamente del lab, nuevos tests y evidencia. Backend/runtime/proxy/mobile/web conservan HEAD/status. No commit/push/deploy automático al terminar.

Fuera de L1: adaptador Lambda real, IAM/KMS/Vault/Entra, cambios Chat/Voice/runtime, empaquetado Linux, recursos, gates cloud, activar flags o corregir C.3/VICTOR. Autorizar L1 no autoriza implantar todo este plan.

## 3. Matriz de aceptación de la integración posterior

Niveles: L = offline/fixtures; C = entorno controlado explícitamente aprobado; P = smoke productivo de cohorte consentida. Un resultado L no sustituye C/P. Datos cross-tenant y fallos inducidos se preparan primero en C; no investigar usuarios ajenos de PROD.

| ID / área | Caso y aceptación | Evidencia | Nivel / responsable |
| --- | --- | --- | --- |
| A01 MCP | SDK conecta/negocia 2025-11-25, lista e invoca las cuatro tools; una llamada por turno | Transcript seguro de métodos/resultados, versiones, 1 handler | L/C; QA/BA |
| A02 Contexto | Hash/caller/contexto verificados; arguments/_meta/clientInfo no cambian actor/rol/tenant | Denegaciones categóricas y contador 0 antes de provider | L/C; SEG |
| A03 Aislamiento | A1/A2 mismo tenant con unread distinto; B otro tenant; no reuso de hijo/session/cookies | Claves fixture, IDs de correlación, cero cruces | L/C; QA |
| A04 Roles | USER y ADMIN policy actual; ADMIN_XOC delegado sobre efectivo; sin delegación/SUPERADMIN/rol inválido DENY | Handler/read count cero en DENY previo; catálogo coherente | L/C; SEG |
| A05 Registry | Unknown/disabled/write/destructive ocultas y denegadas aun por nombre directo | Gateway audit, cero handlers | L/C; BA |
| A06 Ownership get | Ajeno e inexistente dan la misma respuesta segura por clave tenant+ticket | Una lectura tenant-key, nunca query global | L/C; QA |
| A07 Credenciales | aud/purpose/iss/alg/kid/exp equivocados, claim bool/int inválido, replay en otro request, cambio de contexto | Cero datos, sin degradación HS ni token downstream | L/C; SEG |
| A08 Permisos firmantes | Runtime/Voice/worker no pueden firmar como Chat ni leer HS global; Voice MI no acepta identidad declarada | Policy/roles efectivos + pruebas negativas autorizadas | C; SEG/OPS |
| A09 Bypass XOC | Humano solo no consigue bearer legacy; Chat capability no sirve en tickets/scans; nuevo Voice sólo allowlist | Inventario completo de verificadores, resultados sin datos | L/C; SEG/BA |
| A10 Bypass Azure | Directa a SOPHIA legacy con bearer antiguo/humano/propósito ajeno no llega a tools/BackendClient | Counters/runtime logs seguros; Foundry tool definitions | L/C; RA/SEG |
| A11 Resultados | Sólo proyección pública, todas filas ownership antes de recorte; Unicode/duplicación/bytes/canarios | Resultado sanitizado; error size seguro, no URL secreta | L/C; QA |
| A12 Segundo turno | ToolResult correcto, thread y continuación ligados; segundo tool_request se detiene | 1 tools/call, 1 handler, <=1 continuación | L/C/P positivo; BA/RA |
| A13 Errores | Runtime primer/segundo turno 4xx/5xx/timeout, MCP caído, documentos falla, result inválido | Error acotado, sin retry/fallback; otras tools/rutas sanas | L/C; QA/OPS |
| A14 Latencia/carga | Cold/warm, paginación máxima, memoria, cuota, flood limitado autorizado | Tiempos por etapa, p95/p99 con muestra, límites del plan | C; OPS |
| A15 Chat sesiones | Nueva, continuidad, ownership history/delete, thread anterior tras mover runtime | Sesión y contenido esperado sólo de usuario de prueba | L/C/P positivo; QA |
| A16 C.3 | Propuesta no crea, cancelar no crea, confirmar crea, replay mismo ID; auto-ticket no reaparece | Ticket/receipt coherentes, sin doble ejecución | L/C/P acotado; QA/BA |
| A17 Voice compatibilidad | Prueba MI/exchange mantiene bootstrap y 7 operaciones existentes; no se cambia audio ni MCP Voice | Suite contrato actual + auth nueva y smoke acordado | L/C; responsable Voice |
| A18 Módulos ajenos | Diff no toca Push/Inbox writers/reports auth/SQL/Step Functions; health acordado sin datos ajenos | Regresiones y diff/release limitado | L/C/P health; QA |
| A19 Artefacto | Arranque Linux x86_64 sin checkout, sólo wheel canónico, lock y hash verificados | Manifest, origen de imports, SBOM, hash desplegado | L Linux/C; BA |
| A20 Recuperación | Rollback ensayado entre versiones compatibles, keys/flags coherentes, datos no revertidos | RTO real, smoke post-rollback, tickets/receipts intactos | C; OPS/DAT |
| A21 Canary | Selector sólo servidor, no headers/modelo/body, kill switch realmente sin legacy | Request IDs sólo de cohort, herramientas off seguras | L/C/P acotado; BA/SEG |
| A22 Confidencialidad | JWT/capability/refresh/confirmation/pushToken no aparecen en logs/artefactos/resultados | Revisión automatizada con canarios + revisión humana | L/C; SEG |

Suites a conservar cuando se implemente: test_tool_gateway_policy, test_tool_gateway_executor, test_chat_tool_gateway_integration, test_chat_ticket_confirmations, test_chat_access_hardening y pruebas D/D.1 runtime/proxy. Ejecutar después suite backend completa y comparar con los mismos dos fallos históricos, sin arreglos incidentales. Comando solicitado para backend: `python -B -m unittest discover -s tests`, desde entorno preparado/aprobado; usar el arnés de aislamiento existente cuando corresponda, no confundir problemas de dependencias con regresión.

No se ejecutó esa suite completa en esta tarea de planificación. Evidencia nueva de laboratorio: 85 passed y smoke passed, detallada en el plan. A01–A22 son criterios FUTUROS, no checkmarks de producción.

## 4. Costos incrementales de la arquitectura PRINCIPAL

Consulta de precios oficiales: 14-sep-2026. Moneda USD, referencia AWS us-east-1 y Azure Central US, tarifas públicas sin contratos/descuentos/free tier/impuestos. No es cotización ni factura de XOC. No se consultó consumo de la cuenta. La alternativa HTTP no se costea porque no se recomienda implementarla ahora.

### Supuestos explícitos del ejemplo

- N=10000 turnos Chat/mes; 30% pide una tool: T=3000.
- Worker 512 MiB, promedio facturado supuesto 1.5 s por consulta; timeout 5 s como tope de operación, NO promedio medido.
- Chat actual espera ese worker: hasta 1.5 s extra por T, 512 MiB; sin descontar el tiempo que antes consumía el Executor local (estimación conservadora).
- Runtime aislado Azure Flex Consumption 2 GiB, on-demand, cero always-ready; N+T=13000 invocaciones, 3 s promedio de instancia activa por invocación, supuesto conservador sin ahorro por concurrencia. No suma una segunda llamada MCP al modelo: D ya contempla el segundo turno.
- 16000 firmas Chat (N+T+T) y 1000 firmas de bootstrap Voice; dos keys KMS ES256. 1000 lecturas/operaciones de secreto de continuación en Vault, cache de versión autorizada por instancia.
- 30 RRU DynamoDB promedio por T = 90000 RRU/mes. Son UNIDADES facturables, no equivalen a 30 HTTP requests ni a cantidad de filas. Si helpers recorren páginas grandes, medir y recalcular.
- Logs seguros: 1 GB/mes CloudWatch + 0.5 GB/mes Azure Analytics, retención objetivo 30 días; seis series métricas agregadas y seis alarmas estándar AWS de una métrica cada una. Añadir dimensiones por tool/tenant multiplica series y obliga a recalcular. Detalle de etapas por logs seguros consultables; auditoría de seguridad sin muestreo, sin indexar cada user como métrica.
- Artefactos: 2 GB S3 y 2 GB Blob Hot LRS; 1000 PUT + 5000 GET S3, 10000 writes + 10000 reads Blob al mes como ejemplo de build/host. Operaciones adicionales de host storage se miden, no se suponen gratis.
- Red: 1 GB AWS -> Azure y 1 GB Azure -> AWS mensuales como reserva del tráfico de Chat, no todo incremental respecto a D. MCP stdio no agrega egress cross-cloud. Worker DynamoDB/Invoke en la misma región; sin nueva red/NAT.
- Reutilizar red existente Chat únicamente si se acredita. Si falta acceso a Invoke/KMS desde VPC, detener y aprobar diseño/costo; no provisionar NAT automáticamente.

### Tarifas y cálculo orientativo

| Partida | Tarifa de referencia y fórmula | Ejemplo mensual USD |
| --- | --- | ---: |
| Worker Lambda x86 | $0.0000166667/GB-s y $0.20/millón: T×0.5×1.5×tarifa + requests | ~0.038 |
| Espera incremental Chat | T×0.5×1.5×$0.0000166667; requests Chat ya existentes | ~0.038 |
| Dos keys KMS + firmas ES256 | 2×$1 + 17000×$0.15/10000 | ~2.255 |
| Lecturas DynamoDB Standard on-demand | 90000×$0.125/millón RRU; no nueva persistencia | ~0.011 |
| Runtime Azure nuevo (costo bruto, no ahorro neto) | 13000×2×3×$0.000026 + 13000×$0.40/millón | ~2.033 |
| Vault Standard operaciones | 1000×$0.03/10000 | ~0.003 |
| CloudWatch ingestión/almacenamiento | 1×$0.50 + hasta 1×$0.03/GB-mes, sin descuento por compresión | ~0.53 |
| Métricas/alarmas estándar AWS | 6×$0.30 + 6×$0.10 por mes; APIs/consultas extra aparte | ~2.40 |
| Azure Analytics ingestión | 0.5×$2.76/GB; retención/export extra según configuración real | ~1.38 |
| S3 artefactos | 2×$0.023 + 1000×$0.005/1000 + 5000×$0.0004/1000 | ~0.053 |
| Blob Hot LRS | 2×$0.0184 + $0.05/10000 writes + $0.004/10000 reads | ~0.091 |
| Transferencia cross-cloud reservada | 1×$0.09 AWS + 1×$0.087 Azure, sin free allowances | ~0.177 |

Subtotal ilustrativo de esas partidas: **aproximadamente $9/mes**; reservar **$10–20/mes** para este escenario pequeño como presupuesto preliminar técnico, NO como límite ni costo total XOC. Tráfico, paginación, cold starts, logs, pruebas y modelos pueden cambiarlo sustancialmente. Los valores decimales de la tabla explican fórmulas, no precisión de una factura futura.

Fuentes oficiales de tarifas AWS: [Lambda](https://aws.amazon.com/lambda/pricing/), [KMS](https://aws.amazon.com/kms/pricing/), [DynamoDB](https://aws.amazon.com/dynamodb/pricing/), [CloudWatch](https://aws.amazon.com/cloudwatch/pricing/), [S3](https://aws.amazon.com/s3/pricing/), [transferencia EC2/AWS](https://aws.amazon.com/ec2/pricing/on-demand/). Son precios por unidad, no prueba de la clase/capacidad contratada en XOC.

Fuentes Azure: [Functions](https://azure.microsoft.com/en-us/pricing/details/functions/), [modelo de costo Flex](https://learn.microsoft.com/en-us/azure/azure-functions/functions-consumption-costs), [Key Vault](https://azure.microsoft.com/en-us/pricing/details/key-vault/), [Monitor](https://azure.microsoft.com/en-us/pricing/details/monitor/), [Blob](https://azure.microsoft.com/en-us/pricing/details/storage/blobs/), [Bandwidth](https://azure.microsoft.com/en-us/pricing/details/bandwidth/).

Para acreditar cifras que las páginas renderizan dinámicamente se consultó la API pública, sin autenticación ni suscripción: [Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices). Filtros serviceName/armRegionName=centralus:

| Registro público consultado | Resultado no gratuito usado | effectiveStartDate |
| --- | --- | --- |
| Functions / Flex Consumption / On Demand Execution Time | 0.000026 USD / GB Second | 2025-02-01 |
| Functions / On Demand Total Executions | 0.000004 USD / 10 ejecuciones = 0.40/millón | 2025-02-01 |
| Functions / Always Ready Baseline | 0.000004 USD / GB Second; NO incluido en propuesta inicial | 2024-12-01 |
| Key Vault / Standard / Operations | 0.03 USD / 10000 | 2015-08-01 |
| Log Analytics / Analytics Logs Data Ingestion | 2.76 USD / GB | 2018-02-01 |
| General Block Blob v2 / Hot LRS Data Stored | 0.0184 USD / GB-mes, tier 0 | 2017-02-03 |
| Hot LRS Write / Hot Read Operations | 0.05 / 0.004 USD por 10000 | 2016-05-09 |

La API también devolvió tramos gratuitos: deliberadamente no se restaron porque se comparten con otros recursos/suscripción. No se consultaron precios mediante az login ni datos de facturación privados.

### Fuera del subtotal y sensibilidad

- **Modelos/Foundry:** recursos y consumo existentes, modelo/tarifa/volumen reales pendientes; presupuesto = tokens entrada×tarifa entrada + salida×tarifa salida + herramientas facturables existentes. MCP no agrega modelos; mantener máximo dos turnos D. No afirmar costo modelo cero. Medir antes/después para separar cambio MCP de adopción D frente a legacy.
- **Azure runtime anterior:** permanece para consumidores legítimos; no acreditar un ahorro por apagarlo. La tabla incluye el costo bruto del nuevo runtime aislado; puede sustituir parte de cómputo SOPHIA, no toda la Function compartida.
- **Voice:** sin nuevo hosting/audio, pero cambio de identidad implica firmas/validaciones y tráfico; ejemplo 1000 bootstraps, medir sesiones/renovaciones reales. Recursos MI/app registration no significan todas las licencias/servicios Entra gratis; confirmar contrato aplicable.
- **Cold starts:** si no cumplen latencia, no activar. Una instancia Azure always-ready de 2 GiB agregaría sólo de baseline unos $20.74/30 días a $0.000004/GB-s, más consumo activo/tarifa correspondiente; no está autorizada ni incluida. Provisioned concurrency AWS también tendría costo y requiere decisión distinta.
- **Red Chat existente:** NAT ya presente podría agregar $0.045/GB procesado; no se imputa otro NAT fijo. Si hiciera falta uno nuevo, ejemplo us-east-1 $0.045/h (~$32.85/730 h) más tráfico e IPv4, a aprobar aparte, no ocultarlo en $10–20. [Precio VPC](https://aws.amazon.com/vpc/pricing/).
- **Respaldos de datos:** comprobar PITR/retención real. No nuevo backup de todas las tablas. Si se exige activar protección ausente para AgentSession/tickets/receipts, estimar por GB y mecanismo antes de autorizar; no incluido por falta de tamaño.
- **Ensayos/desarrollo:** L1 estimado 1–2 jornadas de ingeniería/QA; integración D.2 + worker + release/ensayo puede necesitar 4–8 jornadas adicionales después de acuerdos, no compromiso de calendario. Multiplicar por tarifa interna, no confundir con costo cloud. Entorno controlado duplica algunos recursos temporalmente y consume modelos si se autoriza el smoke.
- **Operación:** alertas, revisión de accesos/keys y mantenimiento de dependencias necesitan responsable y horas; no desaparecen por ser serverless. Costos de builder, almacenamiento adicional, CloudTrail data events, export/query de logs o restauraciones no se asumen incluidos.

Antes de activar: aprobar presupuesto con tráfico estimado por dueño de producto, alertas de gasto y mediciones controladas. No presentar el subtotal como precio garantizado o gasto total de XOC.

## 5. Decisión y autorización que sigue

| Acción | Estado |
| --- | --- |
| Plan MCP-2 y revisión de laboratorio | Entregados; no cambios de aplicación |
| Primer bloque L1 offline | Recomendado para autorizar ahora; no necesita acceso a VICTOR ni esperar una sesión cloud |
| Conectar Chat real a MCP | Requiere bloque posterior aprobado y matriz identidad/seguridad |
| Deploy runtime/Chat/worker | No autorizado y NO-GO técnico hoy hasta source/artefactos/compatibilidad/rollback |
| Activar canary/smoke productivo | NO-GO hasta D.2/bypass, límites/latencia y pruebas controladas |
| MCP para Voice/otros agentes | Fuera del plan |

Solicitud de autorización sugerida: “Implementa únicamente L1 local del plan MCP-2, con los archivos y tests delimitados, sin tocar backend/runtime/proxy/mobile/web, sin servicios externos, sin commit/push/deploy ni nuevas dependencias. Entrégame diff y resultados antes de continuar”.

El siguiente paso es revisar y aprobar ese bloque; no ejecutarlo por inferencia a partir de este documento.
