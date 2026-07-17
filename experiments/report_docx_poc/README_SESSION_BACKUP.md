# Session Backup - Report DOCX POC

## Estado actual

Este POC ya fue adaptado para trabajar solo dentro de `experiments/report_docx_poc/`.

Ya implementado:

- Generacion DOCX local con `python-docx`.
- Uso de `templates/security-report-v1.docx` como plantilla base.
- La portada de la plantilla se conserva.
- El cuerpo del informe se agrega despues de la portada.
- Flujo con mock o base de datos.
- Loader de BD en modo solo lectura.
- Cliente separado para Azure Foundry Agent.
- Fallback local si falla BD.
- Fallback local si falla Azure.
- Validacion final del DOCX.

## Archivos implementados

- `generate_report_poc.py`
- `azure_foundry_agent_client.py`
- `database_report_loader.py`
- `test_database_connection.py`
- `test_foundry_agent_connection.py`
- `.env.example`
- `README.md`
- `data/mock_report_data.json`

## Flujo actual del generador

1. `ensure_directories()`
2. `load_report_data()`
3. `calculate_derived_metrics()`
4. `generate_charts()`
5. `generate_ai_sections()`
6. `build_docx_context()`
7. `append_report_body_to_existing_template()`
8. `validate_docx()`

## Resultado actual validado

Se pudo generar correctamente en modo mock:

`output/reports/tenant-jockey-salud/report-demo-001/v1-generated.docx`

Validaciones OK:

- DOCX existe
- peso mayor a 20KB
- `word/document.xml` existe
- zipfile OK
- XML OK

## Sobre Azure Foundry

El codigo para Azure ya esta implementado.

El problema que se detecto no era de codigo, sino de autenticacion local.

Antes faltaban estas condiciones:

- `az` no existia en PATH
- no habia sesion `az login`
- no habia credenciales validas para `DefaultAzureCredential`

Durante la sesion se instalo Azure CLI y tambien se instalaron las dependencias Python del POC, incluyendo:

- `azure-ai-projects`
- `azure-identity`
- `azure-core`
- `python-dotenv`
- `sqlalchemy`
- `psycopg2-binary`

## Por que fue necesario instalar Azure CLI

Porque el POC usa `DefaultAzureCredential`.

En desarrollo local, una de las formas mas simples para que `DefaultAzureCredential` obtenga token es reutilizar una sesion iniciada con:

`az login`

Sin Azure CLI, el SDK no podia tomar esa identidad local.

## Lo que falta hacer despues del reinicio

### 1. Abrir una terminal nueva

Esto es necesario para que `az` quede disponible en `PATH` despues de instalar Azure CLI.

### 2. Verificar que Azure CLI responde

```powershell
az --version
```

### 3. Iniciar sesion en Azure

```powershell
az login
```

### 4. Verificar la cuenta autenticada

```powershell
az account show
```

### 5. Cargar variables para Foundry

```powershell
$env:USE_AZURE_FOUNDRY_AGENT="true"
$env:AZURE_FOUNDRY_PROJECT_ENDPOINT="https://aoai-sophia-xoc-eus2.services.ai.azure.com/api/projects/sophia-project"
$env:AZURE_FOUNDRY_AGENT_NAME="Matias"
$env:AZURE_FOUNDRY_AGENT_VERSION="1"
```

Opcional si quieres usar mock sin Azure:

```powershell
$env:USE_AZURE_FOUNDRY_AGENT="false"
```

### 6. Probar conexion al agente

```powershell
python experiments/report_docx_poc/test_foundry_agent_connection.py
```

Salida esperada si funciona:

```json
{"status":"ok","message":"Azure Foundry Agent conectado"}
```

### 7. Generar reporte con Azure

```powershell
python experiments/report_docx_poc/generate_report_poc.py
```

## Variables de entorno importantes

### Base de datos

```powershell
$env:USE_DATABASE="false"
```

Si se quiere usar BD:

```powershell
$env:USE_DATABASE="true"
$env:DATABASE_URL="postgresql+psycopg2://USER:PASSWORD@HOST:PORT/DBNAME"
$env:REPORT_TENANT_ID="tenant-jockey-salud"
$env:REPORT_PERIOD_START="2026-06-20"
$env:REPORT_PERIOD_END="2026-06-26"
```

Alternativa sin `DATABASE_URL`:

```powershell
$env:DB_HOST=""
$env:DB_PORT="5432"
$env:DB_NAME=""
$env:DB_USER=""
$env:DB_PASSWORD=""
$env:DB_SSLMODE="require"
```

### Azure Foundry

```powershell
$env:USE_AZURE_FOUNDRY_AGENT="true"
$env:AZURE_FOUNDRY_PROJECT_ENDPOINT="https://aoai-sophia-xoc-eus2.services.ai.azure.com/api/projects/sophia-project"
$env:AZURE_FOUNDRY_AGENT_NAME="Matias"
$env:AZURE_FOUNDRY_AGENT_VERSION="1"
$env:AZURE_FOUNDRY_MAX_OUTPUT_TOKENS="1000"
$env:AZURE_FOUNDRY_TEMPERATURE="0.2"
```

## Comandos utiles para retomar

Instalar dependencias si hace falta:

```powershell
pip install -r experiments/report_docx_poc/requirements.txt
```

Probar BD:

```powershell
python experiments/report_docx_poc/test_database_connection.py
```

Probar agente:

```powershell
python experiments/report_docx_poc/test_foundry_agent_connection.py
```

Generar con mock:

```powershell
$env:USE_DATABASE="false"
$env:USE_AZURE_FOUNDRY_AGENT="false"
python experiments/report_docx_poc/generate_report_poc.py
```

Generar con BD pero sin IA:

```powershell
$env:USE_DATABASE="true"
$env:USE_AZURE_FOUNDRY_AGENT="false"
python experiments/report_docx_poc/generate_report_poc.py
```

Generar con BD e IA:

```powershell
$env:USE_DATABASE="true"
$env:USE_AZURE_FOUNDRY_AGENT="true"
python experiments/report_docx_poc/generate_report_poc.py
```

## Restricciones que se mantuvieron

- No tocar backend.
- No tocar Lambdas.
- No tocar `serverless.yml`.
- No tocar frontend.
- No tocar produccion.
- No escribir en la BD.
- No permitir queries que no sean `SELECT`.
- No dar acceso directo de BD a la IA.
- No modificar la plantilla original.
- No usar S3.
- No usar Blob Storage.
- No generar PDF.

## Punto exacto para continuar despues

Despues del reinicio, el siguiente paso recomendado es:

1. abrir PowerShell nuevo
2. correr `az --version`
3. correr `az login`
4. correr `az account show`
5. correr `python experiments/report_docx_poc/test_foundry_agent_connection.py`
6. correr `python experiments/report_docx_poc/generate_report_poc.py`

Si `test_foundry_agent_connection.py` falla, el problema ya no deberia ser de codigo del POC sino de autenticacion/permisos del usuario sobre el proyecto Foundry.
