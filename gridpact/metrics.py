"""Run metrics. Computed from step records only, never from the engine's own claims.

Capacity violations are RE-CHECKED here from raw watts, independently of the
validator, so a validator bug could not hide a violation from the results.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from .config import EventSpec, Scenario
from .simulation import POLICIES, POLICY_LABELS, Simulation


def jain_index(xs: Sequence[float]) -> float:
    """Jain's fairness index: (sum x)^2 / (n * sum x^2). 1.0 = perfectly equal, 1/n = one gets all."""
    xs = list(xs)
    sq = sum(x * x for x in xs)
    if not xs or sq == 0:
        return 1.0
    return (sum(xs) ** 2) / (len(xs) * sq)


def compute(records: List[dict], scenario: Scenario) -> dict:
    dt = scenario.dt_hours
    ids = [a.id for a in scenario.agents]
    names = {a.id: a.name for a in scenario.agents}
    per = {a: {"damage": 0.0, "failures": 0, "longest_outage_steps": 0, "_run": 0,
               "satisfaction_sum": 0.0, "critical_unserved_kwh": 0.0} for a in ids}
    violations = 0
    contested = fallback = rejected = 0
    rounds: List[int] = []
    ms: List[float] = []

    for r in records:
        total_w = 0
        bad = False
        for aid in ids:
            a = r["agents"][aid]
            total_w += a["alloc_w"]
            if not (0 <= a["alloc_w"] <= round(a["request_max_kw"] * 1000)):
                bad = True
            p = per[aid]
            p["damage"] += a["damage"]
            p["failures"] += 1 if a["new_failure"] else 0
            if a["status"] == "FAILED":
                p["_run"] += 1
                p["longest_outage_steps"] = max(p["longest_outage_steps"], p["_run"])
            else:
                p["_run"] = 0
            p["satisfaction_sum"] += min(1.0, a["effective_kw"] / a["desired_kw"]) if a["desired_kw"] > 0 else 1.0
            p["critical_unserved_kwh"] += max(0.0, a["min_kw"] - a["effective_kw"]) * dt
        if bad or total_w > r["supply_w"]:
            violations += 1
        if r["contested"]:
            contested += 1
            rounds.append(r["rounds"])
        fallback += 1 if r["fallback"] else 0
        rejected += 1 if r["rejected"] else 0
        ms.append(r["compute_ms"])

    n = max(1, len(records))
    sat = {a: per[a]["satisfaction_sum"] / n for a in ids}
    agents_out = {}
    for a in ids:
        p = per[a]
        agents_out[a] = {
            "name": names[a],
            "damage": round(p["damage"], 2),
            "failures": p["failures"],
            "longest_outage_h": round(p["longest_outage_steps"] * dt, 2),
            "mean_satisfaction": round(sat[a], 4),
            "critical_unserved_kwh": round(p["critical_unserved_kwh"], 2),
        }
    ms_sorted = sorted(ms) or [0.0]
    return {
        "steps": len(records),
        "total_damage": round(sum(per[a]["damage"] for a in ids), 2),
        "failure_events": sum(per[a]["failures"] for a in ids),
        "longest_outage_h": round(max(per[a]["longest_outage_steps"] for a in ids) * dt, 2),
        "jain_fairness": round(jain_index(sat.values()), 4),
        "capacity_violations": violations,
        "critical_unserved_kwh": round(sum(per[a]["critical_unserved_kwh"] for a in ids), 2),
        "contested_steps": contested,
        "rounds_mean": round(sum(rounds) / len(rounds), 2) if rounds else 0.0,
        "rounds_max": max(rounds) if rounds else 0,
        "fallback_steps": fallback,
        "rejected_steps": rejected,
        "compute_ms_mean": round(sum(ms) / len(ms), 3) if ms else 0.0,
        "compute_ms_p95": round(ms_sorted[min(len(ms_sorted) - 1, int(0.95 * len(ms_sorted)))], 3),
        "agents": agents_out,
    }


def pct_change(new: float, old: float) -> float:
    return 0.0 if old == 0 else (new - old) / old * 100.0


def compare(scenario: Scenario, seed: int,
            injected: Sequence[Tuple[int, EventSpec]] = (), with_series: bool = True) -> dict:
    """Run every policy on the same scenario, seed and event log."""
    out = {"scenario": scenario.id, "seed": seed, "policies": {}, "order": list(POLICIES)}
    for p in POLICIES:
        recs = Simulation(scenario, seed, p, injected).run()
        m = compute(recs, scenario)
        m["label"] = POLICY_LABELS[p]
        if with_series:
            cum = 0.0
            series = []
            for r in recs:
                cum += r["step_damage"]
                series.append(round(cum, 2))
            m["damage_series"] = series
        out["policies"][p] = m
    g = out["policies"]["gridpact"]["total_damage"]
    out["damage_change_pct"] = {p: round(pct_change(g, out["policies"][p]["total_damage"]), 1)
                                for p in POLICIES if p != "gridpact"}
    return out
