"""No sleeps, signals or fabricated zeros: observational capture contract."""
from unittest.mock import patch
import pytest
from xoc_mcp_lab_l6 import observability as obs

IDENTITY = dict(pid=42, ppid=11, start_ticks=123, state='S')
METRICS = dict(pid=42, VmRSS=4096, VmHWM=8192, cpu_ticks=0, Threads=2, tasks=2, fds=7)

@pytest.mark.parametrize('point', obs.POINTS)
def test_deterministic_points_measure_exact_owned_process(point):
    with patch.object(obs, 'identity', return_value=IDENTITY), patch.object(obs, 'process_resources', return_value=METRICS) as read:
        row = obs.capture(42, 3, 11, IDENTITY, point)
    read.assert_called_once_with(42)
    assert row['process_alive'] and row['ownership_verified']
    assert row['generation'] == 3 and row['server'] == METRICS
    assert row['measurement_point'] == point and row['unavailable_reasons'] == {}

@pytest.mark.parametrize('observed', [None, dict(IDENTITY, ppid=9), dict(IDENTITY, start_ticks=124), dict(IDENTITY, state='Z')])
def test_absent_recycled_or_wrong_owner_is_not_measured(observed):
    with patch.object(obs, 'identity', return_value=observed), patch.object(obs, 'process_resources') as read:
        row = obs.capture(42, 1, 11, IDENTITY, 'after_ready')
    read.assert_not_called()
    assert not row['process_alive']
    assert all(row['server'][key] is None for key in obs.FIELDS)
    assert row['unavailable_reasons']

def test_exit_during_sample_does_not_credit_old_metrics():
    with patch.object(obs, 'identity', side_effect=[IDENTITY, None]), patch.object(obs, 'process_resources', return_value=METRICS):
        row = obs.capture(42, 1, 11, IDENTITY, 'before_retirement')
    assert not row['identity_stable'] and row['server']['VmRSS'] is None

def test_unavailable_source_is_null_with_reason_not_zero():
    with patch.object(obs, 'identity', return_value=IDENTITY), patch.object(obs, 'process_resources', return_value={'pid':42}):
        row = obs.capture(42, 1, 11, IDENTITY, 'after_ready')
    assert row['process_alive'] and row['server']['fds'] is None
    assert row['unavailable_reasons']['fds'] == 'proc_source_unavailable'

def test_unknown_point_rejected():
    with pytest.raises(ValueError):
        obs.capture(42, 1, 11, IDENTITY, 'invented')
