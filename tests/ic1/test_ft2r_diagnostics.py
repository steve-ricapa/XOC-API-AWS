"""Local tests for evidence accounting; never imports the product or SDK."""
import unittest
from ft2r_analyze import breakdown, population, stats


class EvidenceAccountingTests(unittest.TestCase):
    def row(self, extra):
        phase = dict(kind="phase", phase="startup", child_generation=1,
                     started_at=0., finished_at=4., elapsed=4., ok=True)
        events = [dict(event="S03_entry", at=.1, entry_at=.1, role="server", generation=1, pid=8),
                  dict(event="S02_observed", at=.02, role="supervisor", generation=0, child_generation=1)]
        for event in extra:
            events.append(dict(role="server", generation=1, pid=8, **event))
        return dict(control=[phase], timeline=events)

    def test_nested_imports_are_not_added_twice(self):
        row = self.row([dict(event="import_begin", module="outer", at=.2),
            dict(event="import_begin", module="inner", at=.3),
            dict(event="import_end", module="inner", at=.8),
            dict(event="import_end", module="outer", at=1.)])
        parts = breakdown(row)[0]["components"]
        self.assertAlmostEqual(parts["C_IMPORTS"]["seconds"], .8)
        self.assertAlmostEqual(sum(v["seconds"] for v in parts.values()), 4.)
        self.assertAlmostEqual(sum(v["percent"] for v in parts.values()), 100.)

    def test_optional_import_without_end_is_not_stretched(self):
        row = self.row([dict(event="import_begin", module="optional", at=.2)])
        result = breakdown(row)[0]
        self.assertEqual(result["components"]["C_IMPORTS"]["seconds"], 0)
        self.assertEqual(len(result["incomplete_intervals"]), 1)

    def test_wiring_excludes_nested_import_time(self):
        row = self.row([dict(event="S06_wiring_begin", at=.2),
            dict(event="import_begin", module="provider", at=.3),
            dict(event="import_end", module="provider", at=.8),
            dict(event="S06_wiring_end", at=1.)])
        parts = breakdown(row)[0]["components"]
        self.assertAlmostEqual(parts["E_GATEWAY_WIRING"]["seconds"], .3)
        self.assertAlmostEqual(parts["C_IMPORTS"]["seconds"], .5)

    def test_censored_failure_not_credited_as_completion(self):
        row = dict(passed=False, initial_ready=False, control=[dict(kind="phase", phase="startup", elapsed=4.001, ok=False)])
        result = population([row])
        self.assertEqual(result["censored"], 1)
        self.assertIsNone(result["successful_completion_only"])
        self.assertEqual(result["buckets"]["gt4.0"], 1)

    def test_missing_child_timeline_explicit(self):
        row = self.row([])
        row["timeline"] = []
        self.assertTrue(breakdown(row)[0]["missing_child_timeline"])

    def test_percentile_and_empty_population(self):
        self.assertIsNone(stats([]))
        self.assertEqual(stats([1., 3.])["p50"], 2.)
        self.assertAlmostEqual(stats([1., 3.])["p95"], 2.9)


if __name__ == "__main__":
    unittest.main()
