"""What happens to each utility after power is allocated.

This is a PURE function: it reads the old state and returns brand-new state
objects. It never modifies anything in place, which is what makes the atomic
commit in simulation.py possible.

Per agent, per step (dt = 0.25 h):
  1. grid power feeds the load first (up to desired); any extra recharges the reserve
  2. if short, the reserve covers the gap (up to its discharge rate and the
     agent's own discharge policy, see UtilityAgent.discharge_target_kw)
  3. cascades: a FAILED utility reduces the effective service of utilities
     that depend on it (dependency graph from config), which can make them fail too
  4. damage = weight x (soft_rate x comfort_gap + critical_rate x critical_gap)
               x escalation(consecutive short steps) x dt
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Mapping, Sequence, Tuple

from .agent import CREDIT_CAP, UtilityAgent
from .models import AgentState, Dependency, Request

EPS = 1e-3              # 1 watt, in kW: ignores float noise, never real shortfalls
STRESS_RESERVE = 0.25    # reserve below 25% counts as STRESSED even if fully served
SERVED_OK_FRAC = 0.98    # served >= 98% of desired counts as fully served


@dataclass(frozen=True)
class Outcome:
    grid_kw: float
    served_kw: float           # grid + reserve discharge actually delivered to the load
    effective_kw: float        # served minus effectiveness lost to failed dependencies
    discharge_kw: float
    charge_kwh: float
    damage: float
    status: str
    new_failure: bool
    cascade_from: Tuple[str, ...]
    credits_earned: float


def apply(agents: Mapping[str, UtilityAgent], states: Mapping[str, AgentState],
          requests: Mapping[str, Request], alloc_w: Mapping[str, int],
          credit_deltas: Mapping[str, float], contested: bool,
          deps: Sequence[Dependency], dt: float
          ) -> Tuple[Dict[str, AgentState], Dict[str, Outcome]]:
    ids = list(agents)
    served: Dict[str, float] = {}
    discharge: Dict[str, float] = {}
    charge: Dict[str, float] = {}
    reserve_after: Dict[str, float] = {}

    # ---- 1 + 2: load first, then recharge or discharge ----
    for a in ids:
        ag, st = agents[a], states[a]
        cap = ag.cfg.reserve_capacity_kwh
        grid = alloc_w[a] / 1000.0
        desired = ag.desired_kw(st)
        to_load = min(grid, desired)
        surplus = grid - to_load
        ch_kwh = min(surplus, ag.cfg.recharge_kw) * dt
        ch_kwh = max(0.0, min(ch_kwh, cap - st.reserve_kwh))
        target = ag.discharge_target_kw(st)
        dis = max(0.0, min(desired - to_load, ag.discharge_available_kw(st, dt), target - to_load))
        served[a] = to_load + dis
        discharge[a] = dis
        charge[a] = ch_kwh
        reserve_after[a] = min(cap, max(0.0, st.reserve_kwh - dis * dt + ch_kwh))

    # ---- 3: cascades, iterated to a fixed point ----
    # Each pass can only ADD failures (more failures -> less effectiveness),
    # so this settles in at most len(ids) + 1 passes even with cycles.
    failed = {a: served[a] < agents[a].min_kw(states[a]) - EPS for a in ids}
    effective = dict(served)
    cascade_from: Dict[str, List[str]] = {a: [] for a in ids}
    for _ in range(len(ids) + 1):
        effective = dict(served)
        cascade_from = {a: [] for a in ids}
        for d in deps:
            if failed[d.source]:
                effective[d.target] -= d.impact * agents[d.target].desired_kw(states[d.target])
                cascade_from[d.target].append(d.source)
        new_failed = {a: effective[a] < agents[a].min_kw(states[a]) - EPS for a in ids}
        if new_failed == failed:
            break
        failed = new_failed

    # ---- 4: damage, status, streaks, credits ----
    new_states: Dict[str, AgentState] = {}
    outcomes: Dict[str, Outcome] = {}
    for a in ids:
        ag, st = agents[a], states[a]
        c = ag.cfg
        desired, minl = ag.desired_kw(st), ag.min_kw(st)
        eff = max(0.0, effective[a])
        crit_gap = max(0.0, minl - eff)
        soft_gap = max(0.0, desired - max(eff, minl))
        # Escalation uses the streak BEFORE this step: the longer it has gone
        # unserved, the more each further hour of shortfall hurts.
        mult = 1.0 + c.escalation * min(st.shortfall_streak, c.escalation_cap_steps)
        damage = c.weight * (c.damage_soft * soft_gap + c.damage_critical * crit_gap) * mult * dt

        rfrac = reserve_after[a] / c.reserve_capacity_kwh if c.reserve_capacity_kwh > 0 else 0.0
        if failed[a]:
            status = "FAILED"
        elif eff < desired * SERVED_OK_FRAC - EPS or rfrac < STRESS_RESERVE:
            status = "STRESSED"
        else:
            status = "OK"
        short = eff < desired * SERVED_OK_FRAC - EPS

        # Fairness ledger from the negotiation (empty for baselines), clamped.
        earned = credit_deltas.get(a, 0.0)
        credits = min(CREDIT_CAP, max(0.0, st.credits + earned))

        new_states[a] = replace(
            st,
            reserve_kwh=reserve_after[a],
            credits=credits,
            shortfall_streak=st.shortfall_streak + 1 if short else 0,
            outage_streak=st.outage_streak + 1 if failed[a] else 0,
            status=status,
        )
        outcomes[a] = Outcome(
            grid_kw=alloc_w[a] / 1000.0, served_kw=served[a], effective_kw=eff,
            discharge_kw=discharge[a], charge_kwh=charge[a], damage=damage, status=status,
            new_failure=failed[a] and st.status != "FAILED",
            cascade_from=tuple(cascade_from[a]), credits_earned=earned,
        )
    return new_states, outcomes
