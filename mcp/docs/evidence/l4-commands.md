# L4 LOCAL: comandos y tratamiento de evidencia

Fecha: 16-sep-2026. Sólo XOC-MCP-LAB, fixtures, Docker local y dependencias ya
disponibles. Estos comandos no autorizan cloud, Chat, Voice, VICTOR, despliegues,
descargas ni operaciones Git de escritura.

## Reproducción final en PowerShell

Ejecutar desde el laboratorio. La evidencia C1 fresca está en
`l4-final02-20260916`; el paquete L4 final es `l4-final04-20260916`. Los comandos
siguientes muestran esos identificadores históricos para relacionarlos con la
evidencia. Para reproducirlos se deben cambiar ambos por RunIds nuevos: las
comprobaciones iniciales rechazan reutilizar los directorios ya existentes.
No borrar ni sobrescribir evidencia anterior. El resultado de cada fase lo
acreditan sus JSON, no la mera aparición del comando en esta receta.

```powershell
Set-Location -LiteralPath '<WORKSPACE>\XOC-MCP-LAB'
$ErrorActionPreference = 'Stop'
$l4C1RunId = 'l4-final02-20260916'
$l4RunId = 'l4-final04-20260916'
if ((Test-Path -LiteralPath (Join-Path 'l4-runs' $l4C1RunId)) -or
    (Test-Path -LiteralPath (Join-Path 'l4-runs' $l4RunId))) {
    throw 'Hay un RunId existente; cambiar ambos para una reproducción completa.'
}

# Baseline histórico C1 instalado: no necesita construir la imagen L4.
.\scripts\l4.ps1 -Step C1Tests -RunId $l4C1RunId
.\scripts\l4.ps1 -Step C1Smoke -RunId $l4C1RunId
.\scripts\l4.ps1 -Step C1Provenance -RunId $l4C1RunId

# Dos exportaciones independientes de las fuentes finales, con builds AL2.
.\scripts\l4.ps1 -Step Build -RunId $l4RunId
.\scripts\l4.ps1 -Step Rebuild -RunId $l4RunId
.\scripts\l4.ps1 -Step Image -RunId $l4RunId
.\scripts\l4.ps1 -Step Inspect -RunId $l4RunId

# Validación desde paquetes instalados; ningún checkout se monta en runtime.
.\scripts\l4.ps1 -Step Tests -RunId $l4RunId
.\scripts\l4.ps1 -Step Smoke -RunId $l4RunId
.\scripts\l4.ps1 -Step Provenance -RunId $l4RunId

# Medición después de los gates funcionales. No lanzar otros builds/tests propios
# simultáneamente. No tocar los contenedores ajenos que ya existen en el host.
.\scripts\l4.ps1 -Step C1Benchmark -RunId $l4C1RunId
.\scripts\l4.ps1 -Step Benchmark -RunId $l4RunId
.\scripts\l4.ps1 -Step Inspect -RunId $l4RunId
```

Cada proceso externo del script se comprueba por exit code; un error termina la
fase. `Rebuild` exige igualdad de los hashes de todos los archivos de `build-a`
y `build-b`, no sólo igualdad de nombres o versiones. No modificar las fuentes
entre ambos builds. Se comprobaron los siete archivos de los dos builds
final04 y sus hashes coinciden. Las iteraciones dev01/final01/final02/final03
se conservan y no se convierten retroactivamente en mediciones del paquete
final04. La corrección final rechaza cualquier respuesta HTTP 3xx antes de que
el SDK pueda seguirla y repetir la cabecera firmada, incluso hacia la misma URL.
No se modificó el SDK. Los tests finales ejercitan también 302/307/308.

Las fases `C1*` usan exclusivamente
`sha256:421858fa81bd95a041aa12e85e6528d48eacae293674789156a6b19aebc3fd40`.
Tests/smoke/provenance llaman al `linux_validation` histórico; benchmark llama
al `l3_benchmark` instalado en esa imagen. No reconstruyen ni modifican C1.

Las fases L4 resuelven `xoc-mcp-l4-local:<RunId>` a su ID inmutable antes de crear
el contenedor. Su entrada efectiva es:

```text
/opt/xoc-l2/bin/python -I -B -m xoc_mcp_lab_l4.harness
    --mode tests|smoke|provenance|benchmark
    --output /tmp/results/<mode>.json
```

## Evidencia de ejecución comprobada

Prefijos de las rutas siguientes: `l4-runs/l4-final02-20260916/results/` para
C1 y `l4-runs/l4-final04-20260916/results/` para el candidato L4.

| Paquete/fase | Evidencia relativa al prefijo | Resultado comprobado |
| --- | --- | --- |
| C1 Tests | `c1tests/c1tests/tests.json` | 212 PASS; 0 fallos, errores o skips; exit 0 |
| C1 Smoke | `c1smoke/c1smoke/smoke.json` | 6 casos PASS; 3 hijos recolectados |
| C1 Provenance | `c1provenance/c1provenance/equivalence.json` y `c1provenance/runner.log` | 41 casos; 20 módulos canónicos; exit 0 |
| C1 Benchmark | `c1benchmark/c1benchmark/benchmark.json` | 80/80 secuenciales correctas, 11 fallos inyectados y 3 casos grandes; comprobación final sin hijos |
| Candidata L4 Tests | `tests/tests.json` y `tests/runner.log` | 304 PASS juntos: 212 C1 intactos + 92 L4; 0 fallos/skips; 163,31 s; exit 0; sin hijos al final |
| L4 Smoke | `smoke/smoke.json` | `status=passed`, 25 filas; sin hijos al final |
| L4 Provenance | `provenance/provenance.json` | 41 casos canónicos; 37 HTTP idénticos; 4 identidades inválidas rechazadas por L1; 20 orígenes instalados |
| L4 Benchmark | `benchmark/benchmark.json` y `benchmark/container-process.json` | Ejecución final04 terminada con exit 0, `final_children` vacío; evidencia exportada y retirada propia con exit 0 |

Las cuatro fases C1 y las cuatro fases L4 citadas conservan
`container-process.json` con exit 0 y `container-cleanup.json` con retirada
exit 0 de su contenedor propio. La imagen observada para esas fases L4 es
`sha256:129efefeecc7549df84604ed2e532fa524aaab716f6cf145ca9e5522e6ab72b9`.
Los 212 tests históricos se ejecutaron tanto en C1 original como dentro de la
imagen candidata junto a los 92 nuevos; no se suman ejecuciones en imágenes
distintas para afirmar los 304 PASS. El benchmark final04 ya está reducido en
`reports/l4-benchmark-summary-final04.json` y su validación de cierre en
`reports/l4-validation-final04.json`. El análisis y la decisión B se explican en
[l4-decision.md](l4-decision.md); no se infieren de un exit 0 ni de los tests.

## Resultados y limpieza automática

Cada fase usa un nombre propio `xoc-l4-<RunId>-<fase>`. Se comprueban los controles
del contenedor antes de iniciarlo: red `none`, 512 MiB, 2 CPU, 128 pids, usuario
10001:10001, raíz de sólo lectura, sin bind mounts ni puertos publicados,
capabilities retiradas y `no-new-privileges`. Cliente y servidor HTTP numérico
127.0.0.1 viven dentro del mismo contenedor y comparten esos límites.

El supervisor mantiene vivo su proceso después de que termine el arnés. En ese
intervalo ejecuta Python dentro del mismo contenedor para exportar `/tmp/results`
como tar binario por stdout. El receptor valida el archivo completo antes de
escribir: sólo archivos regulares, sin rutas escapadas/enlaces/duplicados,
sin sobrescribir evidencia distinta y con máximo 32 MiB de contenido. No se
usan montajes del host ni `docker cp` para esta exportación.

Sólo después de exportar se crea la señal `export-complete`, se espera la salida
y se elimina el ID exacto devuelto por el `docker create` de esa invocación.
El límite de espera de la fase es 1800 s y el keepalive de exportación es finito
(180 s). Ante fallo se intenta guardar evidencia parcial y stderr de
infraestructura acotado/sanitizado antes de retirar el contenedor propio.

En `l4-runs/<RunId>/results/<fase>/` se conservan, según el punto alcanzado:

- JSON del arnés y `runner.log`.
- `container-process.json`: exit code y recolección del hijo directo.
- `evidence-export.json`: cantidad de archivos/bytes y método de exportación.
- `container-inspect.json` y `container-final.json`: controles y estado observado.
- `container-cleanup.json`: ID propio retirado y exit code de la retirada.
- `runner-failure.json`, si falló la fase o la exportación.

No hay `docker prune`, eliminación por patrones, limpieza de otros contenedores,
borrado de runs previos ni eliminación automática de las imágenes de evidencia.
`Inspect` sólo consulta la imagen del RunId y contenedores cuyo nombre coincide
con ese prefijo; no elimina nada.

## Incidencia inicial preservada

El primer intento de tests de `l4-dev01-20260916` falló al exportar con
`docker cp` (exit code 1). El stderr no se conservó y el contenedor fue retirado;
la causa exacta no quedó acreditada. No se atribuye ese fallo a una propiedad
específica de Docker o tmpfs y no se presenta como un resultado funcional PASS.
La corrección sustituyó esa exportación por `docker exec` + tar binario y añadió
pruebas de rutas, límites, no sobrescritura y sanitización de errores.

Para repetir una fase contra una imagen existente sin sobrescribir su resultado,
usar una salida y nombre nuevos. Ésta es la forma empleada para la repetición
diagnóstica dev01, no la receta de aceptación final:

```powershell
.\.venv\Scripts\python.exe -I -B packaging\l4_build.py run `
    --image xoc-mcp-l4-local:l4-dev01-20260916 `
    --name xoc-l4-dev01-tests02 --phase tests `
    --output l4-runs\l4-dev01-20260916\results\tests02
if ($LASTEXITCODE -ne 0) { throw 'La fase de diagnóstico falló; conservar la evidencia.' }
```

Se observó además un baseline manual C1 de 212 tests aprobados en stdout. Ese
registro inicial ya está complementado por la repetición detallada de C1 en
final02: 212 PASS, 0 fallos/errores/skips, 125,492 s y exit 0. La tabla anterior
identifica la evidencia nueva por test, además de smoke, provenance y benchmark
frescos. No se presenta stdout manual como sustituto de esos artefactos.

## Windows y reducción de resultados

La suite compartida se repitió con el runner original, sin instalar nada:

```powershell
# Desde XOC-MCP-LAB; no ejecutar simultáneamente al benchmark de aceptación.
.\.venv\Scripts\python.exe -B .\run_tests.py --backend ..\XOC-API-AWS
if ($LASTEXITCODE -ne 0) { throw 'Regresión Windows fallida' }
```

Resultado final04: 289 passed, 15 skipped, 0 failed; los skips corresponden a
pruebas del artefacto Linux instalado y no se cuentan como PASS Windows.
`scripts/l4_windows.py` capturó la repetición con el mismo runner, entorno limpio
y salida sanitizada en `reports/l4-windows-final04.json` (81,93 s pytest /
82,9673 s wrapper), exit 0, sin modificar el runner original ni persistir salida
cruda. El registro anterior `l4-windows-final.json` permanece como histórico.
No volver a ejecutar sobre una salida existente: rehúsa sobrescribirla.

```powershell
# Para otra repetición, sustituir esta ruta histórica por otra nueva bajo reports.
.\.venv\Scripts\python.exe -B .\scripts\l4_windows.py `
    --output .\reports\l4-windows-final04.json
if ($LASTEXITCODE -ne 0) { throw 'Regresión Windows fallida' }
```

Reducción final04 ya ejecutada, sin lanzar workloads ni modificar su evidencia.
Para otra reducción se debe elegir una salida NUEVA; el ejemplo histórico
siguiente rehúsa sobrescribir el informe que ya existe:

```powershell
.\.venv\Scripts\python.exe -B .\scripts\l4_summarize.py `
    --run l4-final04-20260916 --output .\reports\l4-benchmark-summary-final04.json
if ($LASTEXITCODE -ne 0) { throw 'No se pudo resumir la evidencia' }
```

`scripts/l4_finalize.py` reduce evidencia existente; no lanza workloads. Sus
valores por defecto aún identifican la corrida histórica final03, por lo que
el cierre final04 ya ejecutado indicó explícitamente el RunId y el sufijo.
No repetir este comando sobre sus salidas existentes:

```powershell
.\.venv\Scripts\python.exe -B .\scripts\l4_finalize.py `
    --run l4-final04-20260916 --suffix=-final04 `
    --windows l4-windows-final04.json
if ($LASTEXITCODE -ne 0) { throw 'No se pudo cerrar la evidencia final04' }
```

El cierre se completó después del benchmark final04. Se verificó que existen
`l4-validation-final04.json` y `l4-source-changes-final04.json`, sin reemplazar
los informes históricos ni rehacer resultados de final03. El cierre también
rehúsa reemplazar salidas existentes. La preservación final se documenta en
`reports/l4-preservation-final04.json`.
La aceptación temporal y el compromiso de recuperación se consignan en
[l4-decision.md](l4-decision.md); no son autorización de integración.
