# Lectura de evidencia

El estado vigente está en ../../MCP_LOCAL_BASELINE.md y closure-summary.json.
handoff-validation.json describe exclusivamente la validación de esta exportación.

Los informes resultado/l1/l2/l3/l4/l5/l6 conservan su fecha y conclusión histórica.
En particular l6-result.md describe final03 INCONCLUSO; no es la última campaña.
Sus enlaces a JSON y directorios de resultados apuntan al expediente del laboratorio,
conservado aparte en XOC-MCP-EVIDENCE-ARCHIVE-20260916.zip. No se incluyeron resultados
masivos dentro de este repositorio para resolver esos enlaces históricos.

Las rutas del autor se sustituyeron por <WORKSPACE> sólo en documentación; fuentes
canónicas, locks y scripts hash-pinned permanecen byte a byte intactos.

La reconstrucción transportable usa Dockerfile.handoff desde la base pública fijada,
no intenta falsificar el ID de imagen L4 de otra laptop. Scripts históricos conservan
sus comprobaciones originales. El runner transportable permite repetir tests, smoke,
24 escenarios, provenance y poblaciones. Para reproducir el expediente agregado
histórico con l6_finalize.py se requiere restaurar sus entradas de custodia y sus
metadatos originales; no debe sustituirse un ID o una evidencia de proceso por otro.
La suite/recovery portable no se presenta como otra campaña agregada de 1925 gates.
