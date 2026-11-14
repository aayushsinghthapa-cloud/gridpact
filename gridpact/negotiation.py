"""The GridPact negotiation protocol (one time step).

    1. every agent announces a Request (min, max) and an urgency bid
    2. if total max <= supply  -> everyone gets their max, done (0 rounds)
    3. else PHASE 1 rounds: agents concede comfort load / reserve-backed load;
       agents with weak reserves HOLD FIRM at their critical load
    4. if phase 1 deadlocks (nobody can concede, still over supply):
       PHASE 2 rounds: everyone digs into reserves down to their hard floor
    5. if still over supply, or the round cap is hit -> deterministic FALLBACK

The protocol only moves *public* numbers around (offers, floors, bids). Each
concession amount is decided by the agent itself in UtilityAgent.concede_w.
"""
from __future__ import annotations

from typing import Dict, List, Mapping, Optional

from .agent import CREDIT_RATE, PublicRound, UtilityAgent
from .models import AgentState, Proposal, Request, w_to_kw

MAX_ROUNDS = 16


def _kw(w: int) -> str:
    return f"{w_to_kw(w):.1f} kW"


def safe_fallback(supply_w: int, requests: Mapping[str, Request],
                  bids: Mapping[str, float],
                  ceilings: Optional[Mapping[str, int]] = None) -> Dict[str, int]:
    """Deterministic, constraint-safe allocation used when negotiation can't converge.

    1. Sort agents by announced urgency bid (highest first; ties -> agent id).
    2. Walk the list giving each agent its hard floor (request.min_w), but only
       if the WHOLE floor fits in what remains. This keeps as many utilities
       alive as possible, most urgent first. A partial floor keeps nobody
       alive: on Mumbai it once handed all 280 MW to homes that needed 424 MW
       (and failed anyway) while hospitals got nothing (DECISIONS D-18).
    3. Walk the list again, topping each agent up to its ceiling (its last
       offer, or its request max) from whatever supply is left.

    Safe by construction: every grant is clipped to [0, request.max_w] and is
    taken from `remaining`, which starts at supply and never goes negative.
    """
    order = sorted(requests, key=lambda a: (-bids.get(a, 0.0), a))
    remaining = max(0, int(supply_w))
    alloc = {a: 0 for a in requests}
    for a in order:
        floor = max(0, min(requests[a].min_w, requests[a].max_w))
        if floor <= remaining:
            alloc[a] = floor
            remaining -= floor
    for a in order:
        cap = requests[a].max_w if ceilings is None else min(ceilings[a], requests[a].max_w)
        g = max(0, min(cap - alloc[a], remaining))
        alloc[a] += g
        remaining -= g
    return alloc


def credit_ledger(agents: Mapping[str, UtilityAgent], states: Mapping[str, AgentState],
                  conceded: Mapping[str, int], log: List[dict], rounds: int) -> Dict[str, float]:
    """Fairness credits: a public ledger of who gave up MORE than average.

    share_i = conceded_i / desired_i  (computed from public offers)
    delta_i = CREDIT_RATE x (share_i - mean share)

    Above-average sacrifice earns credits; below-average costs credits. It is
    relative on purpose: if every agent concedes the same share, nobody gains,
    so credits can't all saturate together and lose their meaning.
    """
    ids = list(agents)
    shares = {a: agents[a].concession_share(conceded.get(a, 0)) for a in ids}
    mean = sum(shares.values()) / len(ids)
    deltas = {a: CREDIT_RATE * (shares[a] - mean) for a in ids}
    for a in ids:
        if conceded.get(a, 0) > 0 or abs(deltas[a]) >= 0.05:
            rf = agents[a].reserve_frac(states[a])
            log.append({"round": rounds, "kind": "credit", "agent": a, "delta": round(deltas[a], 3),
                        "text": f"{agents[a].cfg.name} conceded {_kw(conceded.get(a, 0))} in total "
                                f"({shares[a]:.0%} of its load vs {mean:.0%} average), reserve {rf:.0%} "
                                f"→ {deltas[a]:+.1f} fairness credits"})
    return deltas


class GridPactPolicy:
    name = "gridpact"
    label = "GridPact (negotiation)"

    def __init__(self, max_rounds: int = MAX_ROUNDS):
        self.max_rounds = max_rounds

    def allocate(self, supply_w: int, requests: Mapping[str, Request],
                 agents: Mapping[str, UtilityAgent],
                 states: Mapping[str, AgentState]) -> Proposal:
        ids = list(requests)
        # Each agent computes its bid from ITS OWN state only.
        bids = {a: agents[a].urgency(states[a]) for a in ids}
        offers = {a: requests[a].max_w for a in ids}
        log: List[dict] = []
        total = sum(offers.values())

        if total <= supply_w:
            log.append({"round": 0, "kind": "grant",
                        "text": f"Demand {_kw(total)} fits supply {_kw(supply_w)}: every request granted in full."})
            return Proposal(dict(offers), 0, False, None, log, {}, bids)

        log.append({"round": 0, "kind": "open",
                    "text": f"Contested: demand {_kw(total)} vs supply {_kw(supply_w)} "
                            f"(short {_kw(total - supply_w)}). Negotiation opens."})
        conceded = {a: 0 for a in ids}
        rounds = 0
        converged = False
        hit_cap = False

        for phase in (1, 2):
            floors = {a: min(agents[a].floor_w(states[a], phase, requests[a]), offers[a]) for a in ids}
            if phase == 1:
                for a in ids:
                    cfg = agents[a].cfg
                    if cfg.strategy == "greedy":
                        log.append({"round": rounds, "kind": "hold", "agent": a,
                                    "text": f"{cfg.name} refuses to concede (greedy strategy)."})
                    elif floors[a] > requests[a].min_w:
                        rf = agents[a].reserve_frac(states[a])
                        log.append({"round": rounds, "kind": "hold", "agent": a,
                                    "text": f"{cfg.name} holds firm at {_kw(floors[a])}: "
                                            f"reserve only {rf:.0%}, near failure."})
            else:
                log.append({"round": rounds, "kind": "phase",
                            "text": "Phase 1 deadlocked. Phase 2: every agent digs into its reserves."})
            phase_slack = {a: offers[a] - floors[a] for a in ids}

            while True:
                excess = sum(offers.values()) - supply_w
                if excess <= 0:
                    converged = True
                    break
                if rounds >= self.max_rounds:
                    hit_cap = True
                    break
                if not any(offers[a] > floors[a] for a in ids):
                    break  # deadlock in this phase
                rounds += 1
                pub = PublicRound(supply_w, rounds, phase, excess, dict(offers), dict(floors), dict(bids))
                # All agents decide simultaneously from the same public snapshot.
                moves = {a: agents[a].concede_w(states[a], pub, offers[a], floors[a], phase_slack[a])
                         for a in ids}
                for a in ids:
                    c = moves[a]
                    if c <= 0:
                        continue
                    before = offers[a]
                    offers[a] -= c
                    conceded[a] += c
                    cfg = agents[a].cfg
                    rf = agents[a].reserve_frac(states[a])
                    log.append({"round": rounds, "kind": "concede", "agent": a, "phase": phase,
                                "watts": c,
                                "text": f"{cfg.name} conceded {_kw(c)} ({_kw(before)} → {_kw(offers[a])}), "
                                        f"reserve {rf:.0%}"})
            if converged or hit_cap:
                break

        if converged:
            log.append({"round": rounds, "kind": "agree",
                        "text": f"Agreement after {rounds} round(s): "
                                f"{_kw(sum(offers.values()))} allocated of {_kw(supply_w)}."})
            deltas = credit_ledger(agents, states, conceded, log, rounds)
            return Proposal(dict(offers), rounds, True, None, log, conceded, bids, deltas)

        reason = "round cap reached" if hit_cap else "deadlock: critical floors exceed supply"
        # Round cap: offers were converging, so they stay as ceilings. Deadlock:
        # the concessions were made to reach an agreement that cannot exist, so
        # they are void and top-up works from the original requests. Otherwise
        # agents that conceded to zero for an unsurvivable floor get nothing back
        # (DECISIONS D-18).
        alloc = safe_fallback(supply_w, requests, bids, ceilings=offers if hit_cap else None)
        log.append({"round": rounds, "kind": "fallback",
                    "text": f"No agreement ({reason}). Fallback: hard floors by urgency, then top-up."})
        for a in sorted(ids, key=lambda x: (-bids[x], x)):
            if alloc[a] < requests[a].min_w:
                log.append({"round": rounds, "kind": "short", "agent": a,
                            "text": f"{agents[a].cfg.name} receives {_kw(alloc[a])}, below its hard floor "
                                    f"{_kw(requests[a].min_w)}: it will fail this step."})
        deltas = credit_ledger(agents, states, conceded, log, rounds)
        return Proposal(alloc, rounds, True, reason, log, conceded, bids, deltas)
