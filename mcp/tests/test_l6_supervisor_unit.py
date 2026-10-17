"""Portable structural/unit checks; these do NOT claim real process recovery.

Installed Linux acceptance separately executes the actual independent processes.
"""
import socket
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from xoc_mcp_lab_l6.control import (ControlChannel, ControlProtocolError, MAX_BACKLOG,
    MAX_MESSAGE_BYTES, decode_message, encode_message, validate_message)
from xoc_mcp_lab_l6.supervisor import PHASE_BUDGETS, WORK_CONFIRMATION_SECONDS, Supervisor


def message(kind="lease", **fields):
    return dict(kind=kind, generation=1, seq=1, sent_at=time.monotonic(), **fields)


def test_C20_control_roundtrip_has_absolute_lease():
    left, right = socket.socketpair()
    sender, receiver = ControlChannel(left, 1), ControlChannel(right, 1)
    try:
        issued = time.monotonic()
        assert sender.send("lease", lease_until=issued + 1) == 1
        received = receiver.receive()[0]
        assert received["lease_until"] == issued + 1
        assert received["seq"] == 1
    finally:
        sender.close()
        receiver.close()


@pytest.mark.parametrize("bad", [dict(assertion="fixture"), dict(arguments={}), dict(result={}),
                                 dict(scope={}), dict(headers={}), dict(claims={})])
def test_C20_control_rejects_authority_and_results(bad):
    with pytest.raises(ControlProtocolError):
        validate_message(message(lease_until=time.monotonic() + 1, **bad))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "1.0"])
def test_C20_control_strict_clock_types(value):
    with pytest.raises(ControlProtocolError):
        encode_message(message(lease_until=value))


def test_C17_old_generation_does_not_advance_sequence():
    left, right = socket.socketpair()
    receiver = ControlChannel(right, 2)
    try:
        stale = message(lease_until=time.monotonic() + 1)
        stale["seq"] = 500
        left.sendall(encode_message(stale))
        assert receiver.receive() == []
        assert receiver.last_received_seq == 0
        fresh = dict(stale, generation=2, seq=1)
        left.sendall(encode_message(fresh))
        assert receiver.receive() == [fresh]
        left.sendall(encode_message(fresh))
        assert receiver.receive() == []
        assert receiver.rejected == 2
    finally:
        left.close()
        receiver.close()


def test_C20_control_backlog_is_fixed_when_peer_does_not_read():
    left, right = socket.socketpair()
    right.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
    channel = ControlChannel(left, 1)
    started = time.monotonic()
    try:
        for _ in range(50000):
            channel.send("lease", lease_until=started + 1)
            if channel.dropped:
                break
        assert channel.backlog <= MAX_BACKLOG
        assert channel.dropped > 0
        assert time.monotonic() - started < 1.0
    finally:
        channel.close()
        right.close()


def test_C20_oversized_frame_closes_channel():
    left, right = socket.socketpair()
    channel = ControlChannel(right, 1)
    try:
        left.sendall(b"x" * MAX_MESSAGE_BYTES)
        with pytest.raises(ControlProtocolError):
            channel.receive()
        assert channel.closed
        assert channel.receive() == []
    finally:
        left.close()
        channel.close()


def test_C20_duplicate_json_keys_rejected():
    with pytest.raises(ControlProtocolError):
        decode_message(b'{"kind":"stop","generation":1,"seq":1,"seq":2,"sent_at":1}\n')


def test_C20_invalid_kind_is_protocol_error():
    with pytest.raises(ControlProtocolError):
        validate_message(message(kind=[]))


def test_C20_snapshot_must_reconcile_and_cannot_exceed_four():
    jobs = [dict(internal_admission_id=index + 1, admitted_at=1.0) for index in range(4)]
    good = message("heartbeat", heartbeat=2.0, occupancy=4, gate_state="OPEN", jobs=jobs)
    assert len(encode_message(good)) < MAX_MESSAGE_BYTES
    with pytest.raises(ControlProtocolError):
        validate_message(dict(good, occupancy=3))
    with pytest.raises(ControlProtocolError):
        validate_message(dict(good, occupancy=4, jobs=jobs + [dict(internal_admission_id=5, admitted_at=1)]))


def test_C20_job_record_cannot_smuggle_result():
    with pytest.raises(ControlProtocolError):
        validate_message(message("heartbeat", heartbeat=2, occupancy=1, gate_state="OPEN",
            jobs=[dict(internal_admission_id=1, admitted_at=1, result="not-metadata")]))


def _ready_supervisor(tmp_path):
    supervisor = Supervisor(18090, tmp_path / "events.jsonl")
    supervisor.state = "READY"
    supervisor.generation = 1
    supervisor._last_check = 0
    supervisor.last_heartbeat = time.monotonic()
    supervisor.child = SimpleNamespace(poll=lambda: None, pid=1)
    supervisor.control = SimpleNamespace(closed=False)
    supervisor._pump = lambda: None
    return supervisor


def test_C04_single_old_job_triggers_even_with_three_free_slots_unit(tmp_path):
    supervisor = _ready_supervisor(tmp_path)
    now = time.monotonic()
    supervisor.last_snapshot = dict(jobs=[dict(internal_admission_id=1,
        admitted_at=now - 5.01, provider_entered_at=now - 4.99)], occupancy=1,
        heartbeat=now, sent_at=now, gate_state="OPEN")
    with patch.object(supervisor, "recover") as recover:
        supervisor.tick()
        recover.assert_called_once_with("work_age_exceeded", now - 4.99)


def test_C18_detector_does_not_need_event_loop_unit(tmp_path):
    supervisor = _ready_supervisor(tmp_path)
    supervisor.last_heartbeat = time.monotonic() - 2.01
    with patch.object(supervisor, "recover") as recover:
        supervisor.tick()
        recover.assert_called_once_with("heartbeat_stale", supervisor.last_heartbeat)


def test_C04_absolute_work_deadline_bypasses_recent_periodic_check_unit(tmp_path):
    supervisor = _ready_supervisor(tmp_path)
    now = time.monotonic()
    supervisor._last_check = now - .001
    supervisor.last_heartbeat = now
    supervisor.last_snapshot = dict(jobs=[dict(internal_admission_id=1,
        admitted_at=now - 5.001, provider_entered_at=now - 5.0)], occupancy=1,
        heartbeat=now, sent_at=now, gate_state="OPEN")
    with patch("xoc_mcp_lab_l6.supervisor.time.monotonic", return_value=now), \
         patch.object(supervisor, "recover") as recover:
        supervisor.tick()
        recover.assert_called_once_with("work_age_exceeded", now - 5.0)


def test_C18_absolute_heartbeat_deadline_bypasses_recent_periodic_check_unit(tmp_path):
    supervisor = _ready_supervisor(tmp_path)
    now = time.monotonic()
    supervisor._last_check = now - .001
    supervisor.last_heartbeat = now - 2.001
    with patch("xoc_mcp_lab_l6.supervisor.time.monotonic", return_value=now), \
         patch.object(supervisor, "recover") as recover:
        supervisor.tick()
        recover.assert_called_once_with("heartbeat_stale", now - 2.001)


def test_C01_no_job_snapshot_does_not_invent_due_work_deadline_unit(tmp_path):
    supervisor = _ready_supervisor(tmp_path)
    now = time.monotonic()
    supervisor._last_check = now - .001
    supervisor.last_heartbeat = now
    supervisor.last_snapshot = None
    with patch("xoc_mcp_lab_l6.supervisor.time.monotonic", return_value=now), \
         patch.object(supervisor, "recover") as recover:
        supervisor.tick()
        recover.assert_not_called()
    assert supervisor._last_check == now - .001


def _stale_deadline_supervisor(tmp_path):
    supervisor = _ready_supervisor(tmp_path)
    now = time.monotonic()
    admitted = now - 5.005
    supervisor._last_check = now - .001
    supervisor.last_heartbeat = admitted + 4.95
    supervisor.last_snapshot = dict(jobs=[dict(internal_admission_id=1,
        admitted_at=admitted, provider_entered_at=admitted + .001)], occupancy=1,
        heartbeat=supervisor.last_heartbeat, sent_at=supervisor.last_heartbeat, gate_state="OPEN")
    return supervisor, now, admitted


def test_C03_finished_before_five_is_not_recovered_from_stale_snapshot_unit(tmp_path):
    supervisor, now, admitted = _stale_deadline_supervisor(tmp_path)
    with patch("xoc_mcp_lab_l6.supervisor.time.monotonic", return_value=now), \
         patch.object(supervisor, "_send", return_value=7) as send, \
         patch.object(supervisor, "recover") as recover:
        supervisor.tick()
        send.assert_called_once_with("snapshot_request")
        recover.assert_not_called()
    # Model an actual 4.99s completion reported by the next post-deadline sample.
    supervisor.last_snapshot.update(jobs=[], occupancy=0, heartbeat=now+.01, sent_at=now+.01)
    supervisor.last_heartbeat = now+.01
    with patch("xoc_mcp_lab_l6.supervisor.time.monotonic", return_value=now+.015), \
         patch.object(supervisor, "recover") as recover:
        supervisor.tick()
        recover.assert_not_called()
    assert supervisor._work_confirmation is None
    assert supervisor.state == "READY"


def test_C04_fresh_snapshot_confirms_retained_old_job_unit(tmp_path):
    supervisor, now, admitted = _stale_deadline_supervisor(tmp_path)
    with patch("xoc_mcp_lab_l6.supervisor.time.monotonic", return_value=now), \
         patch.object(supervisor, "_send", return_value=7):
        supervisor.tick()
    supervisor.last_snapshot.update(heartbeat=now+.01, sent_at=now+.01)
    supervisor.last_heartbeat = now+.01
    with patch("xoc_mcp_lab_l6.supervisor.time.monotonic", return_value=now+.015), \
         patch.object(supervisor, "recover") as recover:
        supervisor.tick()
        recover.assert_called_once_with("work_age_exceeded", admitted+.001)
    assert supervisor._work_confirmation is None


def test_C20_work_confirmation_absence_is_bounded_control_uncertainty_unit(tmp_path):
    supervisor, now, admitted = _stale_deadline_supervisor(tmp_path)
    with patch("xoc_mcp_lab_l6.supervisor.time.monotonic", return_value=now), \
         patch.object(supervisor, "_send", return_value=7):
        supervisor.tick()
    cutoff = admitted + 5 + WORK_CONFIRMATION_SECONDS
    assert supervisor._work_confirmation["confirm_until"] == cutoff
    assert cutoff < admitted + 5.25
    with patch("xoc_mcp_lab_l6.supervisor.time.monotonic", return_value=cutoff+.001), \
         patch.object(supervisor, "recover") as recover:
        supervisor.tick()
        recover.assert_called_once_with("work_age_control_unconfirmed", admitted+.001)
    assert supervisor._work_confirmation is None


def test_C20_old_snapshot_does_not_renew_confirmation_or_request_again_unit(tmp_path):
    supervisor, now, admitted = _stale_deadline_supervisor(tmp_path)
    with patch("xoc_mcp_lab_l6.supervisor.time.monotonic", return_value=now), \
         patch.object(supervisor, "_send", return_value=7):
        supervisor.tick()
    expected = dict(supervisor._work_confirmation)
    supervisor.last_snapshot.update(heartbeat=admitted+4.999, sent_at=now+.01)
    with patch("xoc_mcp_lab_l6.supervisor.time.monotonic", return_value=now+.1), \
         patch.object(supervisor, "_send") as send, \
         patch.object(supervisor, "recover") as recover:
        supervisor.tick()
        recover.assert_not_called()
        send.assert_not_called()
    assert supervisor._work_confirmation == expected


def test_C20_snapshot_request_has_no_payload_or_authority():
    assert decode_message(encode_message(message("snapshot_request")))["kind"] == "snapshot_request"
    with pytest.raises(ControlProtocolError):
        encode_message(message("snapshot_request", arguments={}))


def test_C01_four_young_occupied_is_not_incident_unit(tmp_path):
    supervisor = _ready_supervisor(tmp_path)
    now = time.monotonic()
    supervisor.last_snapshot = dict(jobs=[dict(internal_admission_id=i+1, admitted_at=now-0.5)
        for i in range(4)], occupancy=4, heartbeat=now, sent_at=now, gate_state="OPEN")
    with patch.object(supervisor, "recover") as recover:
        supervisor.tick()
        recover.assert_not_called()
    assert supervisor.state == "READY"


def test_C03_degraded_requires_server_observable_abandonment_unit(tmp_path):
    supervisor = _ready_supervisor(tmp_path)
    now = time.monotonic()
    supervisor.last_snapshot = dict(jobs=[dict(internal_admission_id=1, admitted_at=now-1,
        awaiter_abandoned_at=now-0.1)], occupancy=1, heartbeat=now, sent_at=now,
        gate_state="OPEN", abandoned=1)
    supervisor.tick()
    assert supervisor.state == "DEGRADED"
    supervisor._last_check = 0
    supervisor.last_snapshot.update(jobs=[], occupancy=0, abandoned=0)
    supervisor.tick()
    assert supervisor.state == "READY"


def test_C12_unavailable_never_rearms_unit(tmp_path):
    supervisor = Supervisor(18090, tmp_path / "events.jsonl")
    supervisor.state, supervisor.attempts = "UNAVAILABLE", 2
    assert supervisor.recover("trusted_test_incident") is False
    assert supervisor.attempts == 2 and supervisor.generation == 0


def test_C11_phase_contract_not_relaxed():
    assert PHASE_BUDGETS == dict(close_ack=.25, drain=1., terminate=.65, kill_reap=.35,
                                 startup=4., probe=3.5, open=.25, recovery=12.)


def test_C11_missing_ack_branch_still_enforces_time_budget_unit(tmp_path):
    supervisor = Supervisor(18090, tmp_path / "events.jsonl")
    with patch("xoc_mcp_lab_l6.supervisor.time.monotonic", return_value=10.1):
        assert supervisor._phase("close_ack", 10, success=False, require_success=False)
    with patch("xoc_mcp_lab_l6.supervisor.time.monotonic", return_value=10.3):
        assert not supervisor._phase("close_ack", 10, success=False, require_success=False)
    assert supervisor.events[-1]["elapsed"] > .25


@pytest.mark.parametrize("exceeded_phase", ["close_ack", "drain", "terminate", "kill_reap"])
def test_C11_any_retirement_phase_overrun_blocks_replacement_unit(tmp_path, exceeded_phase):
    """Only decision logic is simulated; this does not accredit OS reap."""
    supervisor = Supervisor(18090, tmp_path / "events.jsonl")
    supervisor.generation = 1
    supervisor.child = SimpleNamespace(pid=12345, poll=lambda: None, terminate=lambda: None,
        kill=lambda: None, wait=lambda timeout: 0, returncode=0)
    supervisor.acks[("close", 1)] = dict(gate_state="CLOSED", occupancy=0, sent_at=0)
    with patch.object(supervisor, "_send", return_value=1), \
         patch.object(supervisor, "_wait", return_value=True), \
         patch.object(supervisor, "_phase", side_effect=lambda name, *a, **k: name != exceeded_phase), \
         patch("xoc_mcp_lab_l6.supervisor._own_children", return_value=set()), \
         patch("xoc_mcp_lab_l6.supervisor._process_token", return_value=None):
        assert not supervisor._retire()
    assert supervisor.child is None  # Cleanup completed despite budget failure.
    assert supervisor._control_fault == "retirement_phase_budget_exceeded"
    assert supervisor.generation == 1
