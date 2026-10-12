"""Pure harness tests; no product imports or sleeps."""
import unittest
from ft2r1_observer import observer_class


class ObserverTests(unittest.TestCase):
    def make(self, now=104.2, result=True):
        rows, calls = [], []
        class Base:
            generation = 1
            def _wait(self, predicate, deadline, abort=True):
                calls.append((deadline, abort))
                return predicate() and result
            def _phase(self, *args, **kwargs):
                return args, kwargs
        obj = observer_class(Base, lambda event, **kw: rows.append(dict(event=event, **kw)), lambda: now)()
        return obj, rows, calls

    def test_startup_only_cap_and_breach(self):
        obj, rows, calls = self.make()
        def started_safely(): return True
        self.assertTrue(obj._wait(started_safely, 104))
        self.assertEqual(calls, [(112.0, True)])
        self.assertEqual(rows[0]["event"], "WOULD_HAVE_TIMED_OUT")
        self.assertEqual(rows[0]["deadline_at"], 104)
        self.assertTrue(rows[1]["would_have_timed_out_at_4s"])

    def test_no_breach_before_four(self):
        obj, rows, _ = self.make(now=103.9)
        def started_safely(): return True
        obj._wait(started_safely, 104)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["would_have_timed_out_at_4s"])

    def test_other_waits_unchanged(self):
        obj, rows, calls = self.make()
        self.assertTrue(obj._wait(lambda: True, 9, abort=False))
        self.assertEqual(calls, [(9, False)])
        self.assertEqual(rows, [])

    def test_phase_override_only_startup(self):
        obj, _, _ = self.make()
        self.assertEqual(obj._phase("startup", 100)[1]["budget"], 12)
        self.assertIsNone(obj._phase("recovery", 100)[1]["budget"])
        self.assertEqual(obj._phase("probe", 100, budget=3.5)[1]["budget"], 3.5)

    def test_failure_not_converted_to_completion(self):
        obj, rows, _ = self.make(now=112.01, result=False)
        def started_safely(): return False
        self.assertFalse(obj._wait(started_safely, 104))
        self.assertEqual(rows[-1]["event"], "natural_startup_incomplete")


if __name__ == "__main__":
    unittest.main()
