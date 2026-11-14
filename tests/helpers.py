from gridpact.agent import UtilityAgent
from gridpact.models import AgentConfig, AgentState


def cfg(aid, min_kw=100, desired_kw=200, cap=400, weight=1.0, rank=1,
        discharge=100, recharge=50, strategy="honest", **kw):
    return AgentConfig(id=aid, name=aid.title(), kind=aid, priority_rank=rank, weight=weight,
                       min_kw=min_kw, desired_kw=desired_kw, reserve_capacity_kwh=cap,
                       reserve_initial_frac=1.0, max_discharge_kw=discharge, recharge_kw=recharge,
                       damage_soft=kw.get("soft", 1.0), damage_critical=kw.get("crit", 6.0),
                       escalation=kw.get("esc", 0.1), escalation_cap_steps=24, strategy=strategy)


def world(*specs):
    """specs: (config, reserve_frac[, extra AgentState kwargs])"""
    agents, states = {}, {}
    for spec in specs:
        c, frac = spec[0], spec[1]
        extra = spec[2] if len(spec) > 2 else {}
        agents[c.id] = UtilityAgent(c)
        states[c.id] = AgentState(reserve_kwh=c.reserve_capacity_kwh * frac, **extra)
    return agents, states
