"""Unit checks for the user-visible ticket notification inbox contract."""
from __future__ import annotations

import unittest

from src.shared.user_notification_inbox import (
    create_ticket_user_notification,
    notification_id_for,
    serialize_user_notification,
    user_partition_key,
)


class UserNotificationInboxTests(unittest.TestCase):
    def test_notification_id_is_deterministic_per_tenant_user_and_event(self) -> None:
        first = notification_id_for("8", "18", "ticket.approved:8:t-1")
        self.assertEqual(first, notification_id_for("8", "18", "ticket.approved:8:t-1"))
        self.assertNotEqual(first, notification_id_for("8", "19", "ticket.approved:8:t-1"))
        self.assertEqual(64, len(first))
        self.assertEqual("TENANT#8#USER#18", user_partition_key("8", "18"))

    def test_non_ticket_events_do_not_create_a_user_visible_item(self) -> None:
        item, created = create_ticket_user_notification(
            {"eventType": "report.generated", "audienceType": "SELF", "recipientUserId": "18"}
        )
        self.assertIsNone(item)
        self.assertFalse(created)

    def test_public_serialization_omits_internal_tenant_and_dedupe_keys(self) -> None:
        value = serialize_user_notification(
            {
                "notificationId": "n-1",
                "tenantId": "8",
                "userId": "18",
                "dedupeKey": "private-key",
                "eventType": "ticket.approved",
                "title": "Aprobado",
                "body": "Ticket aprobado",
                "status": "UNREAD",
                "createdAt": "2026-08-31T00:00:00+00:00",
            }
        )
        self.assertEqual("n-1", value["notificationId"])
        self.assertNotIn("tenantId", value)
        self.assertNotIn("userId", value)
        self.assertNotIn("dedupeKey", value)


if __name__ == "__main__":
    unittest.main()
