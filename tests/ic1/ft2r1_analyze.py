"""FT-2R.1: every observation, no success filtering or fabricated tail."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
from ft2r_analyze import breakdown, stats as original_stats

MODULES = ("xoc_mcp_lab_l6.server", "xoc_mcp_lab_l4.server", "mcp", "mcp.client.session",
           "xoc_mcp_lab.adapter", "src.tool_gateway", "sqlalchemy", "src.shared.context")


def stats(values):
    row = original_stats(values)
    if row is None:
        return None
    seq = sorted(values)
    position = (len(seq)-1)*.75
    lo = int(position)
    row.update(p75=seq[lo]+(seq[min(lo+1, len(seq)-1)]-seq[lo])*(position-lo),
               mean=statistics.mean(seq), stddev_population=statistics.pstdev(seq))
    return row


def summarize(rows):
    complete = [r["seconds"] for r in rows if r["completed"]]
    bounds = [(0,4),(4,4.25),(4.25,4.5),(4.5,5),(5,6),(6,8),(8,12.000001)]
    return dict(n=len(rows), completed_naturally=len(complete), incomplete=sum(not r["completed"] for r in rows),
                natural_seconds=stats(complete),
                above={str(b):sum(v>b for v in complete) for b in (4,5,6,8,12)},
                buckets={f"[{a},{b})":sum(a<=v<b for v in complete) for a,b in bounds},
                incomplete_observations=[r for r in rows if not r["completed"]],
                distance_max_to_4=None if not complete else 4-max(complete))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("directory", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists() or not args.output.resolve().is_relative_to(args.directory.resolve()):
        raise ValueError("FT2R1_NEW_OUTPUT_REQUIRED")
    groups, imports, slices = defaultdict(list), defaultdict(list), defaultdict(lambda:defaultdict(list))
    exclusive_imports = defaultdict(list)
    runs, files, cpu, resources, integrity, cgroups = [], {}, [], [], [], []
    for path in sorted(args.directory.glob("*/*.json")):
        row = json.loads(path.read_bytes())
        if row.get("mode") not in ("natural", "natural-recovery", "original", "guarded", "recovery-original"):
            continue
        key = path.relative_to(args.directory).as_posix()
        files[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        integrity.append(row["integrity"])
        phases = [v for v in row.get("control", []) if v.get("kind") == "phase" and v.get("phase") == "startup"]
        components = {v["generation"]:v for v in breakdown(row)} if row.get("timeline") else {}
        records = []
        for phase in phases:
            gen = phase["child_generation"]
            group = ("U1" if row["mode"] == "natural" else
                     "U2" if row["mode"] == "natural-recovery" and gen>1 else
                     "U2_initial_setup" if row["mode"] == "natural-recovery" else
                     row["mode"] + ("_replacement" if gen>1 else ""))
            ready = next((v for v in row["control"] if v.get("kind") == "ready" and v.get("child_generation") == gen), None)
            rec = dict(file=key, group=group, generation=gen, seconds=phase["elapsed"], completed=phase["ok"],
                       would_have_timed_out_at_4s=phase["elapsed"]>4, ready_equivalent_at=ready.get("ready_at") if ready else None,
                       phase=phase, components=components.get(gen))
            groups[group].append(rec)
            records.append(rec)
            comp = components.get(gen)
            if comp and not comp.get("missing_child_timeline"):
                cohort = "lt4" if phase["elapsed"]<4 else "gte4"
                for name, value in comp["components"].items():
                    slices[cohort][name].append(value["seconds"])
                events = sorted([e for e in row["timeline"] if e["pid"] == comp["child_pid"] and e["at"]<=phase["finished_at"]], key=lambda e:e["at"])
                begin = next(e for e in events if e["event"] == "S03_entry")
                last = events[-1]
                wall = last["at"]-begin["at"]
                if wall>0:
                    cpu.append(dict(file=key, group=group, generation=gen, startup=phase["elapsed"],
                                    wall=wall, cpu=last["cpu"]-begin["cpu"], ratio=(last["cpu"]-begin["cpu"])/wall))
        for event in row.get("timeline", []):
            if event["role"]=="server" and event["event"]=="import_end" and event["module"] in MODULES:
                imports[event["module"]].append(event["elapsed"])
                start, end = event["began"], event["began"]+event["elapsed"]
                nested = sorted((e["began"], min(end,e["began"]+e["elapsed"])) for e in row["timeline"]
                    if e["pid"]==event["pid"] and e["event"]=="import_end" and e["module"]!=event["module"]
                    and start<=e["began"]<end and e["began"]+e["elapsed"]<=end)
                covered, cursor = 0.0, start
                for a,b in nested:
                    if b>cursor:
                        covered += b-max(a,cursor)
                        cursor = b
                exclusive_imports[event["module"]].append(max(0.0,event["elapsed"]-covered))
        resources.extend(row.get("resources", []))
        cgroups += [row.get("cgroup_before", {}), row.get("cgroup_after", {})]
        cgroups += [r.get("cgroup", {}) for r in row.get("resources", [])]
        runs.append(dict(file=key, diagnostic_only=row.get("diagnostic_only", False),
                         outcome=row["passed"], initial_ready=row.get("initial_ready"), records=records,
                         final_children=row.get("final_children"), cleanup=row.get("cleanup"),
                         no_stdio=row.get("no_stdio"), recovery=row.get("recovery"),
                         cgroup_after=row.get("cgroup_after"), error_type=row.get("error_type")))
    sched = []
    for r in resources:
        if r.get("schedstat"):
            values = [int(v) for v in r["schedstat"].split()]
            sched.append(dict(runtime_seconds=values[0]/1e9, runqueue_wait_seconds=values[1]/1e9, slices=values[2]))
    def field_peak(path, field=None):
        values = []
        for cg in cgroups:
            raw = cg.get(path)
            if raw is None:
                continue
            if field is not None:
                pairs = dict(line.split() for line in raw.splitlines())
                raw = pairs.get(field)
            if raw is not None:
                values.append(int(raw))
        return max(values) if values else None
    report = dict(populations={k:summarize(v) for k,v in groups.items()}, runs=runs, files=files,
                  components_exclusive_seconds_by_cohort={c:{k:stats(v) for k,v in values.items()} for c,values in slices.items()},
                  import_inclusive_seconds={k:stats(v) for k,v in imports.items()}, cpu_samples=cpu,
                  import_exclusive_of_observed_children_seconds={k:stats(v) for k,v in exclusive_imports.items()},
                  cpu_wall_all=stats([v["ratio"] for v in cpu]), cpu_wall_above4=stats([v["ratio"] for v in cpu if v["startup"]>4]),
                  resource_samples=len(resources), resource_peaks=dict(
                      rss_kib=max((int(r["rss"].split()[0]) for r in resources if r["rss"]), default=0),
                      hwm_kib=max((int(r["hwm"].split()[0]) for r in resources if r["hwm"]), default=0),
                      threads=max((int(r["threads"]) for r in resources if r["threads"]), default=0),
                      fds=max((r["fds"] for r in resources), default=0),
                      process_cumulative_runqueue_wait_seconds=max((s["runqueue_wait_seconds"] for s in sched), default=None)),
                  cgroup_peaks=dict(memory_bytes=field_peak("memory/memory.max_usage_in_bytes"),
                      memory_failcnt=field_peak("memory/memory.failcnt"),
                      oom_kill=field_peak("memory/memory.oom_control", "oom_kill"),
                      under_oom=field_peak("memory/memory.oom_control", "under_oom"),
                      pids_current=field_peak("pids/pids.current"), pids_max_events=field_peak("pids/pids.events", "max"),
                      nr_throttled=field_peak("cpu/cpu.stat", "nr_throttled"),
                      throttled_time_ns=field_peak("cpu/cpu.stat", "throttled_time")),
                  integrity_identical=all(v==integrity[0] for v in integrity) if integrity else False,
                  integrity=integrity[0] if integrity else None,
                  cautions=["Diagnostic READY is never production acceptance.", "U2 setup cold starts are separate, not added to U1.",
                            "Incomplete phases are NOT imputed as natural completion times.",
                            "Imports are inclusive/nested. Exclusive phase slices do not double-count.",
                            "Module exclusive timings exclude instrumented child imports only; uninstrumented imports remain in own time.",
                            "Natural cohorts have probe overhead; original/guarded controls do not load it.",
                            "Cold means new process/container, not flushed OS page cache."])
    with args.output.open("x", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(json.dumps({k:report[k] for k in ("populations", "resource_peaks", "cpu_wall_all", "integrity_identical")}, indent=2))


if __name__ == "__main__":
    main()
