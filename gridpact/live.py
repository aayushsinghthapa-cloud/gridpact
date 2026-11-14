"""A live dashboard run: GridPact plus the three baselines stepping in lockstep.

WHY: judges should see the comparison *during* the demo, not only on the results
tab. Every baseline gets the same scenario, the same seed and every event the
presenter injects, at the same step. Because each run is deterministic, the
running totals here equal what the results tab computes by replaying the event
log (tests/test_live.py checks that).

The baselines are shadows: they never influence the GridPact run.
"""
from __future__ import annotations

from typing import Dict

from .config import EventSpec, Scenario
from .simulation import POLICIES, POLICY_LABELS, Simulation


class LiveSession:
    def __init__(self, scenario: Scenario, seed: int):
        self.scenario = scenario
        self.seed = seed
        self.sim = Simulation(scenario, seed, "gridpact")
        self.shadows = {p: Simulation(scenario, seed, p) for p in POLICIES if p != "gridpact"}
        self.failures = {p: 0 for p in POLICIES}
        self.shadows_ok = True

    @property
    def done(self) -> bool:
        return self.sim.done

    def step(self) -> dict:
        rec = self.sim.step()                      # raises StepRolledBack -> nothing else moves
        self.failures["gridpact"] += sum(a["new_failure"] for a in rec["agents"].values())
        if self.shadows_ok:
            try:
                for p, s in self.shadows.items():
                    r = s.step()
                    self.failures[p] += sum(a["new_failure"] for a in r["agents"].values())
            except Exception:  # noqa: BLE001 - a shadow problem must never stop the live run
                self.shadows_ok = False
        return rec

    def inject(self, event: EventSpec) -> int:
        step = self.sim.inject(event)
        for s in self.shadows.values():
            s.inject(event)                        # same event, same step: lockstep
        return step

    def standings(self) -> Dict[str, dict]:
        runs = {"gridpact": self.sim, **self.shadows}
        out = {}
        for p, s in runs.items():
            if p != "gridpact" and not self.shadows_ok:
                continue
            out[p] = {"label": POLICY_LABELS[p], "damage": round(s.state.total_damage, 2),
                      "failures": self.failures[p], "step": s.state.step}
        return out
