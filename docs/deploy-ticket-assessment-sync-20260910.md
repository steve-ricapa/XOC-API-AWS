# Despliegue PROD — sincronización de evaluación de tickets

Fecha: 2026-09-10. Resultado final: **ambos stacks UPDATE_COMPLETE; verificación posterior OK**.

## Identidad y versiones

- Cuenta AWS: `811776156524`; región: `us-east-1`.
- Despliegue desde EC2 `13.223.207.0`, usuario `ubuntu`, rol `XOC-Serverless-DeployRole`.
- Backend main publicado: `cf33994b608a2234ea183de42da6d13a67839e09`.
- Mobile main publicado: `303eac8` (sin nuevas dependencias nativas).
- **Release realmente desplegado:** `411823546968c4851f3b0f3df2a2f77159e748f5`,
  rama remota `release/ticket-sync-prod-20260910`.
- Base de ese release: `d99732cdd473b137e97daf1fcb952b3cda7fd534`.

No se desplegó main completo: los dos stacks tenían código de d99732c, mientras
main incorpora un planificador múltiple y otros cambios ajenos a esta corrección.
Se compararon los 110 archivos Python `src/` de las Lambdas vigentes con Git y
coincidieron exactamente con d99732c. Se aplicó únicamente el commit de la
sincronización sobre esa base, se probó y se publicó el release antes de obtenerlo
en worktrees separados de la EC2. No hubo edición directa del código fuente en EC2.

## Alcance efectivo

| Stack | Resultado | Lambda cuyo código cambió |
| --- | --- | --- |
| xoc-api-tickets-prod | UPDATE_COMPLETE | startAutomation |
| xoc-api-automation-prod | UPDATE_COMPLETE | assessTicketAutomation |

Las otras nueve Lambdas conservan su CodeSha256 anterior, incluidas la API de
tickets, aprobación, ejecución de pasos posteriores y registro de resultados.
No se desplegaron chat, auth, ops, reports, admin, tenant, shared ni web.
No se crearon tickets ni se ejecutaron planes como parte del despliegue.
No se modificaron RDS, IAM, variables de entorno, canales push o permisos de usuarios.

Hashes finales de las dos Lambdas actualizadas:

```text
startAutomation:
4qy4UQi+4cEjuqV6qwWkdvSPn/3hl3gZWDh8S+m0dwY=

assessTicketAutomation:
o9y2Z4YxPjhYu0CU2RptNIrWSiSJhccN3+qCftvZtWc=
```

## Cómo se evitó introducir cambios ajenos

Se empaquetó con Serverless y se comparó CloudFormation antes de desplegar. La
reconstrucción inicial detectó diferencias en librerías, incluidas algunas nativas.
Esos binarios reconstruidos **no se utilizaron para actualizar las Lambdas**.

Se prepararon artefactos code-only a partir de los ZIP de producción, reemplazando
exclusivamente estos archivos por el contenido del release publicado:

- src/handlers/workers/assess_ticket_automation.py
- src/handlers/workers/start_automation.py
- src/notifications/events.py
- src/shared/ticket_automation_sync.py (nuevo)

Se comprobó igualdad byte a byte del resto de las entradas del ZIP, integridad ZIP,
CodeSha256 de los artefactos y concordancia con los recursos Lambda Version. La
plantilla conservó Code, Version y Outputs originales para las Lambdas no afectadas.
Antes del deploy se exigió cero diferencias ajenas al código en CloudFormation y
cero diferencias entre las variables de entorno empaquetadas y las efectivas.

Se utilizaron los scripts del repositorio con paquetes revisados:

```bash
# /home/ubuntu/xoc-ticket-sync-prod-4118235
npm run deploy:tickets:prod -- --package /home/ubuntu/xoc-ticket-sync-20260910/package-tickets

# /home/ubuntu/xoc-ticket-sync-automation-4118235
npm run deploy:automation:prod -- --package /home/ubuntu/xoc-ticket-sync-20260910/package-automation
```

Los scripts terminaron correctamente (60 y 80 segundos respectivamente). Hubo
advertencias preexistentes sobre timeout de 30 segundos en casesApi/publicApproval;
no se cambiaron esos límites ni sus funciones.

## Step Functions: comprobación explícita

No se modificó ninguna decisión, transición, aprobación, Retry, Catch, task token
o permiso del workflow. No se llamó StopExecution ni se reinició una ejecución.

Se detectó una discrepancia **preexistente** entre el comentario de CloudFormation
(flechas como `?`) y el comentario de la definición efectiva (flechas Unicode).
CloudFormation reaplicó el comentario histórico durante el deploy. La comprobación
final lo detectó; se restauró únicamente el comentario original efectivo, después
de verificar que absolutamente todo el resto de la definición era idéntico.
Esto produjo una revisión de metadata de Step Functions, no un cambio funcional.
Se conserva esa discrepancia de comentario frente a la plantilla histórica; debe
tenerse presente en futuros despliegues y no confundirse con un cambio de lógica.

Hashes SHA256 de las definiciones efectivas **antes y al terminar**, idénticos:

```text
xoc-api-tickets-prod-ticket-workflow:
d429af5465b911022d4773d04f53b2b0743c43608164e73bbb888b845a8b5ab5

xoc-api-automation-prod-workflow:
f5e36aec4e399083d9d2707eef4b38534562dd6aee23831ae6e880e252d24cc0
```

La ejecución que ya existía, `48337dd9-147a-40bb-8706-19fa6037b013`, continuó
en `RUNNING` después de finalizar. Se verificó también igualdad de los roles.

## Verificaciones

- Release aislado sobre PROD: 47 pruebas backend OK, incluidas condiciones/races
  DynamoDB con Moto, contratos de workers, aprobaciones y notificaciones.
- Rama principal de implementación: 59 pruebas relevantes OK en la fase anterior.
- Mobile: TypeScript y 14 pruebas OK, repetidas después del push.
- Los dos templates desplegados coinciden con los paquetes revisados.
- Las once Lambdas están Active/Successful. Sólo las dos previstas cambian código.
- Se compararon Handler, Runtime, Architectures, Role, Environment, Timeout,
  MemorySize, VpcConfig y Layers contra sus respaldos: sin cambios.
- Smoke real e inerte de startAutomation: evento `deploy.validation` devuelve
  `skipped / unsupported_event_deploy.validation`, sin consultar un ticket,
  crear datos, mandar push ni iniciar Step Functions.
- No se ha confirmado aún el flujo end-to-end de evaluación negativa y push en
  un teléfono después de este deploy; debe probarse con un ticket nuevo.

## Respaldos y cambios ajenos

En EC2, carpeta privada (0700): `/home/ubuntu/xoc-ticket-sync-20260910`.
Contiene templates previos, configuraciones, ZIP originales de las dos Lambdas,
manifiestos code-only, paquetes revisados y definición efectiva original.
No publicar esa carpeta: los snapshots de configuración pueden contener valores
sensibles. No se imprimieron credenciales ni variables de entorno en el reporte.

El repo `/home/ubuntu/XOC_AWS` se mantuvo en su rama/commit original, conservando
los cambios pendientes de `src/handlers/routes/dashboard.py` y
`src/integrations/dashboard_store.py` (99 adiciones/4 eliminaciones entre ambos).
El worktree automation usa un enlace local no versionado a node_modules; no es
un cambio de código fuente ni se subió al repositorio.

En mobile se preservó localmente el cambio previo del usuario en package.json:
`DISABLE_COMMIT_PAUSING_MECHANISM=false`. No se incluyó en este push ni se recompiló
la app: el development build ya instalado se mantiene y carga los cambios JS por Metro.

No se necesitó rollback. Ante una regresión, revisar estos respaldos y preparar
una restauración acotada de las dos Lambdas; no desplegar todos los stacks ni main
completo para resolver esta corrección.

## Prueba que sigue

1. Desde XOC-APP-MOBILE, levantar Metro con `npx.cmd expo start --dev-client --clear`
   si no está activo. Abrir el development build ya instalado y cargar ese bundle.
2. Crear un **ticket nuevo** mediante SOPHIA y confirmar una vez.
3. Mantener abierto su detalle. Si VICTOR no puede resolverlo, debe aparecer
   Revisión manual; si la evaluación falla técnicamente, Evaluación no completada.
4. Verificar el motivo/fecha en Resumen y Timeline, y la notificación sólo al creador.
5. Comprobar un ticket realmente resoluble: debe continuar a WaitForApproval y
   conservar aprobación/rechazo habituales.

El ticket antiguo que quedó PENDING antes del parche no se corrige retroactivamente.
No cambiar estados a mano ni reiniciar su ejecución para simular un resultado.
