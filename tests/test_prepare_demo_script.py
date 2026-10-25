"""Offline checks for demo preparation: no decision or execution API calls."""
import ast
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("e2e_demo", Path(__file__).resolve().parents[1] / "scripts/e2e_remediation.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class Response:
    status = 200

    def __init__(self, value, status=200):
        self.value, self.status = value, status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.value).encode()


class PrepareDemoTests(unittest.TestCase):
    def exercise(self, derived=False):
        requests, tickets, commands = [], {}, []

        def urlopen(req, **kwargs):
            requests.append((req.method, req.full_url))
            path = req.full_url.split("example.invalid")[-1]
            if path == "/auth/login":
                return Response({"access_token": "private-session-token"})
            if path == "/tickets" and req.method == "POST":
                data = json.loads(req.data)
                tid = str(len(tickets) + 1)
                count = 1 if "01-lectura" in data["metadata"]["scenario"] else 3
                tickets[tid] = {"id": tid, "status": "DERIVADO" if derived and tid == "2" else "PREAPROBADO",
                                "pending_decision": {"task_token": "private-task-token", "options": [{}] * count}}
                return Response({"ticket": {"id": tid}}, 201)
            if path.startswith("/tickets/") and req.method == "GET":
                return Response(tickets[path.rsplit("/", 1)[-1]])
            raise AssertionError("Forbidden API call: " + req.method + " " + path)

        def aws(command, **kwargs):
            if "send-command" in command:
                payload = json.loads(command[command.index("--parameters") + 1])
                commands.append(payload["commands"][0])
                result = {"Command": {"CommandId": "mock-command"}}
            else:
                output = ""
                if "\nitems=" in commands[-1]:
                    items = ast.literal_eval(commands[-1].split("\nitems=", 1)[1].split("\n", 1)[0])
                    output = json.dumps([{"scenario": item["scenario"], "target_ok": True, "keeper_ok": True} for item in items])
                result = {"CommandInvocations": [{"Status": "Success", "CommandPlugins": [{"Output": output}]}]}
            return SimpleNamespace(returncode=0, stdout=json.dumps(result), stderr="")

        previous = os.getcwd()
        with tempfile.TemporaryDirectory() as temporary:
            try:
                os.chdir(temporary)
                with patch.dict(os.environ, {"ADMIN_EMAIL": "admin", "ADMIN_PASSWORD": "secret", "API_BASE": "https://example.invalid"}), \
                        patch("sys.argv", ["e2e_remediation.py", "--prepare-demo"]), \
                        patch.object(demo.urllib.request, "urlopen", side_effect=urlopen), \
                        patch.object(demo.subprocess, "run", side_effect=aws), \
                        contextlib.redirect_stdout(io.StringIO()):
                    result = demo.main()
                self.assertEqual(len(tickets), 5)
                self.assertTrue(all(method in ("POST", "GET") for method, _ in requests))
                evidence = list(Path("local-output").rglob("*.json"))
                self.assertTrue(evidence)
                for path in evidence:
                    self.assertNotIn("private-session-token", path.read_text())
                    self.assertNotIn("private-task-token", path.read_text())
                self.assertEqual(len(list(Path("local-output").rglob("demo-guide.md"))), 1)
                self.assertEqual(result, 1 if derived else 0)
            finally:
                os.chdir(previous)

    def test_prepares_five_tickets_without_approving(self):
        self.exercise()

    def test_derivation_is_reported_as_failure(self):
        self.exercise(derived=True)


if __name__ == "__main__":
    unittest.main()
