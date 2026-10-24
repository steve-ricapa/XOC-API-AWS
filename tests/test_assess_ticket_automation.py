"""Unit coverage for Victor on-premise resolution in ticket automation."""
from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("TICKETS_TABLE_NAME", "xoc-api-tickets-test-tickets")
os.environ.setdefault("EVENT_BUS_NAME", "xoc-api-tickets-test-bus")

from src.handlers.workers import assess_ticket_automation


def _fake_db_session(runtime):
    @contextmanager
    def _scope():
        session = MagicMock()
        if runtime is None:
            session.query.side_effect = RuntimeError("db unavailable")
        else:
            query_result = MagicMock()
            query_result.filter.return_value.first.return_value = runtime
            session.query.return_value = query_result
        yield session

    return _scope


def _fake_db_session_without_settings():
    @contextmanager
    def _scope():
        session = MagicMock()
        query_result = MagicMock()
        query_result.filter.return_value.first.return_value = None
        session.query.return_value = query_result
        yield session

    return _scope


class AssessTicketAutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_base = os.environ.get("AGENTS_FUNCTION_BASE_URL_VICTOR")
        self.previous_jwt = os.environ.get("JWT_SECRET_KEY")
        os.environ["AGENTS_FUNCTION_BASE_URL_VICTOR"] = ""
        os.environ["JWT_SECRET_KEY"] = "test-secret"
        assess_ticket_automation.get_settings.cache_clear()

    def tearDown(self) -> None:
        if self.previous_base is None:
            os.environ.pop("AGENTS_FUNCTION_BASE_URL_VICTOR", None)
        else:
            os.environ["AGENTS_FUNCTION_BASE_URL_VICTOR"] = self.previous_base
        if self.previous_jwt is None:
            os.environ.pop("JWT_SECRET_KEY", None)
        else:
            os.environ["JWT_SECRET_KEY"] = self.previous_jwt
        assess_ticket_automation.get_settings.cache_clear()

    def test_tenant_runtime_settings_win_over_global(self) -> None:
        os.environ["AGENTS_FUNCTION_BASE_URL_VICTOR"] = "http://10.0.0.5:8000"
        assess_ticket_automation.get_settings.cache_clear()
        runtime = MagicMock()
        runtime.function_base_url = "http://10.0.0.99:8000"
        runtime.function_route_victor = "/api/agents/VictorDurableAgent/run"
        runtime.is_active = True
        with patch("src.persistence.db.session_scope", _fake_db_session(runtime)):
            base, route, source = assess_ticket_automation._resolve_victor_endpoint(7)
        self.assertEqual("http://10.0.0.99:8000", base)
        self.assertEqual("/api/agents/VictorDurableAgent/run", route)
        self.assertEqual("on_premise", source)

    def test_global_url_used_when_tenant_has_no_runtime_settings(self) -> None:
        os.environ["AGENTS_FUNCTION_BASE_URL_VICTOR"] = "http://10.0.0.5:8000"
        assess_ticket_automation.get_settings.cache_clear()
        with patch("src.persistence.db.session_scope", _fake_db_session_without_settings()):
            base, route, source = assess_ticket_automation._resolve_victor_endpoint(7)
        self.assertEqual("http://10.0.0.5:8000", base)
        self.assertEqual("/api/agents/VictorDurableAgent/run", route)
        self.assertEqual("global", source)

    def test_on_premise_runtime_settings_used_when_no_global(self) -> None:
        runtime = MagicMock()
        runtime.function_base_url = "http://on-premise-victor.local:9000"
        runtime.function_route_victor = "/api/agents/VictorDurableAgent/run"
        runtime.is_active = True
        with patch("src.persistence.db.session_scope", _fake_db_session(runtime)):
            base, route, source = assess_ticket_automation._resolve_victor_endpoint(7)
        self.assertEqual("http://on-premise-victor.local:9000", base)
        self.assertEqual("on_premise", source)

    def test_fallback_when_no_global_and_db_unavailable(self) -> None:
        with patch("src.persistence.db.session_scope", _fake_db_session(None)):
            base, route, source = assess_ticket_automation._resolve_victor_endpoint(7)
        self.assertIsNone(base)
        self.assertEqual("fallback", source)

    def test_handler_returns_default_response_when_unconfigured(self) -> None:
        with patch("src.persistence.db.session_scope", _fake_db_session(None)):
            result = assess_ticket_automation.handler(
                {"ticketId": "t1", "tenantId": 7, "subject": "incidente", "description": "desc", "phase": "plan"},
                None,
            )
        self.assertEqual("fallback", result["planSource"])
        self.assertEqual("basic", result["maxRiskLevel"])
        self.assertEqual("USER", result["approval"]["required_approver_role"])

    def test_handler_uses_on_premise_endpoint_and_assesses_risk(self) -> None:
        runtime = MagicMock()
        runtime.function_base_url = "http://on-premise-victor.local:9000"
        runtime.function_route_victor = "/api/agents/VictorDurableAgent/run"
        runtime.is_active = True

        fake_response = MagicMock()
        fake_response.json.return_value = {
            "plan": {"steps": [{"action_type": "delete"}, {"action_type": "install"}]}
        }
        fake_response.raise_for_status.return_value = None

        with patch("src.persistence.db.session_scope", _fake_db_session(runtime)), patch(
            "src.handlers.workers.assess_ticket_automation.requests.post", return_value=fake_response
        ) as mock_post:
            result = assess_ticket_automation.handler(
                {"ticketId": "t2", "tenantId": 7, "subject": "borrar equipo", "description": "desc", "phase": "plan"},
                None,
            )

        call_url = mock_post.call_args.args[0]
        self.assertTrue(call_url.startswith("http://on-premise-victor.local:9000"))
        self.assertEqual("victor_on_premise", result["planSource"])
        self.assertEqual("risky", result["maxRiskLevel"])
        self.assertEqual("ADMIN", result["approval"]["required_approver_role"])

    def test_normalize_plans_accepts_multi_plan_format(self) -> None:
        data = {
            "plans": [
                {"plan_id": "p1", "title": "A", "plan_summary": "sum", "risk_level": "basic",
                 "plan": [{"order": 1, "action": "shell", "command": "ls"}]},
                {"plan_id": "p2", "title": "B", "plan_summary": "s2", "risk_level": "risky",
                 "plan": [{"order": 1, "action": "shell", "command": "rm"}]},
            ],
            "plans_count": 2,
        }
        plans = assess_ticket_automation._normalize_plans(data)
        self.assertEqual(2, len(plans))
        self.assertEqual("p1", plans[0]["plan_id"])
        self.assertEqual("risky", plans[1]["risk_level"])
        self.assertEqual([{"order": 1, "action": "shell", "command": "rm"}], plans[1]["plan"])

    def test_normalize_plans_skips_empty_and_keeps_legacy(self) -> None:
        data = {"plans": [{"plan_id": "empty", "plan": []}, "garbage"], "plan": {"steps": [{"cmd": "x"}]}}
        plans = assess_ticket_automation._normalize_plans(data)
        self.assertEqual([], plans)

        legacy = {"plan": {"steps": [{"cmd": "x"}]}, "plan_summary": "legacy"}
        plans = assess_ticket_automation._normalize_plans(legacy)
        self.assertEqual(1, len(plans))
        self.assertEqual("plan-1", plans[0]["plan_id"])
        self.assertEqual([{"cmd": "x"}], plans[0]["plan"])

    def test_handler_passes_generate_plans_to_victor(self) -> None:
        runtime = MagicMock()
        runtime.function_base_url = "http://on-premise-victor.local:9000"
        runtime.function_route_victor = "/api/agents/VictorDurableAgent/run"
        runtime.is_active = True

        fake_response = MagicMock()
        fake_response.json.return_value = {
            "plans": [{"plan_id": "p1", "title": "A", "risk_level": "basic",
                       "plan": [{"order": 1, "action": "shell", "command": "ls"}]}],
            "plans_count": 1,
        }
        fake_response.raise_for_status.return_value = None

        with patch("src.persistence.db.session_scope", _fake_db_session(runtime)), patch(
            "src.handlers.workers.assess_ticket_automation.requests.post", return_value=fake_response
        ) as mock_post:
            result = assess_ticket_automation.handler(
                {"ticketId": "t3", "tenantId": 7, "subject": "instalar htop", "description": "desc", "phase": "plan"},
                None,
            )
        sent_payload = mock_post.call_args.kwargs["json"]
        self.assertTrue(sent_payload["generate_plans"])
        self.assertEqual(1, result["plansCount"])
        self.assertEqual("basic", result["maxRiskLevel"])

    def test_handler_custom_plan_steps_forwarded(self) -> None:
        runtime = MagicMock()
        runtime.function_base_url = "http://on-premise-victor.local:9000"
        runtime.function_route_victor = "/api/agents/VictorDurableAgent/run"
        runtime.is_active = True

        fake_response = MagicMock()
        fake_response.json.return_value = {
            "plans": [{"plan_id": "plan-custom", "title": "Custom", "risk_level": "controlled",
                       "plan": [{"order": 1, "command": "echo hi"}]}],
            "plans_count": 1,
        }
        fake_response.raise_for_status.return_value = None

        with patch("src.persistence.db.session_scope", _fake_db_session(runtime)), patch(
            "src.handlers.workers.assess_ticket_automation.requests.post", return_value=fake_response
        ) as mock_post:
            result = assess_ticket_automation.handler(
                {"ticketId": "t4", "tenantId": 7, "subject": "x", "description": "y", "phase": "plan",
                 "customPlanSteps": [{"command": "echo hi"}]},
                None,
            )
        sent_payload = mock_post.call_args.kwargs["json"]
        self.assertEqual([{"command": "echo hi"}], sent_payload["custom_plan_steps"])
        self.assertEqual("controlled", result["maxRiskLevel"])

    def test_normalize_plans_keeps_ai_analysis(self) -> None:
        data = {
            "plans": [{"plan_id": "p1", "title": "A", "analysis": "Analisis de la IA",
                       "plan": [{"order": 1, "command": "ls"}]}]
        }
        plans = assess_ticket_automation._normalize_plans(data)
        self.assertEqual("Analisis de la IA", plans[0]["analysis"])

    def test_plan_phase_does_not_persist_when_persist_is_false(self) -> None:
        runtime = MagicMock()
        runtime.function_base_url = "http://on-premise-victor.local:9000"
        runtime.function_route_victor = "/api/agents/VictorDurableAgent/run"
        runtime.is_active = True

        fake_response = MagicMock()
        fake_response.json.return_value = {
            "plans": [{"plan_id": "p1", "title": "A", "analysis": "texto",
                       "plan": [{"order": 1, "command": "ls"}]}]
        }
        fake_response.raise_for_status.return_value = None

        with patch("src.persistence.db.session_scope", _fake_db_session(runtime)), patch(
            "src.handlers.workers.assess_ticket_automation.requests.post", return_value=fake_response
        ), patch("src.shared.tickets_store.update_ticket_fields") as mock_update:
            result = assess_ticket_automation.handler(
                {"ticketId": "t5", "tenantId": 7, "subject": "x", "description": "y", "phase": "plan",
                 "persist": False, "customInstructions": "analiza esto"},
                None,
            )
        mock_update.assert_not_called()
        self.assertEqual("texto", result["analysis"])

    def test_plan_phase_tolerates_non_string_risk_metadata(self) -> None:
        runtime = MagicMock()
        runtime.function_base_url = "http://on-premise-victor.local:9000"
        runtime.function_route_victor = "/api/agents/VictorDurableAgent/run"
        runtime.is_active = True

        fake_response = MagicMock()
        fake_response.json.return_value = {
            "plans": [{
                "plan_id": "p1",
                "analysis": "analisis custom",
                "plan": [
                    {"order": 1, "command": ["ls", "-la", "/tmp"], "risk_level": 1},
                    {"order": 2, "command": "cat /tmp/x", "risk_level": {"level": "basic"}},
                ],
            }]
        }
        fake_response.raise_for_status.return_value = None

        with patch("src.persistence.db.session_scope", _fake_db_session(runtime)), patch(
            "src.handlers.workers.assess_ticket_automation.requests.post", return_value=fake_response
        ):
            result = assess_ticket_automation.handler(
                {"ticketId": "t6", "tenantId": 7, "subject": "x", "description": "y", "phase": "plan",
                 "persist": False, "customInstructions": "listar /tmp"},
                None,
            )

        steps = result["plans"][0]["plan"]
        self.assertEqual("ls -la /tmp", steps[0]["command"])
        self.assertEqual(2, len(steps))
        self.assertEqual("basic", result["maxRiskLevel"])

    def test_execute_phase_persists_execution_logs(self) -> None:
        runtime = MagicMock()
        runtime.function_base_url = "http://on-premise-victor.local:9000"
        runtime.function_route_victor = "/api/agents/VictorDurableAgent/run"
        runtime.is_active = True

        fake_response = MagicMock()
        fake_response.json.return_value = {
            "all_success": True,
            "step_results": [
                {"order": 1, "command": "ls -la /opt/xoc-test", "description": "Listar artefactos",
                 "success": True, "status": "ok", "returncode": 0, "stdout": "ok", "stderr": ""},
                {"order": 2, "command": "rm -f /opt/xoc-test/incident/backdoor.sh", "description": "Eliminar",
                 "success": True, "status": "ok", "returncode": 0, "stdout": "", "stderr": ""},
            ],
        }
        fake_response.raise_for_status.return_value = None

        with patch("src.persistence.db.session_scope", _fake_db_session(runtime)), patch(
            "src.handlers.workers.assess_ticket_automation.requests.post", return_value=fake_response
        ), patch("src.shared.tickets_store.update_ticket_fields") as mock_update, patch(
            "src.handlers.workers.assess_ticket_automation.publish_ticket_status_notification"
        ):
            result = assess_ticket_automation.handler(
                {"ticketId": "t6", "tenantId": 7, "subject": "x", "description": "y", "phase": "execute",
                 "plan": {"steps": [{"order": 1, "command": "ls"}]}},
                None,
            )

        fields = mock_update.call_args.args[2]
        self.assertEqual("RESUELTO", fields["status"])
        self.assertEqual("2/2 pasos completados", fields["execution_summary"])
        logs = fields["execution_logs"]
        self.assertEqual("t6", logs["correlation_id"])
        self.assertEqual(2, len(logs["timeline"]))
        self.assertEqual((2, 2), (logs["steps_ok"], logs["steps_total"]))
        self.assertEqual("ok", logs["timeline"][0]["status"])
        self.assertEqual("Listar artefactos", logs["timeline"][0]["label"])
        self.assertEqual("ls -la /opt/xoc-test", logs["timeline"][0]["command"])
        self.assertIn("ok", logs["timeline"][0]["stdout"])
        self.assertIn("rm -f", logs["timeline"][1]["detail"])
        self.assertEqual("rm -f /opt/xoc-test/incident/backdoor.sh", logs["timeline"][1]["command"])
        self.assertEqual("completed", logs["final_result"]["status"])
        self.assertIn("$ ls -la /opt/xoc-test", logs["final_result"]["stdout"])
        self.assertEqual(2, result["executionLogs"]["iterations"])

    def test_execute_phase_persists_failure_detail(self) -> None:
        runtime = MagicMock()
        runtime.function_base_url = "http://on-premise-victor.local:9000"
        runtime.function_route_victor = "/api/agents/VictorDurableAgent/run"
        runtime.is_active = True

        fake_response = MagicMock()
        fake_response.json.return_value = {
            "all_success": False,
            "step_results": [
                {"order": 1, "command": "rm -f /opt/xoc-test/x", "description": "Eliminar",
                 "success": False, "status": "failed", "returncode": 1, "stdout": "", "stderr": "No such file"},
            ],
        }
        fake_response.raise_for_status.return_value = None

        with patch("src.persistence.db.session_scope", _fake_db_session(runtime)), patch(
            "src.handlers.workers.assess_ticket_automation.requests.post", return_value=fake_response
        ), patch("src.shared.tickets_store.update_ticket_fields") as mock_update, patch(
            "src.handlers.workers.assess_ticket_automation.publish_ticket_status_notification"
        ):
            assess_ticket_automation.handler(
                {"ticketId": "t7", "tenantId": 7, "subject": "x", "description": "y", "phase": "execute",
                 "plan": {"steps": [{"order": 1, "command": "rm -f /opt/xoc-test/x"}]}},
                None,
            )

        fields = mock_update.call_args.args[2]
        self.assertEqual("FALLIDO", fields["status"])
        self.assertEqual("FAILED", fields["execution_status"])
        self.assertEqual("0/1 pasos completados: fallo en 'Eliminar'", fields["execution_summary"])
        self.assertIn("No such file", fields["execution_logs"]["timeline"][0]["detail"])
        self.assertEqual("No such file", fields["execution_logs"]["timeline"][0]["stderr"])
        self.assertIn("No such file", fields["execution_logs"]["final_result"]["stderr"])
        self.assertEqual("failed", fields["execution_logs"]["final_result"]["status"])

    def test_execute_phase_infers_success_when_victor_omits_all_success(self) -> None:
        runtime = MagicMock()
        runtime.function_base_url = "http://on-premise-victor.local:9000"
        runtime.function_route_victor = "/api/agents/VictorDurableAgent/run"
        runtime.is_active = True

        fake_response = MagicMock()
        fake_response.json.return_value = {
            "step_results": [
                {"order": 1, "command": "ls", "status": "completed", "stdout": "ok"},
                {"order": 2, "command": "echo done", "success": True, "stdout": "done"},
            ],
        }
        fake_response.raise_for_status.return_value = None

        with patch("src.persistence.db.session_scope", _fake_db_session(runtime)), patch(
            "src.handlers.workers.assess_ticket_automation.requests.post", return_value=fake_response
        ), patch("src.shared.tickets_store.update_ticket_fields") as mock_update, patch(
            "src.handlers.workers.assess_ticket_automation.publish_ticket_status_notification"
        ):
            assess_ticket_automation.handler(
                {"ticketId": "t10", "tenantId": 7, "subject": "x", "description": "y", "phase": "execute",
                 "plan": {"steps": [{"order": 1, "command": "ls"}]}},
                None,
            )

        fields = mock_update.call_args.args[2]
        self.assertEqual("RESUELTO", fields["status"])
        self.assertEqual("2/2 pasos completados", fields["execution_summary"])

    def test_mark_derived_phase_marks_ticket_as_derivado(self) -> None:
        with patch(
            "src.handlers.workers.assess_ticket_automation.sync_unresolvable_assessment"
        ) as mock_sync, patch(
            "src.handlers.workers.assess_ticket_automation.requests.post"
        ) as mock_post:
            result = assess_ticket_automation.handler(
                {"ticketId": "t11", "tenantId": 7, "phase": "mark_derived", "reasonCode": "NO_EXECUTABLE_PLAN"},
                None,
            )
        mock_sync.assert_called_once_with(7, "t11", "NO_EXECUTABLE_PLAN")
        mock_post.assert_not_called()
        self.assertEqual("derivado", result["status"])

    def test_mark_failed_phase_persists_failure_without_calling_victor(self) -> None:
        with patch("src.shared.tickets_store.get_tenant_ticket_or_none", return_value=None), patch(
            "src.shared.tickets_store.update_ticket_fields"
        ) as mock_update, patch(
            "src.handlers.workers.assess_ticket_automation.requests.post"
        ) as mock_post:
            result = assess_ticket_automation.handler(
                {"ticketId": "t9", "tenantId": 7, "phase": "mark_failed", "reason": "timeout"},
                None,
            )

        mock_post.assert_not_called()
        fields = mock_update.call_args.args[2]
        self.assertEqual("FALLIDO", fields["status"])
        self.assertEqual("FAILED", fields["execution_status"])
        self.assertEqual("timeout", fields["execution_summary"])
        self.assertEqual("failed", result["status"])

    def test_mark_failed_phase_keeps_existing_detail(self) -> None:
        existing = {
            "execution_status": "FAILED",
            "execution_summary": "0/4 pasos completados: fallo en 'Verificar'",
        }
        with patch("src.shared.tickets_store.get_tenant_ticket_or_none", return_value=existing), patch(
            "src.shared.tickets_store.update_ticket_fields"
        ) as mock_update:
            assess_ticket_automation.handler(
                {"ticketId": "t10", "tenantId": 7, "phase": "mark_failed", "reason": "Lambda.Unknown"},
                None,
            )

        mock_update.assert_not_called()

    def test_execution_logs_are_dynamo_serializable(self) -> None:
        from boto3.dynamodb.types import TypeSerializer

        from src.shared.tickets_store import _dynamo_safe

        logs = assess_ticket_automation._build_execution_logs(
            ticket_id="t8",
            step_results=[
                {"order": 1, "command": "ls -la /opt/xoc-test", "description": "Listar",
                 "success": True, "status": "ok", "returncode": 0, "stdout": "ok", "stderr": ""},
            ],
            all_success=True,
            summary="Listar: OK",
            duration_seconds=1.25,
        )
        self.assertIsInstance(logs["duration_seconds"], float)
        serialized = TypeSerializer().serialize(_dynamo_safe(logs))
        self.assertIn("N", serialized["M"]["duration_seconds"])


if __name__ == "__main__":
    unittest.main()
