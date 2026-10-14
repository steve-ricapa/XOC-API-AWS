"""Ensure the guarded superadmin delete handler is reachable through the shared HTTP API."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SuperadminDeleteRouteConfigTests(unittest.TestCase):
    def test_delete_route_is_registered_and_protected(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is not installed")

        for stage in ("dev", "staging", "prod"):
            with self.subTest(stage=stage):
                config = json.loads(subprocess.check_output(
                    [node, "-e", "console.log(JSON.stringify(require('./serverless.admin')))"],
                    cwd=ROOT,
                    env={**os.environ, "STAGE": stage},
                    text=True,
                ))
                events = config["functions"]["adminApi"]["events"]
                routes = [event["httpApi"] for event in events if "httpApi" in event]
                matching = [route for route in routes if route["method"] == "DELETE"
                            and route["path"] == "/superadmin/users/{userId}"]
                self.assertEqual(1, len(matching))
                self.assertIn("authorizer", matching[0])


if __name__ == "__main__":
    unittest.main()
