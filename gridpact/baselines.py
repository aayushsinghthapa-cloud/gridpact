"""Three classic centralized allocation rules, used as comparison baselines.

They see exactly the same requests, supply and physics as GridPact; only the
allocation rule differs. All of them cap each agent at its request max, so
they are just as constraint-safe and go through the same validator.
"""
from __future__ import annotations

from typing import Dict, Mapping

from .models import Proposal, Request


def _no_contest(supply_w: int, requests: Mapping[str, Request]):
    total = sum(r.max_w for r in requests.values())
    if total <= supply_w:
        return {a: r.max_w for a, r in requests.items()}
    return None


def water_fill_equal(supply_w: int, caps: Mapping[str, int]) -> Dict[str, int]:
    """Equal split with redistribution ("water filling").

    Everyone gets the same share; anyone who needs less than the share keeps
    only what they asked for, and the leftover is re-split among the rest.
    Integer watts: the last few leftover watts go to agents in id order.
    """
    alloc = {a: 0 for a in caps}
    active = sorted(a for a in caps if caps[a] > 0)
    remaining = supply_w
    while active and remaining > 0:
        share = remaining // len(active)
        if share == 0:
            for a in active[:remaining]:
                alloc[a] += 1
            remaining = 0
            break
        nxt = []
        for a in active:
            g = min(share, caps[a] - alloc[a])
            alloc[a] += g
            remaining -= g
            if alloc[a] < caps[a]:
                nxt.append(a)
        # Terminates: either someone hit their cap (list shrinks) or everyone
        # took a full share, leaving remaining < len(active) -> share == 0 next.
        active = nxt
    return alloc


class EqualSplitPolicy:
    name = "equal"
    label = "Equal split"

    def allocate(self, supply_w, requests, agents, states) -> Proposal:
        full = _no_contest(supply_w, requests)
        if full is not None:
            return Proposal(full, 0, False, None)
        alloc = water_fill_equal(supply_w, {a: r.max_w for a, r in requests.items()})
        return Proposal(alloc, 0, True, None)


class ProportionalPolicy:
    name = "proportional"
    label = "Proportional to demand"

    def allocate(self, supply_w, requests, agents, states) -> Proposal:
        full = _no_contest(supply_w, requests)
        if full is not None:
            return Proposal(full, 0, False, None)
        total = sum(r.max_w for r in requests.values())
        # Integer floor division -> the sum can never exceed supply.
        alloc = {a: (r.max_w * supply_w) // total for a, r in requests.items()}
        return Proposal(alloc, 0, True, None)


class StrictPriorityPolicy:
    name = "priority"
    label = "Strict priority"

    def allocate(self, supply_w, requests, agents, states) -> Proposal:
        full = _no_contest(supply_w, requests)
        if full is not None:
            return Proposal(full, 0, False, None)
        # Hospital > Emergency > Water > Telecom (priority_rank from config).
        order = sorted(requests, key=lambda a: (agents[a].cfg.priority_rank, a))
        remaining = supply_w
        alloc = {}
        for a in order:
            g = min(requests[a].max_w, remaining)
            alloc[a] = g
            remaining -= g
        return Proposal(alloc, 0, True, None)
