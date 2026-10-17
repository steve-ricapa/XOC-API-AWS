# L3 — comandos y ejecuciones

Todos los comandos se ejecutan desde:

```powershell
Set-Location -LiteralPath '<WORKSPACE>\XOC-MCP-LAB'
```

Docker Desktop ya estaba iniciado por el usuario. No se instaló ni cambió ningún
servicio. ExecutionPolicy Bypass es sólo para el proceso PowerShell invocado,
no una modificación de la política global. No hay Git writes ni comandos remotos.

## Regresión Windows ejecutada

```powershell
.\.venv\Scripts\python.exe -B run_tests.py --backend '..\XOC-API-AWS'
if ($LASTEXITCODE -ne 0) { throw "Lab tests: $LASTEXITCODE" }
.\.venv\Scripts\python.exe -B smoke.py --backend '..\XOC-API-AWS'
if ($LASTEXITCODE -ne 0) { throw "MCP-1 smoke: $LASTEXITCODE" }
.\.venv\Scripts\python.exe -B smoke_l1.py --backend '..\XOC-API-AWS'
if ($LASTEXITCODE -ne 0) { throw "Signed smoke: $LASTEXITCODE" }
```

La suite original dio 191 PASS antes de cambios. La suite FINAL dio 212 PASS,
84,41 s, exit 0; ambos smokes finales exit 0. Estos comandos importan el gateway
canónico bajo Isolation/Wiring; NO ejecutan la suite global backend ni VICTOR.

## Baseline L2 inmutable ejecutado

```powershell
foreach ($step in @('OriginalTests','OriginalBenchmark')) {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'scripts\l3.ps1' -Step $step -RunId 'l3-original-20260915'
    if ($LASTEXITCODE -ne 0) { throw "L2 $step failed: $LASTEXITCODE" }
}
```

Los pasos se ejecutaron sobre la imagen L2 be002dede580..., sin reconstruirla ni
reescribir verified-20260915. El baseline-probe se construyó ANTES de C1 y ejecutó
Build, Image, Profile y Benchmark con RunId l3-baseline-probe. Los grupos de fuente
adapter/core se verificaron idénticos a L2. Construir hoy desde las fuentes C1
no recrea ese baseline: para compararlo usar su imagen inmutable 54ec2ff0f476...
o los staging preservados, no llamar «baseline» a una compilación del código nuevo.

## Pipeline candidato ejecutado

```powershell
foreach ($step in @('Build','Rebuild','Image','Tests','Smoke','Provenance','Profile','Benchmark')) {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'scripts\l3.ps1' -Step $step -RunId 'l3-candidate-c1'
    if ($LASTEXITCODE -ne 0) { throw "C1 $step failed: $LASTEXITCODE" }
}
```

Se ejecutó cada fase sólo después de exit 0 de la anterior. Build usa el builder
L2; Image usa exclusivamente el wheelhouse l2-runs/20260915/preparation,
pip --no-index/locks con hashes y docker build --network none. Tests/Smoke/
Provenance/Benchmark/Profile corren desde site-packages, no montan checkout,
venv, credenciales, HOME ni docker.sock. Sólo el volumen propio de resultados.

El script comprueba internamente exit codes y se niega a sobrescribir resultados.
NO copiar estos RunIds para repetir sobre carpetas ya existentes: elegir uno nuevo.
No hay que ejecutar Prepare ni descargar dependencias para estos materiales.

## Últimos dos builds, usuario no privilegiado explícito

```powershell
foreach ($step in @('Build','Rebuild')) {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'scripts\l3.ps1' -Step $step -RunId 'l3-c1-final-build'
    if ($LASTEXITCODE -ne 0) { throw "Final $step failed: $LASTEXITCODE" }
}
```

Ejecutados después de añadir --user 10001:10001 al constructor. Hashes y manifests
idénticos entre sí y a C1 ya instalado/probado; no cambió código ejecutable.
No se necesita declarar otra imagen distinta como si fuera el benchmark final.

## Sondas alternadas ejecutadas

```powershell
python -B 'scripts\l3_pair_probe.py' --run-id 'l3-budget-pairs-20260915'
if ($LASTEXITCODE -ne 0) { throw "Paired probe: $LASTEXITCODE" }
```

El script fija las dos imágenes existentes por ID; 5 normales + 2 bloqueadas +
2 tardías por imagen, alternadas, timeout solicitado 3,5 s. Sin red ni montajes,
mismos límites y usuario de la imagen 10001. Tiempo Docker externo separado.
No es startup Lambda, no cambia presupuestos y no reintenta muestras fallidas.

## Smoke MCP-1 Linux independiente ejecutado

Además del smoke firmado del pipeline, se ejecutó el cliente MCP-1 original desde
la imagen instalada, con este programa en memoria (sin editar site-packages):

```powershell
$probe = @'
import asyncio,json,os
from pathlib import Path
from xoc_mcp_lab.isolation import Isolation,configure_logs
from xoc_mcp_lab_harness.observe import ProcessObserver
with Isolation():
 configure_logs()
 from xoc_mcp_lab.client import smoke
 with ProcessObserver() as observer:
  result=asyncio.run(smoke(None))
  result['processes']=observer.evidence()
children=Path('/proc/%s/task/%s/children'%(os.getpid(),os.getpid())).read_text().strip()
assert not children
result['children_before_exit']=children
print(json.dumps(result))
'@
docker run --rm --name xoc-l3-final-mcp1 --platform linux/amd64 --user 10001:10001 --memory 512m --cpus 2 --pids-limit 128 --network none --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev,size=64m --entrypoint /opt/xoc-l2/bin/python sha256:421858fa81bd95a041aa12e85e6528d48eacae293674789156a6b19aebc3fd40 -I -B -c $probe
if ($LASTEXITCODE -ne 0) { throw "Linux MCP-1 smoke: $LASTEXITCODE" }
```

Exit 0: 12 allowed, 16 denied; cuatro hijos reaped antes de finalizar, /proc
children vacío. El programa/cliente mantiene stdout MCP del hijo; el JSON de
resumen es salida del proceso exterior, no del transporte MCP.

## Inspección final ejecutada

```powershell
docker ps -a --filter 'name=xoc-l3-' --format '{{.Names}} {{.Status}}'
if ($LASTEXITCODE -ne 0) { throw "Own containers: $LASTEXITCODE" }
docker image inspect 'xoc-mcp-l3-local:l3-candidate-c1' --format '{{.Id}} {{.Config.User}}'
if ($LASTEXITCODE -ne 0) { throw "Image inspect: $LASTEXITCODE" }
```

No quedaron contenedores propios xoc-l3-. Imagen final 421858fa81bd..., usuario
10001:10001. No se ejecutó Docker prune, limpieza global, kill por nombre ni
destrucción de procesos ajenos. Esta inspección complementa, NO sustituye, la
recolección comprobada DENTRO de cada contenedor antes de salir.
