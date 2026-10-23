import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import requests
from src.handlers.routes import chat


class ChatBudgetTests(unittest.TestCase):
    def test_first_turn_uses_most_of_budget_second_turn_keeps_margin(self):
        with patch.object(chat.time, "monotonic", return_value=100):
            self.assertEqual((1, 13), chat._runtime_timeout(123))
            self.assertEqual((1, 8), chat._runtime_timeout(123, second_turn=True))

    def test_second_turn_cannot_extend_remaining_deadline(self):
        with patch.object(chat.time, "monotonic", return_value=100):
            for remaining in (0.2, 2, 7, 10, 23):
                connect, read = chat._runtime_timeout(100 + remaining, second_turn=True)
                self.assertLessEqual(connect + read, min(9, remaining) + 1e-9)
            with self.assertRaises(requests.Timeout):
                chat._runtime_timeout(100, second_turn=True)

    def test_transport_error_logging_is_safe_and_never_retries(self):
        for error, outcome in (
            (requests.ConnectTimeout("SECRET"), "connect_timeout"),
            (requests.ReadTimeout("SECRET"), "read_timeout"),
            (requests.ConnectionError("SECRET"), "connection_error"),
            (ValueError("SECRET"), "invalid_response"),
        ):
            with self.subTest(outcome=outcome):
                with patch.object(chat.requests, "post", side_effect=error) as post:
                    with self.assertLogs(chat._latency_logger, level="INFO") as logs:
                        with self.assertRaises(type(error)):
                            chat._timed_sophia_post("second_turn", "server-fixture",
                                                   url="https://SECRET", json={"secret": "SECRET"})
                post.assert_called_once()
                self.assertIn("outcome=" + outcome, str(logs.output))
                self.assertNotIn("SECRET", str(logs.output))

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
                self.assertLessEqual(connect + read, min(14, deadline - 100))

    def test_exhausted_lambda_has_no_budget(self):
        request = SimpleNamespace(scope={"aws.context": Mock(get_remaining_time_in_millis=Mock(return_value=2000))})
        with patch.object(chat.time, "monotonic", return_value=100):
            self.assertEqual(100, chat._chat_deadline(request))
