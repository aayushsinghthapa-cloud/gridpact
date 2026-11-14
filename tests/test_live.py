"""The live side-by-side standings equal a full replay of the same event log."""
import unittest

from gridpact.config import load_scenario
from gridpact.live import LiveSession
from gridpact.metrics import compare


class LiveStandings(unittest.TestCase):
    def test_lockstep_totals_equal_replay(self):
        sc = load_scenario("cyclone_landfall")
        live = LiveSession(sc, 41)
        ev = {e.id: e for e in sc.injectable}
        while not live.done:
            if live.sim.state.step == 26:
                live.inject(ev["substation2"])
            if live.sim.state.step == 40:
                live.inject(ev["tower"])
            live.step()
        st = live.standings()
        replay = compare(sc, 41, list(live.sim.injected), with_series=False)
        for p, m in replay["policies"].items():
            self.assertAlmostEqual(st[p]["damage"], m["total_damage"], places=1, msg=p)
            self.assertEqual(st[p]["failures"], m["failure_events"], p)
            self.assertEqual(st[p]["step"], sc.steps)

    def test_shadows_never_change_the_gridpact_run(self):
        from gridpact.simulation import Simulation
        sc = load_scenario("heatwave_brownout")
        live = LiveSession(sc, 9)
        while not live.done:
            live.step()
        alone = Simulation(sc, 9, "gridpact").run()
        strip = lambda rs: [{k: v for k, v in r.items() if k != "compute_ms"} for r in rs]
        self.assertEqual(strip(live.sim.records), strip(alone))


if __name__ == "__main__":
    unittest.main()
