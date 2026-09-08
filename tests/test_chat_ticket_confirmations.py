"""C.3 regression tests: real route/store, atomic in-memory DynamoDB boundary.

No AWS credentials, network, or real database writes. A lock in this fake models
the server-side transaction; production never relies on a Python lock.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib
import os
import sys
from threading import Barrier, Lock
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import uuid

import jwt
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError, ReadTimeoutError
from botocore.session import get_session
from botocore.validate import validate_parameters

from src.handlers.routes import chat
from src import shared
from src.shared import chat_ticket_confirmations as confirmations
from src.shared.errors import AppError, ConflictError, ForbiddenError, ValidationError

_SECRET = "unit-test-only-signing-key-not-a-real-secret"


def _decode_item(item):
    return {key: TypeDeserializer().deserialize(value) for key, value in item.items()}


class AtomicDynamo:
    def __init__(self):
        self.items = {}
        self.lock = Lock()
        self.barrier = None
        self.conflicts = 0
        self.lose_response = False
        self.fail_before_commit = False
        self.fail_update = False
        self.transactions = []
        self.reads = []
        self.committed = {}

    def get_item(self, **kwargs):
        assert kwargs["ConsistentRead"] is True
        key = _decode_item(kwargs["Key"])
        with self.lock:
            self.reads.append(kwargs)
            item = deepcopy(self.items.get((key["pk"], key["sk"])))
        return {"Item": item} if item else {}

    def transact_write_items(self, **kwargs):
        if self.barrier:
            self.barrier.wait(timeout=5)
        with self.lock:
            self.transactions.append(deepcopy(kwargs))
            assert len(kwargs["TransactItems"]) == 2
            if self.fail_before_commit:
                raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "TransactWriteItems")
            if self.conflicts:
                self.conflicts -= 1
                raise ClientError({"Error": {"Code": "TransactionCanceledException"},
                                   "CancellationReasons": [{"Code": "TransactionConflict"}]}, "TransactWriteItems")
            client_token = kwargs["ClientRequestToken"]
            if client_token in self.committed:
                assert self.committed[client_token] == kwargs
                return {}
            pending = []
            for entry in kwargs["TransactItems"]:
                put = entry["Put"]
                assert put["TableName"] == "unit-test-tickets"
                assert put["ConditionExpression"] == "attribute_not_exists(pk) AND attribute_not_exists(sk)"
                item = _decode_item(put["Item"])
                key = (item["pk"], item["sk"])
                if key in self.items:
                    raise ClientError({"Error": {"Code": "TransactionCanceledException"},
                                       "CancellationReasons": [{"Code": "ConditionalCheckFailed"}]}, "TransactWriteItems")
                pending.append((key, put["Item"]))
            # Both conditions pass before either item is persisted.
            self.items.update(deepcopy(dict(pending)))
            self.committed[client_token] = deepcopy(kwargs)
            if self.lose_response:
                self.lose_response = False
                raise ReadTimeoutError(endpoint_url="https://dynamodb.invalid")
        return {}

    def update_item(self, **kwargs):
        if self.fail_update:
            raise RuntimeError("simulated update failure")
        key = _decode_item(kwargs["Key"])
        with self.lock:
            self.items[(key["pk"], key["sk"])]["event_status"] = kwargs["ExpressionAttributeValues"][":event_status"]
        return {}

    def decoded_items(self):
        return [_decode_item(item) for item in self.items.values()]


class ChatTicketConfirmationTests(unittest.TestCase):
    def setUp(self):
        # Do not leave a fake module/table cached for unrelated tests in discovery.
        module_name = "src.shared.tickets_store"
        previous_module = sys.modules.get(module_name)
        previous_attribute = getattr(shared, "tickets_store", None)
        with patch.dict(os.environ, {"TICKETS_TABLE_NAME": "unit-test-tickets"}), patch("boto3.resource"):
            self.store = importlib.import_module(module_name)
        if previous_module is None:
            def remove_test_import():
                sys.modules.pop(module_name, None)
                if previous_attribute is None:
                    delattr(shared, "tickets_store")
                else:
                    shared.tickets_store = previous_attribute
            self.addCleanup(remove_test_import)
        self.db = AtomicDynamo()
        self.events = MagicMock()
        self.events.put_events.return_value = {"FailedEntryCount": 0, "Entries": [{"EventId": "test-event"}]}
        self.user = SimpleNamespace(id=12, role="ADMIN", tenant_id=7, effective_tenant_id=7, delegation_active=False)
        patches = [
            patch.object(chat, "get_jwt_secret_key", return_value=_SECRET),
            patch.object(self.store, "table", SimpleNamespace(name="unit-test-tickets")),
            patch.object(confirmations.boto3, "client", side_effect=lambda service, **kwargs: self.db if service == "dynamodb" else self.events),
            patch("src.shared.config.get_settings", return_value=SimpleNamespace(event_bus_name="test-bus")),
            patch.object(confirmations.time, "sleep"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.token = self.make_token()

    def make_token(self, user=None, **overrides):
        user = user or self.user
        return chat._ticket_confirmation_token(
            action_plan=overrides.pop("action_plan", {"subject": "Revisar VPN", "description": "Solo diagnostico", "severity": "HIGH"}),
            tenant_id=user.effective_tenant_id, current_user=user, request_id="trace-1", **overrides,
        )

    def claims(self, token=None):
        return jwt.decode(token or self.token, _SECRET, algorithms=["HS256"], audience=chat._TICKET_CONFIRMATION_AUDIENCE)

    def confirm(self, token=None, user=None, **body):
        return chat.confirm_chat_ticket_proposal({"confirmation_token": token or self.token, **body}, user or self.user)

    def tickets(self):
        return [item for item in self.db.decoded_items() if item["pk"].startswith("TICKET#")]

    def receipt(self):
        return next(item for item in self.db.decoded_items() if item["pk"].startswith("SOPHIA_CONFIRMATION#"))

    def test_proposal_identity_is_backend_generated_and_matches_signed_token(self):
        proposal = chat._prepare_ticket_proposal({}, action_plan={"subject": "VPN", "proposal_id": "model-controlled"},
                                               tenant_id=7, current_user=self.user, request_id="trace-1")["ticket_proposal"]
        claims = self.claims(proposal["confirmation_token"])
        self.assertEqual(4, uuid.UUID(proposal["proposal_id"]).version)
        self.assertEqual(proposal["proposal_id"], claims["proposal_id"])
        self.assertEqual(claims["proposal_id"], self.claims(proposal["confirmation_token"])["proposal_id"])
        self.assertEqual(300, claims["exp"] - claims["iat"])

    def test_first_confirmation_persists_metadata_priority_and_minimal_contract(self):
        result = self.confirm(tenantId=999, userId=888, role="SUPERADMIN", proposal_id=str(uuid.uuid4()))
        self.assertTrue(result["ticket_created"])
        self.assertFalse(result["already_confirmed"])
        item = self.tickets()[0]
        self.assertEqual(result["ticket_id"], item["ticket_id"])
        self.assertEqual(7, item["tenant_id"])
        self.assertEqual(12, item["created_by_user_id"])
        self.assertEqual({"source": "sophia_chat_confirmed", "proposal_id": self.claims()["proposal_id"],
                          "proposal_request_id": "trace-1"}, item["metadata"])
        self.assertEqual("high", item["priority"])
        self.assertEqual("HIGH", item["severity"])
        self.assertNotIn(self.token, repr(self.db.items))
        self.assertFalse(any(key.startswith("gsi") for key in self.receipt()))
        self.assertEqual("PUBLISHED", self.receipt()["event_status"])
        self.events.put_events.assert_called_once()
        shape = get_session().get_service_model("dynamodb").operation_model("TransactWriteItems").input_shape
        validate_parameters(self.db.transactions[0], shape)

    def test_replay_returns_same_ticket_without_transaction_or_event(self):
        first = self.confirm()
        second = self.confirm()
        self.assertEqual(first["ticket_id"], second["ticket_id"])
        self.assertTrue(second["ticket_created"])
        self.assertTrue(second["already_confirmed"])
        self.assertEqual(1, len(self.tickets()))
        self.assertEqual(1, len(self.db.transactions))
        self.events.put_events.assert_called_once()

    def test_concurrent_requests_condition_failure_recovers_winner(self):
        self.db.barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.confirm(), range(2)))
        self.assertEqual(results[0]["ticket_id"], results[1]["ticket_id"])
        self.assertEqual([False, True], sorted(r["already_confirmed"] for r in results))
        self.assertEqual(1, len(self.tickets()))
        self.assertEqual(2, len(self.db.transactions))
        self.events.put_events.assert_called_once()

    def test_http_response_lost_retry_recovers_persisted_ticket(self):
        self.confirm()  # client discards the HTTP response
        result = self.confirm()
        self.assertEqual(self.receipt()["ticket_id"], result["ticket_id"])
        self.assertTrue(result["already_confirmed"])
        self.events.put_events.assert_called_once()

    def test_dynamo_commit_response_lost_recovers_without_unsafe_event_retry(self):
        self.db.lose_response = True
        first = self.confirm()
        self.assertTrue(first["already_confirmed"])
        self.assertEqual(first["ticket_id"], self.confirm()["ticket_id"])
        self.assertEqual("UNCONFIRMED", self.receipt()["event_status"])
        self.events.put_events.assert_not_called()

    def test_transaction_failure_leaves_no_consumed_proposal_or_ticket(self):
        self.db.fail_before_commit = True
        with self.assertRaises(ClientError):
            self.confirm()
        self.assertEqual({}, self.db.items)
        self.events.put_events.assert_not_called()
        self.db.fail_before_commit = False
        self.assertFalse(self.confirm()["already_confirmed"])

    def test_ticket_condition_failure_does_not_persist_an_orphan_receipt(self):
        ticket_id, item = self.store.build_new_ticket_item({"subject": "Existing"}, 7, 12)
        self.db.items[(item["pk"], item["sk"])] = confirmations._encode(item)
        with patch.object(self.store, "build_new_ticket_item", return_value=(ticket_id, item)):
            with self.assertRaises(ConflictError):
                self.confirm()
        self.assertEqual(1, len(self.db.items))
        self.events.put_events.assert_not_called()

    def test_transaction_conflict_is_retried_with_same_client_request_token(self):
        self.db.conflicts = 1
        self.assertFalse(self.confirm()["already_confirmed"])
        self.assertEqual(self.db.transactions[0], self.db.transactions[1])
        self.assertEqual(1, len(self.tickets()))
        self.events.put_events.assert_called_once()

    def test_exhausted_conflicts_are_retryable_without_orphan_receipt(self):
        self.db.conflicts = 3
        with self.assertRaises(AppError) as caught:
            self.confirm()
        self.assertEqual(503, caught.exception.status_code)
        self.assertEqual({}, self.db.items)
        self.assertFalse(self.confirm()["already_confirmed"])

    def test_event_failed_entry_does_not_undo_ticket_or_republish_on_replay(self):
        self.events.put_events.return_value = {"FailedEntryCount": 1, "Entries": [{"ErrorCode": "InternalFailure"}]}
        first = self.confirm()
        self.assertEqual("FAILED", self.receipt()["event_status"])
        self.assertEqual(first["ticket_id"], self.confirm()["ticket_id"])
        self.events.put_events.assert_called_once()

    def test_event_timeout_is_unknown_and_never_republished_by_confirmation(self):
        self.events.put_events.side_effect = ReadTimeoutError(endpoint_url="https://events.invalid")
        self.confirm()
        self.assertEqual("UNKNOWN", self.receipt()["event_status"])
        self.assertTrue(self.confirm()["already_confirmed"])
        self.events.put_events.assert_called_once()

    def test_event_receipt_update_failure_preserves_success_and_no_resend(self):
        self.db.fail_update = True
        first = self.confirm()
        self.assertEqual("UNCONFIRMED", self.receipt()["event_status"])
        self.assertEqual(first["ticket_id"], self.confirm()["ticket_id"])
        self.events.put_events.assert_called_once()

    def test_deleted_ticket_is_not_resurrected_by_replay(self):
        result = self.confirm()
        del self.db.items[("TICKET#7", f"TICKET#{result['ticket_id']}")]
        with self.assertRaises(ConflictError):
            self.confirm()
        self.assertEqual(0, len(self.tickets()))
        self.assertEqual(1, len(self.db.transactions))
        self.events.put_events.assert_called_once()

    def test_same_trace_different_proposals_are_independent(self):
        first = self.confirm()
        second = self.confirm(self.make_token())
        self.assertNotEqual(first["ticket_id"], second["ticket_id"])
        self.assertEqual(2, len(self.tickets()))

    def test_same_proposal_with_different_signed_content_is_rejected(self):
        self.confirm()
        token = self.make_token(proposal_id=self.claims()["proposal_id"], action_plan={"subject": "Otro", "severity": "high"})
        with self.assertRaises(ConflictError):
            self.confirm(token)
        self.assertEqual(1, len(self.tickets()))

    def test_other_user_tenant_role_and_delegation_state_are_denied_before_store(self):
        contexts = [dict(id=99), dict(effective_tenant_id=8),
                    dict(role="ADMIN_XOC", delegation_active=True), dict(delegation_active=True),
                    dict(role="USER"), dict(role="SUPERADMIN")]
        for changes in contexts:
            with self.subTest(changes=changes):
                user = SimpleNamespace(**{**vars(self.user), **changes})
                with self.assertRaises(ForbiddenError):
                    self.confirm(user=user)
        self.assertEqual([], self.db.reads)
        self.assertEqual([], self.db.transactions)

    def test_admin_xoc_requires_active_delegation_and_same_tenant(self):
        user = SimpleNamespace(id=12, role="ADMIN_XOC", tenant_id=None, effective_tenant_id=7, delegation_active=True)
        token = self.make_token(user)
        self.assertTrue(self.confirm(token, user)["ticket_created"])
        user.delegation_active = False
        with self.assertRaises(ForbiddenError):
            self.confirm(token, user)
        user.delegation_active = True
        user.effective_tenant_id = 8
        with self.assertRaises(ForbiddenError):
            self.confirm(token, user)
        self.assertEqual(1, len(self.db.transactions))

    def test_expired_token_never_reads_or_writes_even_after_confirmation(self):
        self.confirm()
        self.db.reads.clear()
        claims = self.claims()
        claims["exp"] = claims["iat"] - 1
        with self.assertRaises(ValidationError):
            self.confirm(jwt.encode(claims, _SECRET, algorithm="HS256"))
        self.assertEqual([], self.db.reads)
        self.assertEqual(1, len(self.db.transactions))

    def test_legacy_missing_claims_wrong_audience_and_malformed_id_are_rejected(self):
        for field in ("proposal_id", "aud", "iat", "exp", "actor_user_id", "delegation_active"):
            claims = self.claims()
            del claims[field]
            with self.subTest(missing=field), self.assertRaises(ValidationError):
                self.confirm(jwt.encode(claims, _SECRET, algorithm="HS256"))
        for field, value in (("aud", "other-service"), ("proposal_id", "not-a-uuid"), ("actor_user_id", "invalid")):
            claims = self.claims()
            claims[field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.confirm(jwt.encode(claims, _SECRET, algorithm="HS256"))
        self.assertEqual([], self.db.reads)

    def test_wrong_purpose_scope_and_invalid_signature_do_not_reach_store(self):
        for field in ("type", "scope"):
            claims = self.claims()
            claims[field] = "wrong-purpose"
            with self.subTest(field=field), self.assertRaises(ForbiddenError):
                self.confirm(jwt.encode(claims, _SECRET, algorithm="HS256"))
        with self.assertRaises(ValidationError):
            self.confirm(jwt.encode(self.claims(), "different-unit-test-only-signing-key", algorithm="HS256"))
        self.assertEqual([], self.db.reads)

    def test_expired_unconfirmed_token_does_not_create_receipt(self):
        claims = self.claims()
        claims["exp"] = claims["iat"] - 1
        with self.assertRaises(ValidationError):
            self.confirm(jwt.encode(claims, _SECRET, algorithm="HS256"))
        self.assertEqual({}, self.db.items)
        self.assertEqual([], self.db.reads)

    def test_valid_priorities_are_normalized_and_unknown_values_do_not_write(self):
        for severity in ("CRITICAL", "high", "Medium", "low", "INFO"):
            token = self.make_token(action_plan={"subject": "VPN", "severity": severity})
            result = self.confirm(token)
            self.assertEqual(severity.lower(), result["ticket"]["priority"])
            self.assertEqual(severity.upper(), result["ticket"]["severity"])
        token = self.make_token(action_plan={"subject": "VPN", "severity": "P0-invented"})
        with self.assertRaises(ValidationError):
            self.confirm(token)
        self.assertEqual(5, len(self.tickets()))

    def test_shared_ticket_builder_contract_is_unchanged(self):
        _, item = self.store.build_new_ticket_item({"subject": "Legacy", "metadata": {"arbitrary": True}}, 7, 12)
        self.assertNotIn("metadata", item)
        self.assertNotIn("priority", item)
        self.assertNotIn("severity", item)


if __name__ == "__main__":
    unittest.main()
