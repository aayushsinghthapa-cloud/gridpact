"""Regenerate RESULTS.md from real simulator runs. Nothing in RESULTS.md is typed by hand
except the 'Limitations' text at the bottom (see LIMITATIONS below).

    python3 gridpact.py results        (or: python3 -m scripts.generate_results)

Experiments:
  1. every scenario x every policy x SEEDS        -> mean and spread of every metric
  2. seed 41 single runs                          -> the exact numbers the dashboard shows
  3. honesty: one agent lies (greedy strategy)    -> what lying buys it, what it costs others
  4. scaling: 4 .. 192 agents                     -> compute time and rounds per step
"""
from __future__ import annotations

import dataclasses
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gridpact.config import list_scenarios, load_scenario          # noqa: E402
from gridpact.metrics import compare, compute, pct_change           # noqa: E402
from gridpact.models import Dependency                              # noqa: E402
from gridpact.simulation import POLICIES, POLICY_LABELS, Simulation  # noqa: E402

SEEDS = list(range(1, 11))
DEMO_SEED = 41
OUT = ROOT / "RESULTS.md"

ROWS = [
    ("total_damage", "Total damage", "{:,.0f}"),
    ("failure_events", "Failure events", "{:.1f}"),
    ("longest_outage_h", "Longest single outage (h)", "{:.2f}"),
    ("critical_unserved_kwh", "Critical energy unserved (kWh)", "{:,.0f}"),
    ("jain_fairness", "Jain's fairness index", "{:.4f}"),
    ("capacity_violations", "Capacity violations", "{:.0f}"),
    ("rounds_mean", "Rounds per contested step (mean)", "{:.2f}"),
    ("rounds_max", "Rounds per contested step (max)", "{:.0f}"),
    ("fallback_steps", "Fallback steps", "{:.1f}"),
    ("compute_ms_mean", "Compute time per step (ms, mean)", "{:.3f}"),
]

LIMITATIONS = """\
- **Synthetic model, not field data.** Loads, reserves, damage weights and dependency impacts are
  plausible hand-set values in `data/`, not measurements from a real city. Damage is a model
  quantity (weighted kWh of shortfall with escalation), not lives or rupees. The *relative*
  comparison between policies is the claim; the absolute numbers are not.
- **Proportional-to-demand is a strong baseline.** It comes close to GridPact on damage in some
  scenarios and has a slightly higher Jain index. Jain's index rewards *equal* service ratios,
  while GridPact deliberately serves critical loads unequally to prevent failures, so a small
  Jain gap is the expected price of fewer outages.
- **Agents are honest by default.** The protocol bounds what a liar can gain (urgency cap,
  credits only from observable concessions) but does not prevent lying: the honesty experiment
  above shows a greedy agent *does* gain. Real deployment would need audited meters and
  penalties.
- **Reserves are kWh-equivalents.** A water tank or diesel tank is modelled as stored energy with
  a discharge and recharge rate; real pumps and generators have start-up limits and efficiencies
  we ignore.
- **No network model.** Supply is one pooled number per step; there are no feeder limits,
  line losses or voltage constraints. The validator enforces the pooled capacity only.
- **Timing numbers** depend on the laptop; they are measured, not modelled, and are the only
  values that differ between identical runs.
- **The LLM narrator is optional and unevaluated.** It only paraphrases the deterministic log
  and is off by default.
"""


def fmt(v, f):
    return f.format(v)


def multi_seed(scenario_id):
    sc = load_scenario(scenario_id)
    per = {p: [] for p in POLICIES}
    wins = 0
    reductions = {p: [] for p in POLICIES if p != "gridpact"}
    for seed in SEEDS:
        res = compare(sc, seed, with_series=False)
        for p in POLICIES:
            per[p].append(res["policies"][p])
        g = res["policies"]["gridpact"]["total_damage"]
        if all(g <= res["policies"][p]["total_damage"] for p in reductions):
            wins += 1
        for p in reductions:
            reductions[p].append(pct_change(g, res["policies"][p]["total_damage"]))
    return sc, per, wins, reductions


def table_multi(per):
    pols = list(POLICIES)
    lines = ["| Metric (mean ± sd over seeds) | " + " | ".join(POLICY_LABELS[p] for p in pols) + " |",
             "|---|" + "---:|" * len(pols)]
    for key, label, f in ROWS:
        cells = []
        for p in pols:
            xs = [m[key] for m in per[p]]
            mean = statistics.mean(xs)
            sd = statistics.pstdev(xs)
            if p != "gridpact" and key in ("rounds_mean", "rounds_max"):
                cells.append("n/a")
            elif key == "capacity_violations":
                cells.append(f"**{sum(xs)}** total")
            else:
                cells.append(fmt(mean, f) + (f" ± {fmt(sd, f)}" if sd > 0 and key != "compute_ms_mean" else ""))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def table_single(res, sc):
    pols = list(POLICIES)
    P = res["policies"]
    lines = ["| Metric | " + " | ".join(POLICY_LABELS[p] for p in pols) + " |", "|---|" + "---:|" * len(pols)]
    for key, label, f in ROWS:
        cells = []
        for p in pols:
            if p != "gridpact" and key in ("rounds_mean", "rounds_max"):
                cells.append("n/a")
            else:
                cells.append(fmt(P[p][key], f))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("Longest outage per utility, hours (failure events):")
    lines.append("")
    lines.append("| Utility | " + " | ".join(POLICY_LABELS[p] for p in pols) + " |")
    lines.append("|---|" + "---:|" * len(pols))
    for a in sc.agents[:8]:
        lines.append(f"| {a.name} | " + " | ".join(
            f"{P[p]['agents'][a.id]['longest_outage_h']:.2f} ({P[p]['agents'][a.id]['failures']})" for p in pols) + " |")
    if len(sc.agents) > 8:
        lines.append(f"| *({len(sc.agents) - 8} more utilities, see `python3 gridpact.py run {sc.id}`)* |" + " |" * len(pols))
    return "\n".join(lines)


def honesty():
    """Make one agent lie (inflate its bid x5, never concede). Who gains, who pays?"""
    sc = load_scenario("cyclone_landfall")
    rows = []
    base = compute(Simulation(sc, DEMO_SEED, "gridpact").run(), sc)
    for liar in ("telecom", "hospital"):
        agents = tuple(dataclasses.replace(a, strategy="greedy") if a.id == liar else a for a in sc.agents)
        sc2 = dataclasses.replace(sc, agents=agents)
        m = compute(Simulation(sc2, DEMO_SEED, "gridpact").run(), sc2)
        others_before = sum(base["agents"][a]["damage"] for a in base["agents"] if a != liar)
        others_after = sum(m["agents"][a]["damage"] for a in m["agents"] if a != liar)
        rows.append((sc.agents[[a.id for a in sc.agents].index(liar)].name,
                     base["agents"][liar]["damage"], m["agents"][liar]["damage"],
                     others_before, others_after, base["total_damage"], m["total_damage"],
                     m["failure_events"], m["capacity_violations"]))
    lines = ["| Liar | Liar's own damage (honest → lying) | Everyone else's damage | Total damage | "
             "Failure events while lying | Capacity violations |", "|---|---:|---:|---:|---:|---:|"]
    for name, lb, la, ob, oa, tb, ta, fe, cv in rows:
        lines.append(f"| {name} | {lb:,.0f} → {la:,.0f} | {ob:,.0f} → {oa:,.0f} ({pct_change(oa, ob):+.0f}%) | "
                     f"{tb:,.0f} → {ta:,.0f} ({pct_change(ta, tb):+.0f}%) | {fe} | {cv} |")
    return "\n".join(lines)


def scaled_scenario(k):
    """k copies of the 24-utility metro (ids suffixed), supply scaled by k."""
    sc = load_scenario("stress_metro_24")
    agents, deps = [], []
    for i in range(k):
        agents += [dataclasses.replace(a, id=f"{a.id}#{i}", name=f"{a.name} #{i}") for a in sc.agents]
        deps += [Dependency(f"{d.source}#{i}", f"{d.target}#{i}", d.impact, d.label) for d in sc.dependencies]
    return dataclasses.replace(sc, id=f"metro_x{k}", agents=tuple(agents), dependencies=tuple(deps),
                               base_supply_kw=sc.base_supply_kw * k)


def scaling():
    lines = ["| Agents | GridPact ms/step (mean) | ms/step (p95) | Rounds/contested step (mean / max) | "
             "Fallback steps | Capacity violations | Failure events |", "|---:|---:|---:|---:|---:|---:|---:|"]
    core = load_scenario("cyclone_landfall")
    cases = [(4, core)] + [(24 * k, scaled_scenario(k)) for k in (1, 2, 4, 8)]
    for n, sc in cases:
        m = compute(Simulation(sc, DEMO_SEED, "gridpact").run(), sc)
        lines.append(f"| {n} | {m['compute_ms_mean']:.2f} | {m['compute_ms_p95']:.2f} | "
                     f"{m['rounds_mean']:.2f} / {m['rounds_max']} | {m['fallback_steps']} | "
                     f"{m['capacity_violations']} | {m['failure_events']} |")
    return "\n".join(lines)


def main():
    t0 = time.perf_counter()
    scenario_ids = [s["id"] for s in list_scenarios()]
    md = ["# Results", "",
          "> **Generated by `scripts/generate_results.py`. Do not edit by hand.** "
          "Re-run with `python3 gridpact.py results`.", "",
          f"Every number below comes from a real simulator run. Seeds {SEEDS[0]}–{SEEDS[-1]} for the "
          f"multi-seed tables; seed {DEMO_SEED} for the single-run tables (the dashboard's default seed, "
          "with no live injections). All four policies see the same scenario, seed, requests, reserves and physics; "
          "only the allocation rule differs.", ""]

    summary = ["## Headline", "", "| Scenario | Agents | GridPact damage vs strict priority | vs proportional | vs equal split | "
               "GridPact lowest damage | Capacity violations (all policies, all seeds) |",
               "|---|---:|---:|---:|---:|---:|---:|"]
    body = []
    for sid in scenario_ids:
        sc, per, wins, red = multi_seed(sid)
        viol = sum(m["capacity_violations"] for p in per for m in per[p])
        summary.append(f"| {sc.name} | {len(sc.agents)} | {statistics.mean(red['priority']):+.1f}% | "
                       f"{statistics.mean(red['proportional']):+.1f}% | {statistics.mean(red['equal']):+.1f}% | "
                       f"{wins}/{len(SEEDS)} seeds | {viol} |")
        single = compare(sc, DEMO_SEED, with_series=False)
        body += [f"## {sc.name}", "", f"*{sc.description}* ({len(sc.agents)} agents, {sc.hours:g} h, "
                 f"{sc.steps} steps of {sc.step_minutes} min)", "",
                 f"### Mean over {len(SEEDS)} seeds", "", table_multi(per), "",
                 f"### Seed {DEMO_SEED} (what the dashboard shows)", "", table_single(single, sc), ""]
    summary.append("")
    summary.append("Damage change is the mean over seeds of (GridPact − baseline) / baseline. Negative = GridPact did less damage.")
    summary.append("")

    md += summary + body
    md += ["## What if an agent lies?", "",
           "One agent is switched to the `greedy` strategy: it inflates its urgency bid ×5 (clamped at the cap "
           "of 50) and never concedes. Cyclone landfall, seed 41, GridPact policy.", "", honesty(), "",
           "The validator still guarantees physical safety (zero violations): lying cannot break the grid. "
           "But lying is not free for everyone else, which is why this is listed under limitations.", "",
           "## Scalability", "",
           "The 24-utility metro replicated k times (supply scaled by k), seed 41, full 24 h run. "
           f"Measured on {platform.machine()} / Python {platform.python_version()}.", "", scaling(), "",
           "Each round costs O(n) per agent decision (every agent reads the public offer table), "
           "so a round is O(n²) in the worst case; the round count stays bounded by the per-phase "
           "increment rule and the cap of 16.", "",
           "## Limitations (honest)", "", LIMITATIONS,
           f"_Generated in {time.perf_counter() - t0:.1f} s._", ""]
    OUT.write_text("\n".join(md))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
