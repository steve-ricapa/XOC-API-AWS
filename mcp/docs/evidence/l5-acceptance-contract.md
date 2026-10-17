# MCP-2 · L5 LOCAL — Contrato de aceptación de recuperación

Fecha: 16-sep-2026. **DISEÑO PROPUESTO; no tests ni recuperación ejecutados en L5.**

Acompaña a [l5-recovery-decision.md](l5-recovery-decision.md). Estrategia B: supervisor independiente y retirada/reinicio de la instancia completa, conservando cuatro slots. Este contrato define el único bloque posterior L6 LOCAL para aprobación; no autoriza implementarlo, cloud, Chat, VICTOR ni Voice.

## 1. Categorías y punto de partida

- **OBSERVADO EN L4:** resultados históricos final04, no nuevas mediciones.
- **COMPROBADO POR LECTURA DE CÓDIGO:** semántica de fuentes/dependencias fijadas.
- **DISEÑO PROPUESTO:** reglas de este contrato todavía inexistentes.
- **SUPUESTO:** precondición del objetivo local.
- **PENDIENTE DE VALIDACIÓN:** dato que sólo el experimento posterior puede acreditar.

MEDIDO = histórico L4. ESTIMADO = hipótesis explícita sin medición. OBJETIVO POR APROBAR = umbral candidato de este documento. No se convierten entre sí.

**OBSERVADO EN L4:** 80/80 calientes; mediana 26,339 ms / p95 30,771 ms / máximo 92,877 ms. B sana durante A bloqueada: 24,481 ms. Quinto con cuatro bloqueadas: busy en 18,629 ms. Terminar/recolectar bloqueado: 0,666309 s. Arranque mediano de otra población: 2,193653 s. **RTO global: no medido.**

**COMPROBADO POR LECTURA DE CÓDIGO:** timeout de caller no mata thread; list comparte el pool; initialize y ready file no prueban negocio. EventFile síncrono no es un heartbeat confiable. Los helpers canónicos no implementan deadline total de proveedor. Detalle y referencias en §§2–3 de la decisión.

## 2. Contrato temporal

### 2.1 Definiciones y origen de cada reloj

Todos los spans se medirán directamente con reloj monotónico; no sumar medianas/p95. Los claims firmados mantienen sus comprobaciones de tiempo de pared existentes. Comparación entre procesos sólo después de validar el dominio monotónico común en Linux.

| Métrica | Inicio → fin | Qué NO acredita |
| --- | --- | --- |
| T_request | Inicio de QueryClient, incluida preparación → resultado/error y cleanup de ese caller | No demuestra fin del worker |
| T_work_end | Reserva/admisión real → callable y limpieza del contexto completados; registrar aparte Future done | Un request_finished no sirve como fin real |
| T_slot_release | Admisión → capacidad efectivamente reutilizable sin trabajo síncrono/diagnóstico bloqueante pendiente | No usar deadline del caller o un contador puesto artificialmente a cero |
| T_detection | t_fault_observable definido por caso → decisión del supervisor | No es sólo latencia de una sonda HTTP |
| T_recovery | Decisión que cierra admisión/inicia retirada → reemplazo verificado, sonda privada correcta, slot libre y gate READY | No acredita por sí sola el transporte externo ni éxito de una consulta perdida |
| T_restore_service | Mismo t_fault_observable → NUEVA consulta explícita por loopback real completada correctamente | No sustituir por proceso muerto, ready file o sonda sólo ASGI |

Si el proceso es terminado, registrar T_work_end como **termination/reap de generación**, no como retorno del callable ni finally ejecutado. El slot viejo se retira; T_slot_release de esa unidad no se reporta como reutilización normal. La capacidad nueva se prueba por separado.

t_fault_observable por caso:

- Bloqueo inyectado de trabajo: provider_entered confirmado inmediatamente antes de la espera no cooperativa; registrar también admitted_at y first_client_timeout.
- Loop: instante de inyección confirmada por arnés y último heartbeat válido.
- Crash: instante/observación de salida del hijo.
- Fallo de control: primer mensaje perdido/lease vencida conocido en la prueba.
- Incidente real sin instante físico conocido: usar primer indicio observable y declarar incertidumbre; no inventar cuándo empezó.

### 2.2 Límites existentes que NO cambian

**COMPROBADO POR LECTURA DE CÓDIGO:** caller objetivo por defecto 3,5 s y techo experimental 5 s, limitado por vigencia. Espera del pool hasta 3 s; ASGI hasta 5 s con reserva de error hasta 50 ms dentro del mismo total. Temporizadores cooperativos, no hard real-time.

Se conservarán esos valores. Todo exceso se registra, no se borra como outlier ni se soluciona ampliando el timeout. Un fallo de scheduling/host puede impedir una cota absoluta; la aceptación temporal bajo host sano sigue exigiendo techo 5 s. Casos de fault injection con caller menor conservan ese presupuesto y registran el overhead real.

### 2.3 Parámetros candidatos de detección y contención

Todo este bloque es **DISEÑO PROPUESTO / OBJETIVO POR APROBAR**.

| Parámetro | Candidato | Si no se cumple |
| --- | --- | --- |
| Heartbeat y muestreo supervisor | 250 ms | Registrar retraso; no fingir heartbeat del loop desde otro thread |
| Heartbeat ausente | 2 s; detección candidata ≤2,25 s desde último latido válido | Retirar generación por pérdida de liveness/control |
| Edad de trabajo vivo | 5 s desde admisión; decisión candidata ≤5,25 s desde admisión | Recuperación global aun si otros tres slots están libres |
| Lease de admisión | Expiración absoluta emitida a +1 s; renovación 250 ms, generación/secuencia verificadas | Al reanudar guard con lease vencida, fail-closed; renovación atrasada no extiende plazo |
| ACK de cierre | ≤250 ms | No afirmar cierre confirmado; omitir drenaje confiado y terminar hijo |
| Rechazo con loop sano/gate cerrado o pool lleno | Objetivo ≤250 ms y siempre dentro de deadline restante | Fallo del objetivo, sin encolar ni ejecutar después |
| Recuperaciones | Máximo dos intentos por ejecución supervisada | Tercer incidente → UNAVAILABLE; sin reemplazo adicional ni rearme automático |

Umbral de edad identifica violación de presupuesto operativo de ocupación, no demuestra matemáticamente un deadlock. Progreso por páginas/heartbeat nunca renueva esa edad. Cuatro ocupados con edad normal no son fallo por sí solos.

Sin ACK, decidir RECOVERING no garantiza cierre instantáneo: si el loop vuelve antes de expirar la última lease puede admitir solicitudes válidas en esa ventana residual (máximo 1 s desde emisión, no desde recepción). Se acepta explícitamente sólo esa admisión contabilizada anterior al corte; después de ACK, expiración comprobada por el guard o muerte, cero nuevas ejecuciones. Renovaciones viejas/repetidas/de otra generación se descartan y ninguna renovación reabre un gate ya cerrado sin transición explícita. C20 debe demostrar ambos lados de la carrera, no sólo loop permanentemente detenido.

Sin loop responsivo o sin proceso no hay garantía de error JSON rápido: rechazo de conexión, error de transporte o timeout seguro del cliente, sin retry. Si el host tampoco permite progresar al cliente, se declara esa limitación y no un deadline cumplido.

### 2.4 Recuperación y restauración: reservas distintas

**SUPUESTO:** supervisor y host progresan; hijo es terminable/recolectable; recursos dentro de 512 MiB/2 CPU/128 pids; artefactos íntegros locales; fixture disponible; sin I/O externo.

**OBJETIVO POR APROBAR: T_recovery ≤12 s.** Las reservas siguientes son diseño, no duraciones medidas:

| Fase | Reserva máxima candidata |
| --- | ---: |
| Control/cierre/ACK | 0,25 s |
| Drenaje si existe ACK | 1,00 s |
| Solicitud de terminación cooperativa | 0,65 s |
| Kill si necesario + wait/reap | 0,35 s |
| Nuevo proceso: integridad, fake wiring, listener, control/heartbeat | 4,00 s |
| Sonda privada MCP/ASGI nueva autorizada, sin retry | 3,50 s |
| Verificar fin del trabajo/slot, cerrar sonda y abrir gate | 0,25 s |
| Reserva global de coordinación/scheduling entre fases | 2,00 s |
| Total de presupuesto diseñado | **12,00 s** |

Los plazos de fase se miden desde su entrada; la reserva global no permite ocultar que se excedió reap/startup/sonda. ACK ausente permite pasar a kill, no inventar un drenaje realizado. Fallo de reap, startup, integridad, sonda o presupuesto global → UNAVAILABLE y retirada acotada del reemplazo propio si existe, sin intentar otro automáticamente. La limpieza adicional de un candidato fallido se registra aparte y no se llama recuperación exitosa.

La reserva 0,35 s para kill/reap es **candidata no acreditada**. Si es inviable, NO-GO para este contrato; no ampliar silenciosamente. Los 0,666309 s históricos incluían otra secuencia con espera cooperativa y no validan esa fase.

**OBJETIVO POR APROBAR: T_restore_service ≤21 s** para trabajo no cooperativo del ensayo:

- Detección hasta 5,25 s desde admisión (desde provider_entered no puede ser mayor bajo el mismo reloj).
- Recuperación hasta 12 s desde decisión.
- Arnés inicia consulta nueva de red dentro de 0,25 s desde READY.
- Consulta nueva con objetivo total 3,5 s.
- Suma de presupuestos diseñados: 21 s. **No suma de mediciones históricas.** Medir también el span total directo.

Para loop/crash se usa su origen y detección propia, no se les asigna artificialmente una admisión. Sin nueva consulta programada no hay T_restore_service medido: el tiempo que una persona tarde en volver a preguntar no es latencia del servidor.

Sonda privada es una solicitud nueva de prueba explícita, no replay. La consulta posterior de red es otra nueva y obligatoria. Si ésta falla, la aceptación de restauración falla aunque hubiera READY interno: el arnés registra FAIL y solicita al supervisor cierre/retirada y transición real a UNAVAILABLE, comprobando ACK/lease/terminación según corresponda. Si no puede acreditarlo, registrar estado incierto/residual, no UNAVAILABLE ficticio. No repetir ni crear otro reemplazo hasta encontrar un éxito.

## 3. Seguridad, contexto y una sola ejecución

**DISEÑO PROPUESTO conservando controles COMPROBADOS POR LECTURA DE CÓDIGO:**

1. Cada POST verifica L1 y binding de herramienta, argumentos, actor/tenant, request_id, vigencia y deadline. ToolContext frozen procede de VerifiedContext, nunca del supervisor o generación.
2. Mantener exactamente cuatro tools READ_ONLY y Policy/Executor canónicos. ADMIN/USER según registry existente; ADMIN_XOC sólo con delegación válida; SUPERADMIN denegado. No ampliar permisos, fallback legacy, SQL libre o shell.
3. Scope nuevo por solicitud; proveedores fixture resuelven únicamente scope actual. Ningún cache de resultados o autorización entre usuarios.
4. session ID, JSON-RPC ID, request_id, _meta, headers declarativos o nombre de generación no conceden ownership. Identificador técnico de trabajo lo genera el servidor, único dentro de generación.
5. El registro de supervisión guarda sólo metadatos limitados, no scope/future/request/resultado/claims. Los objetos de ejecución pueden conservar contexto original mientras viven; no resetearlos bajo trabajo ajeno.
6. Assertion expirada al entrar o al admitir/empezar trabajo no permite ejecutar. La pérdida/expiración de contexto no genera un tenant o actor por defecto. Un handler ya iniciado no se revoca mágicamente por expirar: sigue ocupando slot hasta fin/corte.
7. Si vence deadline antes de entregar una respuesta, no utilizar ese resultado para completar otra solicitud ni revivir el caller. Se desecha la entrega tardía; fin y cleanup siguen contabilizados.
8. Evento tardío de generación retirada nunca cambia readiness/contadores del hijo nuevo ni completa su respuesta. Mantener como máximo metadatos acotados de generaciones retiradas, no sus resultados.
9. Si la misma ID se presenta por A y B, cada nueva petición verifica su propio contexto. Cancelación de B no encuentra ni cancela A por un mapa global.
10. No retry de tool/caller/supervisor tras timeout, pérdida de respuesta o reinicio. Un wrapper sigue siendo de un uso. Retrys internos de lectura de una biblioteca no se confunden con reejecución de tool; no se añade ninguno nuevo.
11. No prometer exactly-once global: L4 no tiene ledger antireplay; una solicitud explícita nueva válida, incluso con ID repetida, es otro intento. No deduplicar por ID como si fuera autorización.
12. La sonda privada atraviesa verificación, SDK, gateway, Policy/Executor y provider fixture normales. La excepción de gate requiere control en memoria no serializable activado por IPC, nunca un modo seleccionable desde MCP público. Sólo una sonda por startup.
13. Datos de control/logs: sin JWT/assertions/argumentos completos/resultados/documentos/secretos. Mantener audit seguro canónico separado; evidencia de identidad usa fixtures, audit IDs y coincidencias, no credenciales reales.
14. Fixture ES256 conocida sigue siendo **LAB_ONLY**. Nada resuelve OAuth, D.2, autenticación productiva ni adversario con control del host.

Si una respuesta válida ya llegó al caller antes del corte, no se retira retroactivamente ese éxito. Si ejecución ocurrió pero entrega no quedó confirmada, registrar resultado incierto, no éxito ni ejecución inexistente.

## 4. Admisión, capacidad y fin real

**DISEÑO PROPUESTO:**

- Un supervisor, como máximo un servidor vivo por generación activa, cuatro unidades síncronas admitidas en total. list también consume unidad; initialize no demuestra capacidad.
- No cola para esperar recuperación; no sustitución de threads bloqueados; no segundo servidor mientras no se acredita reap del anterior.
- Registro vivo máximo de cuatro unidades, con admisión y finalización explícitas. El observer/log no puede retener un thread después de declarar el slot libre sin que esa ocupación siga contabilizada.
- Release normal sólo tras fin real de la unidad, reset y observaciones de control no bloqueantes. Se debe registrar Future done y distinguirlo de callable end. Fallo previo a submit devuelve su reserva exactamente una vez.
- Si se admite una unidad y luego gate/lease vence antes de empezar, se rechaza sin handler/proveedor, se finaliza esa reserva de forma trazable y no se ejecuta al volver el servicio.
- Snapshot pool, semáforo y unidades/futures deben reconciliarse. Una discrepancia cierra admisión, no se interpreta a favor de disponibilidad.
- Tras kill no se sintetizan worker_finished ni releases normales: generación retirada, proceso recolectado, pool nuevo separado. Nunca atribuir finally a un SIGKILL.
- Control/telemetría con tamaño/backlog fijos. Saturación del canal no puede bloquear workers/loop indefinidamente ni hacer desaparecer silenciosamente contabilidad; snapshot autoritativo de control y señal de fallo explícita.
- Slot libre no garantiza por sí mismo loop/transporte sano. READY requiere integridad, guardas, control, listener, sonda nueva correcta y permiso devuelto; restauración completa requiere consulta posterior por red.

Cadena mínima de prueba de recuperación:

    handler_entered / provider_entered del fallo
    → ocupación real y edad
    → decisión + gate ACK o fallo de control declarado
    → drenaje/corte limitado
    → wait/reap de la generación exacta
    → nueva generación íntegra
    → sonda privada autorizada + slot devuelto
    → READY interno
    → NUEVA consulta de red autorizada y correcta
    → servicio restaurado acreditado

Eliminar un contenedor al final es limpieza del arnés, no reemplaza ninguna evidencia de esta cadena.

## 5. Matriz concreta para el bloque posterior

**Todos los casos siguientes son DISEÑO PROPUESTO / PENDIENTE DE VALIDACIÓN. Ninguno se ejecutó en L5.**

Convención comprobada en xoc_mcp_lab/fixtures.py: A = admin_tenant_a, tenant 91001 / usuario 92001; B = admin_tenant_b, tenant 91002 / usuario 92003; A2 = user_a2, tenant 91001 / usuario 92002 / rol USER. No inventar datos reales. Firmas nuevas según fixture existente; preservar perfiles y proyecciones canónicas.

Para cada caso conservar setup, acción, observaciones cliente/trabajo/capacidad/contexto/tiempo, resultado y límites. Un PASS de fallo inyectado significa que el sistema lo contuvo como se especifica, no que la consulta fallida pasó.

### C01 · Normal y cuatro tools

- Preparación/acción: candidato instalado READY; ejecutar separadamente las cuatro READ_ONLY, incluyendo get de ticket propio.
- Cliente/trabajo/capacidad: respuesta canónica correcta, handler una vez por call, fin real y cuatro permisos libres al reposo.
- Contexto/tiempos: audit actor/tenant/source y binding exactos, reset; T_request, T_work_end, Future done y T_slot_release.
- PASS/FAIL: igualdad con canónico, cero writes/externals, sin reinicio, techo temporal cumplido. Límite: proveedor fixture, no AWS.

### C02 · Error inmediato de proveedor

- Preparación/acción: perfil de fault de startup confiado; provocar excepción tras provider_entered; enviar B después.
- Cliente/trabajo/capacidad: error saneado de A, B correcta; A termina realmente y libera, sin reemplazo.
- Contexto/tiempos: audit separado; una entrada handler de A, cero retry; medir spans y contador de recuperaciones 0.
- PASS/FAIL: error no activa reinicio y B conserva datos propios. Límite: no atribuir error operativo a DENY de Policy aunque proyección comparta status.

### C03 · A lenta finita, B sana

- Preparación/acción: barrera confirma entrada de A al proveedor; retardo fixture 2 s, caller A 0,3 s; B durante espera.
- Cliente/trabajo/capacidad: A timeout, B allowed; A mantiene un slot hasta fin real, luego vuelve a cuatro.
- Contexto/tiempos: A no entrega resultado a B; medir caller, fin ~retardo real y release, sin reinicio. DEGRADED→READY sólo se exige si el servidor observó abandono de su awaiter; el timeout local de 0,3 s no lo asegura en JSON stateless.
- PASS/FAIL: liberar antes del fin es FAIL; terminar dentro del umbral no dispara recuperación. Límite: temporizadores no hard real-time; reportar jitter sin alterar presupuesto.

### C04 · A no cooperativa, B sana antes del corte

- Preparación/acción: esperar handler_entered/provider_entered, bloquear A sin cooperación; lanzar B antes del umbral.
- Cliente/trabajo/capacidad: A timeout, B correcta si termina antes del gate; sólo tres permisos libres mientras A vive.
- Contexto/tiempos: B sólo sus datos; edad A no renovada por B; medir detección, recuperación y restauración nueva.
- PASS/FAIL: nueva generación probada dentro de contrato, ningún release ficticio. Límite: no garantizar éxito de B si se la programa después del cierre.

### C05 · Sana en vuelo durante recuperación global

- Preparación/acción: A envejece; B entra realmente al proveedor justo antes del ACK y se mantiene viva más que drenaje.
- Cliente/trabajo/capacidad: B puede perderse por corte y recibir error/timeout, nunca success inventado ni replay; generación vieja retirada.
- Contexto/tiempos: prueba B admitida antes del corte; medir pérdida, drenaje y fin por proceso, no finally ficticio.
- PASS/FAIL: interrupción visible y acotada al hijo, sin segunda ejecución. Límite: demuestra impacto aceptado, no disponibilidad sin pérdida.

### C06 · Cuatro no cooperativas

- Preparación/acción: cuatro entradas de proveedor confirmadas, no cuatro simples timeouts; luego quinto list/call.
- Cliente/trabajo/capacidad: cuatro slots retenidos, quinto busy sin handler; supervisor recupera instancia; nuevo pool cuatro.
- Contexto/tiempos: incluir A/B; contabilidad independiente; T_detection, T_recovery y T_restore_service directos.
- PASS/FAIL: cierre/reap + nueva query correcta y sin oversubmission. Límite: matar contenedor o ready file solo es FAIL de evidencia.

### C07 · Petición durante recuperación

- Preparación/acción: enviar nuevas solicitudes válidas en DRAINING con ACK, RECOVERING sin hijo o con cierre acreditado y STARTING cerrado. Ventana sin ACK/lease vigente se prueba separadamente en C20.
- Cliente/trabajo/capacidad: rechazo rápido cuando hay loop, si no error/timeout del caller; cero negocio nuevo de esas solicitudes.
- Contexto/tiempos: no encolar scope para después; comprobar que no reaparece tras READY; medir deadline del cliente y rechazo.
- PASS/FAIL: ningún handler inesperado/retry/tenant por defecto. Límite: no exigir JSON a servidor ausente.

### C08 · Nueva consulta después de recuperación

- Preparación/acción: luego de reap, sonda privada correcta y READY, arnés lanza solicitud NUEVA por HTTP real ≤0,25 s después.
- Cliente/trabajo/capacidad: respuesta exacta authorized, handler una vez en generación nueva; slot realmente vuelve.
- Contexto/tiempos: assertion y wrapper nuevos, actor/tenant explícitos, no herencia; T_request ≤3,5 s objetivo y T_restore ≤21 s candidato.
- PASS/FAIL: sin esta prueba no hay restauración acreditada. Límite: fixture/network loopback; no producción.

### C09 · Cancelación y disconnect

- Preparación/acción: A lenta con provider_entered; subcasos timeout, cancel del awaiter, cierre TCP y notifications/cancelled en POST distinto.
- Cliente/trabajo/capacidad: el caller termina o notificación da 202; thread/slot permanecen hasta fin real o recuperación.
- Contexto/tiempos: comprobar A y B separados, request_finished no sustituye worker end; registrar instante de disconnect y su detección real.
- PASS/FAIL: no release por cancel externo ni ejecución extra. Límite: stateless no implementa cancelación remota por requestId.

### C10 · IDs repetidos entre usuarios

- Preparación/acción: A/B comparten JSON-RPC ID y request_id ficticios pero firman sobres propios; cancelar B y provocar incidente de A.
- Cliente/trabajo/capacidad: respuestas/errores sólo a sus callers; ningún permiso o resultado reclamado por ID pública.
- Contexto/tiempos: generación + id interno distinguen trabajo; audit/contextos propios antes/después, spans por unidad.
- PASS/FAIL: cero cancelación/resultado ajeno; no cache de autoridad. Límite: no demostrar replay ledger o exactly-once global inexistentes.

### C11 · Fallo de recuperación

- Preparación/acción: subcasos de arnés controlados: reap no acreditable, startup inválido, integridad fallida, sonda denied/timeout.
- Cliente/trabajo/capacidad: UNAVAILABLE, gate cerrado, cero siguiente reemplazo; candidato propio retirado si existe.
- Contexto/tiempos: ninguna degradación a fake ready o provider alternativo; conservar causa saneada y límite excedido.
- PASS/FAIL: fallo visible y finito; si no se prueba muerte, declarar proceso residual y no crear otro. Límite: simular fallo de reap no prueba comportamiento de kernel no interrumpible real.

### C12 · Segundo y tercer incidente

- Preparación/acción: completar primera recuperación y consulta, provocar segundo incidente y recuperación, luego tercero.
- Cliente/trabajo/capacidad: máximo dos reemplazos automáticos; tercero UNAVAILABLE, sin loop.
- Contexto/tiempos: contador fuera del hijo, no se borra al pasar READY; generaciones únicas; registrar cada intento e impacto total.
- PASS/FAIL: tercer spawn automático o rearme por tiempo sano es FAIL. Límite: un reinicio manual explícito del supervisor constituye nueva operación, no disponibilidad automática.

### C13 · Inválidos masivos acotados

- Preparación/acción: 256 intentos a concurrencia máxima 16, dentro del límite existente 32; firma inválida, DENY y ownership injection mezclados; B válida intercalada.
- Cliente/trabajo/capacidad: rechazos seguros, cero handlers/proveedor para denegados; sin recuperaciones causadas por contadores de error.
- Contexto/tiempos: firma y tenant no aceptados; medir heartbeat, CPU, T_request de B, edad de unidades reales.
- PASS/FAIL: sin reinicios bajo esta carga aprobada y sin pérdida de identidad. Límite: no inmunidad frente a tráfico ilimitado/CPU agotada; fixture pública no autentica atacantes.

### C14 · Error después de ejecutar / respuesta perdida

- Preparación/acción: handler retorna datos correctos; arnés interrumpe entrega antes de confirmar al cliente.
- Cliente/trabajo/capacidad: error/resultado incierto; exactamente una ejecución, release real; no fallback/retry ni recuperación basada sólo en error de entrega.
- Contexto/tiempos: identificar esa unidad original sin guardar sus datos en supervisor; medir fin real frente a error cliente.
- PASS/FAIL: una nueva solicitud sólo si explícita e independiente. Límite: no inferir que backend no ejecutó por no haber recibido respuesta.

### C15 · A/B/A2 tras incidente

- Preparación/acción: luego de C06/C08 alternar tickets/documents de A/B y unread_count de A/A2 con sobres nuevos.
- Cliente/trabajo/capacidad: datos y conteos exactos de fixtures, slots retornan; ticket get ajeno no entrega datos.
- Contexto/tiempos: audit del actor/tenant efectivo correspondiente y resets; sin objetos vivos viejos en registro de control; medir llamadas calientes posteriores.
- PASS/FAIL: cualquier dato cruzado, scope por defecto o handler no autorizado es STOP inmediato. Límite: lectura fixture, no usuarios reales.

### C16 · Recursos antes/después de recuperación

- Preparación/acción: medir todos los procesos propios antes/durante/después de los dos reinicios y después del cierre explícito.
- Cliente/trabajo/capacidad: conservar outcomes; máximo un hijo servidor vivo, cuatro unidades; sin zombis/hijos abandonados tras cleanup acreditado.
- Contexto/tiempos: weak scopes son observación, no garantía instantánea; RSS/cgroup/pids/threads/tasks/fds/CPU y edades correlacionados.
- PASS/FAIL: dentro 512 MiB/2 CPU/128 pids, sin OOM, sin crecimiento estructural no explicado ni slots falsos. Límite: fd no es TCP; cinco rondas no prueban ausencia de fuga indefinida.

### C17 · Evento tardío de generación retirada

- Preparación/acción: retrasar un mensaje técnico en arnés, retirar generación y entregarlo después de READY nuevo; incluir IDs públicos repetidos.
- Cliente/trabajo/capacidad: mensaje descartado para control/resultados nuevos, no resta slots ni abre gate.
- Contexto/tiempos: generación e id interno distintos; cero resultados/scopes viejos almacenados; timestamp de emisión/recepción.
- PASS/FAIL: contadores/autoridad de generación nueva intactos. Límite: no afirmar que proceso ya muerto sigue ejecutando; se simula evento en tránsito, no un thread resucitado.

### C18 · Event loop detenido

- Preparación/acción: inyección local de bloqueo del loop confirmada por arnés; supervisor en proceso separado.
- Cliente/trabajo/capacidad: cliente puede agotar su deadline; no ACK falso de gate; supervisor detecta heartbeat vencido y retira.
- Contexto/tiempos: cero nuevo contexto por fallback; T_detection candidato ≤2,25 s desde último latido, recuperación y nueva query medidos.
- PASS/FAIL: detector avanza aun sin loop; no sustituir heartbeat por thread auxiliar. Límite: no cubre host entero congelado.

### C19 · Crash inesperado del hijo

- Preparación/acción: luego de provider_entered terminar sólo el hijo propio mediante arnés, no contenedor.
- Cliente/trabajo/capacidad: resultado en vuelo incierto, wait/reap exacto, reemplazo dentro del intento disponible.
- Contexto/tiempos: contextos nuevos, no replay; medir detección desde salida y restauración total directa.
- PASS/FAIL: prueba de negocio nueva y ninguna duplicación del original. Límite: salida controlada simula crash, no toda falla de hardware.

### C20 · Control/supervisor perdido y lease

- Preparación/acción: subcasos: romper IPC, saturar backlog acotado, pausar supervisor o terminarlo; retener una renovación en IPC y entregarla tarde; reanudar loop antes y después de expiración, sin cambiar recursos globales.
- Cliente/trabajo/capacidad: antes del corte confirmado, última lease vigente puede permitir admisión residual válida y contabilizada; al vencer lease el guard cierra; trabajo previo sigue contado; no spawn ilimitado.
- Contexto/tiempos: nadie renueva lease mediante MCP; medir emisión, lease_until ABSOLUTO, secuencia/generación, recepción retrasada y guard. Mensaje viejo no obtiene recepción + 1 s ni reabre gate confirmado.
- PASS/FAIL: cero negocio nuevo tras ACK/guard vencido, ventana residual no prolongada y documentada; muerte de supervisor no se declara recuperada. Límite: si loop también muerto, cierre lógico sólo al reanudar o morir; se requiere intervención.

### C21 · Vigencia, respuesta tardía y carrera gate/submit

- Preparación/acción: assertion expira antes de entrar; otro subcaso vence con trabajo iniciado; race ACK entre prevalidar/reservar/empezar.
- Cliente/trabajo/capacidad: vencido antes de ejecutar → cero handler; iniciado conserva slot hasta fin/corte, sin entrega fuera de deadline; gate cerrado impide trabajo no iniciado.
- Contexto/tiempos: misma autoridad original mientras vive; no extender vigencia o adoptar B; clocks y secuencia de admisión/ACK.
- PASS/FAIL: sin ejecución tardía por cola ni release anticipado. Límite: no revocación preventiva instantánea de helper ya corriendo.

### C22 · Sonda privada y readiness

- Preparación/acción: STARTING cerrado; intentar activar sonda con headers, _meta, argumentos, sesión o IDs; luego comando IPC legítimo con fixture nueva.
- Cliente/trabajo/capacidad: público no obtiene bypass; privada pasa por boundary/SDK/gateway y devuelve slot. Sonda inválida/denied nunca abre READY.
- Contexto/tiempos: firma normal, actor explícito, una ejecución; medir startup, probe, cleanup y consulta de red independiente posterior.
- PASS/FAIL: ningún marker de HTTP concede excepción; probe ASGI sólo no basta para restauración. Límite: no acredita autenticación productiva ni host adversario.

### C23 · Observer bloqueado / contador vs thread

- Preparación/acción: en arnés candidato bloquear salida de diagnóstico después del callable o saturar canal de logs; seguir B.
- Cliente/trabajo/capacidad: control independiente; no anunciar permiso reutilizable dejando ocupación síncrona oculta; no cola creciente detrás de observers.
- Contexto/tiempos: sin scopes/resultados retenidos por supervisión; medir callable end, Future done, release y edad.
- PASS/FAIL: I/O de logs no evade detector ni genera falsa readiness; salida segura o recuperación según límite. Límite: hace explícita la brecha de observador encontrada por lectura, no un fallo medido en L4.

### C24 · Fallo de recursos y límites del host

- Preparación/acción: simular de forma determinista fallos de crear proceso/canal/abrir listener; medir en recursos aprobados, sin agotar deliberadamente el host del usuario.
- Cliente/trabajo/capacidad: UNAVAILABLE, rechazo/error acotado si host progresa; no más hijos ni aumento de memoria/pids.
- Contexto/tiempos: ningún fallback/credencial alternativa; registrar fase y deadline incumplido, sin stacktrace sensible.
- PASS/FAIL: fallo cerrado y explícito. Límite: no prueba SIGKILL oportuno bajo kernel no interrumpible, ni garantiza tiempo con host sin CPU.

## 6. Medición comparativa y recursos

**DISEÑO PROPUESTO para L6, no campaña iniciada:**

- Usar Linux AL2/Python 3.11.16/x86_64, SDK 1.30.0 y protocolo 2025-11-25, offline/loopback, presupuesto conjunto original.
- Candidato instalado con procedencia/hash y runs nuevos; L4 intacto como baseline. No ejecutar desde checkout y llamarlo artefacto equivalente.
- Conservar población caliente de 80, cuatro concurrentes, cinco arranques, tres respuestas grandes y cinco rondas de 40 con mezcla histórica. Separar rechazos, errores, timeouts y pérdidas.
- Comparación emparejada en el mismo entorno futuro: al menos tres repeticiones L4/candidato alternando orden; no comparar una muestra nueva sólo contra una mediana histórica y atribuir todo a código.
- Preservar originales 304 tests y ejecutar también nuevos en candidato; no corregir fallos ajenos incidentalmente ni involucrar VICTOR. Un skip de recuperación Linux no cuenta como PASS.
- Incidentes de recuperación en población separada: tres ejecuciones supervisadas independientes para cubrir C04/C06/C18, cada una con máximo dos intentos. Al menos una prueba explícita del tercer incidente. Reiniciar el arnés entre casos es preparación explícita, no breaker que se rearma solo.
- Medir spans directos, distribución y máximo de detección/recovery/restoration por causa, tamaño de muestra y fallos. No percentiles sumados ni columnas de cleanup confundidas con RTO.
- Recursos: RSS/HWM por proceso, cgroup current/peak/OOM/failcnt, CPU del supervisor y servidor, pids/threads/tasks/fds, unidades/futures y slots. Si se desea contar conexiones TCP, medir sockets explícitamente; no inferirlas de fds.
- Comparar baseline estable dentro de cada nueva generación después del mismo calentamiento. Cierre final debe demostrar no hijos/zombis propios, permisos/solicitudes pendientes 0 cuando corresponda; referencias débiles no exigen cero inmediato ni prueban todo el heap.

### Umbrales de ventaja caliente — candidatos, no resultados

Para no aceptar una regresión grande sólo porque todavía entra en 3,5 s, se propone:

- 80/80 allowed en cada repetición caliente y 4/4 concurrentes sanas; ninguna >5 s.
- Objetivo individual normal 3,5 s, registrar toda superación; para la población sana propuesta exigir ninguna.
- Mediana y p95 calientes del candidato ≤1,25 veces los de su repetición L4 emparejada. Informar los tres pares, no seleccionar sólo el mejor.
- Si la variabilidad invalida la comparación, resultado **inconcluso / no aceptación temporal**, no inventar una ganancia ni relajar 25% silenciosamente.
- No se afirma throughput por usuario ni se usa latencia fixture como estimación cloud.

El 25% es una tolerancia de diseño por aprobar, no derivada estadísticamente de L4. Cambiarla exige revisar este gate; no cambiar core ni recursos para “pasar”.

## 7. PASS, FAIL, STOP y declaraciones permitidas

### PASS técnico del bloque posterior

Todos los invariantes de seguridad; casos de matriz con evidencia suficiente; regresiones sin nuevos fallos; contabilidad coherente; recuperación finita medida; consulta nueva de red correcta; recursos y objetivos temporales aprobados cumplidos. Impacto sobre sanas registrado y aceptado, no escondido.

Caso sin handler_entered/provider_entered previo no demuestra bloqueo de proveedor. Caso sin nueva query de red no demuestra restauración. Reap sólo demuestra retirada. Un retorno exitoso con tenant equivocado es FAIL de seguridad aunque sea rápido.

### FAIL / NO-GO

Cualquier criterio anterior incumplido. Separar:

- Falla de implementación/seguridad: cerrar y corregir dentro del bloque autorizado, sin cambiar contrato.
- Objetivo temporal/recursos inviable: conservar resultado negativo; elevar la decisión, no aumentar límites.
- Entorno no apto/evidencia incompleta: inconcluso, no aprobado ni fracaso inventado del algoritmo.

### STOP de alcance

Necesitar otro repo, modificar core/Policy/SQL/SDK/locks, nueva dependencia, mayor presupuesto, cloud, claves productivas, procesos ajenos, VICTOR/Voice o un mecanismo C distinto exige nueva autorización. No compensar un FAIL con legacy, auth débil, más slots, retries o una segunda réplica no aprobada.

Si muere supervisor o no se puede recoleccionar un proceso, no automatizar un ciclo de reemplazos. Declarar estado residual y acción manual local necesaria.

### Qué permitiría declarar un PASS

“En el experimento local fijado, la instancia recuperó capacidad y atendió solicitudes nuevas autorizadas después de los fallos probados, con radio de pérdida conocido y sin cruces de contexto observados; B cumple el contrato aprobado”.

### Qué seguiría sin validarse

Producción, proveedor real, IAM/OAuth/D.2, autenticación multiusuario real, HA, exposición pública, cloud/network latency, host/kernel arbitrario, duración indefinida, zero-loss, exactly-once global y disponibilidad total de XOC. Un PASS LOCAL no autoriza integración automática.

## 8. Preservación de L5

### Método y alcance

Lectura antes y después, sin ejecutar scripts del proyecto: enumeración de archivos del laboratorio, SHA256 de cada archivo y manifiesto por grupo de primer nivel. Incluye .venv, fuentes, tests, locks, artefactos y resultados históricos. Excluye únicamente estos dos documentos nuevos:

- reports/l5-recovery-decision.md
- reports/l5-acceptance-contract.md

Material de cada digest de grupo: filas ordenadas por path relativo, cada una “path:bytes:sha256” en UTF-8, unidas por LF sin LF final; luego SHA256. Las rutas largas se leyeron con streams de .NET y prefijo Windows extendido, después de que Get-FileHash no pudiera resolver una ruta histórica larga. No se renombró ni reparó ningún archivo.

Baseline: **14.112 archivos preexistentes, 606.054.472 bytes**. No se escribieron manifests auxiliares: resumen conservado en este documento y comparación en la sesión de herramientas.

**Resultado de comparación de cierre: PASS de preservación documental.** Los 26 grupos, 14.112 archivos preexistentes y 606.054.472 bytes conservaron exactamente sus manifiestos SHA256; sólo aparecieron los dos documentos excluidos expresamente. Branch, HEAD y listas completas de status de los cinco repos también coincidieron antes/después. Esta comparación es lectura de integridad, no una suite ni un benchmark nuevo.

### Digests de preservación

Son digests de manifiestos por grupo, NO el SHA de un archivo individual. El manifiesto reports excluye los dos L5. Los valores siguientes fueron idénticos antes/después.

| Grupo | Archivos | Bytes | SHA256 del manifiesto inicial |
| --- | ---: | ---: | --- |
| .gitignore | 1 | 52 | 00d3891c8fa13270a03f2c058276349791eb7e567c9fc6fcda8818f520def474 |
| .venv | 8278 | 130003674 | d2432204889798f5c5fdc124f453ffaa8d3e009ed0a098f89b63f9f0587f42cc |
| compatibility.json | 1 | 1654 | 774ff9badf29f01dbd0c0fd1c693c1dc8d604777dce24e04d82c3839af73a291 |
| compatibility-l3-linux.json | 1 | 1395 | 8deff4525738ad4c608913e58892995bca6f8297925b46e0b7d3ec6fdcacb381 |
| compatibility-linux.json | 1 | 3252 | 851297a2515edced3cbbe1cf3551abd256acdc9cf055a26d00b9b53d6971d65f |
| documento-de-diseno.md | 1 | 7752 | 4910b963393f64861a4c88471fd77505eaa41ef675f2af6f109eeaa3ecdc12e7 |
| l2-runs | 4486 | 209737429 | 125c3bf64097368d0f0332ac3074e5fff6ef4a8ad7605d683dc443d99730c779 |
| l3-runs | 496 | 87920614 | 6cce14879557bc7342f5783973ebf8fd2d8d0c9dc1672114122f1dd656a31e3e |
| l4-runs | 714 | 177146096 | cae59e2c3ff3efc247c1dc39126a457e33962df1864198d9853d8573895bde35 |
| packaging | 14 | 54529 | 932c9d6821ef9d1bb0a0f001890f1e88dfba386df69dde8a473aab0cab3e832c |
| README.md | 1 | 23877 | e608a3638019723ffc7dd6422154527c7a7e0c6625f309c6505ae5ee8d8c3c2b |
| reports | 53 | 809806 | 7dda5f7f90aa9c6acee06c7475881228f6a5706a0e09a138b74a79a405b84203 |
| requirements.in | 1 | 303 | 812a42d05b0f9b34f23912ba2118f7dfeae71828ba30b1e9d3c91042120e7fcc |
| requirements.lock.txt | 1 | 1070 | 99e0894f2b2049d57b84ce99f69dfc53744976ef14981e76b6031d0600be537a |
| run_backend_regressions.py | 1 | 1979 | 8e22ffefaafd7534c68f9fb3d377d0f6dd5f5f2bdf946b201b3be4da8eabc2fc |
| run_tests.py | 1 | 813 | 8d9d2fa64b1d21662b9a1f2a2268c22318d7b9a6035ab4fbf5282eaddcc7f134 |
| scripts | 8 | 36059 | f704efa408b2b42dc98e9a9ae666785016b6d1d319325f691f3dc9fcaff9c2bb |
| server.py | 1 | 1941 | 42dc2d3a9b31f9e7008146104f3c742cc1b29ee3e4e123dc3abce6c3d1b0c686 |
| setup.ps1 | 1 | 1285 | 0e8f6661b36ea7fd5b18c41a98db55ad0a9c7336b3cda2f53f3d5b0b784a8e34 |
| smoke.py | 1 | 484 | e0d7ab373eec66ccb4c57a8524ac9676b5b151ac7b2658449222b29c37d8a216 |
| smoke_l1.py | 1 | 3046 | 754c33b0b06b5b8f7945a5473ed82accba51a1d6c21e6b0f3e6fcc31980aebf1 |
| snapshot.ps1 | 1 | 1646 | d285045aaba4e12d07639d6e2eb8e43d01e8093361975f6bed51e3e2470a1ad8 |
| tests | 16 | 119690 | b5cd24c9a35c04c9651ed081398e8b23964d15ed8d5f7345078b4dc405832522 |
| xoc_mcp_lab | 13 | 61287 | 4967a2dc994375a4ae2e4d2f81ff57f0045f92b656e71efb9f15c968b52a188b |
| xoc_mcp_lab_harness | 10 | 37656 | 5f49c4e3f5f709eb680583360af7015e1f093aa43b4b3da7fc38036943a0286b |
| xoc_mcp_lab_l4 | 9 | 77083 | e7dd3221cd37230c8979caf382410ee13a986363fc3ad0efde5e29bc1534cd6d |

### Estado Git y otros repositorios

Sólo consultas read-only de branch, HEAD y status con optional locks desactivados para status. No stage/commit/push/pull/init/reset/stash/clean; no reparación del .git padre. El laboratorio no se declaró repositorio limpio. Los árboles ajenos ya contenían cambios; se conservaron.

| Repo | Branch | HEAD inicial | Entradas de status preexistentes |
| --- | --- | --- | ---: |
| XOC-API-AWS | main | ad4ac1a764de03f65e1ff2f2a2c6762a89ce9eb9 | 3 |
| XOC-APP-MOBILE | main | df151a73551ed5a437c2f1e40b51896cee9dd24c | 10 |
| XOC_PAGE | main | f43d3ed6b2eac389cdf29e9fc9d052137fe188eb | 11 |
| proxy-sophia-xoc | main | 93107ac2a4ad08b2c28e6242fb0aeda666eaf54f | 12 |
| XOC_MICROSERVICIO_AGENTES | main | 6249064a9bd69bb388369d03f502b142bac93e1b | 0 |

La comparación final exige igualdad exacta de branch, HEAD y lista de status, no sólo del número de entradas. Esto acredita conservación del estado Git observado; no se presenta como hash byte a byte de cada repo ajeno. No se escribió en ellos ni se revisó código de Voice/VICTOR.

### Acciones excluidas durante L5

No tests/benchmarks nuevos; no procesos de servidor, contenedores ni builds; no cambios de configuración/recursos; no instalación/importación ejecutable del proyecto; no consultas AWS/Azure/modelos ni red externa; no activación de recuperación. Las herramientas locales sólo leyeron archivos/estado y crearon/revisaron estos dos Markdown.

**Estado de entrega:** diseño completo, B recomendada de forma condicionada; la implementación L6 y su interrupción requieren aprobación posterior.
