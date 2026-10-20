"""Regression tests for the multi-plan approval flow and the automation state machine contract."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("TICKETS_TABLE_NAME", "ticket-approval-test")

from src.handlers.workers import approval_callback, public_approval
from src.shared.errors import ValidationError
from src.shared.tickets_store import resolve_executable_plan

REPO_ROOT = Path(__file__).resolve().parents[1]


def _ticket_with_options(options, recommended_id=None, action_plan=None):
    ticket = {
        "ticket_id": "t1",
        "tenant_id": 7,
        "status": "PREAPROBADO",
        "pending_decision": {
            "options": options,
            "recommended_option_id": recommended_id,
        },
    }
    if action_plan is not None:
        ticket["action_plan"] = action_plan
    return ticket


class ResolveExecutablePlanTests(unittest.TestCase):
    def test_prefers_recommended_option(self):
        ticket = _ticket_with_options(
            [
                {"option_id": "plan-1", "plan": [{"action_type": "install"}]},
                {"option_id": "plan-2", "plan": [{"action_type": "wipe"}]},
            ],
            recommended_id="plan-2",
        )
        plan = resolve_executable_plan(ticket)
        self.assertEqual({"steps": [{"action_type": "wipe"}], "source": "plan-selection"}, plan)

    def test_falls_back_to_first_valid_option(self):
        ticket = _ticket_with_options(
            [
                {"option_id": "plan-1", "plan": []},
                {"option_id": "plan-2", "plan": [{"action_type": "restart"}]},
            ],
            recommended_id="plan-1",
        )
        plan = resolve_executable_plan(ticket)
        self.assertEqual({"steps": [{"action_type": "restart"}], "source": "plan-selection"}, plan)

    def test_falls_back_to_action_plan_when_no_options(self):
        ticket = _ticket_with_options([], action_plan={"steps": [{"action_type": "update"}]})
        plan = resolve_executable_plan(ticket)
        self.assertEqual({"steps": [{"action_type": "update"}]}, plan)

    def test_returns_none_when_no_plan_exists(self):
        self.assertIsNone(resolve_executable_plan(_ticket_with_options([])))
        self.assertIsNone(resolve_executable_plan(None))
        self.assertIsNone(resolve_executable_plan({"pending_decision": None}))


class ApprovalCallbackTests(unittest.TestCase):
    def test_approved_callback_includes_selected_plan(self):
        ticket = _ticket_with_options(
            [{"option_id": "plan-1", "plan": [{"action_type": "install"}]}],
            recommended_id="plan-1",
        )
        sf = MagicMock()
        with patch.object(approval_callback, "get_ticket_by_id_or_none", return_value=ticket), patch.object(approval_callback, "stepfunctions", sf):
            result = approval_callback.handler({"ticketId": "t1", "taskToken": "tok", "approved": True}, None)
        self.assertTrue(result["approved"])
        sf.send_task_success.assert_called_once()
        output = json.loads(sf.send_task_success.call_args.kwargs["output"])
        self.assertTrue(output["approved"])
        self.assertEqual({"steps": [{"action_type": "install"}], "source": "plan-selection"}, output["selected_plan"])

    def test_rejected_callback_does_not_require_plan(self):
        sf = MagicMock()
        with patch.object(approval_callback, "get_ticket_by_id_or_none", return_value=None), patch.object(approval_callback, "stepfunctions", sf):
            result = approval_callback.handler({"ticketId": "t1", "taskToken": "tok", "approved": False, "tenantId": 7}, None)
        self.assertFalse(result["approved"])
        output = json.loads(sf.send_task_success.call_args.kwargs["output"])
        self.assertEqual({"approved": False}, output)

    def test_approved_without_plan_is_rejected_instead_of_crashing_workflow(self):
        ticket = _ticket_with_options([])
        sf = MagicMock()
        with patch.object(approval_callback, "get_ticket_by_id_or_none", return_value=ticket), patch.object(approval_callback, "stepfunctions", sf):
            with self.assertRaises(ValidationError):
                approval_callback.handler({"ticketId": "t1", "taskToken": "tok", "approved": True}, None)
        sf.send_task_success.assert_not_called()


class PublicApprovalTests(unittest.TestCase):
    def test_public_approve_includes_selected_plan(self):
        ticket = _ticket_with_options(
            [{"option_id": "plan-1", "plan": [{"action_type": "install"}]}],
            recommended_id="plan-1",
        )
        ticket["pending_decision"]["max_risk_level"] = "basic"
        sf = MagicMock()
        event = {"body": json.dumps({"ticketId": "t1", "taskToken": "tok", "approved": True})}
        with patch.object(public_approval, "get_ticket_by_id_or_none", return_value=ticket), patch.object(public_approval, "stepfunctions", sf):
            result = public_approval.handler(event, None)
        self.assertEqual(200, result["statusCode"])
        output = json.loads(sf.send_task_success.call_args.kwargs["output"])
        self.assertIn("selected_plan", output)

    def test_public_approve_without_plan_returns_400(self):
        ticket = _ticket_with_options([])
        ticket["pending_decision"]["max_risk_level"] = "basic"
        sf = MagicMock()
        event = {"body": json.dumps({"ticketId": "t1", "taskToken": "tok", "approved": True})}
        with patch.object(public_approval, "get_ticket_by_id_or_none", return_value=ticket), patch.object(public_approval, "stepfunctions", sf):
            result = public_approval.handler(event, None)
        self.assertEqual(400, result["statusCode"])
        sf.send_task_success.assert_not_called()

    def test_non_basic_risk_is_forbidden(self):
        ticket = _ticket_with_options(
            [{"option_id": "plan-1", "plan": [{"action_type": "restart"}]}],
            recommended_id="plan-1",
        )
        ticket["pending_decision"]["max_risk_level"] = "critical"
        sf = MagicMock()
        event = {"body": json.dumps({"ticketId": "t1", "taskToken": "tok", "approved": True})}
        with patch.object(public_approval, "get_ticket_by_id_or_none", return_value=ticket), patch.object(public_approval, "stepfunctions", sf):
            result = public_approval.handler(event, None)
        self.assertEqual(403, result["statusCode"])
        sf.send_task_success.assert_not_called()


class StateMachineDefinitionTests(unittest.TestCase):
    """Guard the ASL contract against regressions that only fail at runtime."""

    @classmethod
    def setUpClass(cls):
        source = (REPO_ROOT / "serverless" / "services" / "automation-resources.js").read_text(encoding="utf-8")
        cls.source = source

    def test_increment_attempt_carries_plans_for_retry_loop(self):
        start = self.source.index("IncrementAttempt")
        block = self.source[start : self.source.index("Next:", start)]
        self.assertIn("'plans.$': '$.state.plans'", block)

    def test_wait_for_approval_requires_plans_and_execute_requires_selected_plan(self):
        wait = self.source[self.source.index("WaitForApproval") : self.source.index("CheckApproval")]
        self.assertIn("'plans.$': '$.state.plans'", wait)
        execute = self.source[self.source.index("ExecuteTicketPlan") : self.source.index("CheckTicketStatus")]
        self.assertIn("'plan.$': '$.approval.selected_plan'", execute)

    def test_compiled_definition_keeps_retry_and_selected_plan_contract(self):
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available to evaluate the Serverless resource module")
        script = (
            "const mod = require('./serverless/services/automation-resources.js');"
            "const res = mod('prod');"
            "const raw = res.Resources.AutomationWorkflowStateMachine.Properties.DefinitionString['Fn::Sub'][0];"
            "const def = JSON.parse(raw);"
            "const s = def.States;"
            "console.log(JSON.stringify({"
            "incrementHasPlans: Object.prototype.hasOwnProperty.call(s.IncrementAttempt.Parameters, 'plans.$'),"
            "waitPayloadHasPlans: Object.prototype.hasOwnProperty.call(s.WaitForApproval.Parameters.Payload, 'plans.$'),"
            "executeHasSelectedPlan: Object.prototype.hasOwnProperty.call(s.ExecuteTicketPlan.Parameters, 'plan.$')"
            "}));"
        )
        completed = subprocess.run(
            [node, "-e", script],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        flags = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertTrue(flags["incrementHasPlans"], "IncrementAttempt must carry plans for the retry loop")
        self.assertTrue(flags["waitPayloadHasPlans"], "WaitForApproval payload requires plans")
        self.assertTrue(flags["executeHasSelectedPlan"], "ExecuteTicketPlan requires approval.selected_plan")


if __name__ == "__main__":
    unittest.main()
