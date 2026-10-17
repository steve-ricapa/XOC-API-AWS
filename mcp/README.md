# XOC MCP — SOPHIA Chat (laboratorio)
LAB_ONLY · OFFLINE runtime · FIXTURES · NOT_PRODUCTION

Servidor MCP local real que reutiliza el Tool Gateway canónico para consultas READ_ONLY.
No es Voice MCP, VICTOR MCP, un gateway alternativo ni un servidor productivo.

Circuito objetivo: SOPHIA Chat → MCP → Tool Gateway → Policy → Executor → Audit → providers XOC.
En esta entrega los providers son fixtures; NO está conectada a SOPHIA productiva.
Transportes probados: stdio histórico y servidor persistente Streamable HTTP local.
La policy recibe identidad verificada por el laboratorio, no autoridad elegida por el modelo.

Tools: xoc.tickets.list, xoc.tickets.get, xoc.documents.list, xoc.notifications.unread_count.
Las tools disabled permanecen disabled; escrituras/auto-ticket no se habilitan.

## Estado y documentos
Ver PROJECT_STATUS.md, MCP_LOCAL_BASELINE.md y docs/evidence/closure-summary.json.
Los documentos de docs/architecture son planes históricos, no autorización de cloud.
docs/evidence/l6-result.md conserva el cierre anterior inconcluso; el baseline y el
resumen de cierre actuales prevalecen para esta entrega. Evidencia masiva va en archive externo.

## Dependencia canónica
Canonical Tool Gateway source: XOC-API-AWS
commit: ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9
Core wheel esperado:
df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41

No editar una copia de Policy/Executor aquí. packaging/gateway-files.json selecciona
blobs exactos para el export/build original. artifacts/wheels contiene sólo cinco
artefactos propios pequeños, generados y verificables: no un wheelhouse público ni
otra fuente editable del gateway. Sus manifests fijan origen y hashes.
No publicar estas wheels en un índice público como producto listo para producción.
No se decidió submodule ni GitHub Packages.

## Preparación Windows
Requisitos: CPython 3.13.1 para el lock Windows, PowerShell y Docker Desktop con motor
Linux/amd64 para el runtime AL2. El backend de desarrollo es un clone separado llamado
XOC-API-AWS con el commit indicado; no cambiar una copia de trabajo ajena para ajustarlo.
El responsable debe preparar un clone de la revisión requerida.

Desde esta carpeta:
~~~powershell
$backend = (Resolve-Path '..\XOC-API-AWS').Path
python -B scripts/handoff.py prepare
powershell.exe -NoProfile -ExecutionPolicy Bypass -File setup.ps1 -PythonExecutable python
.\.venv\Scripts\python.exe -B run_tests.py --backend "$backend"
.\.venv\Scripts\python.exe -B smoke.py --backend "$backend"
.\.venv\Scripts\python.exe -B smoke_l1.py --backend "$backend"
~~~
prepare materializa entradas pequeñas verificadas en directorios ignorados que esperan
los tests históricos. setup descarga dependencias fijadas de PyPI: la preparación NO
es offline; la ejecución de negocio y aceptación sí lo es.
Los tests POSIX se omiten en Windows; ejecutar Linux para validar recuperación.

Verificar/reconstruir el core desde el clone canónico, con salidas nuevas:
~~~powershell
.\.venv\Scripts\python.exe -I -B scripts/handoff.py verify-core --backend "$backend" --output build/core-check
~~~
Lee blobs Git mediante los scripts existentes; no hace checkout, pull, commit ni
escrituras en el backend. Otros artefactos de ese export son de desarrollo y NO
sustituyen las cuatro wheels históricas fijadas.

## Linux: reconstrucción independiente
La imagen de la laptop NO es una dependencia. Se reconstruye desde el digest público
fijado de Amazon Linux 2/Python 3.11.16 y las wheels propias. No se promete que el ID
Docker local resultante sea igual al ID histórico; se verifican los artefactos y
los archivos instalados canónicos.

Obtener explícitamente la imagen base (requiere red pública, no cuenta AWS):
~~~powershell
docker pull --platform linux/amd64 public.ecr.aws/lambda/python@sha256:cc940054e77d4a160c611a20c09cb8ab9325c09d72555a164329eb3e46957d80
.\.venv\Scripts\python.exe -I -B scripts/handoff.py build --output build/linux-a
.\.venv\Scripts\python.exe -I -B scripts/handoff.py build --output build/linux-b
.\.venv\Scripts\python.exe -I -B scripts/handoff.py compare --first build/linux-a/artifacts --second build/linux-b/artifacts
~~~

Preparar dependencias públicas Linux desde los locks SIN resolver versiones nuevas:
~~~powershell
.\.venv\Scripts\python.exe -m pip --isolated download --index-url https://pypi.org/simple --require-hashes --only-binary=:all: --no-deps --platform manylinux_2_24_x86_64 --platform manylinux2014_x86_64 --python-version 311 --implementation cp --abi cp311 --abi abi3 --abi none --dest build/dependencies/wheelhouse -r packaging/requirements-linux-runtime.lock.txt -r packaging/requirements-linux-test.lock.txt
.\.venv\Scripts\python.exe -I -B scripts/handoff.py context --artifacts build/linux-a/artifacts --wheelhouse build/dependencies/wheelhouse --output build/execution
docker build --pull=false --network none --platform linux/amd64 -f build/execution/Dockerfile.handoff -t xoc-mcp-handoff:local build/execution
~~~
Si PyPI ya no entrega alguna wheel exacta, obtenerla de la custodia autorizada y
verificar el hash del lock. No regenerar el lock ni usar otra versión silenciosamente.

## Tests / smoke / aceptación instalados
~~~powershell
.\.venv\Scripts\python.exe -I -B scripts/handoff.py run --image xoc-mcp-handoff:local --phase tests --output build/validation/tests
.\.venv\Scripts\python.exe -I -B scripts/handoff.py run --image xoc-mcp-handoff:local --phase smoke --output build/validation/smoke
.\.venv\Scripts\python.exe -I -B scripts/handoff.py run --image xoc-mcp-handoff:local --phase recovery --output build/validation/recovery
.\.venv\Scripts\python.exe -I -B scripts/handoff.py run --image xoc-mcp-handoff:local --phase provenance --output build/validation/provenance
~~~
Para rendimiento ejecutar serialmente benchmark-l4, benchmark, benchmark, benchmark-l4,
benchmark-l4, benchmark, cada uno con --output nuevo; son los tres pares alternados.
No correr builds/otras pruebas durante ellos. No seleccionar el mejor par.
El runner original mantiene 512 MiB/2 CPU/128 pids, usuario no root, red none,
read-only, tmpfs 128 MiB, sin mounts ni credenciales. Dentro se usa /tmp y paquetes
instalados; NO requiere clone hermano durante runtime.

scripts/l6.ps1 y l6_finalize.py se conservan como reproductores del expediente
original, con IDs/RunIds de aquel laboratorio. Para la entrega transportable usar
scripts/handoff.py. No editar esos pins para fingir que otra imagen es la histórica.
La validación de esta exportación y sus límites constan en docs/evidence/handoff-validation.json.

Servidor stdio manual (fixtures, no consola de chat):
~~~powershell
.\.venv\Scripts\python.exe -B server.py --lab --backend "$backend" --profile admin_tenant_a
~~~
El L6 persistente/supervisado se ejercita con el runner instalado; no se publica
HTTP en el host y no se configura ningún cliente global.

## Integridad, seguridad y colaboración
Ver SECURITY.md y CONTRIBUTING.md. Fixture ES256:
TEST FIXTURE ONLY — PUBLIC — NOT A SECRET — DO NOT USE IN PRODUCTION.
No usar su scalar/issuer/verificador como identidad productiva.

HANDOFF_MANIFEST.json lista los archivos de contenido; excluye el propio manifest y
HANDOFF_SHA256SUMS.txt para evitar autorreferencia imposible. SHA256SUMS cubre contenido
y manifest, excepto a sí mismo. El hash externo del ZIP cubre también SHA256SUMS.
No constituyen firma/autenticación criptográfica de un editor.

No hay .git, login GitHub, commits, push, Actions ni deploy en esta entrega.
El responsable creará el repositorio y elegirá controles de acceso/licencia.

## Siguiente bloque (requiere autorización separada)
Integración controlada SOPHIA Chat ↔ MCP: identidad/firma reales (D.2), providers
READ_ONLY reales, hosting, cliente Chat, cierre de bypass, canary, smoke, observabilidad
y rollback. No tocar VICTOR ni Voice. No avanzar producción con este baseline.
