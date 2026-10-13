import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import requests
from src.handlers.routes import chat


class ChatBudgetTests(unittest.TestCase):
    def test_lambda_remaining_reserves_three_seconds(self):
        request = SimpleNamespace(scope={"aws.context": Mock(get_remaining_time_in_millis=Mock(return_value=8000))})
        with patch.object(chat.time, "monotonic", return_value=100):
            self.assertEqual(105, chat._chat_deadline(request))

    def test_local_budget_is_bounded(self):
        with patch.object(chat.time, "monotonic", return_value=100):
            self.assertEqual(123, chat._chat_deadline(None))

    def test_shared_lambda_26_seconds_preserves_reserve(self):
        request = SimpleNamespace(scope={"aws.context": Mock(get_remaining_time_in_millis=Mock(return_value=26000))})
        with patch.object(chat.time, "monotonic", return_value=100):
            self.assertEqual(123, chat._chat_deadline(request))

    def test_expired_budget_never_starts_http(self):
        with patch.object(chat.time, "monotonic", return_value=100):
            with self.assertRaises(requests.exceptions.Timeout):
                chat._runtime_timeout(99)

    def test_connection_and_read_share_budget(self):
        with patch.object(chat.time, "monotonic", return_value=100):
            for deadline in (100.2, 102, 109, 120):
                connect, read = chat._runtime_timeout(deadline)
                self.assertGreater(connect, 0)
                self.assertGreater(read, 0)
                self.assertLessEqual(connect + read, min(9, deadline - 100))

    def test_exhausted_lambda_has_no_budget(self):
        request = SimpleNamespace(scope={"aws.context": Mock(get_remaining_time_in_millis=Mock(return_value=2000))})
        with patch.object(chat.time, "monotonic", return_value=100):
            self.assertEqual(100, chat._chat_deadline(request))
