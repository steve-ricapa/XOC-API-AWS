"""Build explicit compensating plans from successful steps, never invent undo."""
import hashlib
import json

from src.shared.risk_config import approval_requirement, resolve_step_risk_level


class RollbackUnavailable(ValueError):
    pass


def build_rollback_plan(plan, step_results):
    """Returns a NEW plan for preview/approval, not authorization to execute it.

    Compensation is supported only when declared on the original approved step.
    Its verification command is mandatory. Partial/ambiguous failures require
    manual assessment rather than assuming that a failed call made no changes.
    """
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list) or not steps or not isinstance(step_results, list):
        raise RollbackUnavailable("Original approved plan and execution results are required")
    by_id = {}
    for step in steps:
        sid = step.get("step_id")
        if not sid or sid in by_id:
            raise RollbackUnavailable("Original steps require unique stable step_id values")
        by_id[sid] = step
    completed, seen = [], set()
    for result in step_results:
        sid = result.get("step_id")
        if sid not in by_id or sid in seen:
            raise RollbackUnavailable("Execution results do not uniquely match the original plan")
        seen.add(sid)
        step = by_id[sid]
        if result.get("status") == "ok" and result.get("success") is True:
            completed.append(step)
        elif resolve_step_risk_level(step) != "basic":
            raise RollbackUnavailable("A mutating step failed or is uncertain; assess partial effects first")
    if any(sid not in seen and resolve_step_risk_level(step) != "basic" for sid, step in by_id.items()):
        raise RollbackUnavailable("Missing results for mutating steps")
    undo = []
    for step in reversed(completed):
        if resolve_step_risk_level(step) == "basic":
            continue
        compensation = step.get("rollback")
        if not isinstance(compensation, dict) or compensation.get("supported") is not True:
            raise RollbackUnavailable("A completed change has no declared compensation")
        command, verification = compensation.get("command"), compensation.get("verification_command")
        if not isinstance(command, str) or not command.strip() or not isinstance(verification, str) or not verification.strip():
            raise RollbackUnavailable("Compensation and verification commands are required")
        for suffix, cmd in (("undo", command), ("verify", verification)):
            undo.append({"step_id": f"rollback-{step['step_id']}-{suffix}", "order": len(undo) + 1,
                         "action": "shell", "command": cmd, "risk_level": compensation.get("risk_level") or "risky",
                         "description": f"Rollback {suffix}: {step.get('description') or step['step_id']}"})
    if not undo:
        raise RollbackUnavailable("No completed reversible changes")
    fingerprint = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    rollback = {"source": "rollback", "original_plan_sha256": fingerprint, "steps": undo,
                "requires_new_approval": True}
    requirement = approval_requirement(rollback)
    rollback["risk_level"] = requirement["max_risk_level"]
    return {"plan": rollback, "requirement": requirement}
