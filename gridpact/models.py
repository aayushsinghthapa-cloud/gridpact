"""Plain data records shared by every module.

WHY frozen dataclasses: a frozen record cannot be modified after it is built.
That is how we get "no inconsistent state": a time step never edits the
current state in place. It builds a brand-new state object and the engine
swaps it in with one assignment (see simulation.py). If anything fails before
that assignment, the old state is still untouched, so the step is rolled back.

Units used everywhere:
  * power on the wire (supply, requests, allocations) -> integer WATTS.
    Integers make the capacity check `sum(allocations) <= supply` exact, with
    no floating-point rounding that could hide a 0.0000001 kW violation.
  * agent config, reserves and physics -> kW / kWh floats (human-readable).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

W_PER_KW = 1000


def kw_to_w(kw: float) -> int:
    """Round DOWN to whole watts.

    Rounding down is the safe direction: an agent never asks for, or is given,
    more than the real number.
    """
    return int(kw * W_PER_KW) if kw > 0 else 0


def kw_to_w_ceil(kw: float) -> int:
    """Round UP to whole watts. Used for an agent's hard floor: a survival
    need must never be under-stated by rounding, or the agent could end up
    a fraction of a watt short of its critical load."""
    return int(math.ceil(kw * W_PER_KW - 1e-9)) if kw > 0 else 0


def w_to_kw(w: int) -> float:
    return w / W_PER_KW


# --------------------------------------------------------------------------
# Configuration (loaded from data/*.json, never changes during a run)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentConfig:
    id: str
    name: str
    kind: str                     # hospital / water / emergency / telecom / ...
    priority_rank: int            # used ONLY by the strict-priority baseline (1 = first)
    weight: float                 # criticality: scales damage and the urgency bid
    min_kw: float                 # below this the utility FAILS
    desired_kw: float             # full, comfortable operation
    reserve_capacity_kwh: float   # backup: diesel / battery / water tank (kWh-equivalent)
    reserve_initial_frac: float   # 0..1 at the start of the scenario
    max_discharge_kw: float       # how fast the backup can supply power
    recharge_kw: float            # how fast the backup can refill when served
    damage_soft: float            # damage per kWh of comfort shortfall (desired -> min)
    damage_critical: float        # damage per kWh of critical shortfall (below min)
    escalation: float             # damage grows +escalation per consecutive unserved step
    escalation_cap_steps: int     # escalation stops growing after this many steps
    strategy: str = "honest"      # "honest" or "greedy" (a deliberately lying agent, for tests)
    reserve_label: str = "Backup reserve"   # display only, e.g. "Diesel backup"


@dataclass(frozen=True)
class Dependency:
    """If `source` FAILS, `target` loses `impact` x its desired load in effectiveness.

    Example: Telecom fails -> Emergency Services can't dispatch well, so its
    effective service drops by 35% of its desired load. That loss can push the
    target below its own minimum, which is how failures cascade.
    """
    source: str
    target: str
    impact: float
    label: str = ""


# --------------------------------------------------------------------------
# Private per-agent state (only the owning agent reads it)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentState:
    reserve_kwh: float
    credits: float = 0.0           # fairness credits earned by conceding
    shortfall_streak: int = 0      # consecutive steps served below desired
    outage_streak: int = 0         # consecutive steps FAILED
    status: str = "OK"             # OK / STRESSED / FAILED (public after each step)
    demand_factor: float = 1.0     # >1 during a demand surge event


# --------------------------------------------------------------------------
# What travels between agents and the protocol each step
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Request:
    """Announced publicly by each agent at the start of a step.

    min_w: the least grid power the agent needs this step to avoid failing,
           AFTER counting what its own reserves can cover.
    max_w: desired load plus a top-up for its reserve, if the reserve is not full.
    """
    agent_id: str
    min_w: int
    max_w: int


@dataclass(frozen=True)
class Proposal:
    """Output of an allocation policy, BEFORE validation."""
    allocations: Dict[str, int]          # agent_id -> watts
    rounds: int                          # negotiation rounds used (0 = uncontested)
    contested: bool                      # demand exceeded supply
    fallback: Optional[str]              # None, or why the fallback ran
    log: List[dict] = field(default_factory=list)
    concessions_w: Dict[str, int] = field(default_factory=dict)  # total conceded per agent
    bids: Dict[str, float] = field(default_factory=dict)
    credit_deltas: Dict[str, float] = field(default_factory=dict)  # fairness ledger changes


@dataclass(frozen=True)
class Verdict:
    ok: bool
    checks: Tuple[Tuple[str, bool, str], ...]   # (check name, passed, detail)

    @property
    def failures(self) -> List[str]:
        return [f"{name}: {detail}" for name, passed, detail in self.checks if not passed]


@dataclass(frozen=True)
class ActiveEffect:
    """A temporary world change from an event (supply shock or demand surge)."""
    kind: str                 # "supply" or "demand"
    factor: float
    until_step: int           # exclusive
    agent_id: Optional[str] = None
    label: str = ""


@dataclass(frozen=True)
class SimState:
    """The ENTIRE committed state of a simulation. Replaced, never mutated."""
    step: int
    agents: Tuple[Tuple[str, AgentState], ...]     # ordered (id, state) pairs
    effects: Tuple[ActiveEffect, ...] = ()
    total_damage: float = 0.0

    def agent(self, agent_id: str) -> AgentState:
        for aid, st in self.agents:
            if aid == agent_id:
                return st
        raise KeyError(agent_id)

    def agent_map(self) -> Dict[str, AgentState]:
        return dict(self.agents)
