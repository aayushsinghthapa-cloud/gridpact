"""The time-step engine.

One call to Simulation.step() runs the whole protocol for one 15-minute step:

    events -> supply -> requests + bids -> policy (negotiation or baseline)
      -> VALIDATOR -> (fallback if rejected) -> physics -> invariant check
      -> ATOMIC COMMIT (one reference swap) -> record

ATOMICITY: everything before the commit line builds NEW objects. The current
state is never modified. If anything raises before the commit, `self.state`
still points at the old state: the step is rolled back as if it never ran,
and StepRolledBack is raised with the reason.

DETERMINISM: the only randomness is supply noise, drawn from
random.Random(seed, step). It is reseeded per step instead of using one shared
generator, so there is no generator state to roll back and each step's noise
is a pure function of (seed, step). Injected events are recorded with their
step, so a run with live injections can be replayed exactly.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import physics
from .agent import UtilityAgent
from .baselines import EqualSplitPolicy, ProportionalPolicy, StrictPriorityPolicy
from .config import EventSpec, Scenario
from .models import ActiveEffect, AgentState, Proposal, SimState, kw_to_w, w_to_kw
from .negotiation import GridPactPolicy, safe_fallback
from .validator import validate

POLICIES: Dict[str, Callable] = {
    "gridpact": GridPactPolicy,
    "priority": StrictPriorityPolicy,
    "proportional": ProportionalPolicy,
    "equal": EqualSplitPolicy,
}
POLICY_LABELS = {k: v.label for k, v in POLICIES.items()}


class StepRolledBack(RuntimeError):
    """Raised when a step could not be committed. The state is unchanged."""


class InvariantViolation(RuntimeError):
    pass


def interp_profile(profile: Sequence[Tuple[float, float]], hour: float) -> float:
    """Piecewise-linear interpolation of the supply profile (hour -> fraction)."""
    if hour <= profile[0][0]:
        return profile[0][1]
    for (h0, f0), (h1, f1) in zip(profile, profile[1:]):
        if h0 <= hour <= h1:
            return f0 + (f1 - f0) * (hour - h0) / (h1 - h0)
    return profile[-1][1]


def step_noise(seed: int, step: int, sigma: float) -> float:
    """Deterministic supply noise for (seed, step), clipped to +/- 3 sigma."""
    if sigma <= 0:
        return 0.0
    rng = random.Random(seed * 1_000_003 + step)
    return max(-3 * sigma, min(3 * sigma, rng.gauss(0.0, sigma)))


def check_state_invariants(scenario: Scenario, state: SimState) -> None:
    """Sanity checks on a candidate state BEFORE it is committed."""
    cfgs = {a.id: a for a in scenario.agents}
    if [a for a, _ in state.agents] != [a.id for a in scenario.agents]:
        raise InvariantViolation("agent list changed")
    for aid, st in state.agents:
        cap = cfgs[aid].reserve_capacity_kwh
        for name, v in (("reserve", st.reserve_kwh), ("credits", st.credits)):
            if not math.isfinite(v):
                raise InvariantViolation(f"{aid}: {name} is not finite")
        if not (-1e-9 <= st.reserve_kwh <= cap + 1e-9):
            raise InvariantViolation(f"{aid}: reserve {st.reserve_kwh} outside [0, {cap}]")
        if st.credits < 0 or st.status not in ("OK", "STRESSED", "FAILED"):
            raise InvariantViolation(f"{aid}: bad credits/status")
    if not math.isfinite(state.total_damage) or state.total_damage < 0:
        raise InvariantViolation("total damage must be finite and >= 0")


class Simulation:
    def __init__(self, scenario: Scenario, seed: int = 41, policy: str = "gridpact",
                 injected: Sequence[Tuple[int, EventSpec]] = ()):
        if policy not in POLICIES:
            raise ValueError(f"unknown policy '{policy}'")
        self.scenario = scenario
        self.seed = int(seed)
        self.policy_name = policy
        self.policy = POLICIES[policy]()
        self.agents: Dict[str, UtilityAgent] = {c.id: UtilityAgent(c) for c in scenario.agents}
        self.state = SimState(
            step=0,
            agents=tuple((c.id, AgentState(reserve_kwh=c.reserve_capacity_kwh * c.reserve_initial_frac))
                         for c in scenario.agents),
        )
        self.records: List[dict] = []
        self.injected: List[Tuple[int, EventSpec]] = list(injected)
        self.rollbacks: List[dict] = []

    # ------------------------------------------------------------------
    @property
    def done(self) -> bool:
        return self.state.step >= self.scenario.steps

    def inject(self, event: EventSpec) -> int:
        """Queue an event for the NEXT step to run. Returns that step index."""
        self.injected.append((self.state.step, event))
        return self.state.step

    def run(self) -> List[dict]:
        while not self.done:
            self.step()
        return self.records

    # ------------------------------------------------------------------
    def _events_for(self, t: int) -> List[EventSpec]:
        return ([e for s, e in self.scenario.scheduled if s == t]
                + [e for s, e in self.injected if s == t])

    def _supply_w(self, t: int, effects: Sequence[ActiveEffect]) -> int:
        sc = self.scenario
        frac = interp_profile(sc.profile, t * sc.dt_hours)
        kw = sc.base_supply_kw * frac * (1.0 + step_noise(self.seed, t, sc.noise_frac))
        for e in effects:
            if e.kind == "supply":
                kw *= e.factor
        return kw_to_w(max(0.0, kw))

    # ------------------------------------------------------------------
    def step(self, _fault_hook: Optional[Callable[[str], None]] = None) -> dict:
        """Run one time step. Commits fully or raises StepRolledBack (state unchanged).

        `_fault_hook` exists only so tests can inject a crash at a named stage
        and prove the rollback works.
        """
        if self.done:
            raise StepRolledBack("scenario finished")
        old = self.state
        t = old.step
        sc = self.scenario
        dt = sc.dt_hours
        hook = _fault_hook or (lambda stage: None)
        started = time.perf_counter()
        try:
            # 1. events change the world (new objects only)
            events = self._events_for(t)
            effects = [e for e in old.effects if e.until_step > t]
            states: Dict[str, AgentState] = dict(old.agents)
            for ev in events:
                if ev.type == "supply_shock":
                    effects.append(ActiveEffect("supply", ev.factor, t + ev.duration_steps, None, ev.label))
                elif ev.type == "demand_surge":
                    effects.append(ActiveEffect("demand", ev.factor, t + ev.duration_steps, ev.agent, ev.label))
                elif ev.type == "reserve_loss":
                    st = states[ev.agent]
                    states[ev.agent] = replace(st, reserve_kwh=st.reserve_kwh * (1.0 - ev.fraction))
            hour = t * dt
            for aid in states:
                prof = self.agents[aid].cfg.demand_profile
                f = interp_profile(prof, hour) if prof else 1.0
                for e in effects:
                    if e.kind == "demand" and e.agent_id == aid:
                        f *= e.factor
                states[aid] = replace(states[aid], demand_factor=f)
            hook("events")

            # 2. grid announces supply
            supply_w = self._supply_w(t, effects)

            # 3. each agent announces its request (from its own state only)
            requests = {aid: self.agents[aid].make_request(states[aid], dt) for aid in states}
            hook("requests")

            # 4. allocation policy (GridPact negotiation, or a baseline)
            proposal: Proposal = self.policy.allocate(supply_w, requests, self.agents, states)
            hook("policy")

            # 5. validator: reject -> safe fallback -> (never needed) all-zero
            verdict = validate(supply_w, requests, proposal)
            verdicts = [("proposal", verdict)]
            rejected = not verdict.ok
            if rejected:
                bids = proposal.bids or {a: 1.0 for a in requests}
                fb = safe_fallback(supply_w, requests, bids)
                proposal = Proposal(fb, proposal.rounds, proposal.contested,
                                    "validator rejected proposal: " + "; ".join(verdict.failures),
                                    list(proposal.log) + [{"round": proposal.rounds, "kind": "reject",
                                                           "text": "Validator REJECTED the deal ("
                                                                   + "; ".join(verdict.failures)
                                                                   + "). Safe fallback applied."}],
                                    {}, proposal.bids, {})
                verdict = validate(supply_w, requests, proposal)
                verdicts.append(("fallback", verdict))
                if not verdict.ok:  # all-zero is always valid: 0 <= 0 <= max, 0 <= supply
                    proposal = Proposal({a: 0 for a in requests}, proposal.rounds, proposal.contested,
                                        "zero allocation (last resort)", proposal.log, {}, proposal.bids, {})
                    verdict = validate(supply_w, requests, proposal)
                    verdicts.append(("zero", verdict))
            if not verdict.ok:
                raise InvariantViolation("no valid allocation could be produced")
            hook("validated")

            # 6. physics: reserves, damage, failures, cascades
            new_agent_states, outcomes = physics.apply(
                self.agents, states, requests, proposal.allocations,
                proposal.credit_deltas, proposal.contested, sc.dependencies, dt)
            hook("physics")

            step_damage = sum(o.damage for o in outcomes.values())
            candidate = SimState(
                step=t + 1,
                agents=tuple((aid, new_agent_states[aid]) for aid, _ in old.agents),
                effects=tuple(effects),
                total_damage=old.total_damage + step_damage,
            )
            check_state_invariants(sc, candidate)
            hook("pre-commit")
            record = self._record(t, supply_w, requests, proposal, verdicts, rejected,
                                  states, new_agent_states, outcomes, events, effects,
                                  step_damage, candidate.total_damage, started)
        except Exception as exc:  # roll back: self.state was never touched
            self.rollbacks.append({"step": t, "error": f"{type(exc).__name__}: {exc}"})
            raise StepRolledBack(f"step {t} rolled back: {type(exc).__name__}: {exc}") from exc

        # ---- ATOMIC COMMIT: the only line that changes simulation state ----
        self.state = candidate
        self.records.append(record)
        return record

    # ------------------------------------------------------------------
    def _record(self, t, supply_w, requests, proposal, verdicts, rejected, states_before,
                states_after, outcomes, events, effects, step_damage, total_damage, started) -> dict:
        agents = {}
        for aid, ag in self.agents.items():
            st, o = states_after[aid], outcomes[aid]
            agents[aid] = {
                "request_min_kw": w_to_kw(requests[aid].min_w),
                "request_max_kw": w_to_kw(requests[aid].max_w),
                "desired_kw": ag.desired_kw(states_before[aid]),
                "min_kw": ag.min_kw(states_before[aid]),
                "bid": round(proposal.bids.get(aid, ag.urgency(states_before[aid])), 4),
                "alloc_w": proposal.allocations[aid],
                "alloc_kw": w_to_kw(proposal.allocations[aid]),
                "served_kw": round(o.served_kw, 3),
                "effective_kw": round(o.effective_kw, 3),
                "discharge_kw": round(o.discharge_kw, 3),
                "reserve_frac": round(ag.reserve_frac(st), 4),
                "status": st.status,
                "credits": round(st.credits, 3),
                "credits_earned": round(o.credits_earned, 3),
                "conceded_kw": w_to_kw(proposal.concessions_w.get(aid, 0)),
                "damage": round(o.damage, 4),
                "new_failure": o.new_failure,
                "cascade_from": list(o.cascade_from),
            }
        return {
            "step": t,
            "hour": round(t * self.scenario.dt_hours, 4),
            "supply_kw": w_to_kw(supply_w),
            "supply_w": supply_w,
            "allocated_kw": w_to_kw(sum(proposal.allocations.values())),
            "contested": proposal.contested,
            "rounds": proposal.rounds,
            "fallback": proposal.fallback,
            "rejected": rejected,
            "validator": [{"stage": stage, "ok": v.ok,
                           "checks": [{"name": n, "ok": p, "detail": d} for n, p, d in v.checks]}
                          for stage, v in verdicts],
            "events": [e.label for e in events],
            "active_effects": [e.label for e in effects],
            "log": proposal.log,
            "agents": agents,
            "step_damage": round(step_damage, 4),
            "total_damage": round(total_damage, 4),
            # wall-clock timing: the ONLY field excluded from determinism checks
            "compute_ms": round((time.perf_counter() - started) * 1000.0, 4),
        }


def run_policy(scenario: Scenario, seed: int, policy: str,
               injected: Sequence[Tuple[int, EventSpec]] = ()) -> List[dict]:
    sim = Simulation(scenario, seed, policy, injected)
    return sim.run()
