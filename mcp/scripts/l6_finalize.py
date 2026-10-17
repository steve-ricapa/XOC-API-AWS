"""Read-only, fail-closed reduction of ONE frozen L6 LOCAL acceptance run.

No Docker calls, source imports, retries, benchmark selection or foreign writes.
This report-only script is hashed separately from the installed candidate.
All output files must be new. Missing evidence is INCONCLUSO, not PASS.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
import time
import zipfile

LAB = Path(__file__).resolve().parents[1]
L4_IMAGE = 'sha256:129efefeecc7549df84604ed2e532fa524aaab716f6cf145ca9e5522e6ab72b9'
WHEELS = {
    'xoc_tool_gateway_core-0.0.0+lab.ad4ac1a764de-py3-none-any.whl': 'df95ce6b4b9e7531f43cf90e9e43a850f3d38311679afe9516af501ac6182e41',
    'xoc_mcp_lab-0.2.0+lab.cdce81cbc014-py3-none-any.whl': '0f82b903e2b0bbbc5107a6371a2da95c83c0561c76f5b918a1cb1df1798da2ac',
    'xoc_mcp_lab_harness-0.2.0+lab.cdce81cbc014-py3-none-any.whl': 'f41abe59883f1bc9311ed88f8582b1f31639bc2be65e1163412925c17bd2d736',
    'xoc_mcp_lab_l4-0.4.0+lab.27a62d14058a-py3-none-any.whl': '764352dc77a7c467f6c8c3162a4f93e4fcca0e21b7933db0e2a4557669dcfb5d',
}
LOCKS = {
    'requirements-linux-runtime.lock.txt': '0b439be1007d484201613bae015e0ded27c8f38fa3b5338f8a7f41cc674e858b',
    'requirements-linux-test.lock.txt': '4336d71caffdd9aeeb4143a9b3b2bfc30b61409a89c205f5a9c2b50bd3cbaff5',
}
PHASE_BUDGETS = {'close_ack': .25, 'drain': 1., 'terminate': .65,
                 'kill_reap': .35, 'startup': 4., 'probe': 3.5, 'open': .25, 'recovery': 12.}
CASES = {f'C{i:02}' for i in range(1, 25)}
TOOLS = {'xoc.tickets.list', 'xoc.tickets.get', 'xoc.documents.list', 'xoc.notifications.unread_count'}
OUTPUTS = ('acceptance', 'recovery-measurements', 'performance-comparison', 'process-cleanup', 'reproducibility')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def walk(value, prefix=''):
    if isinstance(value, dict):
        yield prefix, value
        for key, item in value.items():
            yield from walk(item, prefix + '/' + str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk(item, prefix + '/' + str(index))


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def distribution(values):
    if not values or not all(finite(v) and v >= 0 for v in values):
        return None
    ordered = sorted(values)
    return dict(n=len(values), minimum=ordered[0], median=statistics.median(ordered),
                p95_nearest_rank=ordered[math.ceil(.95 * len(ordered))-1], maximum=ordered[-1])


class Audit:
    def __init__(self):
        self.gates = []
        self.inputs = {}

    def check(self, name, good, detail=None, *, missing=False):
        self.gates.append(dict(gate=name, outcome='PASS' if good else ('INCONCLUSO' if missing else 'NO-GO'), detail=detail))
        return good

    def read(self, path, *, required=True):
        try:
            if path.is_symlink():
                raise ValueError('linked evidence')
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict) or not data:
                raise ValueError('Evidence must be a nonempty JSON object')
            self.inputs[path.relative_to(LAB).as_posix()] = dict(sha256=sha(path), bytes=path.stat().st_size)
            return data
        except FileNotFoundError:
            if required:
                self.check('required_input:' + path.relative_to(LAB).as_posix(), False, 'Missing evidence', missing=True)
        except (OSError, ValueError) as error:
            self.check('readable_input:' + path.relative_to(LAB).as_posix(), False, type(error).__name__)
        return None

    def outcome(self):
        values = {row['outcome'] for row in self.gates}
        return 'NO-GO' if 'NO-GO' in values else ('INCONCLUSO' if 'INCONCLUSO' in values else 'PASS LOCAL de B')


def controls_ok(value):
    return (value.get('memory') == 536870912 and value.get('nanocpus') == 2000000000
            and value.get('pids_limit') == 128 and value.get('network') == 'none'
            and value.get('user') == '10001:10001' and value.get('readonly') is True
            and value.get('bind_mounts') == [] and value.get('port_bindings') == {}
            and 'ALL' in value.get('cap_drop', [])
            and any(v.startswith('no-new-privileges') for v in value.get('security_opt', []))
            and value.get('tmpfs') == {'/tmp': 'rw,nosuid,nodev,size=128m,mode=1777'})


def source_path(name):
    if (not name.startswith('xoc_mcp_lab_l6/') or '\\' in name or ':' in name
            or any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('Unsafe source manifest path')
    if name.startswith('xoc_mcp_lab_l6/tests/'):
        path = LAB / 'tests' / name.rsplit('/', 1)[1]
    elif name.startswith('xoc_mcp_lab_l6/_build/'):
        tail = name.rsplit('/', 1)[1]
        path = LAB / ('scripts' if tail.endswith('.ps1') else 'packaging') / tail
    else:
        path = LAB / name
    if not path.resolve().is_relative_to(LAB):
        raise ValueError('Source path escaped laboratory')
    return path


def reproducibility(audit, run):
    manifests = [audit.read(run / side / 'artifact-manifest.json') for side in ('build-a', 'build-b')]
    sources = [audit.read(run / side / 'source-manifest.json') for side in ('build-a', 'build-b')]
    image = audit.read(run / 'image.json')
    result = dict(builds={}, frozen_sources=[], installed_artifact_marker=None, image=image)
    for side, manifest, source in zip(('build-a', 'build-b'), manifests, sources):
        if not manifest or not source:
            continue
        directory = run / side
        entries = {p.name: sha(p) for p in directory.iterdir() if p.is_file()}
        result['builds'][side] = entries
        artifacts = manifest.get('artifacts', [])
        audit.check(side + ':exact_four_preserved_plus_one', len(artifacts) == 5
                    and {r['filename']: r['sha256'] for r in artifacts[:4]} == WHEELS)
        audit.check(side + ':fixed_provenance', manifest.get('l4_image') == L4_IMAGE
                    and manifest.get('sdk') == '1.30.0' and manifest.get('protocol') == '2025-11-25'
                    and manifest.get('lab_only') is True)
        audit.check(side + ':exact_unchanged_locks', manifest.get('locks') == LOCKS
                    and all((LAB / 'packaging' / name).is_file() and sha(LAB / 'packaging' / name) == expected
                            for name, expected in LOCKS.items()))
        audit.check(side + ':artifact_hashes', all(entries.get(r['filename']) == r['sha256'] for r in artifacts))
        audit.check(side + ':source_manifest_hash', entries.get('source-manifest.json') == manifest.get('source_manifest_sha256'))
        own_valid = bool(artifacts and re.fullmatch(r'xoc_mcp_lab_l6-0\.6\.0\+lab\.[a-f0-9]{12}-py3-none-any\.whl', artifacts[-1].get('filename', '')))
        audit.check(side + ':own_wheel_name', own_valid)
        if own_valid and side == 'build-a':
            try:
                with zipfile.ZipFile(directory / artifacts[-1]['filename']) as wheel:
                    marker = json.loads(wheel.read('xoc_mcp_lab_l6/_artifact.json'))
                    audit.check('l6_wheel_source_manifest', marker.get('files') == source['files']
                                and marker.get('source_manifest_sha256') == manifest['source_manifest_sha256'])
                    audit.check('l6_wheel_file_hashes', all(hashlib.sha256(wheel.read(name)).hexdigest() == expected
                                for name, expected in marker['files'].items()))
                    result['installed_artifact_marker'] = {k: marker[k] for k in
                        ('version', 'source_manifest_sha256', 'core_sha256', 'l4_sha256', 'sdk', 'protocol')}
            except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
                audit.check('l6_wheel_readable', False, type(error).__name__)
        for name, expected in source.get('files', {}).items():
            if side != 'build-a':
                continue
            try:
                path = source_path(name)
            except ValueError:
                audit.check('safe_source_manifest_paths', False, name)
                continue
            observed = sha(path) if path.is_file() and not path.is_symlink() else None
            staged = []
            for suffix in ('a', 'b'):
                staged_path = run / ('sources-' + suffix) / 'l6' / name
                staged.append(sha(staged_path) if staged_path.is_file() and not staged_path.is_symlink() else None)
            result['frozen_sources'].append(dict(artifact_path=name, current_sha256=observed,
                expected_sha256=expected, staged_sha256=staged, identical=observed == expected and staged == [expected, expected]))
    audit.check('two_independent_builds_identical', len(result['builds']) == 2
                and result['builds'].get('build-a') == result['builds'].get('build-b'), missing=len(result['builds']) < 2)
    audit.check('current_and_both_staged_sources_match_frozen_artifact', bool(result['frozen_sources'])
                and all(r['identical'] for r in result['frozen_sources']), missing=not result['frozen_sources'])
    if sources[0]:
        expected = set(sources[0].get('files', {}))
        current = {p.relative_to(LAB).as_posix() for p in (LAB / 'xoc_mcp_lab_l6').glob('*.py')}
        current |= {'xoc_mcp_lab_l6/tests/' + p.name for p in (LAB / 'tests').glob('test_l6*.py')}
        audit.check('no_unfrozen_l6_runtime_or_tests', current <= expected, sorted(current - expected))
    if image:
        audit.check('image_fixed_base_and_run', image.get('run_id') == run.name and image.get('l4_base_id') == L4_IMAGE
                    and re.fullmatch(r'sha256:[a-f0-9]{64}', image.get('image_id', '')) is not None
                    and image.get('offline') is True and image.get('dependencies_changed') is False)
    result['builder_processes'] = []
    for side in ('a', 'b'):
        value = audit.read(run / f'build-{side}-process.json')
        if value:
            result['builder_processes'].append(value)
            audit.check('builder_' + side + '_fixed_offline_limits', value.get('before', {}).get('image') == L4_IMAGE
                        and controls_ok(value.get('before', {}).get('controls', {})))
            audit.check('builder_' + side + '_exit_and_cleanup', value.get('final', {}).get('state', {}).get('ExitCode') == 0
                        and value.get('owned_container_removed') is True)
    return result


def normalize_nodeid(nodeid):
    value = str(nodeid).replace('\\', '/')
    match = re.search(r'(xoc_mcp_lab(?:_harness|_l4|_l6)?/tests/.*)', value)
    return match.group(1) if match else value


def tests_gate(audit, result):
    historical = audit.read(LAB / 'l4-runs/l4-final04-20260916/results/tests/tests.json')
    if not result or not historical:
        return {}
    baseline = {normalize_nodeid(r['nodeid']) for r in historical.get('tests', []) if r.get('when') == 'call' and r.get('outcome') == 'passed'}
    rows = result.get('tests', [])
    calls = [r for r in rows if r.get('when') == 'call']
    counts = Counter(normalize_nodeid(r['nodeid']) for r in calls)
    passed = {normalize_nodeid(r['nodeid']) for r in calls if r.get('outcome') == 'passed'}
    missed = sorted(baseline - passed)
    bad = [r for r in rows if r.get('outcome') != 'passed']
    unexpected = sorted(n for n in passed - baseline if not n.startswith('xoc_mcp_lab_l6/tests/'))
    duplicates = sorted(n for n, n_count in counts.items() if n_count != 1)
    audit.check('historical_baseline_is_exactly_304', len(baseline) == 304)
    audit.check('all_304_original_nodeids_passed_together', not missed and len(baseline) == 304, missed)
    audit.check('new_tests_present', any(n.startswith('xoc_mcp_lab_l6/tests/') for n in passed))
    collected = [normalize_nodeid(n) for n in result.get('collected_nodeids', [])]
    audit.check('collected_nodeids_recorded_and_unique', bool(collected) and len(collected) == len(set(collected)),
                dict(collected=len(collected), unique=len(set(collected))), missing='collected_nodeids' not in result)
    audit.check('every_collected_test_called_and_passed', bool(collected) and set(collected) == passed
                and set(collected) == set(counts), sorted(set(collected) ^ passed), missing=not collected)
    expected_files = {'xoc_mcp_lab_l6/tests/' + p.name for p in (LAB / 'tests').glob('test_l6*.py')}
    covered_files = {n.split('::', 1)[0] for n in collected if n.startswith('xoc_mcp_lab_l6/tests/')}
    audit.check('all_frozen_l6_test_files_collected', bool(expected_files) and covered_files == expected_files,
                dict(missing=sorted(expected_files - covered_files), unexpected=sorted(covered_files - expected_files)),
                missing=not collected)
    matrix_nodes = [re.search(r'acceptance\[(C\d{2})\]$', n).group(1) for n in collected
                    if n.startswith('xoc_mcp_lab_l6/tests/') and re.search(r'acceptance\[(C\d{2})\]$', n)]
    audit.check('all_24_acceptance_tests_collected_exactly_once', Counter(matrix_nodes) == Counter({n: 1 for n in CASES}),
                dict(Counter(matrix_nodes)), missing=not collected)
    audit.check('tests_no_failures_errors_skips_duplicates', not bad and not duplicates and result.get('exit_code') == 0
                and result.get('failed', 0) == 0 and result.get('errors', 0) == 0
                and result.get('skipped', 0) == 0, dict(bad=bad, duplicate_nodeids=duplicates))
    audit.check('test_nodeids_have_expected_origin', not unexpected, unexpected)
    audit.check('pytest_total_matches_actual_passed_calls', result.get('passed') == len(passed))
    return dict(actual_passed=len(passed), baseline_passed=len(baseline & passed),
                new_passed=len([n for n in passed if n.startswith('xoc_mcp_lab_l6/tests/')]),
                missing_baseline_nodeids=missed, failed_or_skipped_reports=bad,
                duplicate_nodeids=duplicates, original_nodeids=sorted(baseline),
                collected_nodeids=collected, collected_l6_files=sorted(covered_files))


def metrics_for_population(audit, name, value):
    if not value:
        return None
    hot, concurrent, large, cold = (value.get(k, []) for k in ('sequential', 'concurrent', 'large', 'cold'))
    for label, rows, count in (('hot', hot, 80), ('concurrent', concurrent, 4), ('large', large, 3)):
        audit.check(name + ':' + label + '_all_allowed', len(rows) == count and all(r.get('status') == 'allowed' for r in rows))
    audit.check(name + ':twenty_per_tool', Counter(r.get('tool') for r in hot) == Counter({t: 20 for t in TOOLS}))
    expected_candidate = 'L4' if name.startswith('benchmark-l4-') else 'L6'
    audit.check(name + ':correct_population_implementation', value.get('candidate') == expected_candidate)
    audit.check(name + ':five_startups', len(cold) == 5 and all(r.get('first', {}).get('status') == 'allowed' for r in cold))
    for number, row in enumerate(cold, 1):
        captures = row.get('deterministic_process_samples', [])
        expected_points = ['after_ready', 'after_first_cold_query', 'before_retirement']
        audit.check(f'{name}:cold_{number}:deterministic_points',
                    [r.get('measurement_point') for r in captures] == expected_points,
                    missing=not captures)
        for sample in captures:
            fields = ('VmRSS', 'VmHWM', 'cpu_ticks', 'Threads', 'tasks', 'fds')
            process = sample.get('server', {})
            valid = (sample.get('process_alive') is True and sample.get('ownership_verified') is True
                     and sample.get('identity_stable') is True and not sample.get('unavailable_reasons')
                     and sample.get('pid') == process.get('pid')
                     and type(sample.get('generation')) is int and sample['generation'] >= 1
                     and all(finite(process.get(k)) and process[k] >= 0 for k in fields)
                     and finite(sample.get('monotonic_at')) and finite(sample.get('finished_at'))
                     and sample['finished_at'] >= sample['monotonic_at'])
            audit.check(f'{name}:cold_{number}:capture:{sample.get("measurement_point")}', valid,
                        dict(pid=sample.get('pid'), generation=sample.get('generation'),
                             reasons=sample.get('unavailable_reasons'), exited=sample.get('process_exited_before_measurement')),
                        missing=not valid)
        startup, through_first = row.get('startup_seconds'), row.get('start_through_first_seconds')
        audit.check(f'{name}:cold_{number}:measured_structure', finite(startup) and startup > 0
                    and finite(through_first) and through_first >= startup
                    and isinstance(row.get('cleanup'), dict) and bool(row.get('cleanup'))
                    and isinstance(row.get('events'), list) and bool(row.get('events'))
                    and isinstance(row.get('resource_samples'), list) and bool(row.get('resource_samples'))
                    and isinstance(row.get('control'), list),
                    dict(startup_seconds=startup, start_through_first_seconds=through_first),
                    missing=any(k not in row for k in ('control', 'events', 'resource_samples')))
    normal = hot + concurrent + large + [r.get('first', {}) for r in cold]
    overruns = [r for r in normal if not finite(r.get('total_seconds')) or not 0 <= r['total_seconds'] <= 3.5]
    audit.check(name + ':normal_at_most_3_5_seconds', not overruns, overruns)
    rounds = value.get('stability', [])
    round_rows = []
    for number, item in enumerate(rounds, 1):
        rows = item.get('rows', [])
        outcomes = Counter(r.get('status') for r in rows)
        okay = len(rows) == 40 and outcomes == Counter(allowed=36, error=4)
        okay &= item.get('round') == number and Counter(r.get('tool') for r in rows) == Counter({t: 10 for t in TOOLS})
        okay &= all(r.get('status') == ('error' if i % 13 == 0 else 'allowed')
                    and (i % 13 != 0 or r.get('code') == 'execution_failed')
                    and finite(r.get('total_seconds')) and 0 <= r['total_seconds'] <= 3.5 for i, r in enumerate(rows))
        if expected_candidate == 'L6':
            state = item.get('control') or {}
            okay &= state.get('state') == 'READY' and state.get('attempt') == 0
            # status is the latest bounded heartbeat snapshot, not necessarily
            # a fresh idle observation at this exact client completion time.
            # Demand coherence, not an invented zero from a stale snapshot.
            okay &= type(state.get('occupancy')) is int and 0 <= state['occupancy'] <= 4
            okay &= isinstance(state.get('jobs'), list) and len(state['jobs']) == state['occupancy']
        round_rows.append(dict(round=number, count=len(rows), outcomes=dict(outcomes), passed=okay))
    audit.check(name + ':five_rounds_36_allowed_4_deliberate_errors', len(rounds) == 5 and all(r['passed'] for r in round_rows), round_rows)
    audit.check(name + ':harness_population_acceptance', value.get('population_passed') is True and value.get('acceptance_errors') == [],
                value.get('acceptance_errors'), missing='population_passed' not in value)
    return dict(hot=distribution([r.get('total_seconds') for r in hot]),
                concurrent=distribution([r.get('total_seconds') for r in concurrent]),
                large=distribution([r.get('total_seconds') for r in large]),
                startup=distribution([r.get('startup_seconds') for r in cold]),
                outcomes={label: dict(Counter(r.get('status') for r in rows)) for label, rows in
                          (('hot', hot), ('concurrent', concurrent), ('large', large))},
                normal_overruns=overruns, stability=round_rows)


def performance(audit, phases):
    pairs, all_intervals = [], []
    for sample in (1, 2, 3):
        names = [f'benchmark-l4-{sample}', f'benchmark-{sample}']
        values = [phases.get(n) for n in names]
        reduced = [metrics_for_population(audit, n, v) for n, v in zip(names, values)]
        pair = dict(sample=sample, l4=reduced[0], l6=reduced[1], ratios=None, observed_order=None)
        if all(v is not None for v in values) and all(r and r['hot'] for r in reduced):
            denominators_valid = all(reduced[0]['hot'][key] > 0 for key in ('median', 'p95_nearest_rank'))
            audit.check(f'pair_{sample}:nonzero_measured_baseline', denominators_valid)
            ratios = {key: reduced[1]['hot'][key] / reduced[0]['hot'][key] for key in ('median', 'p95_nearest_rank')} if denominators_valid else {}
            pair['ratios'] = ratios
            audit.check(f'pair_{sample}:median_and_p95_at_most_1_25', len(ratios) == 2 and all(v <= 1.25 for v in ratios.values()), ratios)
            timestamps = [v.get('population_started_at') for v in values]
            ends = [v.get('population_finished_at') for v in values]
            if all(finite(t) for t in timestamps + ends):
                audit.check(f'pair_{sample}:valid_population_spans', all(0 <= a < b for a, b in zip(timestamps, ends)))
                order = sorted(range(2), key=lambda i: timestamps[i])
                pair['observed_order'] = ['L4' if i == 0 else 'L6' for i in order]
                pair['intervals'] = {label: dict(start=a, finish=b) for label, a, b in zip(('L4', 'L6'), timestamps, ends)}
                all_intervals.extend((a, b, n) for a, b, n in zip(timestamps, ends, names))
                audit.check(f'pair_{sample}:not_concurrent', ends[order[0]] <= timestamps[order[1]])
            else:
                audit.check(f'pair_{sample}:ordering_timestamps_recorded', False, 'Need full-population monotonic spans, including cold starts', missing=True)
        pairs.append(pair)
    orders = [p['observed_order'] for p in pairs]
    audit.check('three_pairs_with_alternating_order', all(orders) and orders[0] != orders[1] and orders[1] != orders[2], orders,
                missing=not all(orders))
    ordered = sorted(all_intervals)
    audit.check('all_six_populations_nonoverlapping', len(ordered) == 6 and all(a[1] <= b[0] for a, b in zip(ordered, ordered[1:])),
                ordered, missing=len(ordered) != 6)
    return dict(pairs=pairs, tolerance_ratio=1.25, selected_best_pair=False,
                percentile='nearest-rank ceil(0.95*n)',
                host_variability='Observed local samples; no claim of invariant host scheduling or production SLA.')


def recovery_measurements(audit, value, *, full_matrix=True):
    result = dict(cases=[], phases=[], incidents=[], restorations=[], retirements=[], coordination=[],
                  phase_overruns=[], unexpected_phase_failures=[])
    if not value:
        return result
    cases = value.get('cases', [])
    if full_matrix:
        audit.check('C01_C24_exactly_once', Counter(r.get('case') for r in cases) == Counter({name: 1 for name in CASES}))
        audit.check('all_24_cases_actually_passed', len(cases) == 24 and all(r.get('passed') is True for r in cases),
                    [dict(case=r.get('case'), passed=r.get('passed'), error=r.get('error_code')) for r in cases])
        audit.check('case_totals_consistent', value.get('passed') == 24 and value.get('total') == 24 and value.get('all_passed') is True)
    seen = set()
    for case in cases:
        ident = case.get('case')
        result['cases'].append(dict(case=ident, passed=case.get('passed'), error_code=case.get('error_code')))
        for path, row in walk(case):
            if row.get('kind') in ('phase', 'incident', 'retired'):
                unique = (row.get('kind'), row.get('sent_at'), row.get('seq'), row.get('child_generation'), row.get('phase'))
                if unique in seen:
                    continue
                seen.add(unique)
                output = dict(case=ident, source=path, **row)
                if row['kind'] == 'phase':
                    name = row.get('phase')
                    failed = row.get('ok') is False
                    expected_negative = bool(failed and (
                        (ident == 'C11' and ((name in ('startup', 'probe') and row.get('child_generation', 0) >= 2)
                                           or name == 'recovery' or (name == 'kill_reap' and row.get('simulated') is True)))
                        or (ident == 'C24' and name == 'startup')
                        or (ident == 'C12' and name == 'recovery' and row.get('child_generation') == 3)))
                    safe_fallback = (name == 'close_ack' and row.get('acknowledged') is False) or (name == 'terminate' and row.get('safe_reason') == 'kill_required')
                    classification = 'deliberately_failed_phase_not_successful_recovery' if expected_negative else ('bounded_safe_fallback' if safe_fallback else 'positive_or_cleanup_phase')
                    output['classification'] = classification
                    limit = PHASE_BUDGETS.get(name)
                    valid = limit is not None and finite(row.get('elapsed')) and row['elapsed'] >= 0 and row.get('budget') == limit
                    start, finish = row.get('started_at'), row.get('finished_at')
                    audit.check(f'{ident}:phase:{row.get("sent_at")}:{name}:direct_span',
                        finite(start) and finite(finish) and 0 <= start <= finish and finite(row.get('elapsed'))
                        and math.isclose(finish-start, row['elapsed'], rel_tol=1e-9, abs_tol=1e-9), output)
                    output['within_phase_budget'] = bool(valid and row['elapsed'] <= limit)
                    result['phases'].append(output)
                    if not valid or not output['within_phase_budget']:
                        result['phase_overruns'].append(output)
                    # Expected failure exempts only successful-outcome checks,
                    # never the approved phase's measured time budget.
                    audit.check(f'{ident}:phase:{row.get("sent_at")}:{name}:budget', output['within_phase_budget'], output)
                    if not expected_negative:
                        if row.get('ok') is not True and not safe_fallback:
                            result['unexpected_phase_failures'].append(output)
                elif row['kind'] == 'incident':
                    decision, origin = row.get('decision_at'), row.get('fault_at')
                    output['T_detection_seconds'] = decision-origin if finite(decision) and finite(origin) else None
                    audit.check(f'{ident}:incident_timestamps:{row.get("sent_at")}',
                                finite(decision) and finite(origin) and 0 <= origin <= decision, output)
                    if row.get('safe_reason') in ('work_age_exceeded', 'work_age_control_unconfirmed'):
                        jobs = row.get('jobs') or []
                        valid_jobs = bool(jobs) and finite(decision) and all(finite(j.get('admitted_at')) for j in jobs)
                        output['admission_to_decision_seconds'] = decision-min(j['admitted_at'] for j in jobs) if valid_jobs else None
                        audit.check(f'{ident}:work_detection:{decision}', valid_jobs
                                    and 0 <= output['admission_to_decision_seconds'] <= 5.25, output, missing=not jobs)
                    elif row.get('safe_reason') == 'heartbeat_stale':
                        audit.check(f'{ident}:heartbeat_detection:{decision}', output['T_detection_seconds'] is not None
                                    and 0 <= output['T_detection_seconds'] <= 2.25, output)
                    result['incidents'].append(output)
                else:
                    result['retirements'].append(output)
            if row.get('new_network_query') is True:
                unique = ('restoration', row.get('public_query_started_at'), row.get('new_generation'))
                if unique in seen:
                    continue
                seen.add(unique)
                output = dict(case=ident, source=path, **row)
                result['restorations'].append(output)
                audit.check(f'{ident}:new_tcp_restore:{row.get("public_query_started_at")}',
                    row.get('correct') is True and row.get('new_query', {}).get('status') == 'allowed'
                    and finite(row.get('dispatch_after_ready')) and 0 <= row['dispatch_after_ready'] <= .25
                    and finite(row.get('restore_seconds')) and 0 <= row['restore_seconds'] <= 21
                    and finite(row.get('new_query', {}).get('total_seconds')) and 0 <= row['new_query']['total_seconds'] <= 3.5, output)
    audit.check('no_unexpected_failed_recovery_phases', not result['unexpected_phase_failures'], result['unexpected_phase_failures'])
    for recovery in (r for r in result['phases'] if r['phase'] == 'recovery'):
        start, finish = recovery.get('started_at'), recovery.get('finished_at')
        if not finite(start) or not finite(finish) or finish < start:
            continue  # Invalid direct span is already a failing gate above.
        members = [r for r in result['phases'] if r['case'] == recovery['case'] and r['phase'] != 'recovery'
                   and finite(r.get('started_at')) and finite(r.get('finished_at'))
                   and start <= r['started_at'] <= r['finished_at'] <= finish]
        ordered = sorted((r['started_at'], r['finished_at']) for r in members)
        # Measure the complement of phase intervals in this exact recovery
        # span. This is not a sum of timings from different samples or runs.
        cursor, coordination_seconds = start, 0.
        for began, ended in ordered:
            coordination_seconds += max(0., began-cursor)
            cursor = max(cursor, ended)
        coordination_seconds += max(0., finish-cursor)
        minimum = {'close_ack', 'terminate', 'kill_reap'}
        if recovery.get('ok') is True:
            minimum |= {'startup', 'probe', 'open'}
        present = {r['phase'] for r in members}
        key = f'{recovery["case"]}:recovery:{start}'
        audit.check(key + ':phase_evidence_complete', minimum <= present,
                    dict(required=sorted(minimum), present=sorted(present)), missing=not minimum <= present)
        measured = dict(case=recovery['case'], child_generation=recovery.get('child_generation'),
                        started_at=start, finished_at=finish, elapsed=coordination_seconds, budget=2.,
                        contributing_phase_count=len(members),
                        method='uncovered intervals within this same directly measured recovery span',
                        recovery_success=recovery.get('ok') is True)
        result['coordination'].append(measured)
        audit.check(key + ':coordination_at_most_2_seconds', coordination_seconds <= 2., measured)
    if full_matrix:
        for ident in ('C04', 'C06', 'C18'):
            audit.check(ident + ':independent_recovery_and_new_tcp_evidence', any(r['case'] == ident for r in result['incidents'])
                        and any(r['case'] == ident for r in result['restorations']))
    result['definitions'] = dict(detection='same measured fault origin to supervisor decision',
        recovery='phase=recovery elapsed directly measured; not sum of other samples',
        restoration='same fault origin to new authorized real loopback request completion',
        negative_cases='C11/C24 and C12 third incident are deliberately failed recovery paths, not successful restorations',
        no_ack='ok=false for missing ACK is a safe fallback only if elapsed respects 0.25s and retirement is evidenced',
        cleanup='Final process/container cleanup is never credited as service recovery.')
    return result


def normal_supervisor_control(audit, name, control):
    """All phases of one healthy supervisor, including explicit retirement.

    A READY line alone cannot replace missing startup/probe/open measurements.
    Cold samples have independent supervisor owners, each at generation one.
    """
    rows = control if isinstance(control, list) else []
    audit.check(name + ':control_evidence_present', bool(rows), missing=not rows)
    phases = Counter(r.get('phase') for r in rows if r.get('kind') == 'phase')
    required = ('startup', 'probe', 'open', 'close_ack', 'terminate', 'kill_reap')
    audit.check(name + ':complete_startup_and_retirement_phases',
                all(phases.get(phase) == 1 for phase in required)
                and phases.get('drain', 0) <= 1 and not (set(phases) - set(required) - {'drain'}),
                dict(phases), missing=not rows)
    ready = [r for r in rows if r.get('kind') == 'ready']
    retired = [r for r in rows if r.get('kind') == 'retired']
    stopped = [r for r in rows if r.get('kind') == 'stopped']
    audit.check(name + ':one_ready_no_incident_or_replacement', len(ready) == 1
                and ready[0].get('state') == 'READY' and ready[0].get('child_generation') == 1
                and ready[0].get('attempt') == 0 and ready[0].get('clock_verified') is True
                and not any(r.get('kind') == 'incident' for r in rows), missing=not rows)
    audit.check(name + ':exact_retirement_and_stopped', len(retired) == len(stopped) == 1
                and retired[0].get('child_generation') == stopped[0].get('child_generation') == 1
                and retired[0].get('reaped') is True and retired[0].get('process_absent') is True
                and retired[0].get('residual') is False and stopped[0].get('residual') is False
                and stopped[0].get('child_pid') is None
                and stopped[0].get('dropped') == 0 and stopped[0].get('rejected') == 0,
                dict(retirements=len(retired), stopped=len(stopped)), missing=not rows)
    return recovery_measurements(audit, {'cases': [{'case': name, 'control': rows}]}, full_matrix=False)


def resources(audit, name, value):
    currents, peaks, pids, failcounts, oom, pid_hits = [], [], [], [], [], []
    memory_limits, pid_limits, cpu_quotas = set(), set(), set()
    role_values = defaultdict(lambda: defaultdict(list))
    sample_count = 0
    seen_extra, seen_cgroup, seen_samples, seen_roles = set(), set(), set(), set()
    for path, row in walk(value):
        if 'cgroup_extra' in row or 'final_cgroup_extra' in row:
            extra = row.get('cgroup_extra', row.get('final_cgroup_extra')) or {}
            fingerprint = json.dumps(extra, sort_keys=True)
            if fingerprint in seen_extra:
                extra = {}
            else:
                seen_extra.add(fingerprint)
            for key in ('memory.current', 'memory/memory.usage_in_bytes'):
                if key in extra: currents.append(int(extra[key]))
            for key in ('memory.peak', 'memory/memory.max_usage_in_bytes'):
                if key in extra: peaks.append(int(extra[key]))
            for key in ('pids.current', 'pids.peak', 'pids/pids.current'):
                if key in extra: pids.append(int(extra[key]))
            for key in ('memory.max', 'memory/memory.limit_in_bytes'):
                if key in extra and str(extra[key]).isdigit(): memory_limits.add(int(extra[key]))
            for key in ('pids.max', 'pids/pids.max'):
                if key in extra and str(extra[key]).isdigit(): pid_limits.add(int(extra[key]))
            if 'cpu.max' in extra:
                parts = extra['cpu.max'].split()
                if len(parts) == 2 and parts[0].isdigit(): cpu_quotas.add(int(parts[0])/int(parts[1]))
            if 'cpu/cpu.cfs_quota_us' in extra and 'cpu/cpu.cfs_period_us' in extra:
                cpu_quotas.add(int(extra['cpu/cpu.cfs_quota_us'])/int(extra['cpu/cpu.cfs_period_us']))
            if 'memory/memory.failcnt' in extra: failcounts.append(int(extra['memory/memory.failcnt']))
            for key in ('memory.events', 'memory/memory.oom_control', 'pids.events', 'pids/pids.events'):
                data = dict(line.split() for line in extra.get(key, '').splitlines())
                oom.extend(int(data[k]) for k in ('oom', 'oom_kill', 'under_oom') if k in data)
                if key in ('pids.events', 'pids/pids.events') and 'max' in data:
                    pid_hits.append(int(data['max']))
        fingerprint = json.dumps(row, sort_keys=True) if 'memory_current_bytes' in row else None
        if fingerprint is not None and fingerprint not in seen_cgroup:
            seen_cgroup.add(fingerprint)
            if type(row.get('memory_current_bytes')) is int: currents.append(row['memory_current_bytes'])
            if type(row.get('memory_limit_bytes')) is int: memory_limits.add(row['memory_limit_bytes'])
            if type(row.get('memory_failcnt')) is int: failcounts.append(row['memory_failcnt'])
            for key in ('oom', 'oom_kill', 'under_oom'):
                if key in (row.get('memory_events') or {}): oom.append(row['memory_events'][key])
            if finite(row.get('cpu_quota_us')) and finite(row.get('cpu_period_us')) and row['cpu_period_us']:
                cpu_quotas.add(row['cpu_quota_us']/row['cpu_period_us'])
        if 'at' in row and 'resources' in row:
            fingerprint = (row['at'], json.dumps(row['resources'], sort_keys=True))
            if fingerprint not in seen_samples:
                seen_samples.add(fingerprint)
                sample_count += 1
        for role in ('harness', 'supervisor', 'server'):
            process = row.get(role)
            if role == 'harness' and process is None:
                process = row.get('final_resources')
            if isinstance(process, dict):
                fingerprint = (role, json.dumps(process, sort_keys=True))
                if fingerprint in seen_roles:
                    continue
                seen_roles.add(fingerprint)
                for key in ('VmRSS', 'VmHWM', 'Threads', 'fds', 'tasks', 'cpu_ticks'):
                    if finite(process.get(key)): role_values[role][key].append(process[key])
    audit.check(name + ':actual_joint_memory_peak_and_pids_recorded', bool(peaks) and bool(pids),
                dict(peak_samples=len(peaks), pids_samples=len(pids)), missing=not peaks or not pids)
    audit.check(name + ':memory_and_pids_within_limits', (not currents or max(currents) <= 536870912)
                and (not peaks or max(peaks) <= 536870912) and (not pids or max(pids) <= 128))
    audit.check(name + ':runtime_budget_verified', memory_limits == {536870912} and pid_limits == {128} and cpu_quotas == {2.},
                dict(memory_limits=sorted(memory_limits), pid_limits=sorted(pid_limits), cpu_quotas=sorted(cpu_quotas)),
                missing=not memory_limits or not pid_limits or not cpu_quotas)
    audit.check(name + ':no_oom_or_failcnt', bool(oom) and max(oom) == 0 and (not failcounts or max(failcounts) == 0),
                dict(oom_counters=sorted(set(oom)), failcnt=sorted(set(failcounts))), missing=not oom)
    audit.check(name + ':no_observed_pids_limit_failure', bool(pid_hits) and max(pid_hits) == 0,
                sorted(set(pid_hits)), missing=not pid_hits)
    required_fields = {'VmRSS', 'VmHWM', 'Threads', 'fds', 'tasks', 'cpu_ticks'}
    sampled_stage = name.startswith('benchmark-') or name in ('smoke', 'recovery')
    required_roles = {'harness'}
    if sampled_stage:
        required_roles.add('server')
        if not name.startswith('benchmark-l4-'):
            required_roles.add('supervisor')
        audit.check(name + ':time_series_resource_samples_present', sample_count > 0,
                    dict(unique_samples=sample_count), missing=sample_count == 0)
    absent = {role: sorted(required_fields - set(role_values[role])) for role in required_roles
              if not required_fields <= set(role_values[role])}
    audit.check(name + ':required_process_metrics_present', not absent, absent, missing=bool(absent))
    return dict(resource_samples=sample_count, joint_cgroup_memory_current_max=max(currents, default=None),
                joint_cgroup_memory_peak=max(peaks, default=None), pids_max_observed=max(pids, default=None),
                per_role_observed_maxima={role: {k: max(v) for k, v in fields.items()} for role, fields in role_values.items()},
                required_observed_roles=sorted(required_roles),
                identical_nested_resource_copies_deduplicated=True,
                not_sum_of_per_process_high_water_marks=True,
                limitations='Per-process maxima are not simultaneous memory; fds are not TCP connections; finite rounds do not prove indefinite stability.')


def cleanup(audit, run, phases, image_id):
    report = dict(runtime_phases=[], application_process_records=[])
    for name, value in phases.items():
        folder = run / 'results' / name
        before = audit.read(folder / 'container-before.json')
        final = audit.read(folder / 'container-final.json')
        process = audit.read(folder / 'container-process.json')
        removed = audit.read(folder / 'container-cleanup.json')
        export = audit.read(folder / 'evidence-export.json')
        record = dict(phase=name, before=before, final=final, direct_child=process, removal=removed, export=export)
        report['runtime_phases'].append(record)
        if before:
            audit.check(name + ':candidate_image_and_controls', before.get('image') == image_id and controls_ok(before.get('controls', {})))
        if final and process and removed:
            audit.check(name + ':exit_zero_exported_owned_cleanup', final.get('state', {}).get('ExitCode') == process.get('exit_code') == 0
                and final.get('state', {}).get('OOMKilled') is False and process.get('direct_child_reaped') is True
                and removed.get('owned_container_removed') is True and removed.get('foreign_containers_touched') is False
                and removed.get('recovery_proof') is False and bool(export))
            audit.check(name + ':ownership_ids_consistent', before is not None and before.get('id') == final.get('id') == removed.get('owner', {}).get('id')
                and removed.get('owner', {}).get('name', '').startswith('xoc-l6-' + run.name + '-'))
        if value:
            audit.check(name + ':no_final_child_or_zombie', value.get('final_children') == '')
            seen = set()
            for path, row in walk(value):
                if 'supervisor_reaped' in row or ('process_absent' in row and 'global_shutdown_seconds' in row):
                    identity = json.dumps(row, sort_keys=True)
                    if identity in seen: continue
                    seen.add(identity)
                    report['application_process_records'].append(dict(phase=name, source=path, **row))
                    if 'supervisor_reaped' in row:
                        audit.check(name + ':application_cleanup:' + path, row.get('supervisor_reaped') is True
                            and row.get('supervisor_absent') is True and row.get('own_server_residual') == [])
                        audit.check(name + ':no_unexpected_forced_supervisor_cleanup:' + path,
                            row.get('harness_forced') is False and row.get('control_pump_error') is None,
                            dict(harness_forced=row.get('harness_forced'), control_pump_error=row.get('control_pump_error')))
                        adopted = row.get('adopted_failure_cleanup')
                        explicit_death_case = (name == 'recovery'
                            and '/evidence/supervisor_death/explicit_harness_failure_cleanup' in path
                            and row.get('returncode') == -9)
                        audit.check(name + ':adopted_failure_cleanup_explicit_and_complete:' + path,
                            isinstance(adopted, list) and (not adopted or explicit_death_case)
                            and all(r.get('reaped') is True and r.get('process_absent') is True
                                    and finite(r.get('seconds')) and r['seconds'] >= 0 for r in (adopted or [])),
                            dict(explicit_supervisor_death=explicit_death_case, adopted=adopted))
                    else:
                        audit.check(name + ':L4_application_cleanup:' + path, row.get('process_absent') is True)
    report['container_removal_is_recovery_proof'] = False
    report['adopted_or_forced_cleanup_is_not_successful_automatic_recovery'] = True
    return report


def finalize(run, prefix, preservation_path=None):
    audit = Audit()
    provenance_report = reproducibility(audit, run)
    phases = {}
    for name in ('tests', 'smoke', 'recovery', 'provenance', 'benchmark-l4-1', 'benchmark-1',
                 'benchmark-l4-2', 'benchmark-2', 'benchmark-l4-3', 'benchmark-3'):
        mode = re.sub(r'-[123]$', '', name)
        phases[name] = audit.read(run / 'results' / name / (mode + '.json'))
    suites = tests_gate(audit, phases['tests'])
    recoveries = recovery_measurements(audit, phases['recovery'])
    smoke = phases['smoke']
    if smoke:
        audit.check('independent_smoke_C04', smoke.get('all_passed') is True and smoke.get('total') == 1
                    and len(smoke.get('cases', [])) == 1 and smoke['cases'][0].get('case') == 'C04'
                    and smoke['cases'][0].get('passed') is True)
        recoveries['independent_smoke_measurements'] = recovery_measurements(audit, smoke, full_matrix=False)
    provenance = phases['provenance']
    if provenance:
        comparisons = provenance.get('comparisons', [])
        audit.check('canonical_provenance_41_cases_37_HTTP_comparisons', provenance.get('canonical_equivalence_cases') == 41
                    and len(comparisons) == 41 and sum(r.get('comparison') == 'identical' for r in comparisons) == 37)
        origins = provenance.get('canonical_origins', [])
        audit.check('all_20_canonical_modules_installed', len(origins) == 20 and provenance.get('all_installed') is True
                    and all('/site-packages/' in r.get('file', '') and re.fullmatch(r'[a-f0-9]{64}', r.get('sha256', '')) for r in origins))
        core_wheel = run / 'build-a' / next(iter(WHEELS))
        if core_wheel.is_file() and sha(core_wheel) == WHEELS[core_wheel.name]:
            with zipfile.ZipFile(core_wheel) as archive:
                original = {name: hashlib.sha256(archive.read(name)).hexdigest()
                            for name in archive.namelist() if name.startswith('src/') and name.endswith('.py')}
            observed = {r.get('file', '').split('/site-packages/', 1)[-1]: r.get('sha256') for r in origins}
            audit.check('canonical_import_hashes_equal_exact_twenty_core_sources', len(original) == 20 and observed == original,
                        [name for name, expected in original.items() if observed.get(name) != expected])
        else:
            audit.check('canonical_import_hash_reference_available', False, 'Exact preserved core wheel missing or altered', missing=not core_wheel.is_file())
    pairs = performance(audit, phases)
    pairs['recorded_supervisor_phase_checks'] = {}
    if provenance:
        provenance_report['supervisor_phase_checks'] = normal_supervisor_control(
            audit, 'provenance', provenance.get('control'))
    for name, value in phases.items():
        if not name.startswith('benchmark-') or not value:
            continue
        if value.get('candidate') == 'L6':
            checks = dict(main=normal_supervisor_control(audit, name, value.get('control')), cold=[])
            for number, cold in enumerate(value.get('cold', []), 1):
                checks['cold'].append(normal_supervisor_control(audit, f'{name}:cold_{number}', cold.get('control')))
            pairs['recorded_supervisor_phase_checks'][name] = checks
    resource_report = {}
    for name, value in phases.items():
        if not value: continue
        env = value.get('environment', {})
        audit.check(name + ':compatibility_tuple', env.get('python', '').startswith('3.11.16 ')
                    and env.get('machine') == 'x86_64' and env.get('sdk') == '1.30.0'
                    and env.get('protocol') == '2025-11-25' and env.get('uid') == 10001
                    and env.get('backend_checkout_mounted') is False and env.get('cwd') == '/tmp'
                    and env.get('os', {}).get('ID', '').strip('"') == 'amzn'
                    and env.get('os', {}).get('VERSION_ID', '').strip('"') == '2')
        resource_report[name] = resources(audit, name, value)
        if name.startswith('benchmark-'):
            resource_report[name]['cold_start_resources'] = [resources(audit, f'{name}:cold_{number}', cold)
                for number, cold in enumerate(value.get('cold', []), 1)]
    clean = cleanup(audit, run, phases, (provenance_report.get('image') or {}).get('image_id'))
    preservation_path = preservation_path or LAB / 'reports/l6-preservation.json'
    preservation = audit.read(preservation_path)
    if preservation:
        audit.check('preservation_complete', preservation.get('passed') is True and preservation.get('historical_groups_checked') is True
                    and len(preservation.get('saved_files', [])) == 125
                    and all(r.get('identical') is True for r in preservation.get('saved_files', []))
                    and len(preservation.get('historical_groups', [])) == 26
                    and all(r.get('identical') is True for r in preservation.get('historical_groups', []))
                    and len(preservation.get('repositories', [])) == 5
                    and all(r.get('branch_identical') and r.get('head_identical') and r.get('status_identical') for r in preservation.get('repositories', [])))
    common = dict(schema='xoc-l6-final-report-v1', run_id=run.name, lab_only=True,
                  produced_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                  report_generator_sha256=sha(Path(__file__)), decision=audit.outcome(),
                  input_evidence_sha256=audit.inputs)
    acceptance = dict(**common, accepted=audit.outcome() == 'PASS LOCAL de B', gates=audit.gates,
        failed_or_missing_gates=[r for r in audit.gates if r['outcome'] != 'PASS'], tests=suites,
        cases=recoveries['cases'], resources=resource_report,
        preservation_report=preservation_path.relative_to(LAB).as_posix(),
        scope=dict(VICTOR=False, Voice=False, cloud=False, deploy=False, production_authorization=False,
                   high_availability=False, automatic_replay=False, provider_fixtures_only=True),
        outcome_note='PASS requires every gate. A missing measurement remains INCONCLUSO; observed violations remain NO-GO. No threshold widening or best-pair selection.')
    reports = dict(acceptance=acceptance,
        **{'recovery-measurements': dict(**common, **recoveries),
           'performance-comparison': dict(**common, **pairs),
           'process-cleanup': dict(**common, **clean),
           'reproducibility': dict(**common, **provenance_report)})
    # Validate all destinations before writing any report. 'x' protects races.
    paths = {key: prefix.with_name(prefix.name + '-' + key + '.json') for key in OUTPUTS}
    for path in paths.values():
        if path.exists(): raise FileExistsError('Refusing to overwrite ' + str(path))
    for key, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(reports[key], stream, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False)
            stream.write('\n')
    return dict(decision=acceptance['decision'], accepted=acceptance['accepted'],
                failed_or_missing_gates=acceptance['failed_or_missing_gates'],
                reports={key: p.relative_to(LAB).as_posix() for key, p in paths.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--output-prefix', type=Path, default=Path('reports/l6'))
    parser.add_argument('--preservation', type=Path, default=Path('reports/l6-preservation.json'))
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,40}', args.run):
        parser.error('RunId must be the exact new L6 RunId')
    prefix = (LAB / args.output_prefix).resolve()
    if not prefix.is_relative_to(LAB) or prefix == LAB:
        parser.error('Reports must stay inside XOC-MCP-LAB')
    run = LAB / 'l6-runs' / args.run
    if not run.is_dir() or run.is_symlink():
        parser.error('RunId directory does not exist or is linked')
    for suffix in OUTPUTS:
        target = prefix.with_name(prefix.name + '-' + suffix + '.json')
        if target.exists(): raise FileExistsError('Refusing to overwrite ' + str(target))
    preservation = (LAB / args.preservation).resolve()
    if not preservation.is_relative_to(LAB):
        parser.error('Preservation report must stay in the laboratory')
    result = finalize(run, prefix, preservation)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['accepted'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
