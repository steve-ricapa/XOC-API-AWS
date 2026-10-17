"""Read-only SOPHIA failure observer. Never print tokens, raw traces or payloads.

Usage: python -B tests/ac1/azure_failure_observe.py
Requires the Azure CLI login for subscription XOC-AZURE. The watermark is the
first deployment of stage/terminal telemetry; older failures lack this detail.
"""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone

import requests


SUBSCRIPTION = "884feb5e-8492-4d88-8777-6391393372f5"
APP_ID = "c854497d-ab9d-4dd7-be21-8ecbb68cb789"
INSTRUMENTED_AT = "2026-10-13T05:52:19Z"
APP_URL = f"https://api.applicationinsights.io/v1/apps/{APP_ID}/query"


def queries(observed_at=None):
    # Application Insights can serve a cached response for an identical query.
    # Bind a fresh UTC upper bound so a repeat invocation sees newly ingested events.
    observed_at = observed_at or datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    window = (f"timestamp >= datetime({INSTRUMENTED_AT}) "
              f"and timestamp <= datetime({observed_at}) and timestamp > ago(72h)")
    return {
        "requests": (
            f"requests | where {window} and name == 'sophia_run' "
            "| summarize count=count(), newest=max(timestamp), p95_ms=percentile(duration,95) by resultCode"
        ),
        "failures": (
            f"requests | where {window} and name == 'sophia_run' and resultCode startswith '5' "
            "| top 20 by timestamp desc | project timestamp,operation_Id,resultCode,duration"
        ),
    }


def stage_query(operation_ids):
    ids = [value for value in operation_ids if re.fullmatch(r"[a-f0-9]{32}", value)]
    if not ids:
        return None
    selected = ",".join(f"'{value}'" for value in ids[:20])
    return (
        f"traces | where operation_Id in ({selected}) "
        "and (message startswith 'sophia_provider_stage' or message startswith 'sophia_provider_terminal' "
        "or message startswith 'sophia_gateway_error') "
        "| extend phase=extract('phase=([a-z_]+)',1,message), "
        "stage=extract('stage=([a-z_]+)',1,message), "
        "outcome=extract('outcome=([a-z_]+)',1,message), "
        "status=extract('status=([a-z_]+)',1,message), "
        "code=extract('code=([a-z_]+)',1,message), "
        "http_status=extract('http_status=([0-9]+)',1,message), "
        "duration_ms=extract('duration_ms=([0-9]+)',1,message) "
        "| project timestamp,operation_Id,phase,stage,outcome,status,code,http_status,duration_ms "
        "| order by timestamp asc"
    )


def main():
    # The token stays in this process. Use the Python installation with azure.cli
    # rather than az.cmd, whose launcher can select the wrong Python on Windows.
    token = json.loads(subprocess.check_output([
        sys.executable, "-m", "azure.cli", "account", "get-access-token",
        "--subscription", SUBSCRIPTION, "--resource", "https://api.applicationinsights.io",
        "-o", "json",
    ], stderr=subprocess.DEVNULL))["accessToken"]

    def read(query):
        response = requests.get(APP_URL, headers={"Authorization": "Bearer " + token},
                                params={"query": query}, timeout=30)
        if not response.ok:
            raise RuntimeError("Application Insights query unavailable")
        tables = response.json().get("tables", [])
        if not tables:
            return []
        fields = [column["name"] for column in tables[0]["columns"]]
        return [dict(zip(fields, row)) for row in tables[0]["rows"]]

    statements = queries()
    summary = read(statements["requests"])
    failures = read(statements["failures"])
    stages = read(stage_query([row["operation_Id"] for row in failures])) if failures else []
    print(json.dumps({"instrumented_at": INSTRUMENTED_AT, "summary": summary,
                      "failures": failures, "stages": stages}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, requests.RequestException, RuntimeError, subprocess.CalledProcessError):
        print("SOPHIA telemetry unavailable; no raw response or credentials printed", file=sys.stderr)
        raise SystemExit(1) from None
