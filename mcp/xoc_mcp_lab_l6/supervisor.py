"""LAB_ONLY independent owner of one persistent MCP server, stdlib only.

No gateway imports, identities, business arguments, results, log-file polling,
replay or per-query processes exist here. The inherited harness channel is a
separate endpoint from the inherited server channel. Diagnostics are bounded
in-memory metadata and nonblocking IPC; no diagnostic filesystem write can
stall the detector or lease scheduler.
"""
from __future__ import annotations

import argparse
from collections import deque
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

from .control import ControlChannel, ControlProtocolError

CHECK_INTERVAL = 0.25
LEASE_SECONDS = 1.0
HEARTBEAT_SECONDS = 2.0
WORK_SECONDS = 5.0
# Spent INSIDE the existing 5.25-second decision objective, never added to it.
WORK_CONFIRMATION_SECONDS = 0.20
MAX_RECOVERIES = 2
PHASE_BUDGETS = {"close_ack": 0.25, "drain": 1.0, "terminate": 0.65,
                 "kill_reap": 0.35, "startup": 4.0, "probe": 3.5,
                 "open": 0.25, "recovery": 12.0}
SERVER_PROFILES = frozenset(("none", "mixed", "probe_denied", "probe_timeout",
                             "integrity_fail", "startup_fail", "listener_fail", "startup_hold"))
SUPERVISOR_PROFILES = frozenset(("reap_fail", "process_fail", "channel_fail"))
FAULT_PROFILES = SERVER_PROFILES | SUPERVISOR_PROFILES | frozenset(
    "replacement_" + item for item in SERVER_PROFILES | SUPERVISOR_PROFILES)


def _process_token(pid):
    """Linux process start ticks, disambiguating absence from PID reuse."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        return stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _own_children(pid):
    """Server architecture has threads, never process children. Check all tasks."""
    found = set()
    try:
        for task in Path(f"/proc/{pid}/task").iterdir():
            try:
                found.update(int(p) for p in (task / "children").read_text(encoding="ascii").split())
            except OSError:
                pass
    except OSError:
        pass
    return found


class Supervisor:
    """Synchronous state machine intended to run in its own OS process.

    ``start``/``tick``/``shutdown`` are useful for bounded lifecycle tests;
    production of this local experiment uses ``python -I -B -m ...supervisor``.
    Automatic recovery attempts belong to this owner and never reset at READY.
    """
    def __init__(self, port, events_file, fault_profile="none", parent_control=None,
                 python_executable=None):
        if type(port) is not int or not 1024 <= port <= 65535 or fault_profile not in FAULT_PROFILES:
            raise ValueError("LAB_SUPERVISOR_CONFIGURATION_INVALID")
        self.port = port
        self.events_file = Path(events_file)
        if not self.events_file.is_absolute() or not self.events_file.parent.is_dir():
            raise ValueError("LAB_NEW_ABSOLUTE_OUTPUT_PATH_REQUIRED")
        self.fault_profile = fault_profile
        self.parent = parent_control
        self.python = python_executable or sys.executable
        self.state = "STARTING"
        self.generation = self.attempts = 0
        self.child = self.control = None
        self.child_token = None
        self.events = deque(maxlen=2048)
        self.events_dropped = 0
        self.last_heartbeat = self.last_lease_until = 0.0
        self.last_snapshot = None
        self.hello = self.probe_result = None
        self.acks = {}
        self.safe_reason = "initial_startup"
        self.residual = self.clock_verified = False
        self._renew = False
        self._last_renewed = self._last_check = 0.0
        self._pause_lease_until = 0.0
        self._requested_stop = False
        self._requested_failure = None
        self._control_fault = None
        self._test_recover = False
        self._in_retirement = False
        self._recovering = False
        self._stopped = False
        self._spawned_at = 0.0
        self._open_ack_at = 0.0
        self._recovery_deadline = None
        self._work_confirmation = None

    def _emit(self, kind, **fields):
        event = dict(kind=kind, sent_at=time.monotonic(), **fields)
        if len(self.events) == self.events.maxlen:
            self.events_dropped += 1
        self.events.append(event)
        if self.parent is not None and not self.parent.closed:
            self.parent.send(kind, **fields)

    def _set_state(self, state, safe_reason):
        self.state, self.safe_reason = state, safe_reason
        self._emit("state", state=state, child_generation=self.generation,
                   attempt=self.attempts, safe_reason=safe_reason,
                   child_pid=self.child.pid if self.child is not None else None,
                   residual=self.residual)

    def status(self):
        snapshot = self.last_snapshot or {}
        return dict(state=self.state, child_generation=self.generation, attempt=self.attempts,
                    child_pid=self.child.pid if self.child is not None else None,
                    occupancy=snapshot.get("occupancy"), jobs=snapshot.get("jobs", []),
                    gate_state=snapshot.get("gate_state", "UNKNOWN"),
                    lease_until=self.last_lease_until, heartbeat=self.last_heartbeat,
                    safe_reason=self.safe_reason, residual=self.residual,
                    dropped=self.events_dropped + (self.parent.dropped if self.parent else 0),
                    rejected=self.control.rejected if self.control else 0)

    def _phase(self, phase, started, budget=None, success=True, require_success=True, **extra):
        finished = time.monotonic()
        allowed = PHASE_BUDGETS[phase] if budget is None else budget
        within = finished - started <= allowed
        self._emit("phase", phase=phase, child_generation=self.generation, attempt=self.attempts,
                   started_at=started, finished_at=finished, elapsed=finished - started,
                   budget=allowed, ok=success and within, **extra)
        # A missing close ACK or cooperative exit has an explicitly permitted
        # forced-retirement branch. Its TIME budget remains independently strict.
        return within and (success or not require_success)

    def _profile(self, generation=None):
        profile = self.fault_profile
        if profile.startswith("replacement_"):
            return profile.removeprefix("replacement_") if (generation or self.generation) > 1 else "mixed"
        return profile

    def _send(self, kind, **fields):
        if self.control is None or self.control.closed:
            return None
        return self.control.send(kind, **fields)

    def _receive_child(self):
        if self.control is None or self.control.closed:
            return
        try:
            messages = self.control.receive()
        except ControlProtocolError:
            self._control_fault = "control_invalid"
            return
        now = time.monotonic()
        for message in messages:
            kind = message["kind"]
            if message["sent_at"] > now + 0.05 or message["sent_at"] < self._spawned_at - 0.05:
                self._control_fault = "clock_domain_invalid"
                continue
            if kind == "hello":
                if (self.hello is not None or self.child is None or message["pid"] != self.child.pid
                        or not self._spawned_at - 0.05 <= message["monotonic"] <= now + 0.05):
                    self._control_fault = "startup_control_invalid"
                    continue
                self.hello = message
                # Bracket child's monotonic observation by parent's spawn/receipt.
                self.clock_verified = True
            elif kind == "heartbeat":
                stamp = message["heartbeat"]
                if stamp <= self.last_heartbeat or stamp > now + 0.05 or stamp < self._spawned_at - 0.05:
                    self._control_fault = "heartbeat_clock_invalid"
                    continue
                if any(job.get("generation", self.generation) != self.generation
                       or job["admitted_at"] > stamp + 0.05 for job in message["jobs"]):
                    self._control_fault = "job_snapshot_invalid"
                    continue
                self.last_heartbeat, self.last_snapshot = stamp, message
                payload = {key: message[key] for key in ("heartbeat", "gate_state", "occupancy", "jobs",
                           "lease_until", "abandoned", "safe_reason") if key in message}
                self._emit("snapshot", child_generation=self.generation, **payload)
            elif kind == "ack":
                # A bounded map only retains recent command acknowledgements.
                self.acks[(message["command"], message["command_seq"])] = message
                while len(self.acks) > 8:
                    self.acks.pop(next(iter(self.acks)))
                if message["command"] == "test":
                    payload = {key: message[key] for key in ("command", "command_seq", "gate_state", "occupancy", "action") if key in message}
                    self._emit("ack", child_generation=self.generation, **payload)
            elif kind == "probe_result":
                if self.probe_result is not None:
                    self._control_fault = "duplicate_probe_result"
                else:
                    self.probe_result = message
            elif kind == "fault":
                self._control_fault = message["safe_reason"]
                self._emit("fault", child_generation=self.generation, safe_reason=message["safe_reason"])
            else:
                self._control_fault = "unexpected_child_message"

    def _receive_parent(self):
        if self.parent is None:
            return
        if self.parent.closed:
            self._requested_stop = True
            return
        try:
            messages = self.parent.receive()
        except ControlProtocolError:
            self._requested_failure = "harness_control_invalid"
            self._requested_stop = True
            return
        for message in messages:
            if message["kind"] == "status":
                self._emit("status", **self.status())
            elif message["kind"] == "stop":
                self._requested_stop = True
            elif message["kind"] == "fail_closed":
                self._requested_failure = message["safe_reason"]
            elif message["kind"] == "test" and self.fault_profile != "none":
                action = message["action"]
                if action == "close_control":
                    if self.control is not None:
                        self.control.close()
                elif action == "pause_lease":
                    self._pause_lease_until = time.monotonic() + message.get("duration", 2.0)
                elif action == "recover":
                    self._test_recover = True
                elif action == "status":
                    self._emit("status", **self.status())
                else:
                    self._send("test", **{k: message[k] for k in ("action", "duration") if k in message})
            else:
                self._requested_failure = "harness_command_invalid"

    def _pump(self):
        self._receive_parent()
        self._receive_child()
        now = time.monotonic()
        if (self._renew and not self._requested_stop and self._requested_failure is None
                and now >= self._pause_lease_until and now - self._last_renewed >= CHECK_INTERVAL):
            lease_until = now + LEASE_SECONDS
            if self._send("lease", lease_until=lease_until) is not None:
                self._last_renewed = now
                self.last_lease_until = lease_until
            else:
                self._control_fault = "lease_send_failed"
        if self.parent is not None:
            self.parent.flush()

    def _wait(self, predicate, deadline, *, abort=True):
        while True:
            self._pump()
            now = time.monotonic()
            if now > deadline:
                return False
            if predicate():
                return True
            if abort and (self._requested_stop or self._requested_failure is not None or self._control_fault):
                return False
            time.sleep(min(0.005, max(0, deadline - now)))

    def _startup(self):
        started = time.monotonic()
        if self.child is not None:
            raise RuntimeError("LAB_REAP_REQUIRED_BEFORE_SPAWN")
        self.generation += 1
        self.hello = self.probe_result = self.last_snapshot = None
        self.acks.clear()
        self.last_heartbeat = self.last_lease_until = 0.0
        self._control_fault = None
        self.clock_verified = False
        self._open_ack_at = 0.0
        self._work_confirmation = None
        self._set_state("STARTING", "startup")
        profile = self._profile()
        parent_sock = child_sock = None
        try:
            if profile == "channel_fail":
                raise OSError("LAB_SIMULATED_CHANNEL_FAILURE")
            parent_sock, child_sock = socket.socketpair()
            self.control = ControlChannel(parent_sock, self.generation)
            events = self.events_file.with_name(f"{self.events_file.stem}.g{self.generation}{self.events_file.suffix}")
            if events.exists() or events.is_symlink():
                raise ValueError("LAB_NEW_OUTPUT_REQUIRED")
            server_profile = profile if profile in SERVER_PROFILES else "mixed"
            argv = [self.python, "-I", "-B", "-m", "xoc_mcp_lab_l6.server", "--control-fd",
                    str(child_sock.fileno()), "--generation", str(self.generation), "--port", str(self.port),
                    "--events-file", str(events), "--fault-profile", server_profile]
            if profile == "process_fail":
                raise OSError("LAB_SIMULATED_PROCESS_FAILURE")
            self._spawned_at = time.monotonic()
            self.child = subprocess.Popen(argv, pass_fds=(child_sock.fileno(),), stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
            self.child_token = _process_token(self.child.pid)
            self._renew = True
            self._last_renewed = 0.0
        except (OSError, ValueError):
            self._renew = False
            self._control_fault = "spawn_or_channel_failed"
            if self.control is not None:
                self.control.close()
            elif parent_sock is not None:
                parent_sock.close()
            self._phase("startup", started, success=False, safe_reason="spawn_or_channel_failed", simulated=profile in SUPERVISOR_PROFILES)
            return False
        finally:
            if child_sock is not None:
                child_sock.close()

        def started_safely():
            if self.child.poll() is not None:
                self._control_fault = "child_exited_startup"
                return False
            return bool(self.hello is not None and self.last_heartbeat and self.clock_verified
                        and all(self.hello[key] for key in ("integrity_ok", "listener_bound", "fixtures_ok")))

        ok = self._wait(started_safely, started + PHASE_BUDGETS["startup"])
        if not self._phase("startup", started, success=ok):
            return False
        probe_started = time.monotonic()
        if self._send("probe") is None:
            self._phase("probe", probe_started, success=False)
            return False
        ok = self._wait(lambda: self.probe_result is not None, probe_started + PHASE_BUDGETS["probe"])
        ok = bool(ok and self.probe_result["ok"] and self.probe_result["occupancy"] == 0)
        if not self._phase("probe", probe_started, success=ok):
            return False
        opened_at = time.monotonic()
        lease_until = opened_at + LEASE_SECONDS
        self.last_lease_until = lease_until
        seq = self._send("open", lease_until=lease_until)
        ok = bool(seq is not None and self._wait(lambda: ("open", seq) in self.acks,
                                                 opened_at + PHASE_BUDGETS["open"]))
        if ok:
            ack = self.acks[("open", seq)]
            ok = ack["gate_state"] == "OPEN" and ack.get("occupancy", 0) == 0
            self._open_ack_at = ack["sent_at"]
        if not self._phase("open", opened_at, success=ok):
            return False
        if self._recovery_deadline is not None and time.monotonic() > self._recovery_deadline:
            self._control_fault = "recovery_budget_exceeded"
            return False
        self._set_state("READY", "verified_probe_and_open_ack")
        ready_at = time.monotonic()
        self._emit("ready", state="READY", child_generation=self.generation, attempt=self.attempts,
                   child_pid=self.child.pid, ready_at=ready_at, clock_verified=self.clock_verified,
                   elapsed=ready_at - started)
        return True

    def _retire(self, *, drain=True, simulate=True, cleanup=False):
        self._renew = False
        if self.child is None:
            if self.control is not None:
                self.control.close()
            return True
        process = self.child  # Exact owned Popen handle, never a name/PID search.
        profile = self._profile()
        simulated_reap = simulate and profile == "reap_fail"
        self._in_retirement = True
        close_started = time.monotonic()
        seq = self._send("close", safe_reason=self.safe_reason)
        acknowledged = False
        if seq is not None and process.poll() is None:
            acknowledged = self._wait(lambda: ("close", seq) in self.acks,
                                      close_started + PHASE_BUDGETS["close_ack"] - 0.001, abort=False)
            acknowledged = bool(acknowledged and self.acks[("close", seq)]["gate_state"] == "CLOSED")
        budgets_ok = self._phase("close_ack", close_started, success=acknowledged,
                    require_success=False, acknowledged=acknowledged,
                    safe_reason="closed_ack" if acknowledged else "close_unconfirmed")
        if acknowledged and drain and process.poll() is None:
            self._set_state("DRAINING", "close_acknowledged")
            drain_started = time.monotonic()
            ack = self.acks[("close", seq)]
            if ack.get("occupancy") != 0:
                self._wait(lambda: process.poll() is not None or bool(self.last_snapshot
                           and self.last_snapshot["sent_at"] >= ack["sent_at"]
                           and self.last_snapshot["gate_state"] == "CLOSED"
                           and self.last_snapshot["occupancy"] == 0),
                           drain_started + PHASE_BUDGETS["drain"] - 0.001, abort=False)
            budgets_ok = self._phase("drain", drain_started) and budgets_ok
        self._set_state("RECOVERING", "cleanup_retirement" if cleanup else "owned_child_retirement")
        descendants = _own_children(process.pid)
        terminate_started = time.monotonic()
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        terminated = self._wait(lambda: process.poll() is not None,
                                terminate_started + PHASE_BUDGETS["terminate"] - 0.001, abort=False)
        budgets_ok = self._phase("terminate", terminate_started, success=terminated,
                    require_success=False, safe_reason="terminated" if terminated else "kill_required") and budgets_ok
        kill_started = time.monotonic()
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        reaped = self._wait(lambda: process.poll() is not None,
                           kill_started + PHASE_BUDGETS["kill_reap"] - 0.001, abort=False)
        if reaped:
            try:
                process.wait(timeout=0)
            except subprocess.TimeoutExpired:
                reaped = False
        current_token = _process_token(process.pid)
        absent = reaped and (current_token is None or (self.child_token is not None and current_token != self.child_token))
        # This fault withholds accreditation, not a claim of uninterruptible OS.
        accredited = reaped and absent and not descendants and not simulated_reap
        within = self._phase("kill_reap", kill_started, success=accredited, require_success=False, simulated=simulated_reap,
                             safe_reason="simulated_reap_unconfirmed" if simulated_reap else "reap_checked")
        self.residual = not accredited
        self._emit("retired", child_generation=self.generation, child_pid=process.pid,
                   returncode=process.returncode, reaped=reaped, process_absent=absent,
                   residual=self.residual, finished_at=time.monotonic(), simulated=simulated_reap,
                   safe_reason="simulated_reap_unconfirmed" if simulated_reap else "termination_reap")
        if self.control is not None:
            self.control.close()
        if accredited:
            self.child = None
            self.child_token = None
            # Old slots retire with their generation, never become new capacity.
            self.last_snapshot = None
            self.last_heartbeat = 0.0
            self._work_confirmation = None
        self._in_retirement = False
        if not accredited:
            self._control_fault = "reap_not_accredited"
        if not budgets_ok or not within:
            self._control_fault = "retirement_phase_budget_exceeded"
        return accredited and within and budgets_ok

    def _unavailable(self, safe_reason, *, retire=True):
        self._renew = False
        if retire and self.child is not None:
            self.safe_reason = safe_reason
            self._retire(drain=False, cleanup=True)
        self._set_state("UNAVAILABLE", safe_reason)

    def start(self):
        if os.name != "posix" or not Path("/proc/self/stat").is_file():
            self._set_state("UNAVAILABLE", "linux_process_runtime_required")
            return False
        if self.generation:
            raise RuntimeError("LAB_SUPERVISOR_ALREADY_STARTED")
        if not self._startup():
            self._unavailable(self._control_fault or "startup_or_probe_failed")
            return False
        return True

    def recover(self, safe_reason, fault_at=None):
        if self.state == "UNAVAILABLE" or self._recovering:
            return False
        self._recovering = True
        decision = time.monotonic()
        self._renew = False
        self._recovery_deadline = decision + PHASE_BUDGETS["recovery"]
        replace = self.attempts < MAX_RECOVERIES
        if replace:
            self.attempts += 1
        snapshot = self.last_snapshot or {}
        self._emit("incident", child_generation=self.generation, attempt=self.attempts,
                   safe_reason=safe_reason, decision_at=decision,
                   fault_at=fault_at if fault_at is not None else decision,
                   last_lease_until=self.last_lease_until,
                   occupancy=snapshot.get("occupancy"), jobs=snapshot.get("jobs", []))
        self._set_state("RECOVERING", safe_reason)
        try:
            if not self._retire():
                self._phase("recovery", decision, success=False)
                self._unavailable(self._control_fault or "reap_not_accredited", retire=False)
                return False
            if not replace:
                self._phase("recovery", decision, success=False)
                self._unavailable("automatic_attempt_limit", retire=False)
                return False
            if self._requested_stop or self._requested_failure:
                self._unavailable(self._requested_failure or "explicit_stop", retire=False)
                return False
            if not self._startup():
                self._phase("recovery", decision, success=False)
                self._unavailable(self._control_fault or "replacement_startup_or_probe_failed")
                return False
            if not self._phase("recovery", decision):
                self._unavailable("recovery_budget_exceeded")
                return False
            return True
        finally:
            self._recovering = False
            self._recovery_deadline = None

    def tick(self):
        self._pump()
        if self._requested_failure:
            reason, self._requested_failure = self._requested_failure, None
            self._unavailable(reason)
            return
        if self._requested_stop or self.state not in ("READY", "DEGRADED"):
            return
        now = time.monotonic()
        # Periodic checks do not defer an already known absolute deadline.
        # Otherwise a check just before admitted_at+5 can wait .25 plus loop
        # scheduling before deciding, exceeding the approved 5.25 objective.
        # The existing nonblocking pump cadence is unchanged; so are all limits.
        jobs = self.last_snapshot["jobs"] if self.last_snapshot else ()
        known_deadline_due = (self._work_confirmation is not None
                              or now >= self.last_heartbeat + HEARTBEAT_SECONDS
                              or any(now >= job["admitted_at"] + WORK_SECONDS for job in jobs))
        if now - self._last_check < CHECK_INTERVAL and not known_deadline_due:
            return
        self._last_check = now
        if self.child is None or self.child.poll() is not None:
            self.recover("child_crash", now)
        elif self._control_fault or self.control is None or self.control.closed:
            self.recover(self._control_fault or "control_lost", now)
        elif now - self.last_heartbeat >= HEARTBEAT_SECONDS:
            self.recover("heartbeat_stale", self.last_heartbeat)
        elif self._test_recover:
            self._test_recover = False
            self.recover("trusted_test_incident", now)
        elif self.last_snapshot:
            jobs = self.last_snapshot["jobs"]
            if self._work_confirmation is not None:
                confirmation = self._work_confirmation
                if self.last_snapshot["heartbeat"] >= confirmation["work_deadline"]:
                    # A post-deadline atomic snapshot can establish that the
                    # old unit finished. Missing/old control never establishes it.
                    self._work_confirmation = None
                elif now >= confirmation["confirm_until"]:
                    self._work_confirmation = None
                    self.recover("work_age_control_unconfirmed", confirmation["fault_at"])
                    return
                else:
                    return
            old = [job for job in jobs if now - job["admitted_at"] >= WORK_SECONDS]
            if old:
                oldest = min(old, key=lambda job: job["admitted_at"])
                fault_at = oldest.get("provider_entered_at") or oldest["admitted_at"]
                work_deadline = oldest["admitted_at"] + WORK_SECONDS
                if self.last_snapshot["heartbeat"] >= work_deadline:
                    self.recover("work_age_exceeded", fault_at)
                else:
                    # A unit seen at 4.95s may have genuinely finished at 4.99s.
                    # Ask the existing loop for fresh technical metadata once,
                    # without business, a probe, new authority or a log lookup.
                    self._work_confirmation = dict(work_deadline=work_deadline,
                        confirm_until=work_deadline + WORK_CONFIRMATION_SECONDS,
                        fault_at=fault_at)
                    if (now >= self._work_confirmation["confirm_until"]
                            or self._send("snapshot_request") is None):
                        self._work_confirmation = None
                        self.recover("work_age_control_unconfirmed", fault_at)
            elif (self.last_snapshot["sent_at"] >= self._open_ack_at
                    and self.last_snapshot["gate_state"] != "OPEN"):
                self.recover("public_gate_closed", self.last_snapshot["heartbeat"])
            else:
                desired = "DEGRADED" if self.last_snapshot.get("abandoned", 0) or any(
                    job.get("awaiter_abandoned_at") is not None for job in jobs) else "READY"
                if self.state != desired:
                    self._set_state(desired, "live_awaiter_abandoned" if desired == "DEGRADED" else "work_completed")

    def shutdown(self):
        if self._stopped:
            return
        self._requested_stop = True
        self._renew = False
        self.safe_reason = "explicit_stop"
        # Explicit owner cleanup is separate from simulated/unaccredited recovery.
        if self.child is not None:
            self._retire(drain=False, simulate=False, cleanup=True)
        self._set_state("UNAVAILABLE", "explicit_stop")
        self._emit("stopped", state=self.state, child_generation=self.generation,
                   attempt=self.attempts, residual=self.residual,
                   child_pid=self.child.pid if self.child is not None else None,
                   returncode=self.child.returncode if self.child is not None else 0,
                   dropped=self.events_dropped + (self.parent.dropped if self.parent else 0),
                   rejected=self.control.rejected if self.control else 0)
        if self.parent is not None:
            end = time.monotonic() + 0.05
            while self.parent.backlog and time.monotonic() < end:
                self.parent.flush()
                time.sleep(0.001)
        self._stopped = True

    def run(self):
        try:
            self.start()
            while not self._requested_stop:
                self.tick()
                time.sleep(0.005)
        finally:
            self.shutdown()
        return 2 if self.residual else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="LAB_ONLY finite local MCP instance supervisor")
    parser.add_argument("--control-fd", required=True, type=int, help="inherited private harness endpoint")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--events-file", required=True)
    parser.add_argument("--fault-profile", choices=sorted(FAULT_PROFILES), default="none")
    args = parser.parse_args(argv)
    parent = ControlChannel(socket.socket(fileno=args.control_fd), 1)
    supervisor = Supervisor(args.port, args.events_file, args.fault_profile, parent)

    def stop_signal(_signum, _frame):
        supervisor._requested_stop = True

    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    try:
        return supervisor.run()
    except Exception:
        supervisor.shutdown()
        return 2
    finally:
        parent.close()


if __name__ == "__main__":
    raise SystemExit(main())
