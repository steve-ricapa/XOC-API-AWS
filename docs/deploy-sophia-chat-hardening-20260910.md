# Despliegue PROD: controles de acceso de SOPHIA Chat

Fecha: 2026-09-10. Autorizado por el usuario después de revisar la implementación.
Resultado: **xoc-api-chat-prod UPDATE_COMPLETE; verificaciones posteriores OK**.
Falta el smoke funcional con una sesión real de web/mobile. No se declara MCP
integrado ni se certifica aún una conversación autenticada después de este deploy.

## Identidad y versiones

- Cuenta: `811776156524`; región: `us-east-1`.
- EC2: `13.223.207.0`, usuario `ubuntu`.
- Identidad AWS verificada: rol `XOC-Serverless-DeployRole`, instancia
  `i-0a8c6992b01b0b7fc`.
- Commit de implementación en main: `997cddd`.
- Base exacta del código de Chat que estaba desplegado: `9497210`.
- Release desplegado: `fe3b25f13833fb300ad1049b0e7974b37be8e706`.
- Rama publicada: `release/chat-hardening-prod-20260910`.

Se comprobaron los 120 archivos Python `src/` del ZIP vigente contra Git:
coincidían exactamente con `9497210`. Main incluye cambios ajenos en otros dominios,
por lo que se creó un release separado sobre esa base, aplicando sólo `997cddd`.
Las 75 pruebas enfocadas pasan tanto en main como en el release aislado.

La laptop no tenía credenciales AWS configuradas. La inspección y el despliegue
se hicieron desde la EC2 usando su rol, sin copiar credenciales de AWS al equipo.

## Cambios efectivos y controles previos

Sólo cambia el código de `xoc-api-chat-prod-chatAgentsApi`:

- `src/handlers/routes/agents.py`
- `src/handlers/routes/chat.py`

Se preparó el paquete con Serverless y se comparó CloudFormation con producción.
Resultado: cero diferencias ajenas al código/versionado de la Lambda, y cero
diferencias de variables de entorno literales respecto a la configuración efectiva.

El ZIP final parte del ZIP de producción y sustituye únicamente los dos archivos
anteriores con contenido exacto del release publicado. Se comprobó que todas las
demás entradas del ZIP conservan su contenido byte a byte: sin nuevas dependencias,
sin cambios de binarios nativos, sin incorporar otros archivos de main.
Se actualizó el hash de código en el recurso Lambda Version y el estado empaquetado
de Serverless antes del deploy; no se modificó la plantilla de infraestructura fuente.

No se cambiaron rutas, IAM, configuración del runtime, RDS, modelos, migraciones,
workflows, aprobaciones, VICTOR, push, reportes o código Voice. Tampoco se desplegaron
web/mobile ni stacks distintos de Chat. No se ejecutó código del runtime externo.

## Ejecución

Desde un worktree separado obtenido por Git, no desde el repo principal sucio:

```bash
cd /home/ubuntu/xoc-chat-hardening-prod-fe3b25f
npm run deploy:chat:prod -- --package /home/ubuntu/xoc-chat-hardening-20260910/package-chat
```

Serverless finalizó correctamente en 35 segundos. Mostró una advertencia de
deprecación, sin error de despliegue. No se ejecutó rollback.

Hashes de código Lambda:

```text
Antes: 6AVMm39bd6DbOdiPC5c5LiLMTpfWLC7a18QWdjcsumE=
Final: H/uO4Z5cNLVyRGLy/CIBibwN/Gen8BXzvrvW+6niP2E=
```

## Verificación posterior

- Stack: `UPDATE_COMPLETE`.
- Lambda: `Active`, `LastUpdateStatus=Successful`.
- CodeSha256 coincide con el artefacto revisado.
- ZIP descargado de la Lambda después del deploy: sólo cambian los dos archivos
  previstos; el resto de las entradas sigue idéntico al respaldo.
- Template desplegado idéntico al paquete validado.
- Handler, Runtime, Architectures, Role, Environment, Timeout, MemorySize,
  VpcConfig, Layers, TracingConfig, FileSystemConfigs y EphemeralStorage: iguales.
- Aliases de Lambda: sin cambios.
- Definiciones, roles y revisionId de ambas Step Functions: iguales antes/después.
- Ejecución preexistente `48337dd9-147a-40bb-8706-19fa6037b013`: continuaba `RUNNING`.
  No se llamó StopExecution, StartExecution ni UpdateStateMachine.

Smoke real e inerte por invocación Lambda a la ruta legacy deshabilitada
`POST /agents/auth/token`: devolvió el esperado **410 LEGACY_AGENT_AUTH_DISABLED**,
sin FunctionError. Esto valida importación, arranque, Mangum y enrutamiento, no
la allowlist autenticada. Ese handler no crea usuarios/tokens/tickets, no consulta
RDS ni llama a SOPHIA.

Smoke HTTP anónimo por API Gateway a `GET /chat/sessions`: **401**, acceso rechazado.

API base, sin cambio:

```text
https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com
```

No se generaron JWT de usuarios ni se fabricó contexto autenticado para simular
el smoke funcional. No se crearon tickets, notificaciones o conversaciones reales.

## Respaldos y cambios preservados

Directorio privado en EC2: `/home/ubuntu/xoc-chat-hardening-20260910` (0700).
Incluye ZIP/configuración/template previos, definiciones de Step Functions,
paquete revisado, manifest de hashes y resultado de verificación.
No compartir esa carpeta: los snapshots de configuración pueden contener secretos.
La reversión debe ser acotada a Chat usando esa base; no desplegar main entero.

`/home/ubuntu/XOC_AWS` se conservó en `main`, commit `046a0f0`, con los cambios
previos de `src/handlers/routes/dashboard.py` y `src/integrations/dashboard_store.py`.
Sólo se hizo fetch en ese repo, sin reset, stash, pull o modificaciones de fuente.
El nuevo worktree está en `fe3b25f` y usa un enlace local a node_modules existente;
ese enlace no se publica ni es parte del código del release.

Mobile conserva el cambio local previo de package.json relacionado con Reanimated.
No se necesita instalar otra build por este despliegue backend.

## Prueba funcional pendiente

1. Abrir SOPHIA en la app ya instalada y crear una conversación nueva.
2. Enviar un saludo sencillo, responder de nuevo y comprobar que continúa el mismo chat.
3. Salir, reabrir esa conversación y revisar historial.
4. Repetir con una conversación previa que tenga vínculo de sesión local.
5. Con cuentas de prueba autorizadas: comprobar aislamiento entre usuarios/tenants,
   demo y delegación; IDs ajenos deben rechazarse sin llamada al runtime.
6. Probar propuesta y confirmación de ticket; comprobar que su automatización
   y sincronización de estado siguen el recorrido normal.

No compartir JWT ni contraseñas en logs. Si falla un caso, registrar hora, endpoint,
código HTTP y mensaje seguro para investigar. No reactivar el uso de hilos sin vínculo
local ni eliminar controles para ocultar un fallo de compatibilidad.

Después de ese smoke se puede coordinar la integración del runtime con Tool Gateway;
MCP/Voice y la sincronización visual web siguen fuera de este despliegue.
