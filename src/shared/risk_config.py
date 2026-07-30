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

ACTION_TYPE_RISK_MAP = {
    "view": "basic",
    "list": "basic",
    "export": "basic",
    "report": "basic",
    "update": "controlled",
    "modify": "controlled",
    "configure": "controlled",
    "restart": "controlled",
    "enable": "controlled",
    "disable": "controlled",
    "delete": "risky",
    "remove": "risky",
    "destroy": "risky",
    "terminate": "risky",
    "revoke": "risky",
    "purge": "critical",
    "wipe": "critical",
    "drop": "critical",
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


def compute_max_risk_level(plan: dict) -> str:
    steps = plan.get("steps", []) if isinstance(plan, dict) else []
    if not steps:
        return DEFAULT_RISK_LEVEL
    max_level = DEFAULT_RISK_LEVEL
    max_order = 0
    for step in steps:
        level = (step.get("risk_level") or "").lower()
        if not level:
            action_type = (step.get("action_type") or "").lower()
            level = ACTION_TYPE_RISK_MAP.get(action_type, DEFAULT_RISK_LEVEL)
        order = RISK_LEVEL_ORDER.get(level, 0)
        if order > max_order:
            max_order = order
            max_level = level
    return max_level


def is_publicly_approvable(risk_level: str) -> bool:
    return required_role_for_risk(risk_level) == "USER"
