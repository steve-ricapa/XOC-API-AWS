"""Administrative pilot for approved Intune packages; not a public XOC endpoint.

Credentials are read only from XOC_MS_* environment variables. This tool operates
as the configured Microsoft application and must only be run by its administrator.
"""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.remediation.intune import IntuneClient, IntuneOperation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "policies", "submit", "status", "rollback"))
    parser.add_argument("--config", required=True, help="Local allowlist of laboratory devices and script packages")
    parser.add_argument("--device-id")
    parser.add_argument("--policy-id")
    parser.add_argument("--operation", help="Operation JSON path, stored locally with restricted permissions")
    parser.add_argument("--confirm", action="store_true", help="Explicitly authorize dispatch of an allowlisted package")
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    tenant = os.environ.get("XOC_MS_TENANT_ID")
    if config.get("microsoft_tenant_id") != tenant:
        raise ValueError("Configuration and credential tenant must match")
    client = IntuneClient.from_client_credentials(
        tenant, os.environ.get("XOC_MS_CLIENT_ID"), os.environ.get("XOC_MS_CLIENT_SECRET"))
    if args.action == "inventory":
        print(json.dumps(client.list_devices(), indent=2))
        return
    if args.action == "policies":
        print(json.dumps(client.list_policies(), indent=2))
        return
    if not args.operation:
        raise ValueError("--operation is required")
    operation_path = Path(args.operation)
    if args.action in ("status", "rollback"):
        stored = json.loads(operation_path.read_text(encoding="utf-8"))
        if stored.get("status") in ("DISPATCHING", "UNKNOWN"):
            raise ValueError("Dispatch outcome is uncertain; inspect Intune before any retry")
        operation = IntuneOperation(**stored["operation"])
        if operation.device_id not in config.get("allowed_device_ids", []):
            raise ValueError("Device no longer authorized by the laboratory configuration")
        result = client.poll(operation)
        if args.action == "status":
            print(json.dumps(result, indent=2))
            return
        if result["status"] != "SUCCEEDED":
            raise ValueError("Rollback requires a fresh successful forward result; assess other states manually")
        policy = (config.get("policies", {}).get(operation.policy_id) or {}).get("rollback_policy_id")
        if not policy:
            raise ValueError("No pre-provisioned compensation policy exists for this package")
        device = operation.device_id
        operation_path = operation_path.with_name(operation_path.stem + "-rollback.json")
    else:
        device, policy = args.device_id, args.policy_id
        if policy not in config.get("policies", {}):
            raise ValueError("Policy not authorized by the laboratory configuration")
    if device not in config.get("allowed_device_ids", []):
        raise ValueError("Device not authorized by the laboratory configuration")
    if not args.confirm:
        print(json.dumps({"status": "PREVIEW", "device_id": device, "policy_id": policy,
                          "message": "No order sent. Add --confirm after reviewing the package."}, indent=2))
        return
    record = {"status": "DISPATCHING", "device_id": device, "policy_id": policy}
    # Exclusive creation prevents retrying the same operation file after a crash.
    with operation_path.open("x", encoding="utf-8") as handle:
        json.dump(record, handle)
    try:
        operation = client.submit(device, policy)
    except Exception:
        operation_path.write_text(json.dumps({**record, "status": "UNKNOWN"}), encoding="utf-8")
        raise
    operation_path.write_text(json.dumps({"status": "SUBMITTED", "operation": asdict(operation)}, indent=2), encoding="utf-8")
    print(f"SUBMITTED: {operation_path}. The device has not yet been confirmed remediated.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
