# Report DOCX POC

POC local y aislado para validar generacion de reportes DOCX editables en el backend de XOC.

## Alcance

- Genera una plantilla DOCX base con placeholders Jinja/docxtpl.
- Usa como base la plantilla DOCX real ubicada en `experiments/TXDX-WR-0808-25036-MINORITY_REPORT_SEMANAL_19_junio.docx` cuando existe.
- Genera datos mock si no existen.
- Inserta un grafico PNG en el reporte.
- Produce un DOCX editable final.
- Valida el DOCX generado como ZIP/XML valido.
- Deja una funcion opcional para upload a S3, desactivada por defecto.

## Crear entorno

```powershell
python -m venv venv
```

## Activar entorno en Windows

```powershell
venv\Scripts\activate
```

## Instalar dependencias

```powershell
pip install -r requirements.txt
```

## Ejecutar

```powershell
python generate_report_poc.py
```

## Resultado esperado

```text
output/reports/tenant-jockey-salud/report-demo-001/v1-generated.docx
```

## Notas

- El PDF de referencia no se usa como plantilla editable.
- La plantilla preferente del POC es el DOCX corporativo dejado en `experiments/`.
- El flujo real debe usar una plantilla DOCX corporativa con placeholders.
- Este POC no toca Lambdas, serverless ni AWS real por defecto.
- Si en el futuro `experiments/` debe excluirse del deploy, eso debe resolverse en empaquetado, no en este POC.
