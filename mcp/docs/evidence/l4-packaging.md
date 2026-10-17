# L4 LOCAL: empaquetado aditivo y procedencia

Fecha: 16-sep-2026. L4 añade la distribución `xoc-mcp-lab-l4`, sin sustituir
los paquetes C1 ni cambiar dependencias. No es un artefacto productivo ni una
publicación en un registro. Los comandos reproducibles están en
[l4-commands.md](l4-commands.md); resultados y decisión temporal pertenecen al
informe final, no a esta descripción del empaquetado.

## Paquete final comprobado

El RunId final es `l4-final04-20260916`. Se compararon por lectura los siete
archivos de `build-a` y `build-b`: cuatro wheels, manifest de artefactos,
manifest de fuentes y manifest del gateway. Todos sus hashes coinciden. Se
conservan los dos staging independientes `sources-a` y `sources-b`.

| Artefacto final04 | Evidencia |
| --- | --- |
| Wheel L4 | `xoc_mcp_lab_l4-0.4.0+lab.27a62d14058a-py3-none-any.whl`, 150.887 bytes |
| SHA256 del wheel L4 | `764352dc77a7c467f6c8c3162a4f93e4fcca0e21b7933db0e2a4557669dcfb5d` |
| SHA256 del manifest de fuentes | `27a62d14058ac4beac13e96a4cd2702b71a833d5e831fd1adf78da5250788d4f` |
| SHA256 del manifest de artefactos | `30eea94fbaea60c66359aeb89ddcd01e5b77cdeb04b232cf3a2c5ffe479935b7` |
| Imagen ejecutada | `sha256:129efefeecc7549df84604ed2e532fa524aaab716f6cf145ca9e5522e6ab72b9` |

El manifest está en
`l4-runs/l4-final04-20260916/build-a/artifact-manifest.json`; el ID de imagen y
los controles se observan en `results/tests/container-inspect.json` del mismo
RunId. Los tres wheels históricos y el manifest del gateway mantienen los
hashes de la tabla siguiente, también en el build final.

Validación instalada final04 comprobada: 304/304 tests ejecutados **juntos en la
misma imagen candidata** (212 C1 intactos + 92 L4), 0 fallos/skips, 163,31 s;
smoke PASS (25 filas),
41 casos de equivalencia canónica, 37 comparaciones HTTP idénticas y cuatro
identidades inválidas rechazadas por la frontera L1. Los 20 orígenes canónicos
son instalados; tests/smoke/provenance terminaron con exit 0, sin hijos, y sus
contenedores propios fueron retirados con exit 0. El benchmark final04 también
terminó con exit 0 y `final_children` vacío; su evidencia se exportó antes de
retirar el contenedor propio con exit 0. Se comprobaron su
`results/benchmark/benchmark.json`, `container-process.json` y
`container-cleanup.json`. La reducción y el cierre ya existen en
`reports/l4-benchmark-summary-final04.json` y
`reports/l4-validation-final04.json`; las métricas y decisión B se explican
en [l4-decision.md](l4-decision.md). Estos gates funcionales y exit codes
no sustituyen la evaluación temporal. Los 212
tests también tienen su repetición independiente en C1 original bajo final02.

Final04 incluye la corrección L4 de redirects: se rechazan todas las respuestas
3xx antes del manejo del SDK, sin repetir la assertion ni siquiera hacia la
misma URL. `follow_redirects=False` por sí solo no bastaba para ese manejo del
SDK. La verificación añade casos 302/307/308; no modifica ni reinstala el SDK.
La suite conjunta se activa desde el arnés L4 nuevo, sin editar los tests C1.

## Entradas fijadas

Origen de los tres wheels históricos:
`l3-runs/l3-c1-final-build/build-a/`. La exportación L4 sólo lee ese directorio y
las nuevas fuentes/tests del laboratorio; no lee un checkout backend para
reconstruir, extraer o transformar su código.

| Entrada | Valor fijado |
| --- | --- |
| Imagen C1 | `sha256:421858fa81bd95a041aa12e85e6528d48eacae293674789156a6b19aebc3fd40` |
| Base oficial AL2 | `public.ecr.aws/lambda/python@sha256:cc940054e77d4a160c611a20c09cb8ab9325c09d72555a164329eb3e46957d80` |
| ID local de esa base | `sha256:3dd4ffcf67d1302b4c9808bdc65fbc8dd952debfbaf373392bbd8fb7c9856420` |
| Intérprete/arquitectura | CPython **3.11.16**, Amazon Linux **2**, **x86_64** |
| SDK/protocolo | `mcp==1.30.0` / `2025-11-25` |
| Core `0.0.0+lab.ad4ac1a764de` | SHA256 `df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41` |
| Adapter C1 `0.2.0+lab.cdce81cbc014` | SHA256 `0f82b903e2b0bbbc5107a6371a2da95c83c0561c76f5b918a1cb1df1798da2ac` |
| Harness C1 `0.2.0+lab.cdce81cbc014` | SHA256 `f41abe59883f1bc9311ed88f8582b1f31639bc2be65e1163412925c17bd2d736` |
| Manifest de artefactos C1 | SHA256 `eead716dd9170d7346380e7af647bceaf9211cacf864dedd2e58ed4bacfbd3eb` |
| Manifest del gateway C1 | SHA256 `f2f7589c5b84cfc40ea81f4dca37d343da91513950bb2f877e01b2b99874a87c` |
| Lock runtime Linux | SHA256 `0b439be1007d484201613bae015e0ded27c8f38fa3b5338f8a7f41cc674e858b` |
| Lock tests Linux | SHA256 `4336d71caffdd9aeeb4143a9b3b2bfc30b61409a89c205f5a9c2b50bd3cbaff5` |

Se reutilizan los 48 wheels de
`l2-runs/20260915/preparation/wheelhouse/`, seleccionados y comprobados por los
hashes de los dos locks. HTTPX, Starlette y Uvicorn ya forman parte de esas
dependencias. No se resolvió otro conjunto, no se descargaron wheels y no se
añadió un SDK. El protocolo y las versiones se verifican también en runtime.

## Dos builds independientes

`scripts/l4.ps1` crea `sources-a` y `sources-b` como exportaciones separadas,
y `build-a` y `build-b` como destinos nuevos. Cada exportación contiene fuentes
`xoc_mcp_lab_l4/*.py`, tests `tests/test_l4*.py` bajo el paquete instalado,
manifest de fuentes con SHA256, tres wheels C1 originales y locks exactos.

El host usa stdlib para preparar el snapshot; la construcción del wheel final
se ejecuta con AL2/Python 3.11.16/x86_64 en Docker. Los inputs y outputs viajan
como tar binario por stdin/stdout: no hay montajes del checkout ni del host.
El constructor corre sin red, no root, con raíz de sólo lectura y los mismos
límites 512 MiB/2 CPU/128 pids. Un tmpfs acotado contiene su staging temporal.

`packaging/l4_build.py` reutiliza, sin modificarlo, el `wheel()` de
`packaging/build_artifacts.py`: ZIP_STORED, orden fijo, fecha ZIP 1980-01-01,
modos fijos, metadata/RECORD deterministas, sin pyc, rutas absolutas del host,
editable install ni hooks de build. La versión del nuevo wheel es
`0.4.0+lab.<12 caracteres del hash del manifest de fuentes>`.

Los tres wheels C1 se copian byte a byte y se vuelven a verificar. Sólo el cuarto
wheel es L4. `compare` exige que los nombres y hashes de todos los archivos
producidos coincidan entre `build-a` y `build-b`; fuentes distintas o cualquier
alteración de una entrada fijada hacen fallar la operación. El SHA del cuarto
wheel y el ID final de imagen se toman de final04, no se reciclan de dev01,
final01, final02 o final03. Cualquier modificación de fuentes o tests exige exportar y
construir de nuevo ambos lados, sin alterar los runs anteriores.

## Imagen e instalación

La ruta normal parte de C1, que ya está disponible localmente. El script
comprueba el ID y crea un alias local nuevo exclusivo de L4
`xoc-mcp-l4-base:<RunId>`; no modifica el contenido ni las etiquetas históricas
de C1. El alias evita confundir el ID de configuración de imagen con un digest
de manifest de un registro. La construcción usa `--pull=false --network none`.

`Dockerfile.l4` instala únicamente el wheel L4 con `--no-index --no-deps
--no-cache-dir --no-compile`. Verifica su SHA y rechaza archivos fuera de su
namespace. Comprueba todos los archivos originales C1 antes y después de la
instalación, ejecuta `pip check` y la comprobación de integridad L4. La fase de
instalación de la imagen usa root; el build de wheels y todo el runtime de
validación usan explícitamente 10001:10001. No se afirma que el instalador
dentro de la construcción de imagen sea no privilegiado.

Si faltara C1, el script permite únicamente la base AL2 fijada **ya local** y
reinstalar los tres wheels C1 con sus 48 dependencias offline. Eso reconstruye
los bytes instalados, pero no garantiza el ID de imagen histórica C1; las
fases de comparación `C1*` siguen exigiendo la imagen C1 original. Si ninguna
base local fijada existe, se detiene; no hace pull ni inicia el daemon.

## Comprobación de fuentes instaladas

`xoc_mcp_lab_l4.integrity.require_l4_installed()` falla si se intenta arrancar
desde fuentes sueltas. Exige que L4, core, adapter y harness C1 pertenezcan al
mismo site-packages bajo `sys.prefix`, con las versiones exactas. El archivo
L4 `_artifact.json` relaciona el hash del manifest, cada fuente/test L4 y cada
archivo original de los tres wheels C1. Se verifican bytes, ubicación y ausencia
de enlaces antes de readiness. `require_installed()` histórico mantiene además
las comprobaciones canónicas y del lock de dependencias.

La excepción explícita es `RECORD` del dist-info: pip lo reescribe al instalar
y añade metadatos propios como INSTALLER. El código y los metadatos originales
restantes se verifican contra los wheels; no se omite ningún archivo de código
C1 por esa excepción. Esta comprobación es integridad/procedencia local, no una
firma de release ni un sandbox contra quien ya pueda reescribir la imagen.

Tests, smoke, equivalencia y benchmarks L4 arrancan desde `/tmp` con Python
`-I -B`, sin sys.path al checkout ni mounts del workspace. Los tests L4 viajan
en su propio wheel. La prueba de doble wheel también puede reconstruir el
wheel desde sus fuentes **instaladas** y contrastarlo con el original de
`/offline-l4`, sin tocar site-packages. Las suites C1 permanecen en su harness
original: se ejecutan separadamente en la imagen original y, en final04,
también junto a los tests L4 dentro de la imagen candidata instalada.

## Persistencia de evidencia y límites

Los informes se escriben en tmpfs mientras el contenedor vive. El runner exporta
mediante `docker exec` y tar binario antes de permitir la salida, registra
controles y exit codes y retira sólo su ID exacto. No se presenta la retirada
del contenedor como prueba de cleanup de la aplicación: la recolección del
servidor y los trabajos se comprueba dentro del arnés antes de salir.

La primera exportación dev01 mediante `docker cp` falló y no conservó stderr;
su causa exacta permanece desconocida. La sustitución por tar está documentada
en [l4-commands.md](l4-commands.md), junto con los límites y la retención de
evidencia parcial. No se oculta esa iteración ni se mezclan sus resultados con
el paquete final. El baseline manual C1 de 212 PASS observado en stdout está
complementado por evidencia detallada fresca en
`l4-runs/l4-final02-20260916/results/`: C1Tests 212/212 sin fallos/errores/skips,
C1Smoke seis casos PASS, C1Provenance 41 casos y 20 módulos, C1Benchmark 80/80
secuenciales correctas más sus escenarios de fallos/grandes. Las cuatro fases
registran exit 0 y retirada de sus IDs propios; el benchmark registra cero hijos
pendientes antes de salir. Los comandos y rutas exactas se recogen en
[l4-commands.md](l4-commands.md).

Nada de este empaquetado prueba latencia cloud, hard real-time, capacidad de
producción ni compatibilidad HTTP Windows. No modifica el venv Windows
histórico, el SDK instalado existente ni los artefactos, scripts, locks o tests
L1/L2/L3. Los nuevos informes son aditivos.
