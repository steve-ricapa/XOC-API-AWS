# SOPHIA Chat → MCP HTTP: integración LOCAL

Este harness usa el handler Chat real y el servidor L6 instalado de XOC-MCP
por HTTP loopback. Usuarios, firma ES256, runtime SOPHIA y providers son fixtures.
No conecta a AWS/Azure, no implementa identidad productiva ni habilita un endpoint.

## Composición y límites

`local_execution(LocalMCPExecution(...))` instala un puerto por ContextVar.
Chat crea ToolContext desde su autenticación existente; el modelo no elige
identidad, delegación, endpoint ni transporte. El issuer fixture recibe ese
contexto y firma una sola llamada READ_ONLY. MCP aplica su Policy/Executor
canónicos, y el cliente valida binding, proyección y tamaño de ToolResult.

Se mantiene un único call y un segundo turno del runtime. No hay retries,
redirecciones, replay, fallback HTTP→stdio ni fallback HTTP→ToolExecutor.
Sin puerto local, Chat conserva la ejecución existente. La composición local
se rechaza si hay marcadores de runtime AWS Lambda/Azure App Service.
La fixture ES256 usa un escalar público intencional: nunca usarla en producción.

El deadline real de Chat limita la llamada, incluyendo carga del SDK y cleanup;
su vigencia firmada se redondea hacia abajo. Las dependencias opcionales MCP no
se importan en el camino normal sin composición local. C.3 sigue separado:
propuesta → confirmación humana → creación; MCP no expone create ni write.

## Archivos mantenibles

- `src/shared/sophia_tool_execution.py`: interfaz y composición local.
- `src/shared/sophia_mcp_client.py`: cliente HTTP acotado, validación, fail-closed.
- Chat / `sophia_tool_chat.py`: selección del puerto y propagación del deadline.
- `tests/test_sophia_mcp_execution.py`: contrato del cliente/puerto sin red.
- `integration.py`, `fixture_issuer.py`, `run.py`: 32 E2E locales; I01–I30
  agrupan algunos escenarios, con controles adicionales de redirect/deadline.
- `no_stdio.py`, `guard_selftest.py`: tripwire propagado al harness/supervisor/hijo.
- `http_regression.py`, `fast_track.py`: suite instalada L6, C01–C24,
  smoke/provenance y runner por imagen explícita.
- `backend_regression.py`: focal/full con guardas offline y reporte sanitizado.
- `prepare.py`, `Dockerfile`, lock: contexto explícito sin env/secrets/mounts.

Los diagnósticos FT/IC-1R y `.build` son evidencia local, no componentes productivos.
No interpretar un PASS diagnóstico con 12 s como aceptación de startup L6 de 4 s.

## Reproducción

Requiere Docker Linux y el handoff XOC-MCP verificado. Imagen base esperada:
`sha256:ffd2cdc17c30289ca4eaa3787cabe8190c38017f1840ad2020a7baef2b6c8df4`,
con SDK 1.30.0/Python 3.11.16 y wheels canónicas. Ver los comandos de construcción
offline y manifests del repo XOC-MCP; no sustituir ese baseline por un tag remoto.
Los tres wheels adicionales del lock deben estar previamente en
`../XOC-MCP/build/ic1-extra`; no se descargan durante el build ni el test.

Desde XOC-API-AWS, escoger una ruta de evidencia NUEVA en cada ejecución:

```powershell
python -B tests/ic1/prepare.py --mcp ../XOC-MCP --output tests/ic1/.build/my-run-context
docker build --network none -t xoc-chat-mcp-ic1:local tests/ic1/.build/my-run-context
python -B tests/ic1/fast_track.py tests --output tests/ic1/.build/my-run/l6.json
python -B tests/ic1/fast_track.py recovery --output tests/ic1/.build/my-run/cases.json
python -B tests/ic1/fast_track.py smoke --output tests/ic1/.build/my-run/smoke.json
python -B tests/ic1/fast_track.py provenance --output tests/ic1/.build/my-run/provenance.json
python -B tests/ic1/fast_track.py guard --output tests/ic1/.build/my-run/guard.json
python -B tests/ic1/fast_track.py ic1 --output tests/ic1/.build/my-run/ic1.json
```

Detenerse ante un rojo; conservarlo. No repetir hasta conseguir verde ni aumentar
timeouts. Los runners usan contenedores secuenciales sin red, RO, tmpfs,
512 MiB/2 CPU/128 pids, sin mounts/puertos publicados. `--image` permite seleccionar
una imagen local explícita; cada reporte conserva su ID resuelto.

## Qué NO acredita

Nada de este harness acredita Azure real, IAM/OAuth, firma/rotación productiva,
providers reales, disponibilidad cloud, Voice o VICTOR. El problema histórico
de teardown del worker stdio no se declara corregido; ese lifecycle no participa
en este camino HTTP. IC-2A requiere un bloque separado de identidad/firma,
providers READ_ONLY y diseño de hosting/runtime. No se inicia automáticamente.
