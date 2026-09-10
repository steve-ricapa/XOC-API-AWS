# Sincronización del resultado inicial de VICTOR

Fecha: 2026-09-10. Desplegado en PROD desde un release aislado sobre la versión
vigente; pendiente la prueba funcional en un teléfono con un ticket nuevo.
Ver [reporte del despliegue y verificaciones](deploy-ticket-assessment-sync-20260910.md).

## Alcance y garantías del cambio

La salida `CheckCanResolve -> EndCannotResolve` ya terminaba el workflow sin
ejecutar un plan, pero no persistía ese resultado en el ticket. Ahora el worker
de evaluación registra la salida antes de devolver la misma respuesta de siempre.

- No se modificaron las definiciones de Step Functions, sus transiciones, Choice,
  Retry, Catch, tiempos de aprobación ni task tokens.
- No se cambia `canResolve`, la llamada a VICTOR, el plan, su ejecución ni la
  interpretación previa de una respuesta positiva.
- No se cambia la creación/confirmación de tickets por SOPHIA ni sus permisos.
- No hay cambios en RDS, modelos SQL, infraestructura, IAM ni canales push.
- La sincronización sólo actúa sobre tickets `PENDING` sin planes/decisiones y con
  ejecución ausente, NULL o `STARTED`. Es condicional y usa la clave del tenant.
- Una respuesta duplicada no vuelve a actualizar ni publicar. Un ticket borrado
  no se recrea y un ticket aprobado, ejecutándose o terminado no se retrocede.
- El registro tardío de `STARTED` ya no puede pisar un resultado. El ARN se adjunta
  por separado sin reemplazar el de otra ejecución.

## Contrato persistido y API

Se reutiliza la tabla de tickets y el status existente `DERIVED`; se actualiza
`gsi1pk` para mantener el índice de estado. Los demás índices se conservan.
`DERIVED` significa aquí **requiere revisión manual**, no asignación realizada a
un equipo/persona. El ticket no se marca resuelto ni se rellena `executed_at`.

| Resultado de la evaluación | status | execution_status |
| --- | --- | --- |
| VICTOR devuelve una evaluación negativa | DERIVED | CANNOT_RESOLVE |
| Sin endpoint / timeout / rechazo HTTP / error de comunicación | DERIVED | ASSESSMENT_FAILED |

El atributo adicional `automation_assessment` contiene:

```json
{
  "outcome": "CANNOT_RESOLVE",
  "reason_code": "CANNOT_RESOLVE",
  "message": "VICTOR no puede resolver este ticket automáticamente. Requiere revisión manual.",
  "completed_at": "<fecha ISO UTC>"
}
```

Los otros reason_code son `VICTOR_NOT_CONFIGURED`, `VICTOR_TIMEOUT`,
`VICTOR_ACCESS_DENIED` (sólo rechazo HTTP 401/403 del proveedor) y
`VICTOR_UNAVAILABLE`. Los mensajes son textos controlados, no contenido de
respuestas del proveedor, URLs, JWT, push tokens ni payloads sensibles.
`execution_summary` contiene el mismo mensaje seguro. El serializador actual
expone estos campos en GET del ticket sin añadir endpoints.

## Notificaciones

Se reutiliza `publish_ticket_status_notification` y el evento `ticket.derived`.
Sólo recibe el creador almacenado en el ticket, audiencia SELF y mismo tenant,
con deep link `xoc://ticket/<id>` y la clave de deduplicación existente.
El nuevo texto para `DERIVED` no afirma que haya expirado una aprobación.
La variante previa `DERIVADO`, usada por timeout de aprobación, no cambia.

La escritura y el push son **best effort**, no una transacción/outbox: ante un
fallo de DynamoDB se conserva la decisión del workflow y se registra
`ticket_assessment_sync_failed`; ante un fallo de publicación se mantiene el
resultado guardado. Este parche no añade reconciliación automática ni garantiza
entrega exactamente una vez. Un fallo entre escritura y publicación requiere
revisión operativa, como en el resto de hooks actuales.

## Mobile

- Listado y detalle muestran `Revisión manual` o `Evaluación no completada` con
  el mensaje seguro. El stepper no presenta planificación/aprobación/ejecución
  como etapas alcanzadas en esta salida; se añade el resultado al timeline.
- El detalle vuelve a consultar al abrirse/regresar a primer plano y cada cinco
  segundos mientras está visible, activo y en un estado en progreso.
- Se detiene el polling en estados terminales, al salir de la pantalla, en segundo
  plano y ante HTTP 401/403/404. Hay refresco manual y mensajes de error.
- Una evaluación histórica no oculta un estado posterior del ticket.
- Sin nuevas dependencias nativas, cambios de build, auth, APNs/FCM ni Sophia Voice.
  Se preservó el cambio previo del usuario en package.json (Reanimated).

## Validación local realizada

- 59 pruebas backend OK: sincronización, carreras/condiciones DynamoDB con Moto,
  contratos del worker, aprobación/rechazo, eventos y bandeja de notificaciones.
- 14 pruebas mobile OK: confirmación SOPHIA y presentación/política de refresco.
- `npm.cmd run typecheck`: OK.
- Dos fallos preexistentes en `tests.test_assess_ticket_automation`, reproducidos
  también cargando el worker original de HEAD (sin este parche):
  `test_global_url_wins_and_marks_source_global` usa la variable genérica antigua;
  `test_normalize_plans_skips_empty_and_keeps_legacy` discrepa con el fallback
  legacy actual. No se modificaron esas rutas para mantener el alcance.
- No es una ejecución de la suite completa del monorepo ni un smoke real de AWS.

Reproducir backend en un entorno Python de pruebas con las dependencias del repo
y `moto[dynamodb]==5.2.3` (no agregar Moto a dependencias Lambda):

```powershell
$env:AWS_DEFAULT_REGION = 'us-east-1'
$env:AWS_EC2_METADATA_DISABLED = 'true'
$env:AWS_ACCESS_KEY_ID = 'testing'
$env:AWS_SECRET_ACCESS_KEY = 'testing'
python -m unittest tests.test_ticket_automation_sync tests.test_ticket_approval_flow tests.test_notification_events tests.test_user_notification_inbox -q
```

Mobile, desde XOC-APP-MOBILE:

```powershell
npm.cmd run typecheck
node --test tests/chat-confirmation.test.cjs tests/ticket-sync.test.cjs
```

## Procedimiento de activación y prueba posterior

El despliegue autorizado ya se realizó; no repetirlo a ciegas. El reporte enlazado
arriba identifica el release y los paquetes code-only usados para preservar PROD.

Los handlers modificados pertenecen a **tickets** (`startAutomation`) y
**automation** (`assessTicketAutomation`), no a ops. Los scripts existentes son
`npm run deploy:tickets:prod` y `npm run deploy:automation:prod`. Antes de ejecutar,
revisar el release/commit y los cambios ajenos pendientes para no desplegarlos por
accidente. No hacer deploy general de todos los stacks por este parche.

1. Desplegar el release revisado y cargar el JS mobile actualizado con Metro.
2. Crear un ticket nuevo de lectura, confirmar una sola vez en SOPHIA.
3. Si la evaluación devuelve false, comprobar la misma ruta `EndCannotResolve`.
   Step Functions puede finalizar SUCCEEDED: significa fin correcto del workflow,
   no resolución exitosa del ticket.
4. Verificar el ticket en DynamoDB/GET: DERIVED, ejecución terminal, motivo seguro,
   fecha de evaluación e índice de status coherente.
5. Mantener abierto su detalle: debe actualizarse aproximadamente en el siguiente
   ciclo de cinco segundos, sujeto a red/consistencia eventual. Probar también
   salir/regresar, segundo plano y refresco manual.
6. Verificar notificación/bandeja sólo del creador y navegación al mismo ticket.
7. Con un ticket realmente resoluble verificar que sigue llegando a
   WaitForApproval, y que aprobar/rechazar conserva el comportamiento anterior.

Los tickets ya terminados antes del despliegue **no se reparan retroactivamente**.
No reejecutar sus Step Functions ni cambiar estados manualmente para simular una
prueba. Reparar históricos requeriría validar sus ejecuciones e IDs por separado.
Tampoco se trata aquí cualquier fallo/timeout/abort de Step Functions fuera de
las salidas que el worker ya devuelve como evaluación negativa.
