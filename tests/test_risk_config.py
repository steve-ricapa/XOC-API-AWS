"""Unit coverage for the approval contract (risk_config)."""
from __future__ import annotations

import unittest

from src.shared.risk_config import (
    ACTION_TYPE_RISK_MAP,
    approval_requirement,
    approver_label_for_risk,
    compute_max_risk_level,
    is_publicly_approvable,
    is_role_sufficient,
    required_role_for_risk,
    resolve_step_risk_level,
)


class RiskConfigTests(unittest.TestCase):
    def test_action_type_contract_matches_user_roles(self) -> None:
        # USER aprueba instalaciones y cosas simples
        self.assertEqual("basic", ACTION_TYPE_RISK_MAP["install"])
        self.assertEqual("basic", ACTION_TYPE_RISK_MAP["setup"])
        self.assertEqual("basic", ACTION_TYPE_RISK_MAP["deploy"])
        self.assertEqual("basic", ACTION_TYPE_RISK_MAP["view"])
        self.assertEqual("basic", ACTION_TYPE_RISK_MAP["start"])
        # ADMIN del tenant aprueba algo menos grave
        self.assertEqual("controlled", ACTION_TYPE_RISK_MAP["configure"])
        self.assertEqual("controlled", ACTION_TYPE_RISK_MAP["restart"])
        self.assertEqual("controlled", ACTION_TYPE_RISK_MAP["update"])
        # Admin XOC aprueba eliminacion o modificacion de cosas importantes
        self.assertEqual("risky", ACTION_TYPE_RISK_MAP["delete"])
        self.assertEqual("risky", ACTION_TYPE_RISK_MAP["remove"])
        self.assertEqual("risky", ACTION_TYPE_RISK_MAP["terminate"])
        self.assertEqual("risky", ACTION_TYPE_RISK_MAP["modify_important"])
        self.assertEqual("risky", ACTION_TYPE_RISK_MAP["delete_important"])
        # Superadmin aprueba lo irreversible
        self.assertEqual("critical", ACTION_TYPE_RISK_MAP["purge"])
        self.assertEqual("critical", ACTION_TYPE_RISK_MAP["wipe"])

    def test_required_role_contract(self) -> None:
        self.assertEqual("USER", required_role_for_risk("basic"))
        self.assertEqual("ADMIN", required_role_for_risk("controlled"))
        self.assertEqual("ADMIN_XOC", required_role_for_risk("risky"))
        self.assertEqual("SUPERADMIN", required_role_for_risk("critical"))

    def test_role_hierarchy_is_sufficient(self) -> None:
        self.assertTrue(is_role_sufficient("ADMIN_XOC", "ADMIN"))
        self.assertTrue(is_role_sufficient("ADMIN_XOC", "ADMIN_XOC"))
        self.assertTrue(is_role_sufficient("SUPERADMIN", "ADMIN_XOC"))
        self.assertFalse(is_role_sufficient("ADMIN", "ADMIN_XOC"))
        self.assertFalse(is_role_sufficient("USER", "ADMIN"))
        self.assertFalse(is_role_sufficient("USER", "ADMIN_XOC"))

    def test_publicly_approvable_only_for_basic(self) -> None:
        self.assertTrue(is_publicly_approvable("basic"))
        self.assertFalse(is_publicly_approvable("controlled"))
        self.assertFalse(is_publicly_approvable("risky"))
        self.assertFalse(is_publicly_approvable("critical"))

    def test_compute_max_risk_level_from_action_types(self) -> None:
        plan = {"steps": [{"action_type": "install"}, {"action_type": "delete"}]}
        self.assertEqual("risky", compute_max_risk_level(plan))

    def test_compute_max_risk_level_takes_highest(self) -> None:
        plan = {"steps": [{"action_type": "install"}, {"action_type": "purge"}]}
        self.assertEqual("critical", compute_max_risk_level(plan))

    def test_compute_accepts_list_of_steps(self) -> None:
        plan = [{"action_type": "delete"}]
        self.assertEqual("risky", compute_max_risk_level(plan))

    def test_compute_accepts_nested_plan(self) -> None:
        plan = {"plan": {"steps": [{"action_type": "configure"}]}}
        self.assertEqual("controlled", compute_max_risk_level(plan))

    def test_explicit_step_risk_level_wins(self) -> None:
        plan = {"steps": [{"action_type": "install", "risk_level": "risky"}]}
        self.assertEqual("risky", compute_max_risk_level(plan))

    def test_plan_level_risk_override(self) -> None:
        plan = {"risk_level": "critical", "steps": [{"action_type": "install"}]}
        self.assertEqual("critical", compute_max_risk_level(plan))

    def test_important_impact_escalates_to_risky(self) -> None:
        self.assertEqual("risky", resolve_step_risk_level({"action_type": "modify", "impact": "important"}))
        self.assertEqual("risky", resolve_step_risk_level({"action_type": "update", "important": True}))

    def test_irreversible_impact_escalates_to_critical(self) -> None:
        self.assertEqual("critical", resolve_step_risk_level({"action_type": "modify", "impact": "irreversible"}))

    def test_empty_plan_defaults_to_basic(self) -> None:
        self.assertEqual("basic", compute_max_risk_level(None))
        self.assertEqual("basic", compute_max_risk_level({"steps": []}))
        self.assertEqual("basic", compute_max_risk_level({}))

    def test_approval_requirement_exposes_contract(self) -> None:
        requirement = approval_requirement({"steps": [{"action_type": "delete"}]})
        self.assertEqual("risky", requirement["max_risk_level"])
        self.assertEqual("ADMIN_XOC", requirement["required_approver_role"])
        self.assertEqual("Admin XOC", requirement["approver_label"])
        self.assertFalse(requirement["publicly_approvable"])

        basic = approval_requirement({"steps": [{"action_type": "install"}]})
        self.assertEqual("USER", basic["required_approver_role"])
        self.assertTrue(basic["publicly_approvable"])

    def test_approver_labels(self) -> None:
        self.assertEqual("Usuario", approver_label_for_risk("basic"))
        self.assertEqual("Admin del tenant", approver_label_for_risk("controlled"))
        self.assertEqual("Admin XOC", approver_label_for_risk("risky"))
        self.assertEqual("Superadmin XOC", approver_label_for_risk("critical"))


if __name__ == "__main__":
    unittest.main()
