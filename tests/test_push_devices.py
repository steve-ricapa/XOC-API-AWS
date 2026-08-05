"""Unit coverage for Phase 1 push device behavior without AWS calls."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from src.handlers.routes import devices


class PushDevicesTests(unittest.TestCase):
    claims = {"tenantId": "8", "userId": "18", "role": "USER"}

    def setUp(self) -> None:
        self.previous_stage = os.environ.get("APP_STAGE")
        os.environ["APP_STAGE"] = "prod"
        os.environ["END_USER_MESSAGING_APPLICATION_ID"] = "app-id"

    def tearDown(self) -> None:
        if self.previous_stage is None:
            os.environ.pop("APP_STAGE", None)
        else:
            os.environ["APP_STAGE"] = self.previous_stage

    def test_ios_registration_preserves_explicit_sandbox_environment(self) -> None:
        table = MagicMock()
        table.get_item.return_value = {}
        payload = {
            "deviceId": "ios-test-001",
            "platform": "ios",
            "pushProvider": "apns",
            "pushToken": "apns-token",
            "apnsEnvironment": "sandbox",
            "notificationsEnabled": True,
        }

        with patch.object(devices, "device_registry_table", return_value=table):
            result = devices.register_device(payload, self.claims)

        item = table.put_item.call_args.kwargs["Item"]
        self.assertEqual("sandbox", item["apnsEnvironment"])
        self.assertEqual("ACTIVE", item["status"])
        self.assertEqual("sandbox", result["apnsEnvironment"])

    def test_ios_registration_defaults_to_production_in_prod(self) -> None:
        table = MagicMock()
        table.get_item.return_value = {}
        payload = {
            "deviceId": "ios-test-001",
            "platform": "ios",
            "pushProvider": "apns",
            "pushToken": "apns-token",
        }

        with patch.object(devices, "device_registry_table", return_value=table):
            devices.register_device(payload, self.claims)

        self.assertEqual("production", table.put_item.call_args.kwargs["Item"]["apnsEnvironment"])

    def test_android_rejects_apns_environment(self) -> None:
        payload = {
            "deviceId": "android-test-001",
            "platform": "android",
            "pushProvider": "fcm",
            "pushToken": "fcm-token",
            "apnsEnvironment": "sandbox",
        }

        with self.assertRaises(devices.ValidationError):
            devices.register_device(payload, self.claims)

    def test_ios_sandbox_uses_apns_sandbox_channel_and_apns_message(self) -> None:
        device = {
            "platform": "ios",
            "pushProvider": "apns",
            "apnsEnvironment": "sandbox",
            "pushToken": "apns-token",
            "tokenHash": "a" * 64,
            "status": "ACTIVE",
            "notificationsEnabled": True,
        }
        pinpoint = MagicMock()
        pinpoint.send_messages.return_value = {
            "MessageResponse": {
                "Result": {
                    "apns-token": {
                        "DeliveryStatus": "SUCCESSFUL",
                        "StatusCode": 200,
                        "StatusMessage": "Accepted",
                    }
                }
            },
            "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "request-1"},
        }

        with patch.object(devices, "get_registered_device", return_value=device), patch.object(
            devices, "_pinpoint_client", return_value=pinpoint
        ):
            result = devices.send_test_notification(
                {"deviceId": "ios-test-001", "title": "Test", "body": "Body"}, self.claims
            )

        request = pinpoint.send_messages.call_args.kwargs["MessageRequest"]
        self.assertEqual("APNS_SANDBOX", request["Addresses"]["apns-token"]["ChannelType"])
        self.assertIn("APNSMessage", request["MessageConfiguration"])
        self.assertNotIn("APNS_SANDBOXMessage", request["MessageConfiguration"])
        self.assertEqual("APNS_SANDBOX", result["channelType"])
        self.assertEqual("SUCCESSFUL", result["deliveryStatus"])

    def test_android_keeps_gcm_channel_and_message_configuration(self) -> None:
        device = {
            "platform": "android",
            "pushProvider": "fcm",
            "pushToken": "fcm-token",
            "tokenHash": "b" * 64,
            "status": "ACTIVE",
            "notificationsEnabled": True,
        }
        pinpoint = MagicMock()
        pinpoint.send_messages.return_value = {
            "MessageResponse": {
                "Result": {
                    "fcm-token": {
                        "DeliveryStatus": "SUCCESSFUL",
                        "StatusCode": 200,
                    }
                }
            },
            "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "request-android"},
        }

        with patch.object(devices, "get_registered_device", return_value=device), patch.object(
            devices, "_pinpoint_client", return_value=pinpoint
        ):
            result = devices.send_test_notification(
                {"deviceId": "android-test-001", "title": "Test", "body": "Body"}, self.claims
            )

        request = pinpoint.send_messages.call_args.kwargs["MessageRequest"]
        self.assertEqual("GCM", request["Addresses"]["fcm-token"]["ChannelType"])
        self.assertIn("GCMMessage", request["MessageConfiguration"])
        self.assertEqual("GCM", result["channelType"])

    def test_invalid_push_token_is_soft_invalidated(self) -> None:
        device = {
            "platform": "ios",
            "pushProvider": "apns",
            "apnsEnvironment": "sandbox",
            "pushToken": "apns-token",
            "tokenHash": "a" * 64,
            "status": "ACTIVE",
            "notificationsEnabled": True,
        }
        pinpoint = MagicMock()
        pinpoint.send_messages.return_value = {
            "MessageResponse": {
                "Result": {
                    "apns-token": {
                        "DeliveryStatus": "PERMANENT_FAILURE",
                        "StatusCode": 400,
                        "StatusMessage": "BadDeviceToken",
                    }
                }
            },
            "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "request-2"},
        }

        with patch.object(devices, "get_registered_device", return_value=device), patch.object(
            devices, "_pinpoint_client", return_value=pinpoint
        ), patch.object(devices, "update_registered_device") as update_device:
            result = devices.send_test_notification(
                {"deviceId": "ios-test-001", "title": "Test", "body": "Body"}, self.claims
            )

        self.assertEqual("failed", result["status"])
        self.assertEqual("BadDeviceToken", result["statusMessage"])
        self.assertEqual("INVALID", update_device.call_args.args[3]["status"])
        self.assertEqual(400, update_device.call_args.args[3]["lastFailureStatusCode"])

    def test_delete_soft_deactivates_only_authenticated_device_key(self) -> None:
        with patch.object(devices, "get_registered_device", return_value={"deviceId": "device-1"}), patch.object(
            devices, "update_registered_device"
        ) as update_device:
            result = devices.delete_device("device-1", self.claims)

        self.assertEqual({"status": "INACTIVE", "deviceId": "device-1"}, result)
        self.assertEqual("8", update_device.call_args.args[0])
        self.assertEqual("18", update_device.call_args.args[1])
        self.assertEqual("device-1", update_device.call_args.args[2])
        self.assertEqual("INACTIVE", update_device.call_args.args[3]["status"])


if __name__ == "__main__":
    unittest.main()
