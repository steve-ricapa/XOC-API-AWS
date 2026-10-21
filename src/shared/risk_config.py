import re

# Contrato de aprobacion por riesgo.
# Cada plan de remediacion (generado por Victor, Azure u on-premise) se evalua y
# se le asigna un nivel de riesgo. El rol que debe aprobar la ejecucion depende de
# ese nivel:
#   - basic      -> USER       (instalaciones, consultas y cosas simples)
#   - controlled -> ADMIN      (cambios de configuracion, reinicios y cosas menos graves)
#   - risky      -> ADMIN      (eliminacion de archivos / modificacion de cosas importantes; techo = admin tenant)
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
    "risky": "ADMIN",
    "critical": "SUPERADMIN",
}

APPROVER_LABEL_FOR_RISK = {
    "basic": "Usuario",
    "controlled": "Admin del tenant",
    "risky": "Admin del tenant",
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
    # risky -> ADMIN: eliminacion de archivos (techo de gobernanza = admin tenant)
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


# ---------------------------------------------------------------------------
# Inferencia de riesgo a partir del comando
# ---------------------------------------------------------------------------
# El risk_level declarado por el agente (Victor) o por el usuario no basta para
# gobernar la ejecucion: un paso puede declarar "basic" y ejecutar "rm -rf /".
# La inferencia por comando actua como piso, de forma que el riesgo efectivo de
# un paso es max(declarado, inferido).
COMMAND_RISK_MAP = {
    # --- solo lectura ---
    "ls": "basic", "cat": "basic", "head": "basic", "tail": "basic", "file": "basic",
    "stat": "basic", "find": "basic", "grep": "basic", "egrep": "basic", "fgrep": "basic",
    "rg": "basic", "awk": "basic", "sort": "basic", "uniq": "basic", "wc": "basic",
    "echo": "basic", "printf": "basic", "ps": "basic", "top": "basic", "netstat": "basic",
    "ss": "basic", "ifconfig": "basic", "ip": "basic", "ping": "basic", "df": "basic",
    "du": "basic", "free": "basic", "uname": "basic", "id": "basic", "whoami": "basic",
    "date": "basic", "which": "basic", "whereis": "basic", "tree": "basic", "env": "basic",
    "printenv": "basic", "uptime": "basic", "hostname": "basic", "getent": "basic",
    "lsof": "basic", "journalctl": "basic", "apt-cache": "basic", "dpkg-query": "basic",
    "test": "basic", "true": "basic", "false": "basic", "sleep": "basic", "seq": "basic",
    "diff": "basic", "less": "basic", "more": "basic", "od": "basic", "xxd": "basic",
    "basename": "basic", "dirname": "basic", "realpath": "basic", "readlink": "basic",
    "sha256sum": "basic", "md5sum": "basic", "base64": "basic", "uptime": "basic",
    # --- mutacion controlada ---
    "mkdir": "controlled", "mv": "controlled", "chmod": "controlled", "chown": "controlled",
    "systemctl": "controlled", "service": "controlled", "apt": "controlled",
    "apt-get": "controlled", "dpkg": "controlled", "pip": "controlled", "pip3": "controlled",
    "npm": "controlled", "tee": "controlled", "kill": "controlled", "pkill": "controlled",
    "killall": "controlled", "useradd": "controlled", "usermod": "controlled",
    "groupadd": "controlled", "iptables": "controlled", "ufw": "controlled",
    "mount": "controlled", "umount": "controlled", "crontab": "controlled",
    "update-alternatives": "controlled", "sysctl": "controlled", "insmod": "controlled",
    "modprobe": "controlled", "docker": "controlled", "kubectl": "controlled",
    "git": "controlled", "patch": "controlled", "rsync": "controlled", "cp": "controlled",
    "dd": "controlled", "tar": "controlled", "unzip": "controlled", "curl": "controlled",
    "wget": "controlled", "python": "controlled", "python3": "controlled", "perl": "controlled",
    "sed": "controlled", "awk2": "controlled", "tr": "controlled", "chattr": "controlled",
    "setcap": "controlled", "visudo": "controlled", "gpasswd": "controlled",
    # --- riesgo alto (eliminacion / destruccion acotada) ---
    "rm": "risky", "rmdir": "risky", "unlink": "risky", "shred": "risky",
    "truncate": "risky", "mkfs": "critical", "wipefs": "critical", "fdisk": "critical",
    "sfdisk": "critical", "parted": "critical", "blockdev": "risky",
}

# Rutas del sistema: una operacion recursiva sobre ellas es irreversible.
SYSTEM_PATH_PREFIXES = (
    "/etc", "/usr", "/bin", "/sbin", "/boot", "/lib", "/lib64",
    "/var", "/root", "/dev", "/sys", "/proc", "/srv",
)

CRITICAL_TARGET_FILES = ("/etc/passwd", "/etc/shadow", "/etc/sudoers", "/etc/crontab")

_CRITICAL_TOOL_PATTERN = re.compile(r"\b(mkfs(\.\w+)?|wipefs|fdisk|sfdisk|parted)\b", re.IGNORECASE)
_DD_BLOCK_DEVICE_PATTERN = re.compile(r"\bdd\b[^|;]*\bof=/dev/", re.IGNORECASE)
_PIPE_TO_SHELL_PATTERN = re.compile(r"\|\s*(sudo\s+)?(ba|z|k|da|fi)?sh\b", re.IGNORECASE)
_RECURSIVE_FLAG_PATTERN = re.compile(r"(^|\s)(-[a-zA-Z]*r[a-zA-Z]*|--recursive)(=|\s|$)")
_REDIRECT_PATTERN = re.compile(r">>?\s*([^\s|&;]+)")
_SHELL_PREFIXES = ("sudo", "doas", "nohup", "time", "env", "nice", "ionice")


def _split_command_segments(command: str) -> list[str]:
    """Divide un comando shell en segmentos ejecutables."""
    parts = re.split(r"&&|\|\||[;\n|]", command)
    return [part.strip() for part in parts if part.strip()]


def _segment_head(segment: str) -> tuple[str, str]:
    """Devuelve (comando, argumentos) de un segmento, quitando prefijos."""
    tokens = segment.split()
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if "=" in token and not token.startswith("-") and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token):
            idx += 1
            continue
        break
    while idx < len(tokens) and tokens[idx] in _SHELL_PREFIXES:
        idx += 1
    if idx >= len(tokens):
        return "", ""
    name = tokens[idx].split("/")[-1]
    return name, " ".join(tokens[idx + 1:])


def _mentions_system_path(tokens: list[str]) -> bool:
    for token in tokens:
        cleaned = token.strip().strip("'\"")
        if not cleaned.startswith("/"):
            continue
        if cleaned in ("/", "/*", "/.", "/.."):
            return True
        for prefix in SYSTEM_PATH_PREFIXES:
            if cleaned == prefix or cleaned.startswith(prefix + "/"):
                return True
    return False


def _redirect_target(command: str) -> str | None:
    match = _REDIRECT_PATTERN.search(command)
    return match.group(1).strip("'\"") if match else None


def _command_risk(name: str, args: str, segment: str) -> str | None:
    """Riesgo de un comando individual (None si no se puede determinar)."""
    args_tokens = args.split()
    lowered = args.lower()

    if name in ("rm", "rmdir", "unlink", "shred"):
        recursive = bool(_RECURSIVE_FLAG_PATTERN.search(f" {lowered} ")) and name == "rm"
        if recursive and _mentions_system_path(args_tokens):
            return "critical"
        if name == "shred" and _mentions_system_path(args_tokens):
            return "critical"
        return "risky"

    if name == "find":
        if "-delete" in lowered or "-exec rm" in lowered or "-exec unlink" in lowered:
            return "risky"
        return "basic"

    if name == "dpkg":
        if any(flag in args_tokens for flag in ("-l", "--list", "-s", "--status")):
            return "basic"
        return "controlled"

    if name == "systemctl":
        if any(word in lowered for word in ("status", "list-units", "show", "cat ")):
            return "basic"
        return "controlled"

    if name in ("docker", "kubectl", "helm", "git"):
        if any(word in lowered for word in (" ps", " logs", " get ", " status", " diff", " show", " describe")):
            return "basic"
        return "controlled"

    if name == "sed":
        return "controlled" if "-i" in lowered else "basic"

    if name == "dd":
        return "critical" if _DD_BLOCK_DEVICE_PATTERN.search(segment) else "controlled"

    if name in ("chmod", "chown"):
        if "-R" in args_tokens or "--recursive" in args_tokens:
            return "risky" if _mentions_system_path(args_tokens) else "controlled"
        return "controlled"

    if name in ("mkdir", "mv", "cp"):
        return "controlled"

    if name in ("curl", "wget"):
        if "-o" in args_tokens or "--output" in args_tokens or ">" in segment:
            return "controlled"
        return "basic"

    return COMMAND_RISK_MAP.get(name)


def infer_risk_from_command(command: str) -> str | None:
    """Infiere el nivel de riesgo de un comando shell (None si no se puede)."""
    if not command or not isinstance(command, str):
        return None
    text = command.strip()
    if not text:
        return None

    level: str | None = None

    def _raise(new_level: str | None) -> None:
        nonlocal level
        if not new_level:
            return
        if level is None or RISK_LEVEL_ORDER.get(new_level, 0) > RISK_LEVEL_ORDER.get(level, 0):
            level = new_level

    if _CRITICAL_TOOL_PATTERN.search(text):
        _raise("critical")
    if _DD_BLOCK_DEVICE_PATTERN.search(text):
        _raise("critical")
    if _PIPE_TO_SHELL_PATTERN.search(text):
        _raise("risky")

    for segment in _split_command_segments(text):
        name, args = _segment_head(segment)
        if not name:
            continue
        _raise(_command_risk(name, args, segment))

    target = _redirect_target(text)
    if target:
        if any(target.startswith(path) for path in CRITICAL_TARGET_FILES):
            _raise("critical")
        elif any(target == prefix or target.startswith(prefix + "/") for prefix in SYSTEM_PATH_PREFIXES):
            _raise("controlled")

    return level



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


def _declared_step_risk_level(step: dict) -> str:
    """Riesgo declarado por el agente para un paso (sin inferencia por comando)."""
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


def resolve_step_risk_level(step: dict) -> str:
    """Resuelve el riesgo efectivo de un paso del plan.

    Se prioriza el risk_level declarado por el agente (Victor azure u
    on-premise). Si el paso declara que afecta algo importante (por ejemplo
    `impact: critical` o `important: true`) se sube al nivel minimo indicado.
    Si no declara nada, se deriva del action_type.

    Como el risk_level declarado puede mentir (un paso "basic" que ejecuta
    `rm -rf /`), la inferencia por comando actua como piso: el riesgo final es
    max(declarado, inferido).
    """
    declared = _declared_step_risk_level(step)
    inferred = infer_risk_from_command(step.get("command") or "")
    if inferred and RISK_LEVEL_ORDER.get(inferred, 0) > RISK_LEVEL_ORDER.get(declared, 0):
        return inferred
    return declared


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
