"""HARNESS ONLY: uncensor one original installed L6 child's startup.

No installed file, PHASE_BUDGETS, predicate, protocol, provider or artifact is
changed. The diagnostic supervisor subclass permits its startup wait/phase up
to 12s. Every READY from this process is diagnostic, NEVER L6 acceptance.
Other phase budgets and the global recovery deadline remain canonical.
"""
import time
import sys
import subprocess

PRODUCT_STARTUP_BUDGET = 4.0
DIAGNOSTIC_OBSERVATION_CAP = 12.0


def observer_class(base, emit, clock=time.monotonic):
    class DiagnosticSupervisor(base):
        def _wait(self, predicate, deadline, abort=True):
            if predicate.__name__ != "started_safely":
                return super()._wait(predicate, deadline, abort=abort)
            start = deadline - PRODUCT_STARTUP_BUDGET
            breached = False
            def observe():
                nonlocal breached
                now = clock()
                if now >= deadline and not breached:
                    breached = True
                    emit("WOULD_HAVE_TIMED_OUT", child_generation=self.generation,
                         deadline_at=deadline, observed_at=now,
                         poll_delay_seconds=now-deadline)
                return predicate()
            result = super()._wait(observe, start + DIAGNOSTIC_OBSERVATION_CAP, abort=abort)
            elapsed = clock()-start
            emit("natural_startup_complete" if result else "natural_startup_incomplete",
                 child_generation=self.generation, elapsed=elapsed,
                 would_have_timed_out_at_4s=elapsed > PRODUCT_STARTUP_BUDGET,
                 product_startup_budget=PRODUCT_STARTUP_BUDGET,
                 diagnostic_observation_cap=DIAGNOSTIC_OBSERVATION_CAP)
            return result

        def _phase(self, phase, started, budget=None, success=True, require_success=True, **extra):
            if phase == "startup":
                budget = DIAGNOSTIC_OBSERVATION_CAP
            return super()._phase(phase, started, budget=budget, success=success,
                                  require_success=require_success, **extra)
    return DiagnosticSupervisor


def install_parent():
    import ft2r_probe as probe
    original = subprocess.Popen
    class DiagnosticPopen(original):
        def __init__(self, args, *pos, **kwargs):
            argv = list(args) if isinstance(args, (list, tuple)) else None
            selected = bool(argv and argv[1:5] == ["-I", "-B", "-m", "xoc_mcp_lab_l6.supervisor"])
            if selected:
                probe.emit("S01_spawn", child_generation=0, child_role="supervisor")
                argv = [argv[0], "-I", "-B", "/ic1/harness/ft2r1_observer.py", *argv[5:]]
            super().__init__(argv if selected else args, *pos, **kwargs)
            if selected:
                probe.emit("S02_observed", child_pid=self.pid, child_generation=0)
    subprocess.Popen = DiagnosticPopen


def main():
    sys.path.insert(0, "/ic1/harness")
    import ft2r_probe as probe
    probe.ROLE = "supervisor"
    probe.GENERATION = 0
    probe.emit("S03_entry", entry_at=probe.ENTRY, nearest="diagnostic_supervisor_bootstrap")
    probe.install_parent()  # Original server, original instrumentation, no override in child.
    probe.imports()
    probe.io_observer()
    from xoc_mcp_lab_l6 import supervisor
    if supervisor.PHASE_BUDGETS["startup"] != PRODUCT_STARTUP_BUDGET:
        raise RuntimeError("FT2R1_BASELINE_BUDGET_DRIFT")
    supervisor.Supervisor = observer_class(supervisor.Supervisor, probe.emit)
    return supervisor.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
