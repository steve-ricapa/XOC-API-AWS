# Colaboración propuesta
Flujo recomendado: feature branch → tests → review → PR → merge.
Main protegido, PR obligatorio y revisión son recomendaciones para el administrador,
no políticas corporativas ya configuradas. No se crearon Actions ni pipelines.

Preservar core, locks, fixtures públicas y manifest de baseline. Cambio funcional
requiere revisión explícita y campaña nueva; nunca sobrescribir evidencia histórica.
No tocar VICTOR, Voice ni producción como efecto incidental.
Nunca versionar venv, caches, credenciales o resultados masivos.

Comprobar tests Windows y Linux instalado, smoke y provenance. Si cambia recuperación
ejecutar C01–C24 y los tres pares, sin relajar gates ni elegir la mejor muestra.
Usar scripts/handoff.py para la copia transferida; scripts históricos conservan sus
pins y rutas relativas de campaña. Documentar cualquier diferencia.
