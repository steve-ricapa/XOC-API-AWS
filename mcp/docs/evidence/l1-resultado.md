# MCP-2 L1 — implementación y verificación local

Fecha: 15-sep-2026. Alcance: L1 offline de la matriz MCP-2 del 14-sep.
Estado: implementado en laboratorio; NO integración ni release productivos.

## 1. Resultado y autorización aplicada

Se implementó el bloque L1 delimitado en la sección 2 del documento recibido.
La autorización posterior del usuario permite completar ese bloque sin consultas
intermedias y permite commits si fueran necesarios; prohíbe push. El documento
mantiene explícitamente fuera de L1 cambios Chat/runtime/Voice, recursos y deploy.
No se interpretó «autorización a todo» como eliminación de esos límites.

No se inicializó Git: XOC-MCP-LAB no tiene repo propio y el plan prohíbe crearlo
en L1. No se hizo commit dentro de un repo ajeno ni se reparó el .git padre.
Se entrega diff local reproducible para revisión, no una revisión Git inventada.

## 2. Implementación

```text
Sobre fixture firmado ES256
  -> QueryWorker verifica firma/claims/schema/binding/deadline
  -> SDK ClientSession inicia hijo MCP stdio fijo
  -> hijo consume sobre efímero y lo verifica antes de Wiring
  -> ToolContext inmutable, source=sophia_chat
  -> mismo registry, Policy, Executor, audit y helpers backend
  -> tablas ficticias por Wiring, nunca proveedores reales
  -> misma proyección model_result
  -> JSON-RPC SDK limitado -> correlación/contrato/texto=structuredContent
  -> cierre stdin, espera/terminación y recolección del hijo
```

No se copió Policy ni Executor. No se agregó otro catálogo de negocio, SQL,
tools de escritura, shell tool, modelo, servidor HTTP ni servicios externos.
El cliente/servidor siguen usando mcp==1.30.0 y revisión 2025-11-25. Se consultó
el transporte stdio oficial y se revisó el código del SDK instalado; no se cambió
de versión ni se instaló ninguna dependencia.

### Sobre exacto

- schema_version: xoc-mcp-query-v1.
- request_id: identificador fixture lab-..., máximo 64 caracteres.
- tool: exactamente name y arguments de negocio.
- context_assertion: JWS ES256, máximo 4096 caracteres.
- Claims: iss, aud, purpose, source, actor_user_id, actor_role, tenant_id,
  effective_tenant_id, delegation_active, request_id, tool_hash, iat, exp, deadline.
- iss=xoc-mcp-lab-fixture-issuer; aud=xoc-sophia-query-worker;
  purpose=mcp-read; source=sophia_chat.
- SHA256 sobre JSON canónico tool+arguments; firma protege también todo el contexto.
- Vida máxima 30 s; tipos estrictos, sin coerción bool/int/string, iat no futuro,
  deadline vigente y no posterior a exp. Sin leeway ni refresh.
- Header exacto ES256/JWT/kid fijo. jku, jwk, crit, claves de otro componente,
  HS256, none, issuer/audience/propósito ajenos se rechazan.
- Campos nullable deben EXISTIR: tenant_id=null permite ADMIN_XOC; Policy sigue
  exigiendo delegación. Falta de effective tenant no se convierte en autoridad.

La clave es un FIXTURE PÚBLICO reproducible (escalar 1), no un secreto. Sólo se
reconstruye en memoria en tests; no se escribió un archivo privado. La pública
está fijada dentro del laboratorio. Cualquiera puede firmar fixtures: **esto NO
es autenticación productiva**. Los namespaces aceptados son exclusivamente los
tenants/usuarios inventados; no se admiten tokens reales ni claves externas.

## 3. Límites y comportamiento ante fallos

- Un QueryWorker sirve UN intento; segunda invocación de esa instancia devuelve
  lab_call_limit. El Adapter verificado permite una sola tool+args firmados;
  mismatch o intento denegado también consumen el intento.
- Un nuevo worker con EL MISMO sobre aún vigente puede efectuar otra lectura.
  Esto se prueba y documenta: no hay deduplicación distribuida/exactly-once.
  Reutilizar firma para OTRO request/tool/args/contexto falla.
- Cuatro workers concurrentes por proceso del harness, rechazo inmediato sin cola.
- Trabajo local <=5 s, acotado por deadline firmado. El plazo se inicia antes
  del arranque SDK; protocolo usa el tiempo restante. Cleanup SDK adicional:
  hasta 2 s de gracia más terminación de plataforma; no se promete 5 s totales.
- Cancelación AnyIO del propietario: se conserva el tiempo de trabajo acotado
  para llegar a cleanup. Hijo colgado, muerto/EOF y fallo de proveedor no disparan
  retries ni fallback. El watchdog del hijo usa el deadline firmado.
- Entrada MCP: 65536 bytes, salida MCP: 262144 bytes, sobre privado fixture:
  16384 bytes. Se incluye JSON-RPC completo y newline UTF-8. LF fijo en Windows.
- Una salida mayor devuelve error SDK -32001 pequeño, sin truncar JSON. Entrada
  excesiva/incompleta o UTF-8 inválido cierra sólo el hijo.
- Cliente valida mensaje, correlación, seis campos, isError y texto equivalente
  a structuredContent. La proyección canónica se aplica en el hijo fijo.
- Se preservan los códigos categóricos del lab/audit. Antes de enviar a un modelo
  en una fase futura seguirá siendo necesario el mapeo sanitizado de Fase D.

Limitación del SDK v1: el cliente comprueba tamaño después de que el SDK parsea
la línea; no es límite de asignación de memoria ante un servidor arbitrario hostil.
Sólo se lanza nuestro hijo fijo y su escritor limita la salida. Isolation/Wiring
son controles de pruebas, no sandbox contra código Python local comprometido.
El worker NO es un handler Lambda listo para publicar; el arranque/proceso y
cleanup Linux/Cloud deben medirse y validarse posteriormente.

## 4. Archivos

Sólo XOC-MCP-LAB:

| Archivo | Cambio |
| --- | --- |
| xoc_mcp_lab/request_envelope.py | Nuevo: verifier de fixtures y contexto frozen |
| xoc_mcp_lab/transport.py | Nuevo: extracción mecánica de serve + límites completos |
| server.py | Delega transporte; preserva --lab/--backend/--profile y guards |
| xoc_mcp_lab/adapter.py | Constructor interno verificado, binding y single-use |
| xoc_mcp_lab/verified_server.py | Nuevo: hijo fixture verificado antes de proveedores |
| xoc_mcp_lab/query_worker.py | Nuevo: cliente SDK, admisión, plazo, correlación y cleanup |
| tests/l1_fixtures.py | Nuevo: firmante público exclusivamente de test |
| tests/test_verified_context.py | Nuevo: claims/firma/roles/binding/compatibilidad |
| tests/test_worker_lifecycle.py | Nuevo: SDK real, aislamiento, fallos y cleanup |
| tests/test_wire_limits.py | Nuevo: fronteras bytes/Unicode/escaping y stdio real grande |
| compatibility.json | Nuevo: HEAD/hash backend, lock, SDK/revisión y Windows |
| README.md | Aclara límites y uso L1, sin confundirlo con producción |
| reports/l1-* | Informe, preservación Git, diff y evidencia local |

Los archivos anteriores de pruebas MCP-1 no se modificaron. No hay cambios en
requirements.in/lock, Wiring, Isolation, fixtures originales, client.py ni smoke.py.
No se sobreescribieron informes históricos MCP-1/MCP-2.

## 5. Pruebas ejecutadas

Desde XOC-MCP-LAB, intérprete .venv Windows 3.13.1, sin instalar paquetes:

```powershell
.\.venv\Scripts\python.exe -B run_tests.py --backend '..\XOC-API-AWS'
.\.venv\Scripts\python.exe -B smoke.py --backend '..\XOC-API-AWS'
```

- Baseline antes de editar: 85 passed in 24.14s.
- Suite final ampliada: 183 pruebas (85 anteriores + 98 nuevas), todas verdes.
- Primera ejecución de 183: 64.05 s. Repetida en Windows: 65.65 s; última
  ejecución con la validación final de resultados: 183 passed in 66.07s, exit 0.
- Smoke existente: status=passed, modelo=0, cuatro tools, tres contextos, 12
  consultas positivas y 16 denegaciones contando SUPERADMIN; exit 0, 6.49 s.
- Los nuevos tests inician ClientSession y procesos reales para las cuatro tools,
  dos tenants/dos usuarios, delegación, correlación audit y single-use.
- ASCII y Unicode de salida >64 KiB llegan completos; exceso >256 KiB produce
  error pequeño. Fronteras exactas -1/0/+1 bytes, newline y escaping comprobados.
- Tests de procesos comprueban returncode tras success, timeout, EOF, fallo,
  cancelación y concurrencia. No conexiones AWS/Azure/modelo ni datos reales.
- Comprobación del host después de la última suite: Get-Process python no devolvió
  procesos; no quedaron hijos Python del laboratorio en ejecución.

Correcciones durante desarrollo, cerradas por estas pruebas: nullable tenant en
PyJWT require (sin alterar Policy); IDs cortos de tests Unicode para el límite
de variables Windows; fixtures grandes preservan fechas válidas para el sorter
original; newline LF explícito para que tamaño de wire sea exacto en Windows.

Regresiones backend con el arnés offline YA existente, sin editarlo:

```powershell
python -B run_backend_regressions.py --backend '..\XOC-API-AWS' --dependencies '..\.tmp-ticket-sync-tests' --suite focal
python -B run_backend_regressions.py --backend '..\XOC-API-AWS' --dependencies '..\.tmp-ticket-sync-tests' --suite all
```

- Focal: 114 tests, OK, 1.112 s; incluye Gateway, Chat, Fase D, C.3 y acceso.
- Completa unittest discover mediante arnés: 249 tests, 247 pasan / 2 fallan,
  4.377 s, exit 1. MISMOS fallos históricos de test_assess_ticket_automation:
  test_global_url_wins_and_marks_source_global (URL esperada vs None),
  test_normalize_plans_skips_empty_and_keeps_legacy (lista vacía vs plan legacy).
  No se arreglaron ni se contabilizan como fallos MCP.
- Primer intento de la completa en sandbox: PermissionError leyendo six.py del
  directorio de dependencias previo; 24 errores de carga, no resultado funcional.
  Se repitió con permiso de ejecución fuera del sandbox manteniendo Isolation
  offline; el resultado comparable es el de 249 arriba. Sin instalación ni ACLs.

## 6. Preservación

El snapshot inicial/final de los diez repos es idéntico en HEAD/rama/status y hash
agregado de fuentes/configuración versionadas. Ver l1-repository-preservation.json.
Se excluyen secretos/binarios del hash; no se dice que eso sea un backup restaurable.

| Repo principal | HEAD conservado |
| --- | --- |
| XOC-API-AWS | ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9 |
| XOC_MICROSERVICIO_AGENTES | 6249064a9bd69bb388369d03f502b142bac93e1b |
| proxy-sophia-xoc | 93107ac2a4ad08b2c28e6242fb0aeda666eaf54f |
| XOC-APP-MOBILE | 9968aef40225acf4a30dfb2cbecd0c43a20b12af |
| XOC_PAGE | f43d3ed6b2eac389cdf29e9fc9d052137fe188eb |

Backend conserva sus tres docs no versionados y commits locales; proxy conserva
su test no versionado; mobile conserva package.json modificado. No se mezclaron
esos cambios. No push, commit, deploy, flag, recurso ni modificación RDS/SQL.
No se tocó VICTOR/On-Premise, Voice, Step Functions, Push, C.3, Mobile ni Web.

## 7. Aceptación L1 y siguientes gates

| Criterio L1 | Resultado |
| --- | --- |
| 85 tests y smoke anteriores conservados | PASS |
| Sobre válido, una tool/handler y request_id audit/respuesta | PASS |
| Firma/claims/hash alterados antes de hijo/proveedores | PASS |
| Policy original, ownership, _meta e identidad inmutable | PASS |
| Límites completos/Unicode/errores sin truncar | PASS |
| Fallos/cancelación/saturación con procesos recolectados Windows | PASS |
| Fixtures solamente, entorno limpio, sin nube ni secretos reales | PASS |
| Diff sólo lab y repos ajenos preservados | PASS |

GO: revisión de L1 local y diseño del bloque siguiente.
NO-GO: deploy/activar MCP o afirmar integración real Chat-SOPHIA.

Pendientes FUERA DE L1: autorización delimitada de integración e identidad D.2,
empaquetado canónico Linux sin checkout, release/source PROD acreditados, keys/IAM
reales, retiro de bypass legacy, compatibilidad de sesiones/Voice, latencia cloud,
ensayo de recuperación y smoke con cohorte consentida. No hay requisito de repo
VICTOR ni On-Premise. No se ejecutó la futura ventana del runbook.

Riesgos: HIGH si se intenta usar la clave fixture o este servidor con datos reales;
MEDIUM pendiente Linux, latencia/cleanup y consumo de recursos cloud; LOW para
L1 el versionado Git todavía no resuelto. Los riesgos previos D.2 siguen abiertos;
esta entrega no los oculta ni declara MCP productivo listo.

Fuente técnica: [MCP stdio 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
y código local fijado del SDK, además del Tool Gateway canónico del HEAD citado.
