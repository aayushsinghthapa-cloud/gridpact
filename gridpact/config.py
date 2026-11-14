"""Load and sanity-check scenario and agent files from data/.

WHY data files instead of Python constants: the problem statement asks for
scenarios as config, and it lets anyone add a fifth utility or a new disaster
without touching engine code. We validate every file loudly on load, because a
typo in a JSON file should fail at startup, not as a strange result mid-demo.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import AgentConfig, Dependency

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SCENARIO_DIR = DATA_DIR / "scenarios"

EVENT_TYPES = {"supply_shock", "demand_surge", "reserve_loss"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class EventSpec:
    """A disaster event. Scheduled in the scenario file, or injected live."""
    type: str                       # supply_shock | demand_surge | reserve_loss
    label: str
    factor: float = 1.0             # supply/demand multiplier (shock, surge)
    duration_steps: int = 1         # how long a shock/surge lasts
    agent: Optional[str] = None     # target agent for surge / reserve_loss
    fraction: float = 0.0           # share of reserve lost (reserve_loss)
    id: str = ""                    # stable id for injectable buttons


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    description: str
    hours: float
    step_minutes: int
    base_supply_kw: float
    noise_frac: float
    profile: Tuple[Tuple[float, float], ...]       # (hour, fraction of base) keypoints
    agents: Tuple[AgentConfig, ...]
    dependencies: Tuple[Dependency, ...]
    scheduled: Tuple[Tuple[int, EventSpec], ...]   # (step, event)
    injectable: Tuple[EventSpec, ...]

    @property
    def steps(self) -> int:
        return int(round(self.hours * 60 / self.step_minutes))

    @property
    def dt_hours(self) -> float:
        return self.step_minutes / 60.0


def _req(d: dict, key: str, where: str):
    if key not in d:
        raise ConfigError(f"{where}: missing required field '{key}'")
    return d[key]


def _parse_agent(d: dict, where: str) -> AgentConfig:
    a = AgentConfig(
        id=str(_req(d, "id", where)),
        name=str(_req(d, "name", where)),
        kind=str(d.get("kind", "utility")),
        priority_rank=int(_req(d, "priority_rank", where)),
        weight=float(_req(d, "weight", where)),
        min_kw=float(_req(d, "min_kw", where)),
        desired_kw=float(_req(d, "desired_kw", where)),
        reserve_capacity_kwh=float(_req(d, "reserve_capacity_kwh", where)),
        reserve_initial_frac=float(d.get("reserve_initial_frac", 1.0)),
        max_discharge_kw=float(_req(d, "max_discharge_kw", where)),
        recharge_kw=float(_req(d, "recharge_kw", where)),
        damage_soft=float(d.get("damage_soft", 1.0)),
        damage_critical=float(d.get("damage_critical", 6.0)),
        escalation=float(d.get("escalation", 0.1)),
        escalation_cap_steps=int(d.get("escalation_cap_steps", 24)),
        strategy=str(d.get("strategy", "honest")),
        reserve_label=str(d.get("reserve_label", "Backup reserve")),
        demand_profile=tuple((float(h), float(f)) for h, f in d.get("demand_profile", ())),
    )
    w = f"{where} agent '{a.id}'"
    if not (0 < a.min_kw <= a.desired_kw):
        raise ConfigError(f"{w}: need 0 < min_kw <= desired_kw")
    if a.weight <= 0:
        raise ConfigError(f"{w}: weight must be > 0")
    if not (0 <= a.reserve_initial_frac <= 1):
        raise ConfigError(f"{w}: reserve_initial_frac must be in [0, 1]")
    if min(a.reserve_capacity_kwh, a.max_discharge_kw, a.recharge_kw) < 0:
        raise ConfigError(f"{w}: reserve figures must be >= 0")
    if a.demand_profile and (a.demand_profile[0][0] != 0 or any(f <= 0 for _, f in a.demand_profile)
                             or any(a.demand_profile[i][0] >= a.demand_profile[i + 1][0]
                                    for i in range(len(a.demand_profile) - 1))):
        raise ConfigError(f"{w}: demand_profile must start at hour 0, increase, and stay > 0")
    if a.strategy not in ("honest", "greedy"):
        raise ConfigError(f"{w}: strategy must be 'honest' or 'greedy'")
    return a


def _parse_event(d: dict, step_minutes: int, agent_ids: set, where: str) -> EventSpec:
    etype = str(_req(d, "type", where))
    if etype not in EVENT_TYPES:
        raise ConfigError(f"{where}: unknown event type '{etype}'")
    duration_h = float(d.get("duration_hours", step_minutes / 60))
    ev = EventSpec(
        type=etype,
        label=str(d.get("label", etype)),
        factor=float(d.get("factor", 1.0)),
        duration_steps=max(1, int(round(duration_h * 60 / step_minutes))),
        agent=d.get("agent"),
        fraction=float(d.get("fraction", 0.0)),
        id=str(d.get("id", "")),
    )
    if etype in ("demand_surge", "reserve_loss") and ev.agent not in agent_ids:
        raise ConfigError(f"{where}: event targets unknown agent '{ev.agent}'")
    if ev.factor < 0 or not (0 <= ev.fraction <= 1):
        raise ConfigError(f"{where}: factor must be >= 0 and fraction in [0, 1]")
    return ev


def load_agents_file(path: Path) -> Tuple[Tuple[AgentConfig, ...], Tuple[Dependency, ...]]:
    raw = json.loads(path.read_text())
    where = path.name
    agents = tuple(_parse_agent(d, where) for d in _req(raw, "agents", where))
    ids = [a.id for a in agents]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"{where}: duplicate agent ids")
    if len(agents) < 2:
        raise ConfigError(f"{where}: need at least 2 agents")
    deps = []
    for d in raw.get("dependencies", []):
        dep = Dependency(source=d["source"], target=d["target"],
                         impact=float(d["impact"]), label=d.get("label", ""))
        if dep.source not in ids or dep.target not in ids or dep.source == dep.target:
            raise ConfigError(f"{where}: bad dependency {dep.source}->{dep.target}")
        if not (0 <= dep.impact <= 1):
            raise ConfigError(f"{where}: dependency impact must be in [0, 1]")
        deps.append(dep)
    return agents, tuple(deps)


def load_scenario(scenario_id: str) -> Scenario:
    path = SCENARIO_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise ConfigError(f"unknown scenario '{scenario_id}' (looked for {path})")
    raw = json.loads(path.read_text())
    where = path.name
    agents, deps = load_agents_file(DATA_DIR / _req(raw, "agents", where))
    ids = {a.id for a in agents}
    step_minutes = int(raw.get("step_minutes", 15))
    hours = float(_req(raw, "hours", where))
    supply = _req(raw, "supply", where)
    profile = tuple((float(h), float(f)) for h, f in _req(supply, "profile", where))
    if not profile or profile[0][0] != 0 or any(f < 0 for _, f in profile):
        raise ConfigError(f"{where}: supply profile must start at hour 0 with fractions >= 0")
    if any(profile[i][0] >= profile[i + 1][0] for i in range(len(profile) - 1)):
        raise ConfigError(f"{where}: supply profile hours must be strictly increasing")

    scheduled: List[Tuple[int, EventSpec]] = []
    for i, d in enumerate(raw.get("scheduled_events", [])):
        step = int(round(float(_req(d, "at_hour", where)) * 60 / step_minutes))
        scheduled.append((step, _parse_event(d, step_minutes, ids, f"{where} scheduled[{i}]")))
    injectable = tuple(_parse_event(d, step_minutes, ids, f"{where} injectable[{i}]")
                       for i, d in enumerate(raw.get("injectable_events", [])))

    return Scenario(
        id=scenario_id,
        name=str(raw.get("name", scenario_id)),
        description=str(raw.get("description", "")),
        hours=hours,
        step_minutes=step_minutes,
        base_supply_kw=float(_req(supply, "base_kw", where)),
        noise_frac=float(supply.get("noise_frac", 0.0)),
        profile=profile,
        agents=agents,
        dependencies=deps,
        scheduled=tuple(sorted(scheduled, key=lambda x: x[0])),
        injectable=injectable,
    )


def list_scenarios() -> List[Dict[str, str]]:
    out = []
    for p in sorted(SCENARIO_DIR.glob("*.json")):
        raw = json.loads(p.read_text())
        out.append({"id": p.stem, "name": raw.get("name", p.stem),
                    "description": raw.get("description", ""),
                    "order": raw.get("order", 99)})
    out.sort(key=lambda s: (s["order"], s["id"]))
    return out


def event_from_dict(d: dict, scenario: Scenario) -> EventSpec:
    """Build an event sent by the dashboard (or a test). Validated like file events."""
    return _parse_event(d, scenario.step_minutes, {a.id for a in scenario.agents}, "injected event")
