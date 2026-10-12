"""Negative tests: natural observations must never pass the product gate."""
import json
from pathlib import Path
import tempfile
import unittest
from ft2r1_gate import check_populations


class GateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ft2r1-gate-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name,n,gens in (("original-30",30,1),("guarded-30",30,1),("recovery-20",20,2)):
            (self.root/name).mkdir()
            for i in range(1,n+1):
                row = dict(passed=True, control=[dict(kind="phase",phase="startup",ok=True,budget=4.0,elapsed=2.0) for _ in range(gens)],
                    final_children="", cleanup=dict(own_server_residual=[],harness_forced=False),
                    isolation=dict(network_attempts=0,secret_file_attempts=0),
                    no_stdio=dict(passed=True,guarded_processes=3),
                    recovery=dict(recovery=dict(ok=True),restoration=dict(correct=True)))
                (self.root/name/f"run-{i:02}.json").write_text(json.dumps(row))

    def mutate(self, folder, change):
        path = self.root/folder/"run-01.json"
        row = json.loads(path.read_text())
        change(row)
        path.write_text(json.dumps(row))

    def test_complete_original_population(self):
        self.assertEqual(len(check_populations(self.root)),80)

    def test_diagnostic_never_accepted(self):
        self.mutate("original-30",lambda r:r.update(diagnostic_only=True))
        with self.assertRaisesRegex(AssertionError,"DIAGNOSTIC_NOT_ACCEPTANCE"):
            check_populations(self.root)

    def test_extended_budget_never_accepted(self):
        self.mutate("original-30",lambda r:r["control"][0].update(budget=12.0))
        with self.assertRaisesRegex(AssertionError,"ORIGINAL_CONTRACT_FAILURE"):
            check_populations(self.root)

    def test_guard_failure_stops(self):
        self.mutate("guarded-30",lambda r:r["no_stdio"].update(passed=False))
        with self.assertRaisesRegex(AssertionError,"GUARD_FAILURE"):
            check_populations(self.root)

    def test_missing_replacement_stops(self):
        self.mutate("recovery-20",lambda r:r["control"].pop())
        with self.assertRaisesRegex(AssertionError,"STARTUP_FAILURE"):
            check_populations(self.root)


if __name__=="__main__":
    unittest.main()
