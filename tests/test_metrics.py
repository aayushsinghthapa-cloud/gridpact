"""Metrics are computed from raw records and re-check capacity independently."""
import copy
import unittest

from gridpact.config import load_scenario
from gridpact.metrics import compare, compute, jain_index
from gridpact.simulation import Simulation


class Metrics(unittest.TestCase):
    def test_jain_known_values(self):
        self.assertAlmostEqual(jain_index([1, 1, 1, 1]), 1.0)
        self.assertAlmostEqual(jain_index([1, 0, 0, 0]), 0.25)
        self.assertAlmostEqual(jain_index([1, 0.5]), (1.5 ** 2) / (2 * 1.25))

    def test_violation_recheck_catches_a_tampered_record(self):
        """If a record ever claimed more power than supply, metrics would count it
        even though the validator said ok. Proves the metric does not trust the validator."""
        sc = load_scenario("cyclone_landfall")
        recs = Simulation(sc, 41, "gridpact").run()
        self.assertEqual(compute(recs, sc)["capacity_violations"], 0)
        bad = copy.deepcopy(recs)
        bad[40]["agents"]["telecom"]["alloc_w"] = bad[40]["supply_w"] + 1
        self.assertEqual(compute(bad, sc)["capacity_violations"], 1)

    def test_failure_and_outage_counting(self):
        sc = load_scenario("cyclone_landfall")
        recs = Simulation(sc, 41, "priority").run()
        m = compute(recs, sc)
        manual = sum(a["new_failure"] for r in recs for a in r["agents"].values())
        self.assertEqual(m["failure_events"], manual)
        self.assertGreater(m["longest_outage_h"], 0)

    def test_compare_runs_every_policy_on_identical_supply(self):
        sc = load_scenario("heatwave_brownout")
        res = compare(sc, 5, with_series=True)
        self.assertEqual(set(res["policies"]), {"gridpact", "priority", "proportional", "equal"})
        series = {p: len(m["damage_series"]) for p, m in res["policies"].items()}
        self.assertEqual(set(series.values()), {sc.steps})


if __name__ == "__main__":
    unittest.main()


class NarrationIsOptional(unittest.TestCase):
    def test_off_by_default_and_core_never_imports_the_llm_sdk(self):
        import os
        import subprocess
        import sys
        env = {k: v for k, v in os.environ.items() if k != "GRIDPACT_LLM"}
        code = ("import sys; from gridpact.config import load_scenario; from gridpact.metrics import compare;"
                "import gridpact.server; compare(load_scenario('cyclone_landfall'), 1, with_series=False);"
                "from gridpact import narration; assert not narration.enabled();"
                "assert 'anthropic' not in sys.modules, 'core imported the LLM SDK'")
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_enabled_narration_degrades_instead_of_raising(self):
        """GRIDPACT_LLM=1 with no SDK / no credentials must return a message, never raise."""
        import os
        from unittest import mock
        from gridpact import narration
        from gridpact.simulation import Simulation
        sc = load_scenario("cyclone_landfall")
        sim = Simulation(sc, 41)
        rec = sim.step()
        with mock.patch.dict(os.environ, {"GRIDPACT_LLM": "1"}):
            out = narration.narrate(sc, rec)
        self.assertIn("text", out)
        self.assertFalse(out["enabled"])
