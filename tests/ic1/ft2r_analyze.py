"""Summarize ALL FT-2R observations, retaining censored failures and overlap.

No timing value for a killed startup is claimed as its unknown completion time.
Nested import timings are inclusive; component percentages use disjoint slices.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import statistics


def stats(values):
    if not values:
        return None
    values = sorted(values)
    def percentile(q):
        position = (len(values)-1)*q
        lo = int(position)
        return values[lo] + (values[min(lo+1, len(values)-1)]-values[lo])*(position-lo)
    return dict(n=len(values), min=values[0], p50=percentile(.5), p90=percentile(.9),
                p95=percentile(.95), max=values[-1])


def population(rows):
    phases = [p for r in rows for p in r.get("control", [])
              if p.get("kind") == "phase" and p.get("phase") == "startup"]
    seconds = [p["elapsed"] for p in phases]
    return dict(runs=len(rows), ready=sum(bool(r.get("initial_ready")) for r in rows),
                failed_runs=sum(not r["passed"] for r in rows),
                startup_deadline_observations=stats(seconds),
                successful_completion_only=stats([p["elapsed"] for p in phases if p["ok"]]),
                censored=sum(not p["ok"] for p in phases),
                buckets={"lt3.5": sum(v<3.5 for v in seconds),
                         "3.5to3.8": sum(3.5<=v<3.8 for v in seconds),
                         "3.8to4.0": sum(3.8<=v<=4 for v in seconds),
                         "gt4.0": sum(v>4 for v in seconds)})


def breakdown(row):
    phases = [p for p in row.get("control", []) if p.get("kind") == "phase" and p.get("phase") == "startup"]
    events = row.get("timeline", [])
    owner = row.get("cleanup", {}).get("supervisor_pid")
    result = []
    for phase in phases:
        gen = phase["child_generation"]
        start, end = phase["started_at"], phase["finished_at"]
        child = sorted([e for e in events if e["role"] == "server" and e["generation"] == gen
                        and (owner is None or e["ppid"] == owner)], key=lambda e:e["at"])
        observations = [e for e in events if e["event"] == "S02_observed" and e.get("child_generation") == gen
                        and (owner is None or e["pid"] == owner)]
        if not child:
            result.append(dict(generation=gen, startup=phase, missing_child_timeline=True))
            continue
        spans, open_spans = [], collections.defaultdict(list)
        labels = {"S04_l6_import": "C_IMPORTS", "S05_app": "D_APP_CONSTRUCTION",
                  "S06_wiring": "E_GATEWAY_WIRING", "S07_transport": "F_HTTP_BIND"}
        for event in child:
            name = event["event"]
            if name in ("import_begin", "import_end"):
                key, label = "import:" + event["module"], "C_IMPORTS"
                begin = name == "import_begin"
            else:
                key = name.rsplit("_", 1)[0]
                label = labels.get(key)
                if not label:
                    continue
                begin = name.endswith("_begin")
            if begin:
                open_spans[(key, label)].append(event["at"])
            elif open_spans[(key, label)]:
                spans.append((open_spans[(key, label)].pop(), event["at"], label))
        # A missing end may be a caught optional ImportError, not a stalled import.
        # Never stretch it to the deadline. Its unmeasured tail belongs to I_OTHER.
        incomplete = [dict(name=key, began=began) for (key,_), values in open_spans.items() for began in values]
        entry = next(e for e in child if e["event"] == "S03_entry")["entry_at"]
        if observations:
            spawn = observations[0]["at"]
            spans.extend(((start, spawn, "A_PROCESS_CREATE"), (spawn, entry, "B_PYTHON_BOOT")))
        bind = next((e["at"] for e in child if e["event"] == "S07_transport_end"), None)
        heartbeat = next((e["at"] for e in child if e["event"] == "S08_control_heartbeat"), None)
        if bind is not None:
            spans.append((bind, heartbeat or end, "G_READY_HANDSHAKE"))
        if heartbeat is not None:
            spans.append((heartbeat, end, "H_SUPERVISOR_OBSERVATION"))
        control_marks = {}
        for name in ("hello", "heartbeat"):
            observed = next((e["at"] for e in child if e["event"] == "S08_control_" + name), None)
            control_marks[name] = dict(observed_at=observed,
                relative_to_start=None if observed is None else observed-start,
                before_budget=None if observed is None else observed <= start+phase.get("budget", 4.0))
        spans = [(max(a,start), min(b,end), label) for a,b,label in spans if b>a and b>start and a<end]
        boundaries = sorted({start, end, *[v for a,b,_ in spans for v in (a,b)]})
        # Imports nested in wiring/app are credited to imports, never twice.
        priority = ["A_PROCESS_CREATE", "B_PYTHON_BOOT", "C_IMPORTS", "D_APP_CONSTRUCTION",
                    "E_GATEWAY_WIRING", "F_HTTP_BIND", "G_READY_HANDSHAKE", "H_SUPERVISOR_OBSERVATION", "I_OTHER"]
        totals = {k:0.0 for k in priority}
        for a,b in zip(boundaries, boundaries[1:]):
            labels_here = {label for lo,hi,label in spans if lo <= (a+b)/2 <= hi}
            label = next((k for k in priority if k in labels_here), "I_OTHER")
            totals[label] += b-a
        result.append(dict(generation=gen, observed_startup_seconds=end-start, completed=phase["ok"],
            components={k:dict(seconds=v, percent=100*v/(end-start)) for k,v in totals.items()},
            child_pid=child[0]["pid"], incomplete_intervals=incomplete,
            control_milestones=control_marks,
            markers=[e for e in child if e["event"].startswith("S")]))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.directory.resolve()
    target = args.output.resolve()
    if not target.is_relative_to(source) or target.exists():
        raise ValueError("FT2R_NEW_OUTPUT_REQUIRED")
    groups, files, timelines, focal = collections.defaultdict(list), {}, {}, []
    imports, categories = collections.defaultdict(list), collections.Counter()
    resources, startup_initial, startup_recovery = [], [], []
    io_rows, cpu_rows, cgroups = [], [], []
    diagnostic_failures = []
    for path in sorted(source.glob("*/*.json")):
        row = json.loads(path.read_bytes())
        if "mode" not in row:
            continue
        key = path.relative_to(source).as_posix()
        files[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        groups[row["mode"]].append(row)
        if row.get("timeline"):
            timelines[key] = breakdown(row)
            for owner in row.get("owned_servers", []):
                if owner["supervisor_pid"] != row.get("cleanup", {}).get("supervisor_pid"):
                    timelines[key + ":owner:" + str(owner["supervisor_pid"])] = breakdown(dict(
                        control=owner["control"], timeline=row["timeline"],
                        cleanup=dict(supervisor_pid=owner["supervisor_pid"])))
            for event in row["timeline"]:
                if event["role"] != "server":
                    continue
                if event["event"] == "import_end":
                    imports[event["module"]].append(event["elapsed"])
                categories[event["event"]] += 1
                if event["event"] == "io_counters":
                    io_rows.append(dict(file=key, **event))
            for phase in timelines[key]:
                if phase.get("missing_child_timeline"):
                    continue
                relevant = [e for e in row["timeline"] if e["pid"] == phase["child_pid"]]
                start = next(e for e in relevant if e["event"] == "S03_entry")
                end_limit = next(p["finished_at"] for p in row["control"] if p.get("phase") == "startup"
                                 and p["child_generation"] == phase["generation"])
                before_limit = [e for e in relevant if e["at"] <= end_limit]
                if before_limit:
                    last = max(before_limit, key=lambda e:e["at"])
                    elapsed = last["at"]-start["at"]
                    if elapsed > 0:
                        cpu_rows.append(dict(file=key, generation=phase["generation"],
                            observed_wall=elapsed, cpu_seconds=last["cpu"]-start["cpu"],
                            cpu_wall_ratio=(last["cpu"]-start["cpu"])/elapsed))
        invalid_recovery = any(p.get("safe_reason") == "harness_command_invalid" for p in row.get("control", []))
        if invalid_recovery:
            diagnostic_failures.append(dict(file=key, cause="harness used none instead of official mixed fault profile",
                initial_timeline_usable=True, recovery_accreditation=False))
        if row["mode"] == "timeline" and not invalid_recovery:
            for phase in row.get("control", []):
                if phase.get("kind") == "phase" and phase.get("phase") == "startup":
                    (startup_initial if phase["child_generation"] == 1 else startup_recovery).append(phase)
        if row["mode"] == "focal":
            focal.append(dict(file=key, case=row["case"], passed=row["passed"], error=row.get("error_code"),
                location=row.get("failure_location"), missing_events=[w for w in row["waits"] if not w["ok"]]))
        resources.extend(row.get("resources", []))
        cgroups.append(dict(file=key, before=row.get("cgroup_before"), after=row.get("cgroup_after")))
    def phase_stats(values):
        return dict(n=len(values), passed=sum(v["ok"] for v in values), censored=sum(not v["ok"] for v in values),
                    observed=stats([v["elapsed"] for v in values]),
                    completed_only=stats([v["elapsed"] for v in values if v["ok"]]))
    report = dict(populations={k:population(v) for k,v in groups.items() if k in ("original", "guarded")},
                  files=files, components=timelines, focal=focal,
                  initial_startups=startup_initial, recovery_startups=startup_recovery,
                  timeline_summary=dict(initial=phase_stats(startup_initial), recovery=phase_stats(startup_recovery)),
                  diagnostic_failures=diagnostic_failures, cpu_samples=cpu_rows,
                  cpu_wall_ratio=stats([r["cpu_wall_ratio"] for r in cpu_rows]),
                  io_observations=io_rows, cgroups=cgroups,
                  imports_inclusive_top=sorted([dict(module=k, timing=stats(v), total=sum(v)) for k,v in imports.items()],
                    key=lambda r:r["total"], reverse=True)[:40], event_counts=dict(categories),
                  resource_samples=len(resources),
                  resource_peaks=dict(rss_kib=max((int(r["rss"].split()[0]) for r in resources if r["rss"]), default=0),
                    hwm_kib=max((int(r["hwm"].split()[0]) for r in resources if r["hwm"]), default=0),
                    threads=max((int(r["threads"]) for r in resources if r["threads"]), default=0),
                    fds=max((r["fds"] for r in resources), default=0)),
                  cautions=["Timeout observations are right-censored: completion time unknown, not a measured 4.000s completion.",
                            "Process-cold, not OS page-cache cold; original canonical integrity validation retained.",
                            "Diagnostic bootstrap is not pure original and is not performance acceptance.",
                            "Inclusive imports are nested; use exclusive component slices, not sum(top imports).",
                            "Windows host and Linux monotonic clocks are different domains, never subtracted."])
    with target.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({k:v for k,v in report.items() if k in ("populations", "event_counts", "resource_samples")}))


if __name__ == "__main__":
    main()
