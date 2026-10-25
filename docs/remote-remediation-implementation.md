# Remote remediation implementation status

## Implemented, not yet connected to the ticket workflow

- `src/remediation/intune.py`: application OAuth, managed device inventory,
  existing remediation package inventory, on-demand submission and polling of a
  fresh result for the exact device and Microsoft tenant. HTTP 204 means submitted,
  not resolved. `NO_CHANGE_NEEDED` is distinct from a successful execution.
- `src/remediation/api_information.py`: GET JSON from configured public HTTPS
  origins. Redirects are disabled, foreign origins and private destinations rejected.
  Enterprise private APIs require an appliance-side connector. Host configuration
  must be trusted and stable; this helper alone is not a DNS-rebinding boundary.
- `src/remediation/rollback.py`: builds a new compensating plan in reverse order
  from successful steps with declared compensation and verification commands.
  New risk evaluation and approval are required. Irreversible or uncertain changes
  are rejected. No execution or endpoint authorization is implied by this helper.
- `scripts/intune_pilot.py`: administrative integration pilot for pre-provisioned
  packages on explicitly allowlisted lab devices, including a mapped compensation
  package. It uses Microsoft app privileges, not XOC user governance. Do not expose
  it as an unauthenticated API or equate it with production ticket integration.

These additions do not change the existing tickets, approvals, workflow or Victor
execution. They do not yet add frontend actions, Defender ingestion, SSH, durable
remote jobs or a rollback button. Do not deploy them as if those features existed.

## Microsoft prerequisites

Use an enrolled Windows test device with Intune Management Extension and the
required licensing. Create an Entra application with admin consent for the API
permissions needed by the chosen operations:

- Device inventory: `DeviceManagementManagedDevices.Read.All`.
- Package inventory/results: check current `deviceHealthScripts` permissions;
  Microsoft has moved script operations to `DeviceManagementScripts.Read.All`
  or `DeviceManagementScripts.ReadWrite.All` where applicable.
- On-demand execution: `DeviceManagementManagedDevices.PrivilegedOperations.All`.

Secrets must be configured securely outside Git. The pilot reads:

```text
XOC_MS_TENANT_ID
XOC_MS_CLIENT_ID
XOC_MS_CLIENT_SECRET
```

Example local configuration (IDs are examples, not runnable credentials):

```json
{
  "microsoft_tenant_id": "your-entra-tenant-id",
  "allowed_device_ids": ["your-intune-managed-device-id"],
  "policies": {
    "your-forward-package-id": {
      "rollback_policy_id": "your-compensation-package-id"
    }
  }
}
```

Use a reversible lab package, such as moving an inoffensive file to a known
quarantine directory and restoring it from the compensation package. Both must
detect current state, validate the intended device/path, retain recovery data,
verify the final state and be idempotent. A file deleted without backup cannot
be restored by inventing a reverse command. An OS restore point is not a universal
compensation for all remote changes.

```bash
python3 scripts/intune_pilot.py inventory --config /secure/lab.json
python3 scripts/intune_pilot.py policies --config /secure/lab.json

# Preview only:
python3 scripts/intune_pilot.py submit --config /secure/lab.json \
  --device-id DEVICE_ID --policy-id PACKAGE_ID --operation /secure/operation.json

# After package review, submit explicitly:
python3 scripts/intune_pilot.py submit --config /secure/lab.json \
  --device-id DEVICE_ID --policy-id PACKAGE_ID --operation /secure/operation.json --confirm

python3 scripts/intune_pilot.py status --config /secure/lab.json --operation /secure/operation.json

# Preview mapped compensation; --confirm is required to dispatch it:
python3 scripts/intune_pilot.py rollback --config /secure/lab.json --operation /secure/operation.json
```

Run one operation per device/package at a time. Graph returns latest package/device
state, not a dedicated execution ID from the submission. Freshness alone cannot
disambiguate concurrent submissions of the same package. An uncertain HTTP outcome
must not be blindly retried. The pilot reserves the operation file before dispatch;
production requires a durable distributed lock and an operation ledger.

## Next production integration, before enabling remote execution

1. Register tenant-bound Microsoft connections through encrypted integration
   credentials. Bind device ownership and approved package/version allowlists.
2. Extend the plan contract with typed `api_read` and `intune_remediation` actions,
   an explicit target and declared compensation. Preserve them through custom
   preview, option selection, risk evaluation and execution. They must never be
   interpreted as local shell commands.
3. Add durable dispatch/poll/verify states to the workflow. Keep offline devices
   pending with deadlines; do not hold a Lambda open while waiting for a laptop.
   Bind approvals to the target, package content/version and plan fingerprint.
4. Add rollback preview and a newly approved compensating workflow. Reserve one
   rollback per original execution, preserve original logs and record undo results
   separately. Include original and rollback risk in the UI.
5. Add UI configuration, device selection, asynchronous progress and rollback
   eligibility. Preserve the current shell executor for existing tickets.
6. Test a real lab laptop: before/after evidence, offline recovery, insufficient
   roles, repeated clicks, partial failure, stale results and compensation.

## Sources

- https://learn.microsoft.com/en-us/graph/api/intune-devices-manageddevice-initiateondemandproactiveremediation?view=graph-rest-beta
- https://learn.microsoft.com/en-us/graph/api/resources/intune-devices-devicehealthscriptdevicestate?view=graph-rest-beta
- https://learn.microsoft.com/en-us/intune/device-management/tools/deploy-remediations

The execution and state APIs above use Microsoft Graph beta; validate their
contract and permissions against the actual tenant before production activation.

## Offline validation

```bash
python3 tests/test_remote_remediation_adapters.py
```

This validates mocked contracts, not a real Microsoft connection or laptop change.
