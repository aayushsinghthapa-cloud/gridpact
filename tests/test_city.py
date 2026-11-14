"""Real-city mode (Mumbai): data integrity, guarantees on real numbers, and D-17.

The city page is only worth showing if (a) every number traces to the data
file, (b) the same hard guarantees hold at city scale (MW loads, billions of
watts), and (c) runs are reproducible. These tests check all three.
"""
import json
import unittest

from gridpact import city as C
from gridpact.agent import UtilityAgent, PublicRound
from gridpact.metrics import compute
from gridpact.models import AgentState
from gridpact.simulation import POLICIES, Simulation, check_state_invariants

from tests.helpers import cfg

DISASTERS = [d["id"] for d in C.load_city("mumbai")["disasters"]]


class CityData(unittest.TestCase):
    def test_wards_add_up_to_census_totals(self):
        c = C.load_city("mumbai")
        w = c["population"]["wards"]
        self.assertEqual(len(w), 24)
        self.assertEqual(sum(v[0] for v in w.values()), 12_442_373)
        self.assertEqual(sum(v[1] for v in w.values()), 2_779_943)

    def test_every_zone_ward_exists_and_zones_cover_city_once(self):
        c = C.load_city("mumbai")
        zones = [z for k, z in c["zones"].items() if k != "_note"]
        covered = [w for z in zones for w in z["wards"]]
        self.assertEqual(sorted(covered), sorted(c["population"]["wards"]))

    def test_map_has_every_ward(self):
        m = json.loads((C.CITY_DIR / "mumbai_map.json").read_text())
        self.assertEqual(sorted(w["ward"] for w in m["wards"]), sorted(C.load_city("mumbai")["population"]["wards"]))

    def test_every_cited_source_exists(self):
        c = C.load_city("mumbai")
        text = json.dumps({k: v for k, v in c.items() if k not in ("sources", "dependencies")})
        import re
        cited = set(re.findall(r'"source": "([a-z0-9_]+)"', text))
        self.assertTrue(cited)
        self.assertLessEqual(cited, set(c["sources"]))

    def test_derivation_matches_its_formula(self):
        c = C.load_city("mumbai")
        hosp = next(s for s in c["sectors"] if s["id"] == "hospital")
        kw, formula = C.sector_load_kw(hosp)
        self.assertAlmostEqual(kw, (17190 * 2753 + 31000 * 8860) / 8760, places=6)
        self.assertIn("36.8 MW", formula)

    def test_harm_unit_makes_homes_the_reference(self):
        sc = C.build_scenario("mumbai", "grid_2020")
        homes = next(a for a in sc.agents if a.id == "homes")
        self.assertAlmostEqual(homes.damage_soft, 1.0, places=9)


class CitySupply(unittest.TestCase):
    def test_recorded_loss_lands_at_recorded_time(self):
        c = C.load_city("mumbai")
        dis = C.disaster_by_id(c, "grid_2020")
        demand, _ = C.demand_curve(c, dis)
        self.assertAlmostEqual(demand(10), 2800, places=6)          # anchored to the 2020 report
        sc = C.build_scenario("mumbai", "grid_2020")
        avail = lambda h: dict(sc.profile)[h] * sc.base_supply_kw / 1000
        self.assertGreater(avail(9.75), demand(9.75))                  # no shortage before the trip
        self.assertAlmostEqual(avail(10.0), demand(10) * 1.03 - 2200, places=3)

    def test_intensity_scales_the_loss(self):
        lo = C.build_scenario("mumbai", "grid_2020", 0.5)
        hi = C.build_scenario("mumbai", "grid_2020", 1.5)
        at = lambda sc: dict(sc.profile)[11.0] * sc.base_supply_kw
        self.assertGreater(at(lo), at(hi))

    def test_intensity_out_of_range_is_rejected(self):
        with self.assertRaises(Exception):
            C.build_scenario("mumbai", "grid_2020", 3.0)


class CityGuarantees(unittest.TestCase):
    def test_zero_violations_every_disaster_policy_intensity(self):
        for d in DISASTERS:
            for k in (0.5, 1.0, 1.5):
                sc = C.build_scenario("mumbai", d, k, loss_scale=1.15, restore_scale=1.3)
                for p in POLICIES:
                    with self.subTest(d=d, k=k, p=p):
                        sim = Simulation(sc, 7, p)
                        recs = sim.run()
                        check_state_invariants(sc, sim.state)
                        m = compute(recs, sc)
                        self.assertEqual(m["capacity_violations"], 0)
                        for r in recs:
                            self.assertLessEqual(sum(a["alloc_w"] for a in r["agents"].values()), r["supply_w"])

    def test_city_run_is_deterministic(self):
        strip = lambda out: json.dumps({p: {k: v for k, v in s.items() if k != "metrics"}
                                        for p, s in out["policies"].items()}, sort_keys=True)
        a = C.run_city("mumbai", "deluge_2005", 1.0, 41, members=3)
        b = C.run_city("mumbai", "deluge_2005", 1.0, 41, members=3)
        self.assertEqual(strip(a), strip(b))
        self.assertEqual(a["forecast"], b["forecast"])
        self.assertEqual(a["forecast_violations"], 0)


class ScaleFairConcessions(unittest.TestCase):
    """DECISIONS D-17: a small agent must not be asked to give up as many watts
    as a huge one just because their bids are similar."""

    def test_concession_is_a_fraction_of_slack(self):
        small = UtilityAgent(cfg("small", min_kw=10, desired_kw=40))
        big = UtilityAgent(cfg("big", min_kw=500, desired_kw=4000))
        offers = {"small": 40_000, "big": 4_000_000}
        floors = {"small": 0, "big": 500_000}
        pub = PublicRound(supply_w=2_000_000, round_no=1, phase=1, excess_w=2_040_000,
                          offers=offers, floors=floors, bids={"small": 1.0, "big": 1.0})
        st = AgentState(reserve_kwh=400)
        c_small = small.concede_w(st, pub, offers["small"], floors["small"], 10**9)
        c_big = big.concede_w(st, pub, offers["big"], floors["big"], 10**9)
        # equal bids -> equal FRACTION of slack, not equal watts
        self.assertAlmostEqual(c_small / 40_000, c_big / 3_500_000, places=3)
        self.assertLess(c_small, 40_000)

    def test_mumbai_gridpact_matches_best_baseline(self):
        # Regression for D-17: before the fix GridPact did ~2x the harm of strict priority here.
        out = C.run_city("mumbai", "grid_2020", 1.0, 41, members=1)
        P = out["policies"]
        best = min(P[p]["harm"] for p in P if p != "gridpact")
        self.assertLess(P["gridpact"]["harm"], best * 1.02)


class FallbackWholeFloors(unittest.TestCase):
    """DECISIONS D-18: the deadlock fallback grants a survival floor only if the
    whole floor fits; a partial floor keeps nobody alive."""

    def test_unsurvivable_floor_does_not_swallow_supply(self):
        from gridpact.models import Request
        from gridpact.negotiation import safe_fallback
        reqs = {"hospital": Request("hospital", 0, 80_000), "water": Request("water", 0, 75_000),
                "homes": Request("homes", 424_000, 2_800_000)}
        bids = {"hospital": 7.5, "water": 14.6, "homes": 1.1}
        alloc = safe_fallback(280_000, reqs, bids)
        self.assertEqual(alloc["hospital"], 80_000)
        self.assertEqual(alloc["water"], 75_000)
        self.assertEqual(alloc["homes"], 125_000)       # gets what is left, not everything
        self.assertLessEqual(sum(alloc.values()), 280_000)

    def test_mumbai_near_critical_supply(self):
        out = C.run_city("mumbai", "grid_2020", 1.25, 41, members=1)
        P = out["policies"]
        best = min(P[p]["harm"] for p in P if p != "gridpact")
        self.assertLess(P["gridpact"]["harm"], best * 1.02)


if __name__ == "__main__":
    unittest.main()
