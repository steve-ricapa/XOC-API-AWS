# AC-2R: corrección focalizada del 12 de octubre de 2026

## Evidencia y límites del diagnóstico

Chat respondió HTTP 200 pero devolvió `agentcore_unavailable`. La invocación reciente registró 2874 ms en el cliente, antes de terminar el target. El target autorizó `xoc.tickets.list` y después registró `ValidationError`. Los argumentos no se loguean; un filtro status inválido es la explicación compatible con el código, no un valor concreto demostrado. No se atribuyó el fallo a permisos del usuario ni se ampliaron roles.

## Cambios

- Release aislado Chat, commit `4e8286b`: el timeout HTTP usa el presupuesto restante en lugar de cortarlo por la mitad. Conserva el tope AgentCore 5 s, límite gobernado 18 s, Lambda 20 s y TTL 5 s. Sin reintentos ni fallback.
- Los fallos técnicos se proyectan como `unavailable`, no como una denegación de autorización. Se conserva el código técnico seguro en metadata.
- SOPHIA-only, commit `b4f7520`: filtro status opcional con enum de estados de tickets existentes; instrucciones explícitas para omitirlo al listar todos y no atribuir un fallo técnico a falta de permisos.
- No se cambió el target, IAM, datos, Voice, VICTOR, Step Functions ni XOC-MCP.

## Pruebas

- Chat: 111 pruebas aprobadas, incluidas C.3, contexto, policy, threads y nuevas regresiones de presupuesto y semántica.
- Runtime: 42 pruebas aprobadas, una omitida, en `tests/test_gateway_runtime.py`.
- Inspección de CloudFormation: cambia únicamente Code y versión Lambda; sin diferencias de environment o IAM.
- El paquete conserva dependencias y fuentes ajenas byte a byte. Evidencia en `tests/ic1/.build/ac2r/repair-20261012`.

## Publicación

Azure: build remoto Python 3.12 exitoso, deployment `20622e2a-70c4-472c-927f-24ddf7dd922a`, host Running. Se publicó sólo el directorio allowlisted SOPHIA-only.

AWS: el primer intento falló localmente con ENOENT `.serverless/serverless-state.json`, antes de actualizar el stack. El helper restaura el estado generado del paquete seleccionado para Serverless 3; no se debe empaquetar concurrentemente en el mismo worktree durante un deploy. Reintento exitoso, 47 s; Lambda Active/Successful, SHA `VKixlT8FOnKMHQ44upO2EIPeeaQ6dEltWnbzT5zAy70=`.

## Verificación posterior y parada segura

El smoke directo Azure pasó saludo, continuidad y solicitud de tickets, pero el segundo turno recibió HTTP 401 `invalid_capability`. Una repetición recibió ese mismo error en el saludo. Una capability de servicio enviada después de esperar dos segundos sí recibió HTTP 200 con texto. Esto sugiere sensibilidad temporal en el smoke local, pero no acredita por sí solo la causa ni el estado del circuito AWS→Azure. No se cambió reloj, TTL, iat ni tolerancia de autenticación. No se fabricó una identidad humana.

Se inició retorno a `disabled`, conservando el código corregido, por no estar acreditada la verificación posterior. Las correcciones están implementadas y desplegadas; no hay PASS end-to-end ni reactivación autorizada por resultados completos. Hace falta comprobar Chat normal con la sesión humana legítima y resolver/acreditar esta intermitencia antes de continuar las herramientas.

Retorno confirmado: Serverless exitoso en 53 s; lectura Lambda `SOPHIA_TOOL_EXECUTION=disabled`, `LastUpdateStatus=Successful`, mismo SHA corregido. Esto acredita el rollback de configuración, no el smoke funcional humano posterior, todavía pendiente.

No hay push. La corrección no equivale a PASS end-to-end: falta repetir Chat normal y tickets.list con la sesión legítima, luego el resto de herramientas y el rollback funcional acordado. No se fabricaron JWT humanos ni se reutilizó el confirmation_token expuesto en una captura.

Comandos preparados, desde el backend principal:

```powershell
python -B tests/ac1/chat_ac2r_hotfix.py inspect --mode agentcore
python -B tests/ac1/chat_ac2r_hotfix.py deploy --mode agentcore
# Retorno a disabled con la corrección conservada, sólo cuando corresponda:
python -B tests/ac1/chat_ac2r_hotfix.py inspect --mode disabled
python -B tests/ac1/chat_ac2r_hotfix.py deploy --mode disabled
```
