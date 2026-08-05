# Contrato de aprobacion por riesgo.
# Cada plan de remediacion (generado por Victor, Azure u on-premise) se evalua y
# se le asigna un nivel de riesgo. El rol que debe aprobar la ejecucion depende de
# ese nivel:
#   - basic      -> USER       (instalaciones, consultas y cosas simples)
#   - controlled -> ADMIN      (cambios de configuracion, reinicios y cosas menos graves)
#   - risky      -> ADMIN_XOC  (eliminacion o modificacion de cosas importantes)
#   - critical   -> SUPERADMIN (purga / wipe / destruccion irreversible)
RISK_LEVELS = ["basic", "controlled", "risky", "critical"]

RISK_LEVEL_ORDER = {level: i for i, level in enumerate(RISK_LEVELS)}

ROLE_HIERARCHY = {
    "USER": 1,
    "ADMIN": 2,
    "ADMIN_XOC": 3,
    "SUPERADMIN": 3,
}

REQUIRED_ROLE_FOR_RISK = {
    "basic": "USER",
    "controlled": "ADMIN",
    "risky": "ADMIN_XOC",
    "critical": "SUPERADMIN",
}

APPROVER_LABEL_FOR_RISK = {
    "basic": "Usuario",
    "controlled": "Admin del tenant",
    "risky": "Admin XOC",
    "critical": "Superadmin XOC",
}

ACTION_TYPE_RISK_MAP = {
    # basic -> USER: instalaciones y cosas simples
    "view": "basic",
    "list": "basic",
    "export": "basic",
    "report": "basic",
    "install": "basic",
    "setup": "basic",
    "deploy": "basic",
    "start": "basic",
    "create": "basic",
    # controlled -> ADMIN: algo menos grave
    "update": "controlled",
    "modify": "controlled",
    "configure": "controlled",
    "restart": "controlled",
    "enable": "controlled",
    "disable": "controlled",
    "upgrade": "controlled",
    "downgrade": "controlled",
    "uninstall": "controlled",
    "scale": "controlled",
    "backup": "controlled",
    # risky -> ADMIN_XOC: eliminacion o modificacion de cosas importantes
    "delete": "risky",
    "remove": "risky",
    "destroy": "risky",
    "terminate": "risky",
    "revoke": "risky",
    "reset": "risky",
    "replace": "risky",
    "migrate": "risky",
    "change_tenant_plan": "risky",
    "modify_important": "risky",
    "delete_important": "risky",
    # critical -> SUPERADMIN: irreversible
    "purge": "critical",
    "wipe": "critical",
    "drop": "critical",
    "reinitialize": "critical",
}

DEFAULT_RISK_LEVEL = "basic"
DEFAULT_ROLE = "USER"


def is_role_sufficient(user_role: str, required_role: str) -> bool:
    user_level = ROLE_HIERARCHY.get(user_role.upper(), 0)
    required_level = ROLE_HIERARCHY.get(required_role.upper(), 0)
    return user_level >= required_level


def risk_level_order(risk_level: str) -> int:
    return RISK_LEVEL_ORDER.get(risk_level.lower(), 0)


def required_role_for_risk(risk_level: str) -> str:
    return REQUIRED_ROLE_FOR_RISK.get(risk_level.lower(), DEFAULT_ROLE)


def approver_label_for_risk(risk_level: str) -> str:
    return APPROVER_LABEL_FOR_RISK.get(risk_level.lower(), APPROVER_LABEL_FOR_RISK[DEFAULT_RISK_LEVEL])


def resolve_step_risk_level(step: dict) -> str:
    """Resuelve el riesgo de un paso del plan.

    Se prioriza el risk_level declarado por el agente (Victor azure u
    on-premise). Si el paso declara que afecta algo importante (por ejemplo
    `impact: critical` o `important: true`) se sube al nivel minimo indicado.
    Si no declara nada, se deriva del action_type.
    """
    level = (step.get("risk_level") or "").lower()
    if level in RISK_LEVEL_ORDER:
        return level

    impact = (step.get("impact") or "").lower()
    if impact in ("important", "critical", "high", "irreversible"):
        return "risky" if impact != "irreversible" else "critical"

    if step.get("important") in (True, "true", "True", 1):
        return "risky"

    action_type = (step.get("action_type") or "").lower()
    return ACTION_TYPE_RISK_MAP.get(action_type, DEFAULT_RISK_LEVEL)


def compute_max_risk_level(plan: dict | list | None) -> str:
    """Nivel de riesgo maximo de un plan.

    Acepta:
      - {"steps": [{"action_type": ...|"risk_level": ...}]}
      - {"plan": {"steps": [...]}, ...}
      - [{"action_type": ...}, ...]  (lista de pasos directa)
    Un `risk_level` explicito a nivel de plan se respeta como minimo del resultado.
    """
    if plan is None:
        return DEFAULT_RISK_LEVEL

    plan_level = None
    if isinstance(plan, dict):
        plan_level = (plan.get("risk_level") or "").lower()
        if plan_level not in RISK_LEVEL_ORDER:
            plan_level = None
        nested = plan.get("plan")
        if nested is not None:
            if isinstance(nested, dict):
                plan = nested
            elif isinstance(nested, list):
                plan = nested
        steps = plan.get("steps", []) if isinstance(plan, dict) else plan if isinstance(plan, list) else []
    elif isinstance(plan, list):
        steps = plan
    else:
        steps = []

    max_level = DEFAULT_RISK_LEVEL
    max_order = 0
    for step in steps:
        if not isinstance(step, dict):
            continue
        level = resolve_step_risk_level(step)
        order = RISK_LEVEL_ORDER.get(level, 0)
        if order > max_order:
            max_order = order
            max_level = level

    if plan_level and RISK_LEVEL_ORDER.get(plan_level, 0) > RISK_LEVEL_ORDER.get(max_level, 0):
        max_level = plan_level

    return max_level


def approval_requirement(plan: dict | list | None) -> dict:
    """Resumen del contrato de aprobacion para un plan."""
    max_risk_level = compute_max_risk_level(plan)
    required_role = required_role_for_risk(max_risk_level)
    return {
        "max_risk_level": max_risk_level,
        "required_approver_role": required_role,
        "approver_label": approver_label_for_risk(max_risk_level),
        "publicly_approvable": required_role == "USER",
    }


def is_publicly_approvable(risk_level: str) -> bool:
    return required_role_for_risk(risk_level) == "USER"
