# Report DOCX POC

POC local para generar informes DOCX editables de XOC sin tocar backend, Lambdas, `serverless.yml`, frontend ni produccion.

## Reglas del POC

1. Todo ocurre en `experiments/report_docx_poc/`.
2. El POC no toca backend.
3. El script puede usar mock o BD.
4. La BD se consulta en modo solo lectura.
5. La IA no ejecuta SQL.
6. La IA solo redacta secciones narrativas usando JSON compacto.
7. La portada de `templates/security-report-v1.docx` se conserva.
8. El cuerpo del informe se agrega despues de la portada.
9. Si la BD falla, el script usa mock.
10. Si Azure falla, el script usa fallback local.

## Archivos principales

- `generate_report_poc.py`
- `azure_foundry_agent_client.py`
- `database_report_loader.py`
- `test_database_connection.py`
- `test_foundry_agent_connection.py`
- `data/mock_report_data.json`
- `templates/security-report-v1.docx`
- `requirements.txt`
- `.env.example`

## Dependencias

```powershell
pip install -r requirements.txt
```

## Variables de entorno

Usa `.env.example` como referencia.

Base de datos:

- `USE_DATABASE=true/false`
- `DATABASE_URL=postgresql+psycopg2://USER:PASSWORD@HOST:PORT/DBNAME`
- `REPORT_TENANT_ID=tenant-jockey-salud`
- `REPORT_PERIOD_START=2026-06-20`
- `REPORT_PERIOD_END=2026-06-26`

Si no usas `DATABASE_URL`, puedes definir:

- `DB_HOST`
- `DB_PORT=5432`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`
- `DB_SSLMODE=require`

Azure Foundry:

- `USE_AZURE_FOUNDRY_AGENT=true/false`
- `AZURE_FOUNDRY_PROJECT_ENDPOINT=...`
- `AZURE_FOUNDRY_AGENT_NAME=Matias`
- `AZURE_FOUNDRY_AGENT_VERSION=1`
- `AZURE_FOUNDRY_MAX_OUTPUT_TOKENS=1000`
- `AZURE_FOUNDRY_TEMPERATURE=0.2`

Azure OpenAI por API key:

- `AZURE_OPENAI_ENDPOINT=https://aoai-sophia-xoc-eus2.services.ai.azure.com`
- `AZURE_OPENAI_DEPLOYMENT=gpt-5-mini`
- `AZURE_OPENAI_API_KEY=...`

## Flujo

1. `ensure_directories()`
2. `load_report_data()`
3. `calculate_derived_metrics()`
4. `generate_charts()`
5. `generate_ai_sections()`
6. `build_docx_context()`
7. `append_report_body_to_existing_template()`
8. `validate_docx()`
9. guardar en `output/reports/tenant-jockey-salud/report-demo-001/v1-generated.docx`

## Base de datos

- `database_report_loader.py` usa `SQLAlchemy`.
- Las consultas estan centralizadas en `QUERY CONFIGURATION`.
- Todas las queries son validadas para permitir solo `SELECT`.
- Si alguna query falla o una tabla no existe, la seccion devuelve fallback y el proceso continua.
- El JSON construido desde BD se guarda en `output/report_data_from_database.json`.

## Azure Foundry Agent

- Usa `DefaultAzureCredential`.
- El usuario debe ejecutar `az login`.
- El agente recibe solo un payload compacto.
- El output esperado es JSON valido con secciones narrativas.
- El resultado se guarda en `output/azure_foundry_generated_sections.json`.

## Azure OpenAI por API key

- Si defines `AZURE_OPENAI_API_KEY`, el POC usa `openai.OpenAI` contra `AZURE_OPENAI_ENDPOINT/openai/v1`.
- En esta ruta no hace falta `az login`.
- El deployment debe existir, por ejemplo `gpt-5-mini`.
- El output esperado sigue siendo JSON valido con las mismas claves narrativas.

## Comandos

Instalar dependencias:

```powershell
pip install -r requirements.txt
```

Probar BD:

```powershell
python test_database_connection.py
```

Login Azure:

```powershell
az login
```

Probar agente:

```powershell
python test_foundry_agent_connection.py
```

Probar Azure OpenAI con API key:

```powershell
$env:USE_AZURE_FOUNDRY_AGENT="true"
$env:AZURE_OPENAI_ENDPOINT="https://aoai-sophia-xoc-eus2.services.ai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT="gpt-5-mini"
$env:AZURE_OPENAI_API_KEY="<tu-api-key>"
python test_foundry_agent_connection.py
```

Generar con mock:

```powershell
$env:USE_DATABASE="false"
$env:USE_AZURE_FOUNDRY_AGENT="false"
python generate_report_poc.py
```

Generar con BD pero sin IA:

```powershell
$env:USE_DATABASE="true"
$env:USE_AZURE_FOUNDRY_AGENT="false"
python generate_report_poc.py
```

Generar con BD e IA:

```powershell
$env:USE_DATABASE="true"
$env:USE_AZURE_FOUNDRY_AGENT="true"
python generate_report_poc.py
```

Generar con mock e IA usando API key:

```powershell
$env:USE_DATABASE="false"
$env:USE_AZURE_FOUNDRY_AGENT="true"
$env:AZURE_OPENAI_ENDPOINT="https://aoai-sophia-xoc-eus2.services.ai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT="gpt-5-mini"
$env:AZURE_OPENAI_API_KEY="<tu-api-key>"
python generate_report_poc.py
```

## Resultado

`output/reports/tenant-jockey-salud/report-demo-001/v1-generated.docx`

## Consola esperada

- `Directorios OK`
- `Fuente de datos: BD o mock`
- `Conexion BD OK o fallback a mock`
- `Metricas calculadas OK`
- `Charts generados OK`
- `Azure Foundry usado OK o Fallback local usado OK`
- `Portada cargada desde plantilla existente`
- `Cuerpo agregado despues de portada`
- `DOCX generado OK`
- `Validacion OK`
- `Ruta final del documento: ...`
