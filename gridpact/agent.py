"""One autonomous agent per utility.

An agent is a *policy*: config plus decision rules. Its private state
(AgentState) is passed in by the engine, and every method below receives ONLY
the agent's own state plus public information. There is no code path that
hands an agent another agent's AgentState. That is what "private state"
means in this codebase, and tests/test_agents.py checks it.

Every constant below is documented in ALGORITHM.md and argued in DECISIONS.md.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict

from .models import AgentConfig, AgentState, Request, kw_to_w, kw_to_w_ceil


# ---- tunable protocol constants (all agents use the same published rules) ----
HOLD_FIRM_RESERVE = 0.30   # below 30% reserve, an agent will not spend reserves in phase 1
CONSERVE_RESERVE = 0.50    # below 50% reserve, backup only covers CRITICAL load, not comfort
GAMMA_RESERVE = 2.0        # how strongly an empty reserve raises urgency (x1 .. x3)
BETA_CREDITS = 0.10        # each fairness credit adds +10% to the urgency bid
CREDIT_RATE = 10.0         # credits per 100 percentage points conceded ABOVE the step's average
CREDIT_CAP = 20.0          # credits cannot pile up forever
INCREMENT_FRAC = 0.25      # concede at most 25% of your phase slack per round
MIN_INCREMENT_W = 1_000    # ...but always at least 1 kW, so rounds make progress
URGENCY_CAP = 50.0         # bids are clamped: limits what a lying agent can gain
GREEDY_INFLATION = 5.0     # how much a "greedy" (lying) agent inflates its bid


@dataclass(frozen=True)
class PublicRound:
    """Everything an agent may see during a negotiation round.

    All of it is public: supply, and what every agent has openly announced
    (current offer, floor, urgency bid). No reserves, no damage, no credits.
    """
    supply_w: int
    round_no: int
    phase: int
    excess_w: int                    # sum(offers) - supply; > 0 means still contested
    offers: Dict[str, int]
    floors: Dict[str, int]
    bids: Dict[str, float]


class UtilityAgent:
    def __init__(self, config: AgentConfig):
        self.cfg = config

    @property
    def id(self) -> str:
        return self.cfg.id

    # ---------------- reading own state ----------------
    def reserve_frac(self, st: AgentState) -> float:
        cap = self.cfg.reserve_capacity_kwh
        return st.reserve_kwh / cap if cap > 0 else 0.0

    def desired_kw(self, st: AgentState) -> float:
        return self.cfg.desired_kw * st.demand_factor

    def min_kw(self, st: AgentState) -> float:
        return self.cfg.min_kw * st.demand_factor

    def discharge_available_kw(self, st: AgentState, dt: float) -> float:
        """Max backup power this step: limited by the inverter/pump rate and by what is left."""
        return max(0.0, min(self.cfg.max_discharge_kw, st.reserve_kwh / dt))

    def discharge_target_kw(self, st: AgentState) -> float:
        """How much load the agent is willing to run from its reserve.

        Healthy reserve (>= 50%): cover everything up to desired load.
        Low reserve: only cover critical load, saving the rest for later hours.
        WHY: in a 24 h disaster, burning the last diesel on comfort load at
        hour 3 means failing at hour 10.
        """
        if self.reserve_frac(st) >= CONSERVE_RESERVE:
            return self.desired_kw(st)
        return self.min_kw(st)

    def recharge_want_kw(self, st: AgentState, dt: float) -> float:
        room_kwh = self.cfg.reserve_capacity_kwh - st.reserve_kwh
        if room_kwh <= 1e-9:
            return 0.0
        return min(self.cfg.recharge_kw, room_kwh / dt)

    # ---------------- step start: request + bid ----------------
    def make_request(self, st: AgentState, dt: float) -> Request:
        """Announce (min, max) grid need for this step.

        max = desired load + reserve top-up.
        min = critical load minus what my own backup can cover = the least
              grid power that keeps me from FAILING. It is 0 if the backup
              alone could carry the critical load.
        """
        max_kw = self.desired_kw(st) + self.recharge_want_kw(st, dt)
        hard_floor_kw = max(0.0, self.min_kw(st) - self.discharge_available_kw(st, dt))
        max_w = kw_to_w(max_kw)
        return Request(self.id, min(kw_to_w_ceil(hard_floor_kw), max_w), max_w)

    def urgency(self, st: AgentState) -> float:
        """The urgency bid, computed only from my own state.

            u = weight x harm_rate x escalation x reserve_scarcity x (1 + beta x credits)

        weight            how critical the utility is (config)
        harm_rate         my own damage per kW of comfort shortfall (damage_soft).
                          The bid is meant to say "this is how much harm each kW
                          cut from me does". With every rate at 1.0 (all core
                          scenarios) this term is invisible; on a real city it
                          matters: a kW cut from a hospital does far more harm
                          than a kW cut from the residential bulk. (DECISIONS D-17)
        escalation        1 + e x (consecutive steps I have been short)
        reserve_scarcity  1 + gamma x (1 - reserve fraction): empty backup -> x3
        credits           fairness credits earned by conceding earlier
        """
        c = self.cfg
        streak = min(st.shortfall_streak, c.escalation_cap_steps)
        u = (c.weight
             * c.damage_soft
             * (1.0 + c.escalation * streak)
             * (1.0 + GAMMA_RESERVE * (1.0 - self.reserve_frac(st)))
             * (1.0 + BETA_CREDITS * st.credits))
        if c.strategy == "greedy":
            u *= GREEDY_INFLATION          # the lie: claims to be far more urgent
        return min(max(u, 0.01), URGENCY_CAP)

    # ---------------- during negotiation ----------------
    def floor_w(self, st: AgentState, phase: int, req: Request) -> int:
        """The lowest offer I will accept in this phase.

        Phase 1: if my reserve is healthy I will go down to my hard floor
                 (I'll cover critical load from backup). If my reserve is
                 below 30% I HOLD FIRM at my full critical load.
        Phase 2: (only reached if phase 1 deadlocks) everyone digs into
                 their reserves, down to the hard floor.
        A greedy agent never concedes anything.
        """
        if self.cfg.strategy == "greedy":
            return req.max_w
        if phase == 1 and self.reserve_frac(st) < HOLD_FIRM_RESERVE:
            return min(kw_to_w(self.min_kw(st)), req.max_w)
        return req.min_w

    def concede_w(self, st: AgentState, pub: PublicRound, my_offer: int,
                  my_floor: int, phase_slack_w: int) -> int:
        """How many watts I give up this round.

        Rule: the remaining excess is split among agents that can still
        concede, in proportion to slack / urgency: what I can still give,
        discounted by how urgent I am. So a less urgent agent gives a larger
        FRACTION of its slack, and a small agent is never asked to give more
        than its size warrants. (Splitting by 1/urgency alone, in absolute
        watts, made a 37 MW hospital concede everything next to a 3,000 MW
        residential load: DECISIONS D-17.) Each agent computes ITS OWN share
        from public numbers (offers, floors, bids), so no central party
        assigns concessions. The amount is capped by an increment (25% of my
        slack this phase) so concessions happen gradually over rounds, and by
        my floor.
        """
        slack = my_offer - my_floor
        if slack <= 0 or pub.excess_w <= 0:
            return 0
        willing = [a for a in pub.offers if pub.offers[a] > pub.floors[a]]
        total = sum((pub.offers[a] - pub.floors[a]) / pub.bids[a] for a in willing)
        my_share = (slack / pub.bids[self.id]) / total
        target = my_share * pub.excess_w
        increment = max(MIN_INCREMENT_W, INCREMENT_FRAC * phase_slack_w)
        # ceil -> always progress by >= 1 W; overshoot is at most 1 W per agent,
        # which can only leave supply unused (the safe direction), never exceed it.
        return int(min(math.ceil(target), math.ceil(increment), slack))

    def concession_share(self, conceded_w: int) -> float:
        """What fraction of my normal (desired) load I gave up this step."""
        return max(0, conceded_w) / 1000.0 / self.cfg.desired_kw
