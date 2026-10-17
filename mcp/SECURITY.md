# Seguridad
LAB_ONLY / OFFLINE / FIXTURES / NOT_PRODUCTION

No usar este servidor con datos, tokens, credenciales o usuarios reales.
La fixture ES256 de xoc_mcp_lab_harness/signing.py deriva una clave pública de prueba
de un scalar conocido. TEST FIXTURE ONLY / PUBLIC / NOT A SECRET /
DO NOT USE IN PRODUCTION. Su confianza es exclusivamente de laboratorio.
Las referencias a Bearer, access_token, passwords o secretos en tests son casos
de rechazo/redacción, no credenciales para servicios reales.

No publicar puertos, conectar cloud ni trasladar esta confianza al runtime productivo.
Mantener los bloqueos de egress, límites, contexto por petición y policy canónica.
Si se detecta un secreto: no copiar ni imprimir su valor; registrar sólo archivo,
tipo y acción requerida. Avisar en privado al responsable del proyecto; aún no se
ha definido canal corporativo. El escaneo local es heurístico y no garantiza
ausencia absoluta de secretos. Ver evidencia de revisión.

Los artefactos propios fijados contienen sólo el subset generado por el manifiesto;
no una fuente editable duplicada. No modificar Policy/Executor desde este repo.
