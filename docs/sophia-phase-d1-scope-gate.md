# SOPHIA D.1 — gate de alcance e identidades

Fecha: 2026-09-13. Estado: **detenido antes de modificar código funcional**.

> Actualización posterior del mismo día: el usuario autorizó explícitamente el
> alcance parcial propuesto. `/run` y history/delete ya se implementaron y
> validaron localmente; ver `sophia-phase-d1-partial.md`. Este informe conserva
> la evidencia del gate previo. Las APIs directas/Voice siguen pendientes.

El prompt D.1 exige detenerse si no se puede distinguir de forma segura a
SOPHIA/VICTOR, y excluye cambios en Voice. La revisión encontró dos dependencias
que requieren resolver el alcance antes del cierre integral. Este documento no
implementa D.1 ni levanta los gates pre-deploy de D.

## Estado preservado

| Repo | Rama | HEAD inicial/final de esta revisión |
| --- | --- | --- |
| XOC-API-AWS | main | e1fe34c56f288093d57f04507d6951e4998e0558 |
| XOC_MICROSERVICIO_AGENTES | main | fbf395a926757f72ff966f024e586265f67adbef |

Ambos repos comenzaron limpios, con dos commits locales de D pendientes de push.
El pull del turno anterior no trajo cambios. No se hizo push, commit, deploy,
cambio de flags, rotación de claves ni consulta a datos de negocio en esta revisión.
Único archivo añadido: este informe, en el repo backend.

## 1. HIGH — identidad compartida por Chat legacy y Voice

Fuentes locales comprobadas:

- `src/handlers/routes/agents.py`: token-from-user sólo permite SOPHIA; emite
  `scopes=[agent:invoke]`, `agent_type=SOPHIA`, tenant y subject de servicio.
  No identifica un canal Voice autenticado ni una instancia del proxy.
- `src/handlers/routes/chat.py::_build_agent_invoke_token`: Chat legacy utiliza
  la misma familia de token SOPHIA.
- `src/shared/dependencies.py::get_request_tenant_context`: reconoce el scope
  agent:invoke, pero no distingue Chat, Voice ni el tipo de agente.
- `src/handlers/routes/scans.py`: las rutas de agente usan esa dependencia.

Evidencia remota obtenida en el diagnóstico del 12-sep, **no reconsultada ahora**:
el proxy `ca-sophia-voice-proxy`, revisión `0000037`, imagen `proxy-sophia:v15`,
usa `AZURE_AGENT_TYPE=SOPHIA`, intercambia el JWT mediante
`/agents/auth/token-from-user` y consulta `/scans/agent/summaries`. Hubo respuestas
200 en sesiones de web y mobile. Ambos clientes apuntan a ese proxy.

Consecuencia: con el contrato auditado, bloquear todos los bearer SOPHIA en
esas rutas también bloquearía las consultas de Voice al activar el enforcement.
No es una conclusión sobre la causa del silencio de Voice, cuyo diagnóstico
quedó pausado por petición del usuario. Es una dependencia de autorización.

No se debe introducir una excepción basada en un header, body, User-Agent o
`source=voice` elegible por el cliente: reabriría el bypass. Una identidad de
componente Voice necesitaría emisión y validación de servidor y un alcance de
permisos revisado. Esa separación no está autorizada por el prompt D.1 actual.

## 2. HIGH — tokens legacy sin identidad de agente obligatoria

`XOC_MICROSERVICIO_AGENTES/shared/agent_auth.py::get_agent_token` verifica
agent_type sólo si el claim tiene un valor no vacío. El mismo bearer sin ese
claim supera la validación tanto para SOPHIA como para VICTOR.

Prueba focalizada offline ejecutada con el helper real, clave ficticia, exp
vigente y conexiones de red bloqueadas. No se imprimieron tokens ni se invocaron
Foundry, BackendClient o handlers de datos:

| Claim agent_type | Verificador SOPHIA | Verificador VICTOR |
| --- | --- | --- |
| SOPHIA | Acepta | Deniega |
| VICTOR | Deniega | Acepta |
| Ausente | Acepta | Acepta |
| Vacío | Acepta | Acepta |

Resultado: **4 casos comprobados**, salida 0. No es una suite de regresión ni
una prueba de uso real de tokens sin tipo en producción.

El emisor actual conocido `src/handlers/workers/assess_ticket_automation.py`
sí incluye `agent_type=VICTOR`, y el authorizer proyecta el claim firmado como
agentType. Ese camino puede distinguirse. No se ha acreditado que todos los
consumidores/emisores legacy desplegados usen ese contrato. La ausencia del tipo
no permite inferir VICTOR, SOPHIA, un humano ni otro agente con seguridad.

Cambio mínimo de contrato propuesto, **no implementado**: exigir identidad de
servicio inequívoca y verificada en los caminos directos permitidos a VICTOR;
rechazar tokens sin tipo/desconocidos en modo seguro; acreditar los emisores y
la transición de tokens antiguos antes de activar. No conceder identidad desde
el body ni ampliar scopes por conveniencia. No modificar workers a ciegas.

## 3. Parte separable y decisión pendiente

Se puede acotar una siguiente implementación a los entrypoints SOPHIA de Azure:

1. Enforcement servidor, apagado por defecto, en `/run`, previo a Foundry y
   registry legacy; aceptar únicamente protocolo D y capability válida al activar.
2. Capabilities cortas para history/delete, emitidas por XOC sólo tras ownership
   de AgentSession, ligadas a actor, tenant, operación, thread y request.
3. Regresiones con flags OFF/ON, sin cambiar el handler/verificador de VICTOR.

Esto sería **D.1 parcial**, no cierre integral: las APIs directas seguirían siendo
un gate HIGH hasta resolver las identidades y la dependencia de Voice. Requiere
acordar explícitamente ese alcance; no se ha implementado durante esta revisión.

## 4. Evidencia y riesgos pendientes

- HIGH: dispatcher legacy y acceso history/delete directo siguen como antes.
- HIGH: accesos directos SOPHIA, identidad Voice compartida y tokens sin tipo.
- HIGH: material HS256 compartido; separación de dominios pendiente de D.2.
- Latencia: pendiente de smoke, sin modificar timeout de 20 s.
- Operativo: SHA/bytes/script Azure y release aislado Chat siguen pendientes según
  `sophia-phase-d-predeploy-gate.md`. No se revalidaron en este turno porque se
  alcanzó el gate obligatorio de alcance antes de implementar.
- No se ejecutaron suites completas: no hay código D.1 que validar. No se
  atribuyen resultados anteriores a esta revisión ni se declaran resueltos los
  fallos históricos VICTOR.

## Decisiones

- ¿D.1 implementada/lista para commit? **No**. Sólo informe de gate.
- ¿Lista para deploy coordinado con flags OFF? **No**: no hay candidato D.1.
- ¿Ejecutar D.2 antes de MCP? **Sí**, como fase separada tras resolver los gates
  y la validación correspondiente; no se inicia automáticamente.
- ¿Existe bypass legacy conocido todavía? **Sí**. No se activó ningún cierre.

El siguiente paso requiere decidir entre un cierre parcial de Chat claramente
declarado o ampliar el alcance para separar la identidad de Voice y acreditar
la transición de los tokens VICTOR. No se tocaron Voice, MCP, Step Functions,
schemas RDS, SQL, login/access/refresh ni herramientas del registry.
