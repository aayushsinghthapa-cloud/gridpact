"""HARD GUARANTEE 2: no inconsistent state. A step fully commits or fully rolls back.

We crash the step deliberately at every internal stage and check that the
simulation state is exactly what it was before (same object, same records),
and that the simulation then continues exactly as if the crash never happened.
"""
import unittest

from gridpact.config import load_scenario
from gridpact.simulation import Simulation, StepRolledBack

STAGES = ["events", "requests", "policy", "validated", "physics", "pre-commit"]


def strip_timing(records):
    return [{k: v for k, v in r.items() if k != "compute_ms"} for r in records]


class Boom(Exception):
    pass


class AtomicCommit(unittest.TestCase):
    def setUp(self):
        self.sc = load_scenario("cyclone_landfall")

    def _crash_at(self, stage):
        def hook(s):
            if s == stage:
                raise Boom(f"injected crash at {stage}")
        return hook

    def test_crash_at_every_stage_rolls_back(self):
        for policy in ("gridpact", "priority"):
            for stage in STAGES:
                sim = Simulation(self.sc, 41, policy)
                for _ in range(30):          # get into the contested part of the storm
                    sim.step()
                before_state = sim.state
                before_records = list(sim.records)
                with self.assertRaises(StepRolledBack):
                    sim.step(_fault_hook=self._crash_at(stage))
                self.assertIs(sim.state, before_state, f"{policy}/{stage}: state changed")
                self.assertEqual(sim.records, before_records, f"{policy}/{stage}: record leaked")
                self.assertEqual(len(sim.rollbacks), 1)
                self.assertIn(stage, sim.rollbacks[0]["error"])

    def test_rollback_leaves_no_side_effects(self):
        """Crash, then continue: the run must equal a run that never crashed."""
        clean = Simulation(self.sc, 41, "gridpact").run()
        sim = Simulation(self.sc, 41, "gridpact")
        crashed = 0
        while not sim.done:
            if sim.state.step % 7 == 3 and crashed < 10:
                with self.assertRaises(StepRolledBack):
                    sim.step(_fault_hook=self._crash_at(STAGES[crashed % len(STAGES)]))
                crashed += 1
            sim.step()
        self.assertGreater(crashed, 5)
        self.assertEqual(strip_timing(sim.records), strip_timing(clean))

    def test_state_objects_are_immutable(self):
        sim = Simulation(self.sc, 41, "gridpact")
        sim.step()
        with self.assertRaises(Exception):
            sim.state.step = 99                      # frozen dataclass
        with self.assertRaises(Exception):
            sim.state.agents[0][1].reserve_kwh = 0   # frozen dataclass

    def test_finished_scenario_refuses_more_steps(self):
        sim = Simulation(self.sc, 41, "equal")
        sim.run()
        final = sim.state
        with self.assertRaises(StepRolledBack):
            sim.step()
        self.assertIs(sim.state, final)


if __name__ == "__main__":
    unittest.main()
