"""Run each downstream gate ONCE, in order. Never deploy, stage, or commit."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def check_populations(root):
    evidence = []
    for name, n, generations in (("original-30",30,1), ("guarded-30",30,1), ("recovery-20",20,2)):
        paths = sorted((root/name).glob("run-*.json"))
        assert len(paths)==n, "FT2R1_INCOMPLETE_POPULATION"
        for path in paths:
            r = json.loads(path.read_bytes())
            phases = [p for p in r["control"] if p.get("kind")=="phase" and p.get("phase")=="startup"]
            assert r["passed"] and len(phases)==generations, "FT2R1_STARTUP_FAILURE"
            assert all(p["ok"] and p["budget"]==4.0 and p["elapsed"]<=4.0 for p in phases), "FT2R1_ORIGINAL_CONTRACT_FAILURE"
            assert not r.get("diagnostic_only"), "FT2R1_DIAGNOSTIC_NOT_ACCEPTANCE"
            assert not r.get("timeline"), "FT2R1_PROFILER_NOT_ACCEPTANCE"
            assert not r["final_children"] and not r["cleanup"]["own_server_residual"] and not r["cleanup"]["harness_forced"], "FT2R1_CLEANUP_FAILURE"
            assert not any(r["isolation"].values()), "FT2R1_ISOLATION_FAILURE"
            if name=="guarded-30":
                assert r["no_stdio"]["passed"] and r["no_stdio"]["guarded_processes"]==3, "FT2R1_GUARD_FAILURE"
            if generations==2:
                assert r["recovery"]["recovery"]["ok"] and r["recovery"]["restoration"]["correct"], "FT2R1_RECOVERY_FAILURE"
            evidence.append(dict(file=str(path.relative_to(root)), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return evidence


def main():
    p = argparse.ArgumentParser()
    p.add_argument("directory", type=Path)
    p.add_argument("--image", required=True)
    p.add_argument("--mcp", type=Path, required=True)
    p.add_argument("--dependencies", type=Path, required=True)
    args = p.parse_args()
    root = args.directory.resolve(strict=True)
    assert root.is_relative_to(HERE/".build"), "FT2R1_INVALID_OUTPUT"
    target = root/"gate.json"
    assert not target.exists(), "FT2R1_NO_RERUN"
    if subprocess.check_output(["docker", "ps", "-q"], text=True).strip():
        raise RuntimeError("FT2R1_CONCURRENT_CONTAINER_STOP")
    result = dict(passed=False, stages=[], populations=[])
    try:
        result["populations"] = check_populations(root)
        for mode in ("tests", "recovery", "smoke", "provenance", "guard", "ic1"):
            print("START " + mode, flush=True)
            output = root/("gate-"+mode+".json")
            command = [sys.executable,"-B",str(HERE/"fast_track.py"),mode,"--image",args.image,"--output",str(output)]
            run = subprocess.run(command, capture_output=True, timeout=660)
            r = json.loads(output.read_bytes()) if output.exists() else dict(passed=False)
            result["stages"].append(dict(stage=mode, passed=r["passed"], exit_code=run.returncode,
                evidence=output.name, sha256=hashlib.sha256(output.read_bytes()).hexdigest() if output.exists() else None))
            print(json.dumps(result["stages"][-1]), flush=True)
            assert run.returncode==0 and r["passed"], "FT2R1_GATE_FAILED_"+mode
            if mode=="tests":
                assert len(r["tests"])==142 and all(t["outcome"]=="passed" for t in r["tests"]), "FT2R1_L6_COUNT"
            elif mode=="recovery":
                assert r["total"]==24 and len(r["cases"])==24 and all(c["passed"] for c in r["cases"]), "FT2R1_CASE_COUNT"
            elif mode=="ic1":
                assert r["tests"]==32 and r["failures"]==r["errors"]==0 and r["http_tool_calls"]>0, "FT2R1_IC1_COUNT"
        for suite in ("focal", "all"):
            print("START backend-"+suite, flush=True)
            output = root/("backend-"+suite+".json")
            run = subprocess.run([sys.executable,"-B",str(HERE/"backend_regression.py"),
                "--mcp",str(args.mcp),"--dependencies",str(args.dependencies),"--suite",suite,"--output",str(output)],
                capture_output=True, timeout=300)
            r = json.loads(output.read_bytes()) if output.exists() else dict(passed=False, cases=[])
            failures = [c for c in r["cases"] if c["outcome"]!="passed"]
            result["stages"].append(dict(stage="backend-"+suite, passed=r["passed"], exit_code=run.returncode,
                evidence=output.name, total=r.get("total"), failures=failures,
                sha256=hashlib.sha256(output.read_bytes()).hexdigest() if output.exists() else None))
            print(json.dumps(result["stages"][-1]), flush=True)
            if suite=="focal":
                assert run.returncode==0 and r["passed"] and r["total"]==120, "FT2R1_FOCAL_FAILED"
                assert r["network_attempts"]==r["secret_file_attempts"]==0, "FT2R1_FOCAL_ISOLATION"
            else:
                expected = {"test_assess_ticket_automation.AssessTicketAutomationTests.test_global_url_wins_and_marks_source_global":50,
                            "test_assess_ticket_automation.AssessTicketAutomationTests.test_normalize_plans_skips_empty_and_keeps_legacy":125}
                assert r["total"]==255 and len(failures)==2 and all(c["test"] in expected and c["category"]=="AssertionError"
                    and c["location"]["line"]==expected[c["test"]] for c in failures), "FT2R1_FULL_NEW_REGRESSION"
                assert r["secret_file_attempts"]==0, "FT2R1_FULL_SECRET_ACCESS"
                result["stages"][-1]["accepted_historical_only"] = True
        result["passed"] = True
    except Exception as error:
        result["error_type"] = type(error).__name__
        # Only our fixed assertion codes, never external exception content.
        if isinstance(error, AssertionError):
            result["gate_error"] = str(error)
        print(json.dumps(dict(passed=False, error_type=result["error_type"], gate=result.get("gate_error"))), flush=True)
    finally:
        with target.open("x", encoding="utf-8") as f:
            json.dump(result,f,indent=2)
    return int(not result["passed"])


if __name__=="__main__":
    raise SystemExit(main())
