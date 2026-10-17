# MCP local baseline — L6-HANDOFF-20260916

Fecha: 16-sep-2026. Estado: **PASS LOCAL**.
LAB_ONLY / OFFLINE / FIXTURES / NOT_PRODUCTION.

“MCP local funcional y recuperable bajo los escenarios probados.
No implica autenticación productiva, datos reales, cloud, HA o integración
con SOPHIA productiva.”

SDK MCP 1.30.0; protocolo 2025-11-25. Linux Amazon Linux 2 x86_64, Python 3.11.16.
Windows portable: Python 3.13.1.

- RunId: l6-handoff01-20260916.
- Backend: ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9.
- Core wheel SHA256: df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41.
- L6 wheel SHA256: 6fa74cf9fc336d52c7900ab4c35f3c4fd82e6e1e2b60c57ccc63156b2c0908d6.
- Source manifest SHA256: 0e43242f3bc3d463c00b7ab7e4de708c6a6b8f81cde921b437bf72bdee2311c5.
- Artifact manifest SHA256: 777b06480c5f24c7857635ee2ae0ddfbee9b71a15b823de7bf136df9075762db.
- Gateway manifest SHA256: f2f7589c5b84cfc40ea81f4dca37d343da91513950bb2f877e01b2b99874a87c.
- Imagen: sha256:68e32feca03785dcfc3ad8dd69635d8361ba819bd7d9a3e79b3077247b29993b.
- Dos builds idénticos; cuatro wheels históricas intactas.
- Linux: 446 passed, cero failed/skipped; incluye los 304 históricos juntos.
- Windows: 407 passed, 39 skips POSIX/históricos, cero fallos.
- C01–C24: 24/24 PASS. Gates: 1925 PASS / 0 FAIL / 0 INCONCLUSO.
- 17 restauraciones TCP correctas; máximo 8,952720558 s (límite 21 s).
- Máximo recovery de la matriz: 5,366286994 s (límite 12 s).
- Razones medianas L6/L4: 1,005917 / 1,075222 / 1,065324.
- Razones p95 L6/L4: 1,026890 / 0,982859 / 1,046921. Todas ≤1,25.

Los tres cold_2 tienen mediciones reales en READY, después de primera consulta
y antes de retirada. Se preserva el sampler periódico. PID/generación/propietario
verificados, RSS/HWM separados de cgroup; CPU en ticks, threads/tasks y fds de /proc.
Sólo observabilidad y gates de evidencia cambiaron. No sleeps adicionales, cambios
de carga, timeout, lease, gate, supervisor, probe, slots ni semántica de recuperación.

Límites: 512 MiB, 2 CPU, 128 pids, cuatro slots. Máximo dos intentos automáticos.
Un retiro puede perder consultas sanas de otros tenants ficticios. No replay;
ni garantía de entrega sin pérdidas ni de ejecución exactamente una vez.
Fallo del supervisor cierra por lease y requiere intervención explícita.

Evidencia resumida: docs/evidence/closure-summary.json. Evidencia fuente completa:
reports/l6-handoff-*.json dentro del archive externo. El expediente anterior final03 permanece
inconcluso en sus propios resultados; esta campaña nueva no lo sobrescribe.
No autoriza producción, VICTOR, Voice ni conexión automática con SOPHIA.
