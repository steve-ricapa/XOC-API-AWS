"""LAB_ONLY bounded, metadata-only private control protocol (stdlib only).

The channel is not an authentication or business-result transport. It accepts
only the finite schemas below, never pickle, scopes, assertions or arguments.
One owning thread uses each endpoint; socket I/O is always nonblocking.
"""
from __future__ import annotations

from collections import deque
import json
import math
import re
import socket
import time

MAX_MESSAGE_BYTES = 4096
MAX_BACKLOG = 8
MAX_BATCH = 16
STATES = frozenset(("STARTING", "READY", "DEGRADED", "DRAINING", "RECOVERING", "UNAVAILABLE"))
TEST_ACTIONS = frozenset(("loop_block", "crash", "noack", "observer_block", "observer_unblock",
                          "close_control", "pause_lease", "recover", "status", "stale_snapshot"))
_SAFE = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")


class ControlProtocolError(ValueError):
    """A structural protocol failure; the peer must be treated as unknown."""


def _integer(value, minimum=0, maximum=2**53 - 1):
    return type(value) is int and minimum <= value <= maximum


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _safe(value):
    return isinstance(value, str) and _SAFE.fullmatch(value) is not None


def _jobs(value):
    if not isinstance(value, list) or len(value) > 4:
        return False
    keys = {"generation", "internal_admission_id", "admitted_at", "worker_started_at",
            "provider_entered_at", "provider_finished_at", "callable_finished_at", "context_reset_at",
            "future_done_at", "slot_released_at", "awaiter_abandoned_at", "provider_read_count"}
    ids = set()
    for job in value:
        if not isinstance(job, dict) or not {"internal_admission_id", "admitted_at"} <= job.keys() or job.keys() - keys:
            return False
        ident = job["internal_admission_id"]
        if not (_safe(ident) or _integer(ident, 1)) or ident in ids:
            return False
        ids.add(ident)
        if not _number(job["admitted_at"]):
            return False
        for key, item in job.items():
            if key == "internal_admission_id":
                continue
            if key == "generation" and not _integer(item, 1):
                return False
            if key == "provider_read_count" and not _integer(item, 0, 40):
                return False
            if key.endswith("_at") and item is not None and not _number(item):
                return False
    return True


_FIELDS = {
    "lease_until": _number, "monotonic": _number, "heartbeat": _number,
    "pid": lambda v: _integer(v, 1), "child_pid": lambda v: v is None or _integer(v, 1),
    "child_generation": lambda v: _integer(v), "attempt": lambda v: _integer(v, 0, 2),
    "integrity_ok": lambda v: type(v) is bool, "listener_bound": lambda v: type(v) is bool,
    "fixtures_ok": lambda v: type(v) is bool, "ok": lambda v: type(v) is bool,
    "residual": lambda v: type(v) is bool, "acknowledged": lambda v: type(v) is bool,
    "clock_verified": lambda v: type(v) is bool, "reaped": lambda v: type(v) is bool,
    "process_absent": lambda v: type(v) is bool, "simulated": lambda v: type(v) is bool,
    "gate_state": lambda v: v in ("CLOSED", "OPEN", "UNKNOWN"),
    "state": lambda v: v in STATES,
    "occupancy": lambda v: v is None or _integer(v, 0, 4),
    "abandoned": lambda v: _integer(v, 0, 4), "jobs": _jobs,
    "command": lambda v: v in ("close", "open", "test", "stop", "probe", "lease"),
    "command_seq": lambda v: _integer(v, 1), "action": lambda v: v in TEST_ACTIONS,
    "duration": lambda v: _number(v) and v <= 30,
    "safe_reason": _safe, "reason": _safe, "phase": _safe,
    "started_at": _number, "finished_at": _number, "slot_released_at": _number,
    "ready_at": _number, "decision_at": _number, "fault_at": _number,
    "elapsed": _number, "budget": _number, "last_lease_until": _number,
    "returncode": lambda v: v is None or (type(v) is int and -255 <= v <= 255),
    "dropped": lambda v: _integer(v), "rejected": lambda v: _integer(v),
}

# Required and optional fields per type. No catch-all payload/dict is permitted.
_SCHEMAS = {
    "lease": ({"lease_until"}, set()),
    "close": (set(), {"reason", "safe_reason"}),
    "probe": (set(), set()),
    "snapshot_request": (set(), set()),
    "open": ({"lease_until"}, set()),
    "stop": (set(), set()),
    "test": ({"action"}, {"duration"}),
    "hello": ({"monotonic", "pid", "integrity_ok", "listener_bound", "fixtures_ok"}, set()),
    "heartbeat": ({"heartbeat", "gate_state", "occupancy", "jobs"}, {"abandoned", "lease_until", "safe_reason"}),
    "ack": ({"command", "command_seq", "gate_state"}, {"occupancy", "action", "child_generation"}),
    "probe_result": ({"ok", "occupancy", "safe_reason"}, {"started_at", "finished_at", "slot_released_at"}),
    "fault": ({"safe_reason"}, {"child_generation", "phase"}),
    "fail_closed": ({"safe_reason"}, set()),
    "status": (set(), {"state", "child_generation", "attempt", "child_pid", "occupancy", "jobs",
                           "gate_state", "lease_until", "heartbeat", "safe_reason", "residual", "dropped", "rejected"}),
    "snapshot": ({"child_generation", "heartbeat", "gate_state", "occupancy", "jobs"}, {"lease_until", "abandoned", "safe_reason"}),
    "state": ({"state", "child_generation", "attempt", "safe_reason"}, {"child_pid", "residual"}),
    "ready": ({"state", "child_generation", "attempt", "child_pid", "ready_at", "clock_verified"}, {"elapsed"}),
    "phase": ({"phase", "child_generation", "attempt", "started_at", "finished_at", "elapsed", "budget", "ok"}, {"safe_reason", "acknowledged", "simulated"}),
    "incident": ({"child_generation", "attempt", "safe_reason", "decision_at", "fault_at", "last_lease_until"}, {"occupancy", "jobs"}),
    "retired": ({"child_generation", "child_pid", "returncode", "reaped", "process_absent", "residual", "finished_at"}, {"safe_reason", "simulated"}),
    "stopped": ({"state", "child_generation", "attempt", "residual"}, {"child_pid", "returncode", "dropped", "rejected"}),
}


def validate_message(message):
    """Validate exact safe schemas, returning the original metadata dictionary."""
    if not isinstance(message, dict):
        raise ControlProtocolError("LAB_CONTROL_OBJECT_REQUIRED")
    common = {"kind", "generation", "seq", "sent_at"}
    if not common <= message.keys() or not isinstance(message.get("kind"), str) or message["kind"] not in _SCHEMAS:
        raise ControlProtocolError("LAB_CONTROL_KIND_INVALID")
    if not _integer(message["generation"], 1) or not _integer(message["seq"], 1) or not _number(message["sent_at"]):
        raise ControlProtocolError("LAB_CONTROL_ENVELOPE_INVALID")
    required, optional = _SCHEMAS[message["kind"]]
    if not required <= message.keys() or message.keys() - common - required - optional:
        raise ControlProtocolError("LAB_CONTROL_FIELDS_INVALID")
    for key in message.keys() - common:
        try:
            valid = _FIELDS[key](message[key])
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise ControlProtocolError("LAB_CONTROL_FIELD_INVALID")
    if message["kind"] in ("heartbeat", "snapshot") and message["occupancy"] != len(message["jobs"]):
        raise ControlProtocolError("LAB_CONTROL_OCCUPANCY_MISMATCH")
    return message


def encode_message(message):
    validate_message(message)
    data = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii") + b"\n"
    if len(data) > MAX_MESSAGE_BYTES:
        raise ControlProtocolError("LAB_CONTROL_MESSAGE_TOO_LARGE")
    return data


def _unique_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ControlProtocolError("LAB_CONTROL_DUPLICATE_KEY")
        result[key] = value
    return result


def decode_message(data):
    if not isinstance(data, bytes) or len(data) > MAX_MESSAGE_BYTES or not data.endswith(b"\n"):
        raise ControlProtocolError("LAB_CONTROL_FRAME_INVALID")
    try:
        message = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(ControlProtocolError("LAB_CONTROL_NONFINITE")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ControlProtocolError("LAB_CONTROL_JSON_INVALID") from error
    return validate_message(message)


class ControlChannel:
    """Fixed eight-frame output backlog, bounded input work and strict sequencing."""
    def __init__(self, sock, generation):
        if not _integer(generation, 1):
            raise ValueError("LAB_CONTROL_GENERATION_INVALID")
        self.socket, self.generation = sock, generation
        sock.setblocking(False)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, MAX_MESSAGE_BYTES * 2)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, MAX_MESSAGE_BYTES * 2)
        self._output = deque()
        self._input = bytearray()
        self.last_sent_seq = self.last_received_seq = 0
        self.closed = False
        self.rejected = self.dropped = 0

    @property
    def backlog(self):
        return len(self._output)

    def send(self, kind, **fields):
        if self.closed:
            return None
        self.flush()
        if len(self._output) >= MAX_BACKLOG:
            self.dropped += 1
            return None
        seq = self.last_sent_seq + 1
        data = encode_message(dict(kind=kind, generation=self.generation, seq=seq,
                                   sent_at=time.monotonic(), **fields))
        self.last_sent_seq = seq
        self._output.append(data)
        self.flush()
        return None if self.closed else seq

    def flush(self):
        for _ in range(MAX_BACKLOG):
            if self.closed or not self._output:
                break
            try:
                count = self.socket.send(self._output[0])
                if count == 0:
                    self.close()
                    break
                if count == len(self._output[0]):
                    self._output.popleft()
                else:
                    self._output[0] = self._output[0][count:]
                    break
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                self.close()
                break

    def send_test_message(self, message):
        """Queue one typed transit-fault frame; caller must require LAB fault mode.

        This deliberately does not mutate the normal sender sequence. It is
        used solely to simulate an already emitted, delayed old-generation
        frame on the real inherited channel, not a surviving retired process.
        """
        data = encode_message(message)
        if self.closed:
            return False
        self.flush()
        if len(self._output) >= MAX_BACKLOG:
            self.dropped += 1
            return False
        self._output.append(data)
        self.flush()
        return not self.closed

    def receive(self):
        if self.closed:
            return []
        self.flush()
        messages = []
        for _ in range(MAX_BATCH):
            split = self._input.find(b"\n")
            if split < 0:
                if self.closed:
                    break
                try:
                    data = self.socket.recv(MAX_MESSAGE_BYTES)
                except (BlockingIOError, InterruptedError):
                    break
                except OSError:
                    self.close()
                    break
                if not data:
                    self.close()
                    break
                self._input.extend(data)
                split = self._input.find(b"\n")
                if split < 0:
                    if len(self._input) >= MAX_MESSAGE_BYTES:
                        self.close()
                        raise ControlProtocolError("LAB_CONTROL_MESSAGE_TOO_LARGE")
                    continue
            data = bytes(self._input[:split + 1])
            del self._input[:split + 1]
            try:
                message = decode_message(data)
            except ControlProtocolError:
                self.rejected += 1
                self.close()
                raise
            if message["generation"] != self.generation or message["seq"] <= self.last_received_seq:
                self.rejected += 1
                continue
            self.last_received_seq = message["seq"]
            messages.append(message)
        return messages

    def close(self):
        if not self.closed:
            self.closed = True
            self.socket.close()
            self._output.clear()
            self._input.clear()
