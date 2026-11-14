"""HARD GUARANTEE 3: fully deterministic given a seed (and the injected-event log)."""
import json
import unittest

from gridpact.config import list_scenarios, load_scenario
from gridpact.simulation import POLICIES, Simulation


def fingerprint(records):
    """Everything except wall-clock timing, serialized canonically."""
    clean = [{k: v for k, v in r.items() if k != "compute_ms"} for r in records]
    return json.dumps(clean, sort_keys=True)


class Determinism(unittest.TestCase):
    def test_same_seed_same_result(self):
        for s in list_scenarios():
            sc = load_scenario(s["id"])
            for policy in POLICIES:
                a = Simulation(sc, 123, policy).run()
                b = Simulation(load_scenario(s["id"]), 123, policy).run()
                self.assertEqual(fingerprint(a), fingerprint(b), f"{s['id']}/{policy}")

    def test_different_seed_different_supply(self):
        sc = load_scenario("cyclone_landfall")
        a = [r["supply_w"] for r in Simulation(sc, 1, "gridpact").run()]
        b = [r["supply_w"] for r in Simulation(sc, 2, "gridpact").run()]
        self.assertNotEqual(a, b)

    def test_live_injection_replays_exactly(self):
        sc = load_scenario("cyclone_landfall")
        live = Simulation(sc, 41, "gridpact")
        while not live.done:
            if live.state.step in (10, 31, 32, 60):
                live.inject(sc.injectable[live.state.step % len(sc.injectable)])
            live.step()
        replay = Simulation(sc, 41, "gridpact", injected=live.injected).run()
        self.assertEqual(fingerprint(live.records), fingerprint(replay))

    def test_step_by_step_equals_full_run(self):
        sc = load_scenario("heatwave_brownout")
        full = Simulation(sc, 9, "gridpact").run()
        sim = Simulation(sc, 9, "gridpact")
        stepped = [sim.step() for _ in range(sc.steps)]
        self.assertEqual(fingerprint(full), fingerprint(stepped))


if __name__ == "__main__":
    unittest.main()
