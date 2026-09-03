"""Unit coverage for Victor on-premise resolution in ticket automation."""
from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

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


class AssessTicketAutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_base = os.environ.get("AGENTS_FUNCTION_BASE_URL")
        self.previous_jwt = os.environ.get("JWT_SECRET_KEY")
        os.environ["AGENTS_FUNCTION_BASE_URL"] = ""
        os.environ["JWT_SECRET_KEY"] = "test-secret"
        assess_ticket_automation.get_settings.cache_clear()

    def tearDown(self) -> None:
        if self.previous_base is None:
            os.environ.pop("AGENTS_FUNCTION_BASE_URL", None)
        else:
            os.environ["AGENTS_FUNCTION_BASE_URL"] = self.previous_base
        if self.previous_jwt is None:
            os.environ.pop("JWT_SECRET_KEY", None)
        else:
            os.environ["JWT_SECRET_KEY"] = self.previous_jwt
        assess_ticket_automation.get_settings.cache_clear()

    def test_global_url_wins_and_marks_source_global(self) -> None:
        os.environ["AGENTS_FUNCTION_BASE_URL"] = "http://10.0.0.5:8000"
        assess_ticket_automation.get_settings.cache_clear()
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


if __name__ == "__main__":
    unittest.main()
