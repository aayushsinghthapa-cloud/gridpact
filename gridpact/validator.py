"""The neutral validator.

It enforces physics, never preferences. It does not know urgency, damage or
priority, and it never chooses who gets what. It answers one question: "Is
this proposed split physically allowed?"

  C1  every agent appears exactly once, no unknown agents
  C2  every allocation is a whole number of watts (no NaN, no floats)
  C3  0 <= allocation_i <= request_i.max for every agent
  C4  sum(allocations) <= available supply      (integers: exact, no tolerance)

Every check is recorded (passed or not) so the dashboard and log can show it.
"""
from __future__ import annotations

from typing import Mapping

from .models import Proposal, Request, Verdict


def validate(supply_w: int, requests: Mapping[str, Request], proposal: Proposal) -> Verdict:
    alloc = proposal.allocations
    checks = []

    expected, got = set(requests), set(alloc)
    checks.append(("C1 agent set", expected == got,
                   "ok" if expected == got else f"missing={sorted(expected - got)} extra={sorted(got - expected)}"))

    bad_type = [a for a, v in alloc.items() if type(v) is not int]   # bool is rejected too
    checks.append(("C2 integer watts", not bad_type,
                   "ok" if not bad_type else f"non-integer for {sorted(bad_type)}"))

    out_of_range = [a for a in expected & got
                    if type(alloc[a]) is int and not (0 <= alloc[a] <= requests[a].max_w)]
    checks.append(("C3 0 <= a_i <= request_i", not out_of_range and not bad_type,
                   "ok" if not out_of_range else f"out of range: {sorted(out_of_range)}"))

    if bad_type:
        checks.append(("C4 sum <= supply", False, "cannot sum non-integer allocations"))
    else:
        total = sum(alloc.values())
        checks.append(("C4 sum <= supply", total <= supply_w,
                       f"{total} W <= {supply_w} W" if total <= supply_w else f"{total} W > {supply_w} W"))

    return Verdict(all(p for _, p, _ in checks), tuple(checks))
