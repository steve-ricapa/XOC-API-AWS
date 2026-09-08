"""Exercise the real backfill and index builder with an entirely mocked AWS boundary."""
from copy import deepcopy
from decimal import Decimal
import io
import os
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import MagicMock, call, patch

from src import shared


class BackfillTicketIndexesTests(unittest.TestCase):
    def setUp(self):
        self.table = MagicMock()
        resource = MagicMock()
        resource.Table.return_value = self.table
        for p in (
            patch.dict(os.environ, {"TICKETS_TABLE_NAME": "unit-test-tickets"}),
            patch("boto3.resource", return_value=resource),
            patch("boto3.client", side_effect=AssertionError("No AWS client allowed")),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            p.start()
            self.addCleanup(p.stop)

        # Loading the script imports the real builder. Restore its import state
        # so the mock-backed table cannot leak into other tests in discovery.
        module_name = "src.shared.tickets_store"
        previous_module = sys.modules.get(module_name)
        missing = object()
        previous_attribute = getattr(shared, "tickets_store", missing)

        def restore_import():
            if previous_module is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous_module
            if previous_attribute is missing:
                if hasattr(shared, "tickets_store"):
                    delattr(shared, "tickets_store")
            else:
                shared.tickets_store = previous_attribute

        self.addCleanup(restore_import)
        script = Path(__file__).resolve().parents[1] / "scripts" / "backfill_tickets_dynamo_indexes.py"
        self.main = runpy.run_path(str(script))["main"]
        self.ticket = {
            "pk": "TICKET#7", "sk": "TICKET#historical-ticket",
            "tenant_id": Decimal("7"), "ticket_id": "historical-ticket",
            "status": " pending ", "created_at": "2026-07-01T00:00:00+00:00",
        }
        # Explicit expectations, not recomputed with the function under test.
        self.indexes = {
            "gsi1pk": "TICKET#7#STATUS#PENDING",
            "gsi1sk": "2026-07-01T00:00:00+00:00#historical-ticket",
            "gsi2pk": "TICKET#historical-ticket",
            "gsi2sk": "TICKET#7",
            "gsi3pk": "ALL_TICKETS",
            "gsi3sk": "2026-07-01T00:00:00+00:00#7#historical-ticket",
        }

    def run_items(self, *items):
        self.table.scan.return_value = {"Items": deepcopy(list(items))}
        self.main()

    def assert_ticket_update(self):
        self.table.update_item.assert_called_once_with(
            Key={"pk": self.ticket["pk"], "sk": self.ticket["sk"]},
            UpdateExpression="SET #gsi1pk = :gsi1pk, #gsi1sk = :gsi1sk, #gsi2pk = :gsi2pk, #gsi2sk = :gsi2sk, #gsi3pk = :gsi3pk, #gsi3sk = :gsi3sk",
            ExpressionAttributeNames={f"#{key}": key for key in self.indexes},
            ExpressionAttributeValues={f":{key}": value for key, value in self.indexes.items()},
        )

    def test_historical_ticket_without_entity_type_gets_exact_indexes(self):
        self.run_items(self.ticket)
        self.assert_ticket_update()

    def test_sophia_receipt_with_all_required_attributes_is_ignored(self):
        receipt = {**self.ticket, "pk": "SOPHIA_CONFIRMATION#7", "sk": "PROPOSAL#proposal-1",
                   "entity_type": "SOPHIA_TICKET_CONFIRMATION", "status": "CONFIRMED"}
        self.run_items(receipt)
        self.table.update_item.assert_not_called()

    def test_explicit_receipt_is_ignored_even_with_ticket_shaped_keys(self):
        self.run_items({**self.ticket, "entity_type": "SOPHIA_TICKET_CONFIRMATION"})
        self.table.update_item.assert_not_called()

    def test_unrelated_item_with_ticket_attributes_is_ignored(self):
        self.run_items({**self.ticket, "pk": "AUDIT#7", "sk": "ENTRY#1"})
        self.table.update_item.assert_not_called()

    def test_missing_or_mismatched_identity_is_ignored(self):
        for changes in ({"pk": "TICKET#8"}, {"sk": "TICKET#other"}, {"pk": None}, {"sk": None}):
            with self.subTest(changes=changes):
                self.run_items({**self.ticket, **changes})
        self.table.update_item.assert_not_called()

    def test_complete_ticket_is_not_updated(self):
        self.run_items({**self.ticket, **self.indexes})
        self.table.update_item.assert_not_called()

    def test_partial_or_stale_indexes_are_repaired_as_before(self):
        self.run_items({**self.ticket, "gsi1pk": "stale", "gsi2pk": self.indexes["gsi2pk"]})
        self.assert_ticket_update()

    def test_updated_at_fallback_and_string_tenant_are_preserved(self):
        ticket = {**self.ticket, "tenant_id": "7", "updated_at": self.ticket["created_at"]}
        del ticket["created_at"]
        self.run_items(ticket)
        self.assert_ticket_update()

    def test_missing_required_attributes_are_still_skipped(self):
        for field in ("tenant_id", "ticket_id", "status", "created_at"):
            ticket = dict(self.ticket)
            del ticket[field]
            with self.subTest(field=field):
                self.run_items(ticket)
        self.table.update_item.assert_not_called()

    def test_pagination_continues_after_receipt_only_page(self):
        receipt = {**self.ticket, "pk": "SOPHIA_CONFIRMATION#7", "sk": "PROPOSAL#1",
                   "entity_type": "SOPHIA_TICKET_CONFIRMATION"}
        cursor = {"pk": receipt["pk"], "sk": receipt["sk"]}
        self.table.scan.side_effect = [
            {"Items": [receipt], "LastEvaluatedKey": cursor},
            {"Items": [self.ticket]},
        ]
        self.main()
        self.assertEqual([call(), call(ExclusiveStartKey=cursor)], self.table.scan.call_args_list)
        self.assert_ticket_update()


if __name__ == "__main__":
    unittest.main()
