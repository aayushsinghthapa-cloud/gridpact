"""HARD GUARANTEE 1: zero capacity violations, at every step, in every scenario.

We do not trust the validator's own report here. Each test re-derives the
check from the raw numbers in the step record, for every policy, many seeds,
and random live event injections.
"""
import random
import unittest

from gridpact.config import list_scenarios, load_scenario
from gridpact.models import Proposal, Request
from gridpact.negotiation import safe_fallback
from gridpact.simulation import POLICIES, Simulation
from gridpact.validator import validate

SEEDS = (1, 7, 41, 2026)


def assert_record_safe(tc: unittest.TestCase, rec: dict, ctx: str):
    allocs = [a["alloc_w"] for a in rec["agents"].values()]
    tc.assertTrue(all(type(w) is int for w in allocs), ctx)
    tc.assertLessEqual(sum(allocs), rec["supply_w"], f"{ctx}: sum > supply")
    for aid, a in rec["agents"].items():
        max_w = int(round(a["request_max_kw"] * 1000))
        tc.assertGreaterEqual(a["alloc_w"], 0, f"{ctx}: {aid} negative")
        tc.assertLessEqual(a["alloc_w"], max_w, f"{ctx}: {aid} above its request")
        tc.assertGreaterEqual(a["reserve_frac"], 0.0)
        tc.assertLessEqual(a["reserve_frac"], 1.0 + 1e-9)
    tc.assertTrue(rec["validator"][-1]["ok"], f"{ctx}: final validator verdict not ok")


class CapacityInvariant(unittest.TestCase):
    def test_every_scenario_every_policy_every_seed(self):
        for s in list_scenarios():
            sc = load_scenario(s["id"])
            for policy in POLICIES:
                for seed in SEEDS:
                    sim = Simulation(sc, seed, policy)
                    recs = sim.run()
                    self.assertEqual(len(recs), sc.steps)
                    self.assertEqual(sim.rollbacks, [])
                    for r in recs:
                        assert_record_safe(self, r, f"{s['id']}/{policy}/seed{seed}/step{r['step']}")

    def test_random_live_injections(self):
        """Inject random events at random steps, like a judge mashing buttons."""
        for s in list_scenarios():
            sc = load_scenario(s["id"])
            if not sc.injectable:
                continue
            for policy in POLICIES:
                rng = random.Random(f"{s['id']}-{policy}")
                sim = Simulation(sc, 99, policy)
                while not sim.done:
                    if rng.random() < 0.15:
                        sim.inject(rng.choice(sc.injectable))
                    r = sim.step()
                    assert_record_safe(self, r, f"{s['id']}/{policy}/inject/step{r['step']}")

    def test_extreme_supply_collapse(self):
        """Supply shocks stacked to ~0: everything still validates (agents fail, grid does not break)."""
        from gridpact.config import EventSpec
        sc = load_scenario("cyclone_landfall")
        for policy in POLICIES:
            sim = Simulation(sc, 3, policy)
            for _ in range(4):
                sim.inject(EventSpec("supply_shock", "near blackout", factor=0.05, duration_steps=10))
            while not sim.done:
                assert_record_safe(self, sim.step(), f"collapse/{policy}")


class ValidatorRejectsBadDeals(unittest.TestCase):
    def setUp(self):
        self.req = {"a": Request("a", 100, 500), "b": Request("b", 0, 300)}

    def v(self, alloc, supply=700):
        return validate(supply, self.req, Proposal(alloc, 1, True, None))

    def test_accepts_valid(self):
        self.assertTrue(self.v({"a": 400, "b": 300}).ok)

    def test_over_supply(self):
        self.assertFalse(self.v({"a": 401, "b": 300}).ok)

    def test_one_watt_over_supply(self):
        self.assertFalse(self.v({"a": 500, "b": 201}).ok)

    def test_above_request(self):
        self.assertFalse(self.v({"a": 501, "b": 0}, supply=10_000).ok)

    def test_negative(self):
        self.assertFalse(self.v({"a": -1, "b": 0}).ok)

    def test_missing_or_extra_agent(self):
        self.assertFalse(self.v({"a": 1}).ok)
        self.assertFalse(self.v({"a": 1, "b": 1, "c": 1}).ok)

    def test_float_and_nan(self):
        self.assertFalse(self.v({"a": 1.5, "b": 0}).ok)
        self.assertFalse(self.v({"a": float("nan"), "b": 0}).ok)
        self.assertFalse(self.v({"a": True, "b": 0}).ok)

    def test_every_check_is_logged(self):
        verdict = self.v({"a": 400, "b": 300})
        self.assertEqual([c[0][:2] for c in verdict.checks], ["C1", "C2", "C3", "C4"])


class BrokenPolicyIsContained(unittest.TestCase):
    """A buggy/malicious allocation policy must not break the guarantee."""

    class Greedy:
        name, label = "broken", "broken"

        def allocate(self, supply_w, requests, agents, states):
            # gives everyone double their request: violates C3 and C4
            return Proposal({a: r.max_w * 2 for a, r in requests.items()}, 0, True, None)

    def test_rejected_then_fallback_committed(self):
        sc = load_scenario("cyclone_landfall")
        sim = Simulation(sc, 41, "gridpact")
        sim.policy = self.Greedy()
        recs = sim.run()
        self.assertTrue(all(r["rejected"] for r in recs))
        for r in recs:
            self.assertFalse(r["validator"][0]["ok"])
            assert_record_safe(self, r, f"broken/step{r['step']}")


class FallbackIsAlwaysSafe(unittest.TestCase):
    def test_fuzz(self):
        rng = random.Random(5)
        for _ in range(3000):
            n = rng.randint(1, 30)
            reqs = {}
            for i in range(n):
                mx = rng.randint(0, 600_000)
                reqs[f"a{i}"] = Request(f"a{i}", rng.randint(0, mx), mx)
            bids = {a: rng.uniform(0.01, 50) for a in reqs}
            supply = rng.randint(0, 3_000_000)
            ceilings = {a: rng.randint(0, reqs[a].max_w) for a in reqs} if rng.random() < .5 else None
            alloc = safe_fallback(supply, reqs, bids, ceilings)
            self.assertTrue(validate(supply, reqs, Proposal(alloc, 0, True, "fb")).ok)


if __name__ == "__main__":
    unittest.main()
