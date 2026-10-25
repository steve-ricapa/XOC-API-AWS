"""Offline contracts: no Microsoft tenant or laptop is contacted."""
from datetime import datetime, timedelta, timezone
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.remediation.intune import IntuneClient, IntuneOperation, RemoteError
from src.remediation.api_information import query_information
from src.remediation.rollback import RollbackUnavailable, build_rollback_plan


class IntuneTests(unittest.TestCase):
    def setUp(self):
        self.transport = Mock()
        self.client = IntuneClient("tenant-a", "secret", transport=self.transport)
        self.now = datetime.now(timezone.utc)
        self.op = IntuneOperation("tenant-a", "device-a", "policy-a", self.now.isoformat())

    def state(self, **changes):
        value = {"managedDevice": {"id": "device-a"}, "lastStateUpdateDateTime": (self.now + timedelta(seconds=1)).isoformat(),
                 "remediationState": "success", "detectionState": "success"}
        return {**value, **changes}

    def poll(self, states):
        self.transport.return_value = (200, {"value": states})
        return self.client.poll(self.op)

    def test_submission_only_returns_operation(self):
        self.transport.return_value = (204, None)
        operation = self.client.submit("device-a", "policy-a")
        self.assertIsInstance(operation, IntuneOperation)
        self.assertNotIn("SUCCEEDED", json.dumps(operation.as_dict()))
        args, kwargs = self.transport.call_args
        self.assertIn("managedDevices/device-a/initiateOnDemandProactiveRemediation", args[1])
        self.assertEqual(json.loads(kwargs["data"]), {"scriptPolicyId": "policy-a"})

    def test_submission_rejects_wrong_response(self):
        self.transport.return_value = (200, {})
        with self.assertRaises(RemoteError):
            self.client.submit("device-a", "policy-a")

    def test_stale_success_is_not_current_success(self):
        self.assertEqual(self.poll([self.state(lastStateUpdateDateTime=(self.now - timedelta(days=1)).isoformat())])["status"], "PENDING")

    def test_other_device_result_is_ignored(self):
        self.assertEqual(self.poll([self.state(managedDevice={"id": "another-device"})])["status"], "PENDING")

    def test_success_requires_detection_success(self):
        self.assertEqual(self.poll([self.state(detectionState="fail")])["status"], "PENDING")
        self.assertEqual(self.poll([self.state()])["status"], "SUCCEEDED")

    def test_error_is_not_success(self):
        for change in ({"remediationState": "remediationFailed"}, {"remediationState": "scriptError"},
                       {"postRemediationDetectionScriptError": "verify failed"}):
            self.assertEqual(self.poll([self.state(**change)])["status"], "FAILED")

    def test_skipped_is_distinct_from_executed(self):
        self.assertEqual(self.poll([self.state(remediationState="skipped")])["status"], "NO_CHANGE_NEEDED")

    def test_cross_tenant_operation_rejected(self):
        with self.assertRaises(RemoteError):
            self.client.poll(IntuneOperation("tenant-b", "device-a", "policy-a", self.now.isoformat()))
        self.transport.assert_not_called()

    def test_bearer_token_never_follows_foreign_nextlink(self):
        self.transport.return_value = (200, {"value": [], "@odata.nextLink": "https://attacker.invalid/v1.0/devices"})
        with self.assertRaises(RemoteError):
            self.client.list_devices()
        self.assertEqual(self.transport.call_count, 1)

    def test_identifier_path_injection_rejected(self):
        with self.assertRaises(ValueError):
            self.client.submit("../devices", "policy-a")
        self.transport.assert_not_called()

    def test_authentication_contract(self):
        self.transport.return_value = (200, {"access_token": "secret"})
        client = IntuneClient.from_client_credentials("tenant-a", "app", "private", transport=self.transport)
        self.assertEqual(client.microsoft_tenant_id, "tenant-a")
        self.assertNotIn("private", self.transport.call_args.args[1])
        self.assertIn(b"grant_type=client_credentials", self.transport.call_args.kwargs["data"])


class InformationTests(unittest.TestCase):
    def query(self, **kwargs):
        return query_information("https://api.example.com", "/status", allowed_hosts={"api.example.com"}, **kwargs)

    def test_get_information(self):
        transport = Mock(return_value=(200, {"healthy": True}))
        with patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
            self.assertEqual(self.query(transport=transport), {"healthy": True})
        self.assertEqual(transport.call_args.args[0], "GET")

    def test_private_address_rejected(self):
        with patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("169.254.169.254", 443))]):
            with self.assertRaises(ValueError):
                self.query(transport=Mock())

    def test_unregistered_origin_rejected(self):
        with self.assertRaises(ValueError):
            query_information("https://unknown.invalid", "/", allowed_hosts={"api.example.com"})

    def test_origin_override_rejected(self):
        with self.assertRaises(ValueError):
            query_information("https://api.example.com", "//attacker.invalid", allowed_hosts={"api.example.com"})


class RollbackTests(unittest.TestCase):
    def plan(self):
        return {"steps": [{"step_id": "inspect", "command": "ls /tmp/x", "risk_level": "basic"},
                          {"step_id": "move", "command": "mv /tmp/x /tmp/backup", "risk_level": "controlled",
                           "rollback": {"supported": True, "command": "mv /tmp/backup /tmp/x",
                                        "verification_command": "test -f /tmp/x", "risk_level": "controlled"}}]}

    def results(self):
        return [{"step_id": "inspect", "status": "ok", "success": True},
                {"step_id": "move", "status": "ok", "success": True}]

    def test_compensation_is_a_new_plan_requiring_approval(self):
        result = build_rollback_plan(self.plan(), self.results())
        self.assertTrue(result["plan"]["requires_new_approval"])
        self.assertEqual(result["plan"]["steps"][0]["command"], "mv /tmp/backup /tmp/x")
        self.assertEqual(result["plan"]["steps"][1]["command"], "test -f /tmp/x")
        self.assertEqual(result["requirement"]["required_approver_role"], "ADMIN")

    def test_irreversible_delete_rejected(self):
        plan = self.plan()
        plan["steps"][1] = {"step_id": "move", "command": "rm -f /tmp/x", "risk_level": "basic"}
        with self.assertRaises(RollbackUnavailable):
            build_rollback_plan(plan, self.results())

    def test_compensation_risk_cannot_be_masked(self):
        plan = self.plan()
        plan["steps"][1]["rollback"].update(command="rm -rf /", risk_level="basic")
        self.assertEqual(build_rollback_plan(plan, self.results())["requirement"]["required_approver_role"], "SUPERADMIN")

    def test_missing_verification_rejected(self):
        plan = self.plan()
        del plan["steps"][1]["rollback"]["verification_command"]
        with self.assertRaises(RollbackUnavailable):
            build_rollback_plan(plan, self.results())

    def test_failed_or_missing_mutation_requires_manual_assessment(self):
        for results in (self.results()[:1], [self.results()[0], {"step_id": "move", "status": "failed", "success": False}]):
            with self.assertRaises(RollbackUnavailable):
                build_rollback_plan(self.plan(), results)

    def test_duplicate_execution_results_rejected(self):
        with self.assertRaises(RollbackUnavailable):
            build_rollback_plan(self.plan(), self.results() + self.results())

    def test_compensations_run_in_reverse_order(self):
        plan = self.plan()
        plan["steps"].append({"step_id": "config", "command": "cp /tmp/new /tmp/config", "risk_level": "controlled",
                              "rollback": {"supported": True, "command": "cp /tmp/old /tmp/config", "verification_command": "test -f /tmp/config"}})
        results = self.results() + [{"step_id": "config", "status": "ok", "success": True}]
        self.assertEqual(build_rollback_plan(plan, results)["plan"]["steps"][0]["step_id"], "rollback-config-undo")


class PilotTests(unittest.TestCase):
    def invoke(self, action, *, confirmed=False, existing=False, remote_status="SUCCEEDED"):
        spec = importlib.util.spec_from_file_location("intune_pilot", Path(__file__).resolve().parents[1] / "scripts/intune_pilot.py")
        pilot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pilot)
        with tempfile.TemporaryDirectory() as temp:
            config, operation = Path(temp) / "lab.json", Path(temp) / "operation.json"
            config.write_text(json.dumps({"microsoft_tenant_id": "tenant-a", "allowed_device_ids": ["device-a"],
                                          "policies": {"policy-a": {"rollback_policy_id": "undo-a"}}}))
            client = Mock()
            remote_op = IntuneOperation("tenant-a", "device-a", "policy-a", datetime.now(timezone.utc).isoformat())
            client.submit.return_value = remote_op
            client.poll.return_value = {"status": remote_status}
            if action == "rollback" or existing:
                operation.write_text(json.dumps({"status": "SUBMITTED", "operation": remote_op.as_dict()}))
            args = ["pilot", action, "--config", str(config), "--operation", str(operation),
                    "--device-id", "device-a", "--policy-id", "policy-a"]
            if confirmed:
                args.append("--confirm")
            with patch.dict(os.environ, {"XOC_MS_TENANT_ID": "tenant-a", "XOC_MS_CLIENT_ID": "app", "XOC_MS_CLIENT_SECRET": "secret"}), \
                    patch("sys.argv", args), patch.object(pilot.IntuneClient, "from_client_credentials", return_value=client), \
                    contextlib.redirect_stdout(io.StringIO()):
                try:
                    pilot.main()
                except Exception as exc:
                    return client, exc
            return client, None

    def test_submit_preview_never_dispatches(self):
        client, error = self.invoke("submit")
        self.assertIsNone(error)
        client.submit.assert_not_called()

    def test_confirmed_submit_uses_allowlisted_package(self):
        client, error = self.invoke("submit", confirmed=True)
        self.assertIsNone(error)
        client.submit.assert_called_once_with("device-a", "policy-a")

    def test_existing_operation_is_not_replayed(self):
        client, error = self.invoke("submit", confirmed=True, existing=True)
        self.assertIsInstance(error, FileExistsError)
        client.submit.assert_not_called()

    def test_rollback_requires_successful_forward_result(self):
        client, error = self.invoke("rollback", confirmed=True, remote_status="PENDING")
        self.assertIsInstance(error, ValueError)
        client.submit.assert_not_called()

    def test_rollback_dispatches_only_mapped_compensation(self):
        client, error = self.invoke("rollback", confirmed=True)
        self.assertIsNone(error)
        client.submit.assert_called_once_with("device-a", "undo-a")


if __name__ == "__main__":
    unittest.main()
