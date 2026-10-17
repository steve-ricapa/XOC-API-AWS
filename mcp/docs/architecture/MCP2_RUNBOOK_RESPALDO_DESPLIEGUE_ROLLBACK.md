# MCP-2 — Runbook propuesto de respaldo, despliegue y rollback

Fecha: 14-sep-2026. NO ejecutar con esta autorización. Complementa MCP2_PLAN_TRANSICION.md y MCP2_MATRIZ_CAMBIOS_Y_ACEPTACION.md.

## 1. Alcance y responsables

Este documento describe una futura ventana aprobada, no una acción programada ni un recurso cloud. Hoy no se hicieron backups, deploys, restores, cambios de permisos o sesiones cloud.

Roles a asignar, sin inventar personas: responsable de cambio (RC), backend AWS (BA), runtime Azure (RA), identidad/seguridad (SEG), operaciones/recuperación (OPS), QA y dueño de datos (DAT). RC debe poder detener la expansión; OPS ejecuta rollback; SEG veta una reapertura de bypass. Fecha/hora, suplentes y contacto de emergencia se acuerdan después.

El plan principal separa cuatro unidades de release: Chat AWS, worker MCP AWS, runtime SOPHIA Azure y proxy Voice (este último sólo identidad). El cierre selectivo de rutas SOPHIA de la Function antigua requiere su propio diff/revisión del paquete completo. VICTOR/On-Premise no es participante ni prerrequisito.

## 2. Evidencia mínima futura, SIN ejecutarla ahora

No se solicita login ni credenciales para elaborar este plan. Los siguientes comandos son consultas propuestas para una tarea posterior con lectura cloud autorizada. Las variables se rellenarán con recursos acreditados; no usar nombres históricos sin comprobarlos. No mostrar variables de entorno completas, credenciales o URLs de descarga firmadas.

| Decisión | Consulta/procedimiento mínimo | Permiso de lectura necesario | Evidencia a custodiar |
| --- | --- | --- | --- |
| Cuenta/región AWS de la ventana | `aws sts get-caller-identity`; comprobar región explícita us-east-1 | STS GetCallerIdentity | Account/ARN sin secretos; comparar cuenta esperada 811776156524 |
| Función real del stack Chat | `aws cloudformation describe-stack-resources --stack-name "$approvedStack" --region us-east-1` | cloudformation:DescribeStackResources | Mapping lógico -> ARN físico, stack/revisión aprobados |
| Base/timeout/config no secreta | `aws lambda get-function-configuration --function-name "$chatFunctionArn" --region us-east-1 --query '{Arn:FunctionArn,Version:Version,CodeSha256:CodeSha256,Runtime:Runtime,Memory:MemorySize,Timeout:Timeout,Role:Role,RevisionId:RevisionId,VpcConfig:VpcConfig}'` | lambda:GetFunctionConfiguration | Revalidar 20 s, arquitectura y rol; no imprimir Environment |
| Alias/versiones actuales | lambda list-aliases / list-versions-by-function, y CloudFormation GetTemplate del stack acreditado | lambda:ListAliases/ListVersionsByFunction; cloudformation:GetTemplate | Registrar versión que sirve tráfico y plantilla exacta; no asumir que el API usa alias |
| Código que realmente se ejecuta | Lambda GetFunction, usar Code.Location únicamente en memoria para descargar a custodia aprobada, comparar ZIP/archivos con Git; nunca publicar esa URL | lambda:GetFunction; acceso al enlace temporal bajo autorización | ZIP exacto/hash y diferencias de fuentes; CodeSha256 no es commit Git |
| Capacidad y red | GetFunctionConcurrency/GetAccountSettings; DescribeRouteTables/DescribeVpcEndpoints para las subredes Chat; políticas IAM específicas | Lectura Lambda/EC2/IAM de esos recursos | Holgura antes de reservar 8; camino a Invoke/KMS sin inventar un NAT nuevo |
| Azure recurso/slot/runtime | `az functionapp show --resource-group rg-sophia-poc --name prod-a-xoc-cons --query '{id:id,host:defaultHostName,kind:kind,state:state,plan:serverFarmId,identity:identity}'`; `az functionapp deployment slot list -g rg-sophia-poc -n prod-a-xoc-cons --query '[].id'` | Microsoft.Web/sites/read y sites/slots/read | Recurso y slot reales. Suscripción histórica XOC-AZURE 884feb5e-8492-4d88-8777-6391393372f5, revalidar |
| Artefacto Azure/source of truth | Responsable RA entrega deployment ID, source commit, branch, script y hash; lectura acotada del artefacto en storage/SCM identificado | sites/deployments/read; Storage Blob Data Reader sólo en contenedor de paquetes si corresponde | Comparación byte/normalización declarada. Sin permisos: no usar account keys como atajo |
| Configuración runtime/agente | Exportación filtrada de nombres de settings, referencias de secretos, protocolo/flags; definición del agente Foundry: ID, modelo/versión, nombres/tipos de tools e instrucciones aprobadas sin contenido de usuarios | Lectura de configuración autorizada y del agente del proyecto; no listar secretos | Probar ausencia de tools alternativas y continuidad de thread; no invocar modelo para inventario |
| Identidad efectiva | Políticas IAM/Key Policy por key ARN; role assignments Azure por principal y scope; acceso efectivo de MI a Vault/Storage | IAM Get/List pertinentes, KMS GetKeyPolicy, Microsoft.Authorization/roleAssignments/read | Matriz emisor/verificador/quién puede firmar o leer; no GetSecretValue ni material de claves |
| Respaldo de tablas afectadas | DynamoDB DescribeContinuousBackups/DescribeTable; RDS DescribeDBInstances sólo para conocer backup retention/recovery window si aplica a AgentSession | dynamodb:DescribeContinuousBackups/DescribeTable; rds:DescribeDBInstances | PITR/retención reales. No Scan, SELECT, export o restauración en esta consulta |
| Custodia de artefactos | GetBucketVersioning/GetBucketEncryption/GetBucketLifecycleConfiguration o propiedades Blob equivalentes; revisar acceso | Lectura sólo de storage de releases | No asumir versionado o cifrado administrado correctamente por existir un bucket |

CloudFormation template o app settings pueden contener datos sensibles: exportar mediante filtrado local bajo rol autorizado; el informe sólo incluye campos permitidos y referencias. El hecho de tener AdministratorAccess no autoriza un inventario indiscriminado ni convierte main en PROD.

## 3. Qué significa respaldo en este cambio

- **Snapshot de evidencia:** HEAD, árbol, status, SHA256, resultados y manifest. Permite comparar; no restaura servicios ni datos por sí solo. snapshot.ps1 del laboratorio pertenece a esta categoría.
- **Respaldo restaurable:** artefacto/configuración/datos conservados con mecanismo de recuperación probado y permisos para recuperarlos. Un hash o un enlace temporal no lo reemplaza.
- **Rollback de código/configuración:** regresar a una combinación aprobada de versiones, claves públicas, endpoints y flags; normalmente conserva todos los datos legítimos.
- **Restore de datos:** operación excepcional del dueño de datos, con análisis de consistencia y pérdida. NO es respuesta automática a un deploy fallido.

### Inventario de protección propuesto

Todo mecanismo cloud de esta tabla es **propuesto o pendiente de acreditación**, salvo la evidencia Git local. Retenciones son propuestas a aprobar por RC/SEG/DAT, no políticas institucionales existentes.

| Objeto protegido | Mecanismo / recuperación | Custodia y control | Verificación restaurable | Responsable / retención propuesta |
| --- | --- | --- | --- | --- |
| Fuente backend, runtime, adapter y proxy que se cambia | Commit aprobado por repo + bundle/archivo fuente completo y manifest; recuperar checkout exacto sin main móvil | Repo remoto aprobado y copia de release cifrada restringida; no .env ni claves | Checkout aislado desde copia, verificar objetos/hashes y tests; incluir archivos no versionados requeridos antes de release | BA/RA, 90 días y mínimo 2 releases aceptadas |
| ZIP Chat y worker, wheels/lock/builder | Conservar artefactos EXACTOS anterior/nuevo, plantillas y versión Lambda asociada; redeploy artefacto o alias aprobado | Prefijo de releases en storage aprobado con versionado/cifrado y acceso OPS; comprobar, no asumir | Desplegar artefacto anterior en función/entorno de ensayo autorizado; importación, rutas y MCP; hash desplegado coincide | BA/OPS, 90 días / 2 releases |
| Paquete runtime Azure y definición agente | Artefacto de deployment anterior, source y lock/build log, configuración del agente versionada sin usuarios; publicar/reapuntar compatible | Storage Azure aprobado, cifrado y acceso RA/OPS. URL firmada no es backup | Reconstituir Function de ensayo con dependencias Linux y agente de prueba; dos turnos, continuación, thread history/delete | RA, 90 días / 2 releases |
| Imagen proxy por cambio de identidad | Digest inmutable de imagen anterior/nueva + fuente/requirements/config no secreta; restaurar digest y tráfico de revisión | Registry aprobado con retención de manifests/capas; no confiar sólo en v15/latest | Arranque y contrato bootstrap/7 llamadas con mocks y luego prueba autorizada; no audio rediseñado | Responsable Voice/OPS, 90 días / 2 releases |
| Configuración/flags/URL/roles | Manifest por release con valores no secretos, referencias versionadas, policies y asociaciones; reaplicar la combinación aprobada | Cifrado y permisos OPS/SEG. Diff no público de settings sensibles, reporte sólo referencias | Ensayo compara settings/roles efectivos y prueba permisos positivos/negativos | OPS/SEG, igual al artefacto |
| Claves KMS y secreto de continuación | KMS no exportable: conservar keys y políticas requeridas, impedir borrado. Vault: versión referenciada y recuperación nativa/soft-delete comprobada; no exportar a Git | KMS/Vault existentes o por aprobar, permisos firmante separados de custodio; acceso de emergency recovery documentado | Ensayar verificación antigua/nueva y recuperación de referencia en entorno de prueba; nunca afirmar que un backup de policy restaura una key borrada | SEG, durante vida de releases+ventana de tokens, sin borrado durante transición |
| AgentSession / estado Chat afectado | Mantener backup/PITR existente si se acredita; si no, aprobar respaldo consistente acotado del almacenamiento antes de cambio de bindings/semántica | Sistema DB actual, cifrado/acceso DAT; no copia ad-hoc con SQL inventado | Restaurar a destino aislado y verificar relaciones de sesión/usuario/tenant con fixtures o muestra autorizada | DAT, propuesta 7 días recuperables, aprobar costo/RPO |
| Tickets + recibos C.3 | NO nueva escritura MCP ni cambio de esquema; no backup masivo por defecto. Acreditar recuperación existente y preservar ambos al rollback | Tablas actuales y mecanismo autorizado por DAT | Ensayo de replay conserva mismo ticket; si se restaura un conjunto, reconciliar ticket, receipt, propuesta y efectos ya emitidos | DAT/BA, retención según política real; propuesta mínimo cubrir ventana+7 días |
| Reports/Inbox leídos por MCP | Sin mutación de este cambio: no generar snapshot extra por cada deploy. Verificar protección existente; no restaurar para arreglar SDK | Custodia actual, sin export de documentos/notificaciones | Pruebas con fixtures; restauración sólo si hay incidente de datos independiente aprobado | DAT, política actual a acreditar |
| Evidencias/auditoría de ventana | Registro correlacionado seguro, métricas, decisiones, hashes y resultado de smoke | Carpeta/repo de evidencias aprobado y almacenamiento cifrado; mínimo acceso; redactar PII/tokens | Verificar legibilidad/correlación/retención; no sustituye el backup funcional | RC/SEG, 90 días propuestos |

Si un nuevo Vault o almacenamiento resulta necesario, se crea únicamente con autorización/infra posterior; no se supone que ya existe. No hacer backup completo de la cuenta. No cambiar schema RDS ni restaurarlo por fallos de SDK/MCP. El cambio READ_ONLY necesita sobre todo recuperación de artefactos/configuración; Chat y confirmación siguen escribiendo sesiones/tickets legítimos y no deben perderlos.

### RTO / RPO para aprobación

| Objetivo propuesto | Meta, supuesto y límite |
| --- | --- |
| Contener fallo de tools | <=5 min para impedir admisión MCP por selector/kill switch server-side ya ensayado; no prometer efecto instantáneo en requests en vuelo |
| Recuperar Chat a combinación segura | <=30 min si aliases/artefactos/config están disponibles y el ensayo lo demuestra; ampliar si requiere reconstrucción remota Azure |
| Recuperar consultas MCP | <=60 min con versión segura conocida; si sólo existe legacy inseguro, mantener tools indisponibles y tratarlo como incidente abierto |
| RPO por rollback de código | No se planea deshacer datos; objetivo conservar todas las escrituras confirmadas. No promesa de pérdida cero ante fallo simultáneo de almacenamiento |
| Restore excepcional de datos | Proponer RPO <=5 min y RTO <=4 h sólo si PITR/backup y ensayo acreditan esos valores. Si el mecanismo real es diario, declarar RPO hasta 24 h y pedir aceptación; no renombrarlo como 5 min |

Si se requiere restore: congelar sólo las operaciones afectadas con autorización, recuperar primero a destino separado, reconstruir una línea de tiempo consistente y reconciliar tickets/receipts/sesiones/efectos push ya emitidos. Nunca conservar tickets y borrar sus recibos, o viceversa, sin plan de deduplicación explícito. No ejecutar Step Functions ni notificaciones nuevamente como parte de una restauración improvisada.

## 4. Preparación antes de reservar la ventana

No contar compilación, investigación de PROD o primera creación de identidad como un cambio rápido durante una prueba con usuarios. Preparar y ensayar antes:

1. Aprobar arquitectura, contratos y matriz de cambios. Asignar RC/BA/RA/SEG/OPS/QA/DAT.
2. Implementar y validar offline, luego en entorno existente AUTORIZADO o aislamiento de prueba aprobado. Acreditar Linux, firmantes, IAM, latencia y ausencia de bypass.
3. Acreditar base productiva y construir release aislado por repo. Seleccionar cambios sólo D.2/MCP; excluir multi_planner, cases/hotfix y otros cambios que no pertenezcan al release aprobado. No inferir inclusión por estar en ad4/main.
4. La EC2 sigue siendo máquina de despliegue. Preparar un checkout/worktree limpio de la revisión de release publicada y aprobada. No `git pull origin main` ciego para este cambio; comprobar HEAD/status/diff y artefacto que se va a usar. No editar suelto allí.
5. Preparar artefactos/restauración del §3 y ejecutar ensayo con recursos autorizados. Conservar hashes del antes y después y tiempos reales. Ningún archivo secreto en paquetes/logs.
6. Registrar todas las combinaciones de cliente/worker/runtime/identidad/flags. Tener versión segura que rechaza tools y conserva Chat/C.3; **flag false antiguo no equivale a kill switch seguro**.
7. Acordar tenants/usuarios canary con datos de prueba, mensajes de prueba y expectativas. Ningún token en Postman/export/log compartido.
8. Congelar otros deploys de los componentes afectados; inventariar credenciales/verificadores legacy y fecha de retirada ligada a pruebas. No esperar pasivamente un plazo arbitrario.

Tiempo orientativo preparación operativa, una vez implementado: 1–2 jornadas para releases, ensayos y evidencias; más si falta acreditar el artefacto Azure o restauración. No incluye desarrollo ni aprobaciones externas.

## 5. Ventana propuesta

Duración a reservar: **150 minutos**, sin fecha/hora todavía. Supone infraestructura/identidad ya aprobadas y preparadas, artefactos compilados y rollback ensayado. Plan normal ~100 min + hasta 40 min de recuperación + 10 min de holgura. Si el build remoto debe hacerse en ventana o cambian permisos/recursos no ensayados, recalcular, no improvisar.

| Paso / objetivo | Responsable y alcance | Comando/procedimiento (futuro) | Evidencia de éxito | STOP | Recuperación | Tiempo |
| --- | --- | --- | --- | --- | --- | ---: |
| W0 Abrir cambio | RC/OPS; los cuatro componentes | Registrar autorización, ventana, dueños, salud y ausencia de deploy concurrente | Acta + baseline métricas | Sin dueño/rollback o versión cambió | No iniciar, revalidar base | 10 min |
| W1 Verificar respaldo | OPS/SEG; almacén release y refs | Verificar hashes, retención y resultado del ensayo; no exportar claves | Manifest anterior recuperable + prueba registrada | Falta artefacto/key necesaria | No desplegar | 5 min |
| W2 Confirmar contexto del deploy | BA/RA; checkout AWS/Azure | `git status --short`, `git branch --show-current`, `git rev-parse HEAD`; comparar con manifest | Árbol limpio y revisión exacta autorizada | Suciedad o diff ajeno | Conservarlo y detener; no reset/clean | 5 min |
| W3 Publicar worker inactivo | BA; stack nuevo autorizado | Script de package.json/infra por implementar y revisar; nombre real pendiente. Sin endpoint público, sin tráfico Chat aún | Hash/versión/rol/concurrency + smoke fixture autorizado | IAM amplio, imports inesperados o hijo no cierra | Retirar admisión del alias, conservar artefacto/logs | 10 min |
| W4 Runtime aislado, sin tráfico humano | RA; nueva Function App SOPHIA | Core Tools `func azure functionapp publish <APP_APROBADA> --build remote`, sólo desde staging de release restringido y tras validar método con RA | Deployment ID, build log/hash, imports/agent tools, contrato positivo/negativo | Fuente no acreditada, paquete incluye rutas ajenas o firma legacy aceptada | Mantener URL anterior sin cambiar, recuperar artefacto seguro si hubo actualización | 15 min |
| W5 Chat preparado sin canary | BA; stack Chat | `npm run deploy:chat:prod` EXISTE, pero únicamente desde release verificado con parámetros/artefacto aprobados. Adaptación de pipeline a artefacto reproducible pendiente | CloudFormation estable, hash/version, rutas y flags según manifest | Stack cambia recursos ajenos o health/C.3 falla | Restaurar artefacto/config aprobados, tools indisponibles | 10 min |
| W6 Cerrar identidad/alternativas | SEG+BA+RA+Voice; sólo puntos D.2 | Aplicar secuencia ensayada de nuevos verificadores, proxy identidad y rechazo legacy SOPHIA. Update proxy por digest; comando de actualización exacto pendiente de script acotado | Voz conserva bootstrap; humano solo no obtiene legacy; directa Azure/API denegada; cero fallback | Cualquier bypass o incompatibilidad | No activar MCP; conservar modo seguro, rollback selectivo compatible | 15 min |
| W7 Activar canary | RC/BA; selector Chat server-side | Selección exacta tenant+user y versión worker/runtime del manifest. Kill switch seguro probado; flags/config por procedimiento revisado | Sólo cohort aprobada usa MCP | Selección desde body/modelo o config desconocida | Vaciar cohort / modo tools unavailable | 5 min |
| W8 Smoke funcional | QA+BA+RA; datos autorizados | Cuatro tools, dos contextos, continuidad, C.3, métricas; pruebas maliciosas/destructivas ya se hicieron en entorno aislado | Resultados/IDs seguros y 1 tool+1 handler+1 continuación; no regresiones | Cualquier fuga, error inesperado o umbrales del plan | Contener y ejecutar §7 | 15 min |
| W9 Decidir y cerrar | RC/SEG/QA | Revisar evidencia, aceptar cohorte o revertir; NO expansión automática a todos | Acta con versión segura, backlog y dueño | Evidencia incompleta | Mantener cohort cerrada o tools no disponibles | 10 min |
| R Reserva rollback | OPS+BA+RA | §7, no gastar este margen en debug durante ventana | Versión segura recuperada | Recuperación excede tiempo o keys incompatibles | Incidente formal, tools cerradas, avisar impacto | 40 min |
| Holgura | RC | No nuevas funciones ni alcance | Ventana dentro del tiempo | Trabajo no ensayado | Reprogramar | 10 min |

W4 publica TODO el proyecto preparado, no un handler de prod-a-xoc-cons. El comando estándar no acredita el script histórico del responsable. Una publicación a la Function original requiere aparte revisar su paquete completo y sólo el guard SOPHIA; no tocar código/infra de otros agentes. No ejecutar deploy/aca-deploy.ps1 como update: su bootstrap crea recursos/identidades. No inventar `--dry-run` ni lanzar publish para averiguar qué empaqueta.

W5 usa el script real existente, pero hoy no garantiza seleccionar un ZIP preconstruido. Antes de ventana debe estar resuelto si el pipeline preserva el artefacto ensayado o reconstruye de forma reproducible y verifica hash. Sin resolverlo, STOP. No añadir un comando `deploy:mcp:prod` ficticio como si ya existiera.

Observación posterior: 60 min supervisados tras cierre técnico y revisión de métricas a 24 h; no sustituyen la retirada verificada de credenciales legacy. Expansión requiere aceptación separada; con bajo tráfico ampliar muestra en entorno autorizado, no inventar percentiles.

## 6. Smoke de ventana

Prerrequisito: tests de la matriz aceptados en entorno controlado. En PROD sólo usuarios/tenants aprobados y acciones acotadas:

1. Chat normal sin tool; nueva sesión y continuidad de una sesión anterior.
2. List/get del ticket de prueba, documentos de prueba y unread conocido. Request/tenant/user obtenidos por contexto backend; confirmar 1 handler y audit_id correlacionado.
3. Dos cuentas autorizadas comparan únicamente sus propios datos de prueba. No buscar datos de terceros ni inyectar carga/fallos contra PROD sin permiso explícito.
4. Doble tool request se prueba con runtime fixture en preproducción; en producción verificar contador nunca >1, no inducirlo con prompts peligrosos.
5. Propuesta -> cancelar no crea; propuesta nueva -> confirmar crea una vez; replay devuelve el mismo ID. No reejecutar un ticket legítimo ni restaurarlo para repetir prueba.
6. History/delete únicamente sobre sesión de prueba propia; conservar las demás. Operaciones reales requieren autorización del dueño de la prueba.
7. Voice sólo prueba de compatibilidad de identidad/bootstrap acordada; no declarar su audio o herramientas gobernados por MCP.
8. Revisar p95/p99 sólo con muestra suficiente, errores separados de denegaciones previstas, memoria/hijos/concurrencia y health de rutas independientes. Log seguro por request_id, no JWT/pushToken/prompts.

## 7. Rollback seguro y verificable

No asumir que una Lambda histórica concreta o main constituyen rollback. Durante preparación registrar `BASE_CHAT`, `BASE_WORKER`, `BASE_RUNTIME`, `BASE_PROXY`, keys/kids, URLs, schema de envelope y flags que fueron ENSAYADOS juntos.

Orden propuesto:

1. RC detiene ampliación. BA deshabilita admisión MCP de la cohorte mediante control server-side seguro. No poner automáticamente SOPHIA_TOOL_GATEWAY_ENABLED=false: hoy esa rama usa legacy.
2. Dejar terminar o expirar requests en vuelo dentro de sus límites; no repetir tools. Worker puede seguir hasta 5 s aunque Chat haya fallado. Confirmar cero hijos remanentes y registrar requests ambiguos.
3. Si sólo falla worker, volver su alias a versión compatible acreditada, o mantener tools no disponibles. Chat normal/C.3 no necesitan una respuesta inventada del Gateway.
4. Si falla Chat, restaurar release Chat que mantenga cierre de bypass y contratos thread/C.3; configurar pareja runtime/worker correspondiente. Si API apunta a $LATEST, no hay alias mágico: redeploy del artefacto/config aprobados es el procedimiento, a ensayar antes.
5. Si falla runtime, volver a artefacto/URL segura acreditada compatible con firmas y continuaciones. No enviar continuación de una versión incompatible a otro runtime. Mantener una respuesta de indisponibilidad de herramientas mientras se recupera; preservar bindings/sesiones existentes.
6. Si falla identidad Voice, rollback del proxy y su verificador como pareja aprobada. Si exige reaceptar bearer legacy SOPHIA, NO automatizar: SEG/RC deben aceptar explícitamente ese riesgo o mantener indisponible la función afectada. No quitar la protección de Chat para resolver Voice.
7. Restaurar config/alias/permisos no secretos del manifest, sin rotar ni borrar keys. Los verificadores deben admitir sólo kids/propósitos aprobados. Guardar la versión fallida para análisis, sin secretos.
8. Repetir health/Chat normal/history/C.3/Voice bootstrap acordado. No restaurar RDS/DynamoDB. DAT valida que tickets y receipts creados durante la ventana siguen coherentes.
9. Registrar tiempos reales, causa, impacto, estado final y riesgo residual. Si se excede RTO, incidente formal y tools cerradas hasta otro cambio autorizado.

Las versiones Lambda conservan código y parte de configuración inmutable; no sustituyen copia de permisos, triggers/aliases y demás asociaciones que pueden cambiar. [Versiones Lambda](https://docs.aws.amazon.com/lambda/latest/dg/configuration-versions.html).

## 8. Compatibilidad de release y flags

Identificadores conceptuales de versiones, NO tags/recursos existentes:

- C0/R0: Chat/runtime productivos por acreditar, posiblemente legacy.
- C1/R1: código D local actual (capability compartida), no identidad D.2 cerrada.
- C2: Chat futuro con identidad separada, puerto MCP, selector y modo seguro sin tools.
- W2: worker MCP futuro con envelope v1 verificado y Gateway fijado.
- R2: runtime aislado sin tools legacy, claves independientes y D compatible.
- V2: proxy con MI/exchange nuevo, sin secreto HS256 compartido; I2: emisión/verificación XOC de esos contratos.

| Combinación | Resultado y condición |
| --- | --- |
| C0/R0 o D flag=false actual | Comportamiento histórico a acreditar; puede conservar riesgo legacy. NO es baseline segura MCP por definición |
| C1/R1 D=true | Dos turnos locales soportados, pero bypass y HS compartido siguen abiertos; NO GO activación MCP |
| C2 + W2 + R2 + I2/V2 cerrados; selector MCP off seguro | Chat normal/C.3 y thread según pruebas; tool_request -> unavailable sin ejecución/fallback. Estado de preparación/rollback recomendado |
| C2 + W2 + R2 + I2/V2; selector on para cohort | Combinación objetivo sólo si manifest, firmas, source y gates pasan |
| C2 activo + W ausente/version desconocida | Fallo seguro; no llamar Executor local. No ampliar cohort |
| C2 nuevo signer + R1 verificador HS | Rechazo de auth esperado; no degradar algoritmo ni recurrir a bearer legacy |
| C1 + R2 sólo nuevas firmas | Rechazo esperado. Ordenar releases antes del cambio de tráfico |
| R2 exige protocolo pero D flag=false en Chat | Rechazo seguro; configuración inválida, STOP |
| V2 + I1 antiguo | Bootstrap incompatible; probar migración primero. No afirmar que IAM/MI existente soluciona esto sola |
| V1 + I2 sólo componente nuevo | Rechazo seguro pero rompe Voice; no cortar antes de actualizar/probar V2 |
| I2 temporal acepta ambos durante migración | Riesgo HIGH legacy mientras el humano solo pueda obtener/usar bearer SOPHIA antiguo; RC/SEG dueños, NO activación MCP todavía |
| Rollback reabre R0/exchange legacy | Excepción con aceptación explícita de riesgo; preferir tools unavailable, no recuperación automática |

Flags existentes: SOPHIA_TOOL_GATEWAY_ENABLED, SOPHIA_THREAD_CAPABILITIES_ENABLED (backend), SOPHIA_GATEWAY_REQUIRED (runtime). Selector MCP, modo tools unavailable y selección por cohort son **propuestas por implementar**; no escribir nombres como si ya tuvieran efecto. El kill switch nunca cambia automáticamente audiencias o algoritmos.

## 9. Criterio para cerrar la transición

RC sólo firma cierre con manifest de versiones servido en cada extremo, smoke aceptado, retiro de emisión/aceptación legacy SOPHIA probado, no acceso de runtime/proxy a material de firma global, compatibilidad Voice documentada, rollback ensayado y retención aprobada. Una ventana temporal de convivencia tiene dueño y condición de salida verificable; no se cierra simplemente porque pasó una hora.

Si no se acredita un artefacto o una clave necesaria para volver atrás, el deploy no inicia. Si lo que falla es la nueva herramienta, el resto de XOC conserva sus datos y funcionalidades autorizadas; no se sacrifican Step Functions, C.3, tickets, Push o modelos SQL para recuperar MCP.
