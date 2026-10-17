# L4: frontera de contexto (decisión previa a implementar)

Alcance: LAB_ONLY, un proceso MCP 1.30.0 persistente, Streamable HTTP
stateless/JSON en 127.0.0.1. No OAuth ni despliegue productivo.

1. Cada POST transporta el sobre L1 canónico completo en la cabecera privada
   `X-XOC-Lab-Envelope`, codificado base64url (ASCII). El JWS interior sigue
   limitado a 4096 caracteres y el sobre decodificado a 16384 bytes. Nunca
   viaja por query, argumentos, `_meta` ni logs. No claves aportadas por cliente.
2. Ingress invoca `verify_envelope` L1 sin modificarlo: clave pública fijada,
   firma ES256, claims exactos, propósito, audiencia, identidad ficticia,
   vigencia, request_id y binding nombre/argumentos. Initialize/list también
   verifican contexto; no ejecutan negocio. Cada POST se verifica de nuevo.
3. Para tools/call se contrasta el nombre y argumentos MCP efectivos contra
   VerifiedContext.matches; sólo entonces se crea Adapter.from_verified.
   Adapter, Policy, Executor, helpers y proyección canónicos no cambian.
4. Un ContextVar contiene un objeto nuevo de solicitud; ToolContext es frozen.
   El transporte y el hilo reciben ese objeto explícitamente/copy_context.
   Ningún request_id o JSON-RPC id indexa autoridad global. La frontera fake
   se instala una sola vez; las tablas consultan el ContextVar en cada operación.
5. `finally` restaura el ContextVar tanto en ASGI como en el hilo. Un timeout
   no mata un hilo: el slot y el contexto siguen ligados a ESA ejecución hasta
   su final real; no se reutilizan para otra petición. Observación finita sin
   retener resultados, headers, assertions ni documentos.
6. Máximo cuatro trabajos síncronos admitidos; adquisición no bloqueante,
   quinto rechazo inmediato, sin cola ilimitada. La liberación sucede en el
   finally del trabajo real, no al abandonar su await. Una llamada por wrapper;
   una petición independiente puede reutilizar assertion vigente. No ledger,
   garantía exactly-once ni replay protection adicional.

SDK público: Server + StreamableHTTPSessionManager(stateless=True,
json_response=True), validación Host/Origin explícita; GET y DELETE 405.
No SSE persistente, CORS abierto, sesiones como autenticación ni factory
privada de C1. El SDK stateless crea una sesión por POST: una notificación
cancelled en otro POST no interrumpe el trabajo previo. No añadir mapa global
por JSON-RPC ID; medir timeout/disconnect y demostrar que B sigue disponible.

Body UTF-8 acumulado y acotado incluso por fragmentos: 65536 bytes + tiempo de
recepción. Respuesta JSON completa: 262144 bytes, exceso sustituido por error
pequeño. Headers y framing HTTP no se cuentan como body; límites propios de
header/envelope se aplican aparte. Cliente numérico loopback, sin redirects.

El proceso arranca con aislamiento, fixtures y core instalado verificado antes
de readiness. Servidor y cliente en mismo contenedor Linux sin red externa,
512 MiB/2 CPU/128 pids conjuntos. Código y datos fixture inmutables compartidos;
contadores, faults, resultados y presupuestos son por petición.

Fuentes: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
y https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/cancellation.
Se medirá el coste de arranque aparte del coste caliente y cleanup por petición.
No se modifica C1 ni se presupone que el candidato sea superior.
