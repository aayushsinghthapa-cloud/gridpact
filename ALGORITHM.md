# The GridPact negotiation algorithm

This document gives the exact math the code runs. Every symbol below maps to a
named constant or function, so you can check it against the source:
[gridpact/agent.py](gridpact/agent.py), [gridpact/negotiation.py](gridpact/negotiation.py),
[gridpact/validator.py](gridpact/validator.py), [gridpact/physics.py](gridpact/physics.py).
The worked example at the end comes from a real run, with nothing typed by hand.

## 0. Units and time

- One **step** = 15 simulated minutes, so Δt = 0.25 h. A 24 h scenario is 96 steps.
- Power on the wire (supply, requests, allocations) is in **integer watts**. That makes the
  capacity check `Σ allocations ≤ supply` exact. Configs and physics use kW and kWh.

## 1. What each agent knows

Private (only the agent itself reads it): reserve level R (kWh), fairness credits F,
shortfall streak k (consecutive steps served below 98% of desired), demand factor (events).

Public: supply S, every agent's announced request (min, max), urgency bid u, and the
current offers and floors during negotiation ([`PublicRound`](gridpact/agent.py)).

## 2. Request (per agent, start of step)

```
desired     D = desired_kw × demand_factor
critical    M = min_kw     × demand_factor
discharge   X = min(max_discharge_kw, R / Δt)              # what the backup can supply now
recharge    C = min(recharge_kw, (capacity − R) / Δt)      # 0 if the reserve is full

request.max = D + C                                         # rounded down to whole watts
request.min = max(0, M − X)                                 # rounded UP: the least grid power that avoids failure
```

## 3. Urgency bid

```
u = weight × h × (1 + e × min(k, cap)) × (1 + γ × (1 − R/capacity)) × (1 + β × F)
u = clamp(u, 0.01, 50)
```

The bid is meant to read as "the harm each kW cut from me does right now". That is why the
agent's own comfort-harm rate h (`damage_soft`, private config) is in it.

| Symbol | Meaning | Value |
|---|---|---|
| weight | how critical the utility is (config) | Hospital 1.0, Emergency 0.9, Water 0.75, Telecom 0.6 |
| h | the agent's own harm per kW of comfort shortfall (`damage_soft`) | 1.0 for every core utility (so it drops out); Mumbai sectors differ by ~30×, see [CITY.md](CITY.md) |
| e | escalation per consecutive short step | 0.08 – 0.10 (config) |
| γ | reserve-scarcity weight `GAMMA_RESERVE` | 2.0 (an empty reserve triples the bid) |
| β | credit weight `BETA_CREDITS` | 0.10 per credit |
| 50 | `URGENCY_CAP` | bounds what a lying agent can claim |

## 4. Uncontested step

If `Σ request.max ≤ S`, every agent gets its `request.max`. That takes 0 rounds.

## 5. Contested step: rounds

**Floors.** Each agent picks the lowest offer it will accept in each phase:

```
phase 1:  floor = request.min          if R/capacity ≥ 0.30   (willing to run critical load on backup)
          floor = M                    if R/capacity < 0.30   (HOLDS FIRM: protects its last reserve)
phase 2:  floor = request.min          for everyone           (only reached if phase 1 deadlocks)
greedy agents: floor = request.max (never concede)
```

**One round.** Let E = Σ offers − S (the excess still to remove) and W = the set of agents whose
offer is still above their floor. Every agent i in W *independently* computes:

```
slack_j    = offer_j − floor_j                  # public: offers and floors are announced
share_i    = (slack_i/u_i) / Σ_{j∈W} (slack_j/u_j)   # less urgent → gives a bigger FRACTION of its slack
target_i   = ceil(share_i × E)
increment  = ceil(max(1 kW, 0.25 × phase_slack_i))   # phase_slack = offer at phase start − floor
c_i        = min(target_i, increment, offer_i − floor_i)
offer_i   ← offer_i − c_i
```

Shares are weighted by slack so a concession is a *fraction of what you can give*, not an absolute
amount: a 37 MW hospital is never asked to give as many watts as a 3,000 MW residential load just
because their bids are similar (DECISIONS.md D-17).

All agents decide from the same public snapshot, then all concessions apply at once. Since the
shares add up to 1, the concessions never overshoot by more than 1 W per agent (from the `ceil`).
Overshooting only leaves power unused, which is the safe direction.

**Stop conditions**

| Condition | What happens |
|---|---|
| E ≤ 0 | **agreement**: allocation = final offers |
| W empty in phase 1 | **deadlock** → phase 2 (everyone may dig into reserves) |
| W empty in phase 2 | **deadlock**: hard floors exceed supply → fallback |
| 16 rounds used (`MAX_ROUNDS`) | **round cap** → fallback |

## 6. Fairness credits (relative ledger)

After the rounds, using only public numbers:

```
share_i  = conceded_i / desired_kw_i
Δcredits = 10 × (share_i − mean_j share_j)        # CREDIT_RATE = 10
F_i     ← clamp(F_i + Δcredits, 0, 20)            # CREDIT_CAP = 20
```

An agent that gave up a larger slice of its load than average earns credits, and its future bids
rise by 10% per credit. An agent that gave up less than average pays credits back. The ledger is
**relative** on purpose. An earlier version paid absolute credits, and every agent hit the cap
within a few hours of a crisis, so credits stopped meaning anything. See DECISIONS.md D-9.

Starvation is prevented by two growing terms. A utility that keeps losing power gains urgency
from (1 + e·k), and one that keeps giving way gains it from (1 + βF). Both shrink its share of
future concessions over time.

## 7. Deterministic fallback (`safe_fallback`)

Used when there is a deadlock, the round cap is hit, or the validator rejects a proposal:

1. Order agents by announced bid, highest first. Ties go to the lower agent id.
2. In that order, give each agent its whole `request.min` **if it fits** in the remaining supply;
   otherwise skip it. A partial floor keeps nobody alive, so it would only waste power (D-18).
3. In the same order, top each agent up from what remains: toward its last offer after a round
   cap (those offers were converging), or toward `request.max` after a deadlock (concessions made
   for an agreement that cannot exist are void, D-18).

Every grant is clipped to [0, request.max] and taken from `remaining`, which starts at S and
never goes negative. So the fallback satisfies the validator by construction (fuzz-tested over
3,000 random cases in `tests/test_invariants.py`). If the fallback were somehow rejected
anyway, the engine allocates all zeros, which is always valid. If even that fails, the step is
rolled back.

## 8. Validator (physics only)

| Check | Rule |
|---|---|
| C1 | the set of agents equals the set of requesters, with no extras and none missing |
| C2 | every allocation is an `int` (rejects floats, NaN and booleans) |
| C3 | 0 ≤ a_i ≤ request_i.max |
| C4 | Σ a_i ≤ S (integers, exact) |

The validator never sees urgency, damage or priority, so it cannot choose who gets power. It
can only say "physically allowed" or "not allowed".

## 9. Physics after the deal

```
to_load    = min(grid, D);  surplus = grid − to_load
charge     = min(surplus, recharge_kw) × Δt                  (capped at capacity)
discharge  = min(D − to_load, X, target − to_load)            target = D if R/cap ≥ 0.5 else M
served     = to_load + discharge
effective  = served − Σ_{failed sources s → this agent} impact_s × D     (cascades, iterated to a fixed point)
FAILED     if effective < M
damage     = weight × (soft × max(0, D − max(eff, M)) + critical × max(0, M − eff))
             × (1 + e × min(k_before, cap)) × Δt
```

Cascades iterate until nothing changes (at most n + 1 passes). The set of failed agents can only
grow from one pass to the next, so the loop terminates even if the dependency graph has a cycle.

## 10. Atomic commit

Steps 1–9 build **new** frozen objects. The engine runs `check_state_invariants` on the candidate
state, then commits with a single assignment, `self.state = candidate`. Any exception before that
line leaves the old state untouched. `tests/test_atomicity.py` crashes the step at all six
internal stages and checks that the state object is identical afterwards.

---

## Worked example: one real contested step

<!-- WORKED-EXAMPLE:START -->
*Generated by `scripts/worked_example.py` from a real run: Cyclone landfall, seed 41, step 38 (clock 09:30, 9.5 h into the scenario). Re-run the script to regenerate; do not edit by hand.*

**Step 1. Grid announces supply:** S = **486.902 kW** (= 486,902 W, the integer the validator checks).
Active events: Storm casualties arrive: Hospital load +20%.

**Step 2. Each agent computes its request and bid from its own private state.**

| Agent | Demand factor | Desired | Critical min | Reserve | Discharge available | Recharge want | Request max | Request min (hard floor) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Hospital | ×1.20 | 540.0 kW | 360.0 kW | 408.6 kWh (34.1%) | 250.0 kW | 150.0 kW | 690.000 kW | 110.000 kW |
| Emergency Services | ×1.00 | 200.0 kW | 120.0 kW | 68.0 kWh (28.3%) | 100.0 kW | 60.0 kW | 260.000 kW | 20.000 kW |
| Water Pumping | ×1.00 | 360.0 kW | 200.0 kW | 277.9 kWh (34.7%) | 160.0 kW | 120.0 kW | 480.000 kW | 40.000 kW |
| Telecom | ×1.00 | 150.0 kW | 80.0 kW | 96.9 kWh (30.3%) | 80.0 kW | 60.0 kW | 210.000 kW | 0.000 kW |

Request max = desired + recharge want. Request min = max(0, critical min − discharge available), rounded **up** to whole watts.

Urgency bid u = weight × harm rate (1.0 for every core utility) × (1 + e × streak) × (1 + γ × (1 − reserve fraction)) × (1 + β × credits), with γ = 2.0, β = 0.1:

| Agent | weight | streak (e) | escalation | reserve fraction | scarcity | credits | credit factor | **bid u** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Hospital | 1.0 | 10 (0.1) | 2.00 | 0.3405 | 2.3189 | 6.607 | 1.6607 | **7.7021** |
| Emergency Services | 0.9 | 11 (0.1) | 2.10 | 0.2833 | 2.4333 | 0.000 | 1.0000 | **4.5990** |
| Water Pumping | 0.75 | 14 (0.08) | 2.12 | 0.3474 | 2.3052 | 4.103 | 1.4103 | **5.1689** |
| Telecom | 0.6 | 10 (0.08) | 1.80 | 0.3027 | 2.3946 | 20.000 | 3.0000 | **7.7586** |

**Step 3. Contested?** Total requested = 1640.000 kW > supply 486.902 kW, so the excess to remove is **1153.098 kW**. Negotiation opens.

**Step 4. Rounds.** Each agent's phase-1 floor: its hard floor if reserve ≥ 30%, otherwise its full critical min (it *holds firm*). In each round every agent that can still concede takes the share (slack_i/u_i) / Σ(slack_j/u_j) of the remaining excess, capped at 25% of its phase slack and at its floor. All agents decide from the same public snapshot.

- Emergency Services holds firm at 120.0 kW: reserve only 28%, near failure.

| Round | Hospital | Emergency Services | Water Pumping | Telecom | Σ offers | Excess after |
|---:|---:|---:|---:|---:|---:|---:|
| start | 690.0 | 260.0 | 480.0 | 210.0 | 1640.0 | 1153.1 |
| R1 | 545.0 (−145.0) | 225.0 (−35.0) | 370.0 (−110.0) | 157.5 (−52.5) | 1297.5 | 810.6 |
| R2 | 400.0 (−145.0) | 190.0 (−35.0) | 260.0 (−110.0) | 105.0 (−52.5) | 955.0 | 468.1 |
| R3 | 255.0 (−145.0) | 155.0 (−35.0) | 150.0 (−110.0) | 52.5 (−52.5) | 612.5 | 125.6 |
| R4 | 211.6 (−43.4) | 137.5 (−17.5) | 100.9 (−49.1) | 36.9 (−15.6) | 486.9 | 0.0 |

Result: Agreement after 4 round(s): 486.9 kW allocated of 486.9 kW.

**Fairness ledger** (share = conceded ÷ desired; Δcredits = 10 × (share − average share)):

- Hospital conceded 478.4 kW in total (106% of its load vs 97% average), reserve 34% → +0.9 fairness credits
- Emergency Services conceded 122.5 kW in total (61% of its load vs 97% average), reserve 28% → -3.6 fairness credits
- Water Pumping conceded 379.1 kW in total (105% of its load vs 97% average), reserve 35% → +0.8 fairness credits
- Telecom conceded 173.1 kW in total (115% of its load vs 97% average), reserve 30% → +1.8 fairness credits

**Step 5. Validator** (integer watts):

- ✔ C1 agent set: ok
- ✔ C2 integer watts: ok
- ✔ C3 0 <= a_i <= request_i: ok
- ✔ C4 sum <= supply: 486900 W <= 486902 W

Committed atomically: the engine swapped in the new state for step 39.

**Step 6. Physics after the deal** (load first, then reserve covers the gap per the agent's discharge policy):

| Agent | Allocated | Reserve discharge | Served | Status | Reserve after | Credits after | Damage this step |
|---|---:|---:|---:|---|---:|---:|---:|
| Hospital | 211.601 kW | 148.399 kW | 360.000 kW | STRESSED | 31.0% | 7.531 | 90.000 |
| Emergency Services | 137.456 kW | 0.000 kW | 137.456 kW | STRESSED | 28.3% | 0.000 | 29.552 |
| Water Pumping | 100.942 kW | 99.058 kW | 200.000 kW | STRESSED | 31.6% | 4.925 | 63.600 |
| Telecom | 36.901 kW | 43.099 kW | 80.000 kW | STRESSED | 26.9% | 20.000 | 18.900 |

Step damage 202.052; cumulative 1380.740.
<!-- WORKED-EXAMPLE:END -->
