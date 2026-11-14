"""Agent privacy, physics (reserves, damage, cascades) and config validation."""
import dataclasses
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from gridpact import physics
from gridpact.agent import PublicRound, UtilityAgent
from gridpact.config import ConfigError, list_scenarios, load_agents_file, load_scenario
from gridpact.models import Dependency
from tests.helpers import cfg, world

DT = 0.25


class AgentPrivacy(unittest.TestCase):
    def test_public_round_has_no_private_fields(self):
        names = {f.name for f in dataclasses.fields(PublicRound)}
        self.assertEqual(names, {"supply_w", "round_no", "phase", "excess_w", "offers", "floors", "bids"})

    def test_agent_methods_take_only_own_state(self):
        """No agent decision method accepts a collection of other agents' states."""
        for name, fn in inspect.getmembers(UtilityAgent, inspect.isfunction):
            params = list(inspect.signature(fn).parameters)
            self.assertNotIn("states", params, name)
            self.assertNotIn("agents", params, name)

    def test_urgency_grows_with_streak_and_empty_reserve(self):
        from gridpact.models import AgentState
        ag = UtilityAgent(cfg("h", cap=400))
        base = ag.urgency(AgentState(reserve_kwh=400))
        self.assertGreater(ag.urgency(AgentState(reserve_kwh=400, shortfall_streak=5)), base)
        self.assertGreater(ag.urgency(AgentState(reserve_kwh=40)), base)


class Physics(unittest.TestCase):
    def run_phys(self, agents, states, alloc_kw, deps=()):
        reqs = {a: agents[a].make_request(states[a], DT) for a in agents}
        alloc = {a: int(alloc_kw[a] * 1000) for a in agents}
        return physics.apply(agents, states, reqs, alloc, {}, True, deps, DT)

    def test_full_service_recharges_reserve(self):
        agents, states = world((cfg("h", desired_kw=200, recharge=40), 0.5))
        new, out = self.run_phys(agents, states, {"h": 240})
        self.assertAlmostEqual(new["h"].reserve_kwh, 200 + 40 * DT)
        self.assertEqual(out["h"].status, "OK")
        self.assertEqual(out["h"].damage, 0)

    def test_reserve_covers_shortfall_and_drains(self):
        agents, states = world((cfg("h", min_kw=100, desired_kw=200, discharge=100), 0.8))
        new, out = self.run_phys(agents, states, {"h": 120})
        self.assertAlmostEqual(out["h"].served_kw, 200)          # healthy reserve covers comfort too
        self.assertAlmostEqual(new["h"].reserve_kwh, 320 - 80 * DT)

    def test_low_reserve_only_covers_critical_load(self):
        agents, states = world((cfg("h", min_kw=100, desired_kw=200, discharge=100), 0.3))
        _, out = self.run_phys(agents, states, {"h": 50})
        self.assertAlmostEqual(out["h"].served_kw, 100)          # conserves the rest
        self.assertEqual(out["h"].status, "STRESSED")

    def test_failure_below_min(self):
        agents, states = world((cfg("h", min_kw=100, discharge=0), 0.0))
        new, out = self.run_phys(agents, states, {"h": 60})
        self.assertEqual(out["h"].status, "FAILED")
        self.assertTrue(out["h"].new_failure)
        self.assertEqual(new["h"].outage_streak, 1)
        # damage = w * (soft*(200-100) + crit*(100-60)) * dt
        self.assertAlmostEqual(out["h"].damage, (1 * 100 + 6 * 40) * DT)

    def test_damage_escalates_with_streak(self):
        from gridpact.models import AgentState
        agents, _ = world((cfg("h", discharge=0, esc=0.1), 0.0))
        s0 = {"h": AgentState(reserve_kwh=0, shortfall_streak=0)}
        s5 = {"h": AgentState(reserve_kwh=0, shortfall_streak=5)}
        _, o0 = self.run_phys(agents, s0, {"h": 60})
        _, o5 = self.run_phys(agents, s5, {"h": 60})
        self.assertAlmostEqual(o5["h"].damage / o0["h"].damage, 1.5)

    def test_cascade_telecom_fails_takes_down_emergency(self):
        agents, states = world((cfg("tel", discharge=0), 0.0), (cfg("em", min_kw=100, desired_kw=200), 0.9))
        deps = (Dependency("tel", "em", 0.6),)
        _, out = self.run_phys(agents, states, {"tel": 0, "em": 200}, deps)
        self.assertEqual(out["tel"].status, "FAILED")
        self.assertEqual(out["em"].cascade_from, ("tel",))
        self.assertAlmostEqual(out["em"].effective_kw, 200 - 0.6 * 200)
        self.assertEqual(out["em"].status, "FAILED")   # 80 < 100: cascaded failure

    def test_cascade_cycle_terminates(self):
        agents, states = world((cfg("a", discharge=0), 0.0), (cfg("b", discharge=0), 0.0))
        deps = (Dependency("a", "b", 0.9), Dependency("b", "a", 0.9))
        _, out = self.run_phys(agents, states, {"a": 0, "b": 200}, deps)
        self.assertEqual(out["b"].status, "FAILED")


class Config(unittest.TestCase):
    def test_all_scenarios_load(self):
        ids = [s["id"] for s in list_scenarios()]
        self.assertGreaterEqual(len(ids), 4)
        for sid in ids:
            sc = load_scenario(sid)
            self.assertGreaterEqual(len(sc.agents), 4)
            self.assertGreaterEqual(sc.hours, 12)

    def test_stress_scenario_has_20_plus_agents(self):
        self.assertTrue(any(len(load_scenario(s["id"]).agents) >= 20 for s in list_scenarios()))

    def test_bad_agent_rejected(self):
        bad = {"agents": [
            {"id": "x", "name": "X", "priority_rank": 1, "weight": 1, "min_kw": 300, "desired_kw": 200,
             "reserve_capacity_kwh": 1, "max_discharge_kw": 1, "recharge_kw": 1},
            {"id": "y", "name": "Y", "priority_rank": 2, "weight": 1, "min_kw": 1, "desired_kw": 2,
             "reserve_capacity_kwh": 1, "max_discharge_kw": 1, "recharge_kw": 1}]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            p.write_text(json.dumps(bad))
            with self.assertRaises(ConfigError):
                load_agents_file(p)


if __name__ == "__main__":
    unittest.main()
