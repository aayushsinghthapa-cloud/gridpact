"""Real-city mode: Mumbai, built from published numbers.

This module turns data/cities/<city>.json (every figure tagged with its source)
into an ordinary Scenario, so the SAME engine, validator, physics and baselines
run on it. Nothing here allocates power; it only builds inputs and reads results.

Three things happen here:
  1. Derivation: published inputs (beds, kWh per bed, towers, kW per tower...)
     become agent loads, reserves and harm rates. Every derived number keeps
     its formula, so the page can show its working.
  2. Disaster: a recorded loss (MW, onset, restoration shape) is subtracted
     from that day's demand curve to give the available supply.
  3. Forecast: the recorded loss size and restoration time are uncertain, so
     we run an ensemble: N versions of the day with those inputs perturbed
     within stated bounds, and report median and 10th–90th percentiles.

Units: the engine works in kW; the page shows MW.
"""
from __future__ import annotations

import json
import random
import statistics
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .config import DATA_DIR, ConfigError, Scenario
from .metrics import compute
from .models import AgentConfig, Dependency
from .simulation import POLICIES, POLICY_LABELS, Simulation, interp_profile

CITY_DIR = DATA_DIR / "cities"
STEP_MINUTES = 15
HOURS = 24
INTENSITY_RANGE = (0.5, 1.5)


@lru_cache(maxsize=4)
def load_city(city_id: str = "mumbai") -> dict:
    path = CITY_DIR / f"{city_id}.json"
    if not path.exists():
        raise ConfigError(f"unknown city '{city_id}'")
    raw = json.loads(path.read_text())
    _check_city(raw)
    return raw


def _check_city(c: dict) -> None:
    """Fail loudly on a broken data file (a typo must not become a strange result)."""
    srcs = c["sources"]
    wards = c["population"]["wards"]
    if sum(v[0] for v in wards.values()) != c["population"]["total"]:
        raise ConfigError("ward populations do not add up to the city total")
    if sum(v[1] for v in wards.values()) != c["population"]["households"]:
        raise ConfigError("ward households do not add up to the city total")
    for s in c["sectors"]:
        for i in s["inputs"]:
            if i["kind"] in ("measured", "derived") and i.get("source") not in srcs:
                raise ConfigError(f"{s['id']}.{i['id']}: measured input without a known source")
    for d in c["disasters"]:
        for item in d["timeline"] + d["record"]:
            if item["source"] not in srcs:
                raise ConfigError(f"{d['id']}: unknown source '{item['source']}'")
        for z in d["affected"]:
            if z not in c["zones"]:
                raise ConfigError(f"{d['id']}: unknown zone '{z}'")


def _inputs(sector: dict) -> Dict[str, float]:
    return {i["id"]: i["value"] for i in sector["inputs"]}


# ---------------------------------------------------------------------------
# 1. Derivation: published inputs -> loads
# ---------------------------------------------------------------------------

def sector_load_kw(sector: dict) -> Tuple[float, str]:
    """Normal (desired) load of one critical sector, and the formula used."""
    v = _inputs(sector)
    sid = sector["id"]
    if sid == "hospital":
        kwh = v["beds_public"] * v["kwh_bed_public"] + v["beds_private"] * v["kwh_bed_private"]
        kw = kwh / 8760.0
        return kw, (f"({v['beds_public']:,} beds × {v['kwh_bed_public']:,} kWh + {v['beds_private']:,} beds × "
                    f"{v['kwh_bed_private']:,} kWh) ÷ 8,760 h = {kw / 1000:.1f} MW")
    if sid == "water":
        kw = v["pumping_mw"] * 1000.0
        return kw, f"BMC pumping stations: {v['pumping_mw']} MW (measured)"
    if sid == "telecom":
        kw = v["towers"] * v["kw_tower"]
        return kw, f"{v['towers']:,} sites × {v['kw_tower']} kW = {kw / 1000:.1f} MW"
    if sid == "emergency":
        kw = v["fire_stations"] * v["kw_fire"] + v["police_stations"] * v["kw_police"]
        return kw, (f"{v['fire_stations']} fire stations × {v['kw_fire']} kW + {v['police_stations']} police stations × "
                    f"{v['kw_police']} kW = {kw / 1000:.1f} MW")
    raise ConfigError(f"no load formula for sector '{sid}'")


def sector_units(city: dict, sector: dict) -> float:
    """How many beds / residents / stations / households the sector serves."""
    unit = sector["unit"]
    v = _inputs(sector)
    if unit == "beds":
        return v["beds_public"] + v["beds_private"]
    if unit == "residents":
        return city["population"]["total"]
    if unit == "households":
        return city["population"]["households"]
    if unit == "stations":
        return v["fire_stations"] + v["police_stations"]
    raise ConfigError(f"unknown unit '{unit}'")


def demand_curve(city: dict, disaster: dict):
    """City demand in MW as a function of the hour, for this disaster's day."""
    shape = [tuple(p) for p in city["grid"]["shape"]["points"]]
    d = disaster["demand"]
    if "anchor_mw" in d:
        peak = d["anchor_mw"] / interp_profile(shape, d["anchor_hour"])
    else:
        peak = d["peak_mw"]
    return (lambda h: peak * interp_profile(shape, h)), peak


def harm_unit(city: dict, peak_mw: float, crit_total_kw: Optional[float] = None) -> float:
    """Engine damage per household-hour without power.

    Agent damage rates are expressed in one common unit, the homes' comfort
    rate, so that the harm-per-kW term in every bid compares like with like
    (agent.urgency). Multiply engine damage by this to read it as
    "household-hours without power, equivalent".
    """
    if crit_total_kw is None:
        crit_total_kw = sum(sector_load_kw(s)[0] for s in city["sectors"] if not s.get("residual"))
    homes = next(s for s in city["sectors"] if s.get("residual"))
    desired = peak_mw * 1000.0 - crit_total_kw
    band = desired * (1 - _inputs(homes)["critical_share"])
    return sector_units(city, homes) * homes["harm"]["soft"] / (homes["weight"] * band)


def build_agents(city: dict, peak_mw: float, demand_mw) -> Tuple[Tuple[AgentConfig, ...], List[dict]]:
    """Agent configs for the engine, plus a derivation table for the page."""
    critical = [s for s in city["sectors"] if not s.get("residual")]
    loads = {s["id"]: sector_load_kw(s) for s in critical}
    crit_total_kw = sum(kw for kw, _ in loads.values())
    unit = harm_unit(city, peak_mw, crit_total_kw)
    agents, table = [], []
    for s in city["sectors"]:
        v = _inputs(s)
        if s.get("residual"):
            # Homes and businesses = everything the city draws that the critical
            # sectors don't. Follows the daily curve, so it carries a demand profile.
            desired = peak_mw * 1000.0 - crit_total_kw
            pts = [h / 4 for h in range(0, HOURS * 4 + 1)]
            profile = tuple((h, max(0.05, (demand_mw(h) * 1000.0 - crit_total_kw) / desired)) for h in pts)
            formula = (f"{peak_mw:,.0f} MW day peak − {crit_total_kw / 1000:.1f} MW critical sectors "
                       f"= {desired / 1000:,.0f} MW, following the daily curve")
        else:
            desired, formula = loads[s["id"]]
            profile = ()
        min_kw = desired * v["critical_share"]
        cap_kwh = min_kw * v.get("backup_hours", 0)
        discharge = min_kw * v.get("backup_cover", 0)
        units = sector_units(city, s)
        harm = s["harm"]
        w = s["weight"]
        # Harm rates are set so that losing the WHOLE comfort band for one hour
        # costs units × harm.soft, and losing ALL critical load costs units ×
        # harm.critical. The engine multiplies by weight, so we divide it out.
        soft_rate = units * harm["soft"] / (w * max(1e-9, desired - min_kw)) / unit
        crit_rate = units * harm["critical"] / (w * min_kw) / unit
        agents.append(AgentConfig(
            id=s["id"], name=s["name"], kind=s["kind"], priority_rank=s["priority_rank"], weight=w,
            min_kw=round(min_kw, 3), desired_kw=round(desired, 3),
            reserve_capacity_kwh=round(cap_kwh, 3), reserve_initial_frac=s["reserve_initial_frac"],
            max_discharge_kw=round(discharge, 3), recharge_kw=round(cap_kwh / 4.0, 3),
            damage_soft=soft_rate, damage_critical=crit_rate,
            escalation=s["escalation"], escalation_cap_steps=24,
            reserve_label=s["reserve_label"], demand_profile=profile,
        ))
        table.append({
            "id": s["id"], "name": s["name"], "kind": s["kind"], "unit": s["unit"], "units": units,
            "desired_mw": round(desired / 1000, 2), "critical_mw": round(min_kw / 1000, 2),
            "backup_mwh": round(cap_kwh / 1000, 1), "formula": formula,
            "inputs": s["inputs"], "harm": harm, "reserve_label": s["reserve_label"],
        })
    return tuple(agents), table


# ---------------------------------------------------------------------------
# 2. Disaster -> available supply
# ---------------------------------------------------------------------------

def _envelope(disaster: dict, hour: float, restore_scale: float) -> float:
    """Fraction of the loss still out at `hour` (0 before onset).

    restore_scale > 1 means slower restoration: the recorded timeline after
    onset is stretched by that factor.
    """
    loss = disaster["loss"]
    onset = loss["onset_hour"]
    if hour < onset:
        return 0.0
    env = [tuple(p) for p in loss["envelope"]]
    t = onset + (hour - onset) / restore_scale
    return max(0.0, interp_profile(env, t))


def build_scenario(city_id: str, disaster_id: str, intensity: float = 1.0,
                   loss_scale: float = 1.0, restore_scale: float = 1.0) -> Scenario:
    city = load_city(city_id)
    dis = disaster_by_id(city, disaster_id)
    if not (INTENSITY_RANGE[0] <= intensity <= INTENSITY_RANGE[1]):
        raise ConfigError(f"intensity must be between {INTENSITY_RANGE[0]} and {INTENSITY_RANGE[1]}")
    demand_mw, peak = demand_curve(city, dis)
    agents, _ = build_agents(city, peak, demand_mw)
    margin = city["grid"]["supply_margin"]["value"]
    loss = dis["loss"]
    pts = []
    for q in range(0, HOURS * 4 + 1):
        h = q / 4
        dem = demand_mw(h)
        lost = (loss["mw"] if "mw" in loss else loss["share"] * dem) * intensity * loss_scale
        avail = max(0.0, dem * (1 + margin) - lost * _envelope(dis, h, restore_scale))
        pts.append((h, avail))
    base_mw = max(a for _, a in pts)
    profile = tuple((h, a / base_mw) for h, a in pts)
    deps = tuple(Dependency(d["source"], d["target"], d["impact"], d["label"]) for d in city["dependencies"])
    return Scenario(
        id=f"{city_id}:{disaster_id}", name=f"{city['name']}: {dis['name']}",
        description=dis["summary"], hours=HOURS, step_minutes=STEP_MINUTES,
        base_supply_kw=base_mw * 1000.0, noise_frac=city["grid"]["noise_frac"]["value"],
        profile=profile, agents=agents, dependencies=deps, scheduled=(), injectable=(),
    )


def disaster_by_id(city: dict, disaster_id: str) -> dict:
    for d in city["disasters"]:
        if d["id"] == disaster_id:
            return d
    raise ConfigError(f"unknown disaster '{disaster_id}'")


# ---------------------------------------------------------------------------
# 3. Results in human terms
# ---------------------------------------------------------------------------

def human_impact(city: dict, scenario: Scenario, records: List[dict]) -> dict:
    """Translate kW shortfalls into beds, residents, stations and households.

    Service is taken as proportional to power: a sector at 70% of its desired
    load serves 70% of its people. "Without critical service" counts the share
    of critical load that went unserved. Both are stated on the page.
    """
    dt = scenario.dt_hours
    units = {s["id"]: sector_units(city, s) for s in city["sectors"]}
    out = {s["id"]: {"reduced": 0.0, "critical": 0.0, "failed_h": 0.0, "peak_reduced": 0.0}
           for s in city["sectors"]}
    for r in records:
        for aid, a in r["agents"].items():
            des, mn, eff = a["desired_kw"], a["min_kw"], a["effective_kw"]
            short = max(0.0, 1.0 - eff / des) if des > 0 else 0.0
            crit = max(0.0, mn - eff) / mn if mn > 0 else 0.0
            o = out[aid]
            o["reduced"] += units[aid] * short * dt
            o["critical"] += units[aid] * crit * dt
            o["failed_h"] += dt if a["status"] == "FAILED" else 0.0
            o["peak_reduced"] = max(o["peak_reduced"], units[aid] * short)
    return {aid: {k: round(v, 1) for k, v in o.items()} for aid, o in out.items()}


KEY_METRICS = ("harm", "failure_events", "critical_failed_h", "homes_dark_hh_h",
               "hospital_crit_bed_h", "water_crit_res_h", "telecom_crit_res_h",
               "hospital_red_bed_h", "water_red_res_h", "telecom_red_res_h")


def _summary(city, scenario, records, unit: float) -> dict:
    m = compute(records, scenario)
    hi = human_impact(city, scenario, records)
    return {
        "harm": round(m["total_damage"] * unit, 1),
        "failure_events": m["failure_events"],
        "violations": m["capacity_violations"],
        "fallback_steps": m["fallback_steps"],
        "critical_failed_h": round(sum(v["failed_h"] for k, v in hi.items() if k != "homes"), 2),
        "homes_dark_hh_h": hi["homes"]["reduced"],
        "hospital_crit_bed_h": hi["hospital"]["critical"],
        "water_crit_res_h": hi["water"]["critical"],
        "telecom_crit_res_h": hi["telecom"]["critical"],
        "hospital_red_bed_h": hi["hospital"]["reduced"],
        "water_red_res_h": hi["water"]["reduced"],
        "telecom_red_res_h": hi["telecom"]["reduced"],
        "impact": hi,
        "metrics": m,
    }


def _percentiles(xs: Sequence[float]) -> dict:
    xs = sorted(xs)
    if len(xs) == 1:
        return {"p10": xs[0], "p50": xs[0], "p90": xs[0]}
    q = statistics.quantiles(xs, n=10, method="inclusive")
    return {"p10": round(q[0], 1), "p50": round(statistics.median(xs), 1), "p90": round(q[-1], 1)}


def _series(records: List[dict], ids: Sequence[str]) -> dict:
    """Compact per-step series for the charts (MW)."""
    return {
        "hour": [r["hour"] for r in records],
        "supply_mw": [round(r["supply_kw"] / 1000, 2) for r in records],
        "agents": {aid: {
            "alloc_mw": [round(r["agents"][aid]["alloc_kw"] / 1000, 3) for r in records],
            "served_mw": [round(r["agents"][aid]["served_kw"] / 1000, 3) for r in records],
            "effective_mw": [round(r["agents"][aid]["effective_kw"] / 1000, 3) for r in records],
            "desired_mw": [round(r["agents"][aid]["desired_kw"] / 1000, 3) for r in records],
            "min_mw": [round(r["agents"][aid]["min_kw"] / 1000, 3) for r in records],
            "status": [r["agents"][aid]["status"][0] for r in records],
            "reserve": [r["agents"][aid]["reserve_frac"] for r in records],
        } for aid in ids},
        "rounds": [r["rounds"] for r in records],
    }


def run_city(city_id: str, disaster_id: str, intensity: float = 1.0, seed: int = 41,
             members: Optional[int] = None) -> dict:
    """The recorded day under all four policies, plus an ensemble forecast."""
    city = load_city(city_id)
    dis = disaster_by_id(city, disaster_id)
    fc = city["forecast"]
    n = int(members if members is not None else fc["members"])
    sc = build_scenario(city_id, disaster_id, intensity)
    ids = [a.id for a in sc.agents]
    _, peak = demand_curve(city, dis)
    unit = harm_unit(city, peak)

    central = {}
    for p in POLICIES:
        recs = Simulation(sc, seed, p).run()
        s = _summary(city, sc, recs, unit)
        s["series"] = _series(recs, ids)
        s["label"] = POLICY_LABELS[p]
        central[p] = s

    # Ensemble: each member perturbs the uncertain inputs. The draws depend only
    # on (seed, member), so the forecast is reproducible like everything else.
    draws = []
    for k in range(n):
        rng = random.Random(f"{seed}:{disaster_id}:{k}")
        draws.append((rng.uniform(1 - fc["loss_spread"], 1 + fc["loss_spread"]),
                      rng.uniform(1 - fc["restore_spread"], 1 + fc["restore_spread"]),
                      seed * 1000 + k))
    ens: Dict[str, Dict[str, List[float]]] = {p: {m: [] for m in KEY_METRICS} for p in POLICIES}
    viol = 0
    for loss_s, rest_s, mseed in draws:
        msc = build_scenario(city_id, disaster_id, intensity, loss_s, rest_s)
        for p in POLICIES:
            s = _summary(city, msc, Simulation(msc, mseed, p).run(), unit)
            viol += s["violations"]
            for m in KEY_METRICS:
                ens[p][m].append(s[m])
    forecast = {p: {m: _percentiles(v) for m, v in ms.items()} for p, ms in ens.items()}
    wins = sum(1 for i in range(n)
               if ens["gridpact"]["harm"][i] <= min(ens[p]["harm"][i] for p in POLICIES if p != "gridpact"))

    demand_mw, peak = demand_curve(city, dis)
    return {
        "city": city_id, "disaster": disaster_id, "intensity": intensity, "seed": seed,
        "peak_mw": round(peak, 1),
        "demand_mw": [round(demand_mw(h), 1) for h in central["gridpact"]["series"]["hour"]],
        "policies": central,
        "forecast": forecast,
        "forecast_members": n,
        "forecast_wins": wins,
        "forecast_violations": viol,
    }


def city_info(city_id: str = "mumbai") -> dict:
    """Everything the page needs to describe the city, before any run."""
    city = load_city(city_id)
    map_path = CITY_DIR / f"{city_id}_map.json"
    first = sorted(city["disasters"], key=lambda d: d["order"])[0]
    demand_mw, peak = demand_curve(city, first)
    _, table = build_agents(city, peak, demand_mw)
    disasters = []
    for d in sorted(city["disasters"], key=lambda d: d["order"]):
        dm, pk = demand_curve(city, d)
        disasters.append({k: d[k] for k in ("id", "name", "date", "kind", "summary", "loss", "demand",
                                            "affected", "timeline", "record")} | {"peak_mw": round(pk, 1)})
    return {
        "id": city["id"], "name": city["name"], "full_name": city["full_name"],
        "location": city["location"], "area_km2": city["area_km2"], "accessed": city["accessed"],
        "population": city["population"], "zones": city["zones"], "grid": city["grid"],
        "sectors": table, "dependencies": city["dependencies"], "facilities": city["facilities"],
        "disasters": disasters, "forecast": city["forecast"], "sources": city["sources"],
        "map": json.loads(map_path.read_text()) if map_path.exists() else None,
        "intensity_range": INTENSITY_RANGE,
    }
