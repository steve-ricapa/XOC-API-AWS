"""Unit coverage for the ticket approval flow (approve/reject resume the workflow)."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ["TICKETS_TABLE_NAME"] = "xoc-api-tickets-test-tickets"
os.environ["EVENT_BUS_NAME"] = "xoc-api-tickets-test-bus"
os.environ["CASES_TABLE_NAME"] = "xoc-api-automation-test-cases-v2"
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from src.handlers.domains import tickets_dynamo
from src.handlers.workers import generate_case, register_timeout_case, wait_for_approval
from src.notifications import tickets as ticket_notifications
from src.shared.errors import AppError, ValidationError
from src.shared.tickets_store import normalize_status


class WaitForApprovalTests(unittest.TestCase):
    def test_persists_task_token_deadline_and_status(self) -> None:
        item = {"tenant_id": 7, "ticket_id": "t1", "pending_decision": {}}
        with patch.object(wait_for_approval, "get_tenant_ticket_or_none", return_value=item) as mock_get, patch.object(
            wait_for_approval, "update_ticket_fields"
        ) as mock_update, patch.object(wait_for_approval, "publish_ticket_status_notification") as publish_notification:
            result = wait_for_approval.handler(
                {"ticketId": "t1", "tenantId": "7", "taskToken": "tok-1", "maxRiskLevel": "risky"},
                None,
            )
        self.assertEqual("tok-1", result["taskToken"])
        mock_get.assert_called_once_with(7, "t1")
        updates = mock_update.call_args.args[2]
        self.assertEqual("PREAPROBADO", updates["status"])
        self.assertEqual("AWAITING_APPROVAL", updates["execution_status"])
        pending = updates["pending_decision"]
        self.assertEqual("tok-1", pending["task_token"])
        self.assertEqual("ADMIN", pending["required_approver_role"])
        self.assertTrue(pending["requested_at"])
        self.assertTrue(pending["approval_deadline"])
        publish_notification.assert_called_once_with(
            tenant_id=7,
            ticket_id="t1",
            status="PREAPROBADO",
            attempt_count=None,
        )

    def test_builds_plan_options_from_plans(self) -> None:
        plans = [
            {
                "plan_id": "plan-1",
                "title": "Plan A",
                "plan_summary": "Enfoque directo",
                "total_steps": 2,
                "risk_level": "controlled",
                "plan": [{"order": 1, "command": "cmd1"}, {"order": 2, "command": "cmd2"}],
            },
            {
                "plan_id": "plan-2",
                "title": "Plan B",
                "plan_summary": "Enfoque backup",
                "total_steps": 1,
                "risk_level": "risky",
                "plan": [{"order": 1, "command": "restore"}],
            },
        ]
        options = wait_for_approval._build_plan_options(plans)
        self.assertEqual(2, len(options))
        self.assertEqual("plan-1", options[0]["option_id"])
        self.assertTrue(options[0]["is_recommended"])
        self.assertFalse(options[1]["is_recommended"])
        self.assertEqual(2, options[0]["total_steps"])
        self.assertEqual([{"order": 1, "command": "cmd1"}, {"order": 2, "command": "cmd2"}], options[0]["plan"])

    def test_build_plan_options_skips_empty_plans(self) -> None:
        plans = [
            {"plan_id": "empty", "title": "Vacio", "plan": []},
            {"plan_id": "ok", "title": "Ok", "plan": [{"command": "x"}]},
            "not-a-dict",
        ]
        options = wait_for_approval._build_plan_options(plans)
        self.assertEqual(1, len(options))
        self.assertEqual("ok", options[0]["option_id"])

    def test_build_plan_options_empty_when_no_plans(self) -> None:
        self.assertEqual([], wait_for_approval._build_plan_options([]))
        self.assertEqual([], wait_for_approval._build_plan_options("nope"))

    def test_plan_phase_populates_options(self) -> None:
        item = {"tenant_id": 7, "ticket_id": "t1", "pending_decision": {}}
        plans = [{
            "plan_id": "p1",
            "title": "P1",
            "plan_summary": "sum",
            "total_steps": 1,
            "risk_level": "basic",
            "plan": [{"order": 1, "command": "ls"}],
        }]
        with patch.object(wait_for_approval, "get_tenant_ticket_or_none", return_value=item), patch.object(
            wait_for_approval, "update_ticket_fields"
        ) as mock_update, patch.object(wait_for_approval, "publish_ticket_status_notification"):
            wait_for_approval.handler(
                {"ticketId": "t1", "tenantId": "7", "taskToken": "tok-1", "maxRiskLevel": "basic", "plans": plans},
                None,
            )
        pending = mock_update.call_args.args[2]["pending_decision"]
        self.assertEqual("plan-selection", pending["decision_id"])
        self.assertEqual("p1", pending["recommended_option_id"])
        self.assertEqual(1, len(pending["options"]))
        self.assertEqual([{"order": 1, "command": "ls"}], pending["options"][0]["plan"])

    def test_missing_task_token_raises(self) -> None:
        with patch.object(wait_for_approval, "get_tenant_ticket_or_none", return_value={"pending_decision": {}}):
            with self.assertRaises(ValidationError):
                wait_for_approval.handler({"ticketId": "t1", "tenantId": "7"}, None)


class TicketApprovalFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        tickets_dynamo.get_settings.cache_clear()

    def tearDown(self) -> None:
        tickets_dynamo.get_settings.cache_clear()

    def _preaprobado_ticket(self, required_role: str = "USER", task_token: str | None = "tok-123") -> dict:
        return {
            "ticket_id": "t1",
            "tenant_id": 7,
            "status": "PREAPROBADO",
            "pending_decision": {
                "max_risk_level": "basic",
                "required_approver_role": required_role,
                "task_token": task_token,
            },
        }

    def test_tenant_user_resolves_ticket_by_tenant(self) -> None:
        claims = {"role": "USER", "tenantId": 7}
        with patch.object(tickets_dynamo, "get_tenant_ticket_or_404", return_value={"tenant_id": 7}) as tenant_lookup, patch.object(
            tickets_dynamo, "get_ticket_by_id_or_none"
        ) as global_lookup:
            item = tickets_dynamo._resolve_ticket_for_action(claims, "t1")
        tenant_lookup.assert_called_once_with(7, "t1")
        global_lookup.assert_not_called()
        self.assertEqual(7, item["tenant_id"])

    def test_platform_operator_resolves_ticket_globally(self) -> None:
        claims = {"role": "ADMIN_XOC"}
        item = {"ticket_id": "t1", "tenant_id": 3}
        with patch.object(tickets_dynamo, "get_tenant_ticket_or_404") as tenant_lookup, patch.object(
            tickets_dynamo, "get_ticket_by_id_or_none", return_value=item
        ) as global_lookup:
            result = tickets_dynamo._resolve_ticket_for_action(claims, "t1")
        global_lookup.assert_called_once_with("t1")
        tenant_lookup.assert_not_called()
        self.assertIs(item, result)

    def test_platform_operator_missing_ticket_raises_not_found(self) -> None:
        claims = {"role": "SUPERADMIN"}
        with patch.object(tickets_dynamo, "get_ticket_by_id_or_none", return_value=None):
            with self.assertRaises(tickets_dynamo.NotFoundError):
                tickets_dynamo._resolve_ticket_for_action(claims, "missing")

    def test_approve_resumes_workflow(self) -> None:
        item = self._preaprobado_ticket()
        with patch.object(tickets_dynamo.stepfunctions, "send_task_success") as mock_send:
            tickets_dynamo._resume_workflow(item, approved=True)
        mock_send.assert_called_once_with(taskToken="tok-123", output='{"approved": true}')

    def test_reject_resumes_workflow_as_not_approved(self) -> None:
        item = self._preaprobado_ticket()
        with patch.object(tickets_dynamo.stepfunctions, "send_task_success") as mock_send:
            tickets_dynamo._resume_workflow(item, approved=False)
        mock_send.assert_called_once_with(taskToken="tok-123", output='{"approved": false}')

    def test_resume_without_task_token_raises(self) -> None:
        item = self._preaprobado_ticket(task_token=None)
        with self.assertRaises(ValidationError):
            tickets_dynamo._resume_workflow(item, approved=True)

    def test_resume_send_failure_raises_app_error(self) -> None:
        item = self._preaprobado_ticket()
        with patch.object(tickets_dynamo.stepfunctions, "send_task_success", side_effect=Exception("boom")):
            with self.assertRaises(AppError):
                tickets_dynamo._resume_workflow(item, approved=True)

    def test_assert_can_approve_blocks_low_role(self) -> None:
        item = self._preaprobado_ticket(required_role="ADMIN_XOC")
        with self.assertRaises(tickets_dynamo.ForbiddenError):
            tickets_dynamo._assert_can_approve({"role": "USER"}, item)

    def test_assert_can_approve_allows_sufficient_role(self) -> None:
        item = self._preaprobado_ticket(required_role="ADMIN_XOC")
        tickets_dynamo._assert_can_approve({"role": "ADMIN_XOC"}, item)

    def test_derivado_is_a_valid_ticket_status(self) -> None:
        self.assertEqual("DERIVADO", normalize_status("derivado"))

    def test_resume_workflow_passes_selected_plan(self) -> None:
        item = self._preaprobado_ticket()
        selected_plan = {"steps": [{"order": 1, "command": "x"}]}
        with patch.object(tickets_dynamo.stepfunctions, "send_task_success") as mock_send:
            tickets_dynamo._resume_workflow(item, approved=True, selected_plan=selected_plan)
        mock_send.assert_called_once_with(taskToken="tok-123", output='{"approved": true, "selected_plan": {"steps": [{"order": 1, "command": "x"}]}}')

    def test_plan_steps_for_option_returns_selected_steps(self) -> None:
        item = {
            "pending_decision": {
                "options": [
                    {"option_id": "p1", "plan": [{"order": 1, "command": "a"}]},
                    {"option_id": "p2", "plan": [{"order": 1, "command": "b"}]},
                ]
            }
        }
        plan = tickets_dynamo._plan_steps_for_option(item, "p2")
        self.assertEqual({"steps": [{"order": 1, "command": "b"}], "source": "plan-selection"}, plan)

    def test_plan_steps_for_option_unknown_returns_none(self) -> None:
        item = {"pending_decision": {"options": [{"option_id": "p1", "plan": [{"command": "a"}]}]}}
        self.assertIsNone(tickets_dynamo._plan_steps_for_option(item, "nope"))

    def test_select_decision_resumes_with_selected_plan(self) -> None:
        item = self._preaprobado_ticket()
        item["pending_decision"]["options"] = [
            {"option_id": "p1", "plan": [{"order": 1, "command": "sel"}]},
        ]
        with patch.object(tickets_dynamo, "_get_ticket_or_404", return_value=item), patch.object(
            tickets_dynamo, "update_ticket_fields"
        ) as mock_update, patch.object(tickets_dynamo, "table") as mock_table, patch.object(
            tickets_dynamo, "_emit_event"
        ):
            mock_table.update_item.return_value = None
            with patch.object(tickets_dynamo.stepfunctions, "send_task_success") as mock_send:
                tickets_dynamo.select_ticket_decision(
                    "t1",
                    {"selected_option_id": "p1"},
                    {"role": "USER", "tenantId": 7},
                )
        mock_send.assert_called_once_with(
            taskToken="tok-123",
            output='{"approved": true, "selected_plan": {"steps": [{"order": 1, "command": "sel"}], "source": "plan-selection"}}',
        )
        mock_update.assert_called_once_with(7, "t1", {"action_plan": {"steps": [{"order": 1, "command": "sel"}], "source": "plan-selection"}})


class GenerateCaseTests(unittest.TestCase):
    def test_rejected_action_is_accepted(self) -> None:
        with patch(
            "src.handlers.workers.generate_case.create_case",
            return_value={"case_id": "c1", "status": "NO_RESUELTO", "created_at": "x"},
        ) as mock_create, patch.object(generate_case, "get_tenant_ticket_or_none", return_value={"status": "RECHAZADO"}), patch.object(
            generate_case, "publish_ticket_status_notification"
        ) as publish_notification:
            result = generate_case.handler(
                {"ticket_id": "t1", "tenant_id": 7, "subject": "s", "action": "rejected"}, None
            )
        self.assertEqual("c1", result["caseId"])
        mock_create.assert_called_once()
        self.assertEqual("rejected", mock_create.call_args.kwargs["action"])
        publish_notification.assert_called_once_with(tenant_id=7, ticket_id="t1", status="RECHAZADO")

    def test_derivado_action_is_accepted(self) -> None:
        with patch(
            "src.handlers.workers.generate_case.create_case",
            return_value={"case_id": "c2", "status": "NO_RESUELTO", "created_at": "x"},
        ) as mock_create:
            result = generate_case.handler(
                {"ticket_id": "t1", "tenant_id": 7, "subject": "s", "action": "derivado"}, None
            )
        self.assertEqual("c2", result["caseId"])
        self.assertEqual("derivado", mock_create.call_args.kwargs["action"])

    def test_failed_after_attempts_updates_ticket_and_notifies_creator(self) -> None:
        with patch(
            "src.handlers.workers.generate_case.create_case",
            return_value={"case_id": "c3", "status": "NO_RESUELTO", "created_at": "x"},
        ), patch.object(generate_case, "get_tenant_ticket_or_none", return_value={"status": "PENDING"}), patch.object(
            generate_case, "update_ticket_fields"
        ) as update_ticket, patch.object(generate_case, "publish_ticket_status_notification") as publish_notification:
            generate_case.handler(
                {"ticket_id": "t1", "tenant_id": 7, "subject": "s", "action": "failed_after_attempts"}, None
            )
        update_ticket.assert_called_once_with(
            7,
            "t1",
            {
                "status": "FALLIDO",
                "execution_status": "FAILED",
                "execution_summary": "Failed after 0 attempts",
            },
        )
        publish_notification.assert_called_once_with(tenant_id=7, ticket_id="t1", status="FALLIDO")

    def test_invalid_action_raises(self) -> None:
        with self.assertRaises(ValidationError):
            generate_case.handler({"ticket_id": "t1", "tenant_id": 7, "action": "bogus"}, None)


class RegisterTimeoutCaseTests(unittest.TestCase):
    def test_marks_ticket_derivado_and_creates_case(self) -> None:
        with patch("src.handlers.workers.register_timeout_case.update_ticket_fields") as mock_update, patch(
            "src.handlers.workers.register_timeout_case.create_case",
            return_value={"case_id": "c1", "status": "NO_RESUELTO", "created_at": "x"},
        ) as mock_create, patch.object(register_timeout_case, "publish_ticket_status_notification") as publish_notification:
            result = register_timeout_case.handler({"ticketId": "t1", "tenantId": 7, "subject": "s"}, None)
        mock_update.assert_called_once_with(7, "t1", {"status": "DERIVADO", "execution_status": "TIMED_OUT"})
        self.assertEqual("c1", result["caseId"])
        self.assertEqual("derivado", mock_create.call_args.kwargs["action"])
        publish_notification.assert_called_once_with(tenant_id=7, ticket_id="t1", status="DERIVADO")


class TicketNotificationPublisherTests(unittest.TestCase):
    def test_uses_ticket_creator_and_publishes_self_event(self) -> None:
        ticket = {"created_by_user_id": 18}
        with patch.object(ticket_notifications, "_get_ticket", return_value=ticket), patch.object(
            ticket_notifications, "publish_notification_requested"
        ) as publish_event:
            result = ticket_notifications.publish_ticket_status_notification(
                tenant_id=7,
                ticket_id="123e4567-e89b-42d3-a456-426614174000",
                status="RESUELTO",
            )
        self.assertTrue(result)
        event = publish_event.call_args.args[0]
        self.assertEqual("SELF", event["audienceType"])
        self.assertEqual("18", event["recipientUserId"])

    def test_publish_failure_is_best_effort(self) -> None:
        with patch.object(ticket_notifications, "_get_ticket", return_value={"created_by_user_id": 18}), patch.object(
            ticket_notifications, "publish_notification_requested", side_effect=RuntimeError("event bridge unavailable")
        ):
            result = ticket_notifications.publish_ticket_status_notification(
                tenant_id=7,
                ticket_id="123e4567-e89b-42d3-a456-426614174000",
                status="RESUELTO",
            )
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
