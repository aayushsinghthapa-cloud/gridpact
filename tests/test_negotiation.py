"""The protocol behaves as documented in ALGORITHM.md."""
import unittest

from gridpact import agent as A
from gridpact.negotiation import GridPactPolicy
from gridpact.validator import validate
from tests.helpers import cfg, world

DT = 0.25


def requests_for(agents, states):
    return {a: agents[a].make_request(states[a], DT) for a in agents}


class Protocol(unittest.TestCase):
    def test_uncontested_grants_everything_with_zero_rounds(self):
        agents, states = world((cfg("h"), 1.0), (cfg("t"), 1.0))
        reqs = requests_for(agents, states)
        p = GridPactPolicy().allocate(10_000_000, reqs, agents, states)
        self.assertFalse(p.contested)
        self.assertEqual(p.rounds, 0)
        self.assertEqual(p.allocations, {a: r.max_w for a, r in reqs.items()})

    def test_contested_converges_within_supply(self):
        agents, states = world((cfg("h", weight=1.0), 0.9), (cfg("t", weight=0.5), 0.9))
        reqs = requests_for(agents, states)
        supply = 300_000
        p = GridPactPolicy().allocate(supply, reqs, agents, states)
        self.assertTrue(p.contested)
        self.assertIsNone(p.fallback)
        self.assertGreater(p.rounds, 0)
        self.assertTrue(validate(supply, reqs, p).ok)
        # converged = almost all supply used (overshoot is at most 1 W per agent)
        self.assertGreaterEqual(sum(p.allocations.values()), supply - len(agents))

    def test_less_urgent_agent_concedes_more(self):
        agents, states = world((cfg("h", weight=1.0), 0.9), (cfg("t", weight=0.3), 0.9))
        reqs = requests_for(agents, states)
        p = GridPactPolicy().allocate(300_000, reqs, agents, states)
        self.assertGreater(p.concessions_w["t"], p.concessions_w["h"])

    def test_low_reserve_agent_holds_firm_at_critical_load(self):
        agents, states = world((cfg("h"), 0.10), (cfg("t"), 0.95))
        reqs = requests_for(agents, states)
        p = GridPactPolicy().allocate(330_000, reqs, agents, states)
        self.assertTrue(any(e["kind"] == "hold" and e.get("agent") == "h" for e in p.log))
        self.assertGreaterEqual(p.allocations["h"], 100_000)   # never below its min_kw in phase 1

    def test_concessions_happen_in_increments_over_rounds(self):
        agents, states = world((cfg("h"), 0.9), (cfg("t"), 0.9))
        reqs = requests_for(agents, states)
        p = GridPactPolicy().allocate(150_000, reqs, agents, states)
        per_round = [e for e in p.log if e["kind"] == "concede" and e["agent"] == "t"]
        self.assertGreater(len(per_round), 1)
        self.assertTrue(all("reserve" in e["text"] for e in per_round))

    def test_deadlock_goes_to_deterministic_fallback(self):
        # Hard floors (min - reserve discharge) = 100 + 100 kW, supply only 120 kW.
        agents, states = world((cfg("h", discharge=0), 0.0), (cfg("t", discharge=0), 0.0))
        reqs = requests_for(agents, states)
        p = GridPactPolicy().allocate(120_000, reqs, agents, states)
        self.assertIn("deadlock", p.fallback)
        self.assertTrue(validate(120_000, reqs, p).ok)
        again = GridPactPolicy().allocate(120_000, reqs, agents, states)
        self.assertEqual(p.allocations, again.allocations)
        # the more urgent agent keeps its whole hard floor; the other's floor cannot fit,
        # so it gets no partial floor, and the leftover goes to top-ups in bid order (D-18)
        top, other = sorted(p.bids, key=lambda a: (-p.bids[a], a))   # documented tie-break: id
        self.assertGreaterEqual(p.allocations[top], reqs[top].min_w)
        self.assertEqual(p.allocations[top] + p.allocations[other], 120_000)
        self.assertLessEqual(p.allocations[top], reqs[top].max_w)

    def test_round_cap_triggers_fallback_and_stays_safe(self):
        agents, states = world((cfg("h"), 0.9), (cfg("t"), 0.9))
        reqs = requests_for(agents, states)
        p = GridPactPolicy(max_rounds=1).allocate(150_000, reqs, agents, states)
        self.assertEqual(p.fallback, "round cap reached")
        self.assertTrue(validate(150_000, reqs, p).ok)

    def test_greedy_agent_never_concedes_and_bid_is_capped(self):
        agents, states = world((cfg("liar", strategy="greedy", weight=100), 0.9), (cfg("t"), 0.9))
        reqs = requests_for(agents, states)
        p = GridPactPolicy().allocate(300_000, reqs, agents, states)
        self.assertEqual(p.concessions_w.get("liar", 0), 0)
        self.assertLessEqual(p.bids["liar"], A.URGENCY_CAP)
        self.assertTrue(validate(300_000, reqs, p).ok)


class Credits(unittest.TestCase):
    def test_ledger_rewards_above_average_sacrifice(self):
        """Credits move by CREDIT_RATE x (my concession share - average share)."""
        agents, states = world((cfg("h", weight=1.0), 0.9), (cfg("t", weight=0.3), 0.9))
        reqs = requests_for(agents, states)
        p = GridPactPolicy().allocate(300_000, reqs, agents, states)
        shares = {a: p.concessions_w.get(a, 0) / 1000 / agents[a].cfg.desired_kw for a in agents}
        mean = sum(shares.values()) / 2
        for a in agents:
            self.assertAlmostEqual(p.credit_deltas[a], A.CREDIT_RATE * (shares[a] - mean))
        self.assertGreater(p.credit_deltas["t"], 0)        # less urgent agent gave more -> earns
        self.assertLess(p.credit_deltas["h"], 0)           # gave less than average -> spends
        self.assertAlmostEqual(sum(p.credit_deltas.values()), 0.0)
        self.assertTrue(any(e["kind"] == "credit" and "fairness credits" in e["text"] for e in p.log))

    def test_equal_sacrifice_earns_nothing(self):
        agents, states = world((cfg("a"), 0.9), (cfg("b"), 0.9))
        reqs = requests_for(agents, states)
        p = GridPactPolicy().allocate(300_000, reqs, agents, states)
        self.assertTrue(all(abs(d) < 0.05 for d in p.credit_deltas.values()))

    def test_credits_raise_urgency(self):
        agents, _ = world((cfg("t"), 0.5))
        from gridpact.models import AgentState
        poor = AgentState(reserve_kwh=200, credits=0)
        rich = AgentState(reserve_kwh=200, credits=10)
        self.assertAlmostEqual(agents["t"].urgency(rich) / agents["t"].urgency(poor), 1 + 10 * A.BETA_CREDITS)

    def test_starvation_prevention_over_time(self):
        """An agent that keeps conceding gains urgency until it stops being the one who gives way."""
        from gridpact.models import AgentState
        a = world((cfg("t", weight=0.6), 0.9))[0]["t"]
        st0 = AgentState(reserve_kwh=360)
        later = AgentState(reserve_kwh=360, credits=20, shortfall_streak=8)
        self.assertGreater(a.urgency(later), 2.5 * a.urgency(st0))


if __name__ == "__main__":
    unittest.main()
