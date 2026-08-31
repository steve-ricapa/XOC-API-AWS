"""Security tests for backend report-type authorization."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from src.handlers.routes import reports
from src.reports.authorization import (
    get_allowed_report_types_for_role,
    validate_report_type_for_role,
)
from src.shared.errors import ForbiddenError, ValidationError


class _AuthenticatedUser:
    def __init__(self, role: str | None, *, tenant_id: int = 7, delegated: bool = False):
        self.role = role
        self.tenant_id = tenant_id
        self.delegation_active = delegated
        self.effective_tenant_id = tenant_id


class ReportAuthorizationTests(unittest.TestCase):
    def test_explicit_policy_matches_product_roles(self) -> None:
        self.assertEqual(frozenset({"minority_report"}), get_allowed_report_types_for_role("ADMIN"))
        self.assertEqual(frozenset({"small_report"}), get_allowed_report_types_for_role("ADMIN_XOC"))
        self.assertEqual(frozenset(), get_allowed_report_types_for_role("USER"))
        self.assertEqual(frozenset(), get_allowed_report_types_for_role("SUPERADMIN"))

    def test_admin_can_only_request_minority_report(self) -> None:
        validate_report_type_for_role("minority_report", "ADMIN")
        for document_type in ("small_report", "informe_soporte"):
            with self.subTest(document_type=document_type), self.assertRaises(ForbiddenError):
                validate_report_type_for_role(document_type, "ADMIN")

    def test_admin_xoc_can_only_request_small_report(self) -> None:
        validate_report_type_for_role("small_report", "ADMIN_XOC", {"tenantId": 7, "delegationActive": True})
        for document_type in ("minority_report", "informe_soporte"):
            with self.subTest(document_type=document_type), self.assertRaises(ForbiddenError):
                validate_report_type_for_role(document_type, "ADMIN_XOC")

    def test_user_and_superadmin_are_blocked_by_default(self) -> None:
        for role in ("USER", "SUPERADMIN", None, "unknown"):
            for document_type in ("minority_report", "small_report", "informe_soporte"):
                with self.subTest(role=role, document_type=document_type), self.assertRaises(ForbiddenError):
                    validate_report_type_for_role(document_type, role)

    def test_missing_or_unknown_document_type_is_a_validation_error(self) -> None:
        for document_type in (None, "", "executive_summary", "compliance_report"):
            with self.subTest(document_type=document_type), self.assertRaises(ValidationError):
                validate_report_type_for_role(document_type, "ADMIN")

    def test_denied_request_never_creates_document_or_publishes_event(self) -> None:
        user = _AuthenticatedUser("ADMIN")
        with patch.object(reports, "create_document_job") as create_job, patch.object(
            reports, "_publish_event"
        ) as publish_event, self.assertRaises(ForbiddenError):
            reports.request_document(
                {"document_type": "small_report"},
                claims={"userId": "12"},
                current_user=user,
            )

        create_job.assert_not_called()
        publish_event.assert_not_called()

    def test_allowed_request_reaches_document_job_creation(self) -> None:
        user = _AuthenticatedUser("ADMIN")
        item = {"document_id": "doc-1", "document_type": "minority_report", "status": "PENDING"}
        with patch.object(reports, "create_document_job", return_value=("doc-1", item)) as create_job, patch.object(
            reports, "table"
        ) as document_table, patch.object(reports, "_publish_event"):
            response = reports.request_document(
                {"document_type": "minority_report"},
                claims={"userId": "12"},
                current_user=user,
            )

        self.assertEqual("doc-1", response["documentId"])
        create_job.assert_called_once()
        document_table.put_item.assert_called_once_with(Item=item)

    def test_delegated_admin_xoc_can_create_only_small_report_for_effective_tenant(self) -> None:
        user = _AuthenticatedUser("ADMIN_XOC", tenant_id=8, delegated=True)
        item = {"document_id": "doc-2", "document_type": "small_report", "status": "PENDING"}
        with patch.object(reports, "create_document_job", return_value=("doc-2", item)) as create_job, patch.object(
            reports, "table"
        ) as document_table, patch.object(reports, "_publish_event"):
            response = reports.request_document(
                {"document_type": "small_report"},
                claims={"userId": "9", "actingTenantId": "8"},
                current_user=user,
            )

        self.assertEqual("doc-2", response["documentId"])
        self.assertEqual(8, create_job.call_args.kwargs["tenant_id"])
        document_table.put_item.assert_called_once_with(Item=item)


if __name__ == "__main__":
    unittest.main()
