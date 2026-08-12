"""Unit coverage for Phase 1 push device behavior without AWS calls."""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from src.handlers.routes import devices
from src.shared import device_registry


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

    def test_user_can_send_self_campaign_with_no_devices(self) -> None:
        with patch.object(devices, "list_active_devices_by_user", return_value=[]), patch.object(
            devices, "create_notification_campaign"
        ) as create_campaign, patch.object(devices, "update_notification_campaign_result") as update_campaign:
            result = devices.send_notification_to_audience(
                {"audienceType": "SELF", "title": "Test", "body": "Body"}, self.claims
            )

        self.assertEqual("no_devices", result["status"])
        self.assertEqual(0, result["totalDevices"])
        self.assertEqual("SELF", result["audienceType"])
        self.assertEqual("NO_DEVICES", update_campaign.call_args.args[2]["status"])
        campaign = create_campaign.call_args.args[0]
        self.assertNotIn("pushToken", campaign)
        self.assertEqual("8", campaign["tenantId"])

    def test_user_cannot_send_tenant_all_campaign(self) -> None:
        with self.assertRaises(devices.ForbiddenError):
            devices.send_notification_to_audience(
                {"audienceType": "TENANT_ALL", "title": "Test", "body": "Body"}, self.claims
            )

    def test_admin_can_send_tenant_all_campaign(self) -> None:
        admin_claims = {"tenantId": "8", "userId": "19", "role": "ADMIN"}
        with patch.object(devices, "list_active_devices_by_tenant", return_value=[]), patch.object(
            devices, "create_notification_campaign"
        ), patch.object(devices, "update_notification_campaign_result"):
            result = devices.send_notification_to_audience(
                {"audienceType": "TENANT_ALL", "title": "Test", "body": "Body"}, admin_claims
            )

        self.assertEqual("no_devices", result["status"])
        self.assertEqual("TENANT_ALL", result["audienceType"])

    def test_tenant_admins_is_blocked_without_safe_rds_resolver(self) -> None:
        admin_claims = {"tenantId": "8", "userId": "19", "role": "ADMIN"}
        with self.assertRaisesRegex(devices.ValidationError, "audience resolver not implemented for TENANT_ADMINS"):
            devices.send_notification_to_audience(
                {"audienceType": "TENANT_ADMINS", "title": "Test", "body": "Body"}, admin_claims
            )

    def test_campaign_rejects_more_than_100_devices_before_sending(self) -> None:
        active_devices = [
            {
                "deviceId": f"device-{index}",
                "userId": "18",
                "platform": "android",
                "pushProvider": "fcm",
                "pushToken": f"token-{index}",
                "status": "ACTIVE",
                "notificationsEnabled": True,
            }
            for index in range(101)
        ]
        admin_claims = {"tenantId": "8", "userId": "19", "role": "ADMIN"}
        with patch.object(devices, "list_active_devices_by_tenant", return_value=active_devices), patch.object(
            devices, "_pinpoint_client"
        ) as pinpoint:
            with self.assertRaisesRegex(devices.ValidationError, "Audience exceeds max devices"):
                devices.send_notification_to_audience(
                    {"audienceType": "TENANT_ALL", "title": "Test", "body": "Body"}, admin_claims
                )

        pinpoint.assert_not_called()

    def test_campaign_invalidates_bad_token_and_records_partial_failure(self) -> None:
        device = {
            "deviceId": "ios-test-001",
            "userId": "18",
            "platform": "ios",
            "pushProvider": "apns",
            "apnsEnvironment": "production",
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
            "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "campaign-request"},
        }
        with patch.object(devices, "list_active_devices_by_user", return_value=[device]), patch.object(
            devices, "create_notification_campaign"
        ), patch.object(devices, "update_notification_campaign_result") as update_campaign, patch.object(
            devices, "update_registered_device"
        ) as update_device, patch.object(devices, "_pinpoint_client", return_value=pinpoint):
            result = devices.send_notification_to_audience(
                {"audienceType": "SELF", "title": "Test", "body": "Body"}, self.claims
            )

        self.assertEqual("failed", result["status"])
        self.assertEqual(1, result["failedCount"])
        self.assertEqual(1, result["invalidTokenCount"])
        self.assertEqual("INVALID", update_device.call_args.args[3]["status"])
        self.assertEqual("FAILED", update_campaign.call_args.args[2]["status"])
        request = pinpoint.send_messages.call_args.kwargs["MessageRequest"]
        self.assertEqual("APNS", request["Addresses"]["apns-token"]["ChannelType"])

    def test_registry_active_queries_exclude_inactive_and_invalid_devices(self) -> None:
        table = MagicMock()
        table.query.return_value = {
            "Items": [
                {"tenantId": "8", "status": "ACTIVE", "notificationsEnabled": True, "deviceId": "active"},
                {"tenantId": "8", "status": "INACTIVE", "notificationsEnabled": True, "deviceId": "inactive"},
                {"tenantId": "8", "status": "INVALID", "notificationsEnabled": True, "deviceId": "invalid"},
                {"tenantId": "8", "status": "ACTIVE", "notificationsEnabled": False, "deviceId": "disabled"},
            ]
        }

        with patch.object(device_registry, "device_registry_table", return_value=table):
            result = device_registry.list_active_devices_by_tenant("8")

        self.assertEqual(["active"], [item["deviceId"] for item in result])
        self.assertIn("FilterExpression", table.query.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
