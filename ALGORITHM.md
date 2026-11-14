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
u = weight × (1 + e × min(k, cap)) × (1 + γ × (1 − R/capacity)) × (1 + β × F)
u = clamp(u, 0.01, 50)
```

| Symbol | Meaning | Value |
|---|---|---|
| weight | how critical the utility is (config) | Hospital 1.0, Emergency 0.9, Water 0.75, Telecom 0.6 |
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
share_i    = (1/u_i) / Σ_{j∈W} (1/u_j)          # the least urgent gives the most
target_i   = ceil(share_i × E)
increment  = ceil(max(1 kW, 0.25 × phase_slack_i))   # phase_slack = offer at phase start − floor
c_i        = min(target_i, increment, offer_i − floor_i)
offer_i   ← offer_i − c_i
```

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
from (1 + e·k), and one that keeps giving way gains it from (1 + βF). Both reverse the
1/u ordering over time.

## 7. Deterministic fallback (`safe_fallback`)

Used when there is a deadlock, the round cap is hit, or the validator rejects a proposal:

1. Order agents by announced bid, highest first. Ties go to the lower agent id.
2. In that order, give each agent `min(request.min, remaining supply)`. This keeps the most urgent alive first.
3. In the same order, top each agent up toward its last offer (or `request.max`) from what remains.

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
| Hospital | ×1.20 | 540.0 kW | 360.0 kW | 439.6 kWh (36.6%) | 250.0 kW | 150.0 kW | 690.000 kW | 110.000 kW |
| Emergency Services | ×1.00 | 200.0 kW | 120.0 kW | 62.9 kWh (26.2%) | 100.0 kW | 60.0 kW | 260.000 kW | 20.000 kW |
| Water Pumping | ×1.00 | 360.0 kW | 200.0 kW | 270.7 kWh (33.8%) | 160.0 kW | 120.0 kW | 480.000 kW | 40.000 kW |
| Telecom | ×1.00 | 150.0 kW | 80.0 kW | 91.5 kWh (28.6%) | 80.0 kW | 60.0 kW | 210.000 kW | 0.000 kW |

Request max = desired + recharge want. Request min = max(0, critical min − discharge available), rounded **up** to whole watts.

Urgency bid u = weight × (1 + e × streak) × (1 + γ × (1 − reserve fraction)) × (1 + β × credits), with γ = 2.0, β = 0.1:

| Agent | weight | streak (e) | escalation | reserve fraction | scarcity | credits | credit factor | **bid u** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Hospital | 1.0 | 10 (0.1) | 2.00 | 0.3663 | 2.2673 | 3.259 | 1.3259 | **6.0125** |
| Emergency Services | 0.9 | 14 (0.1) | 2.40 | 0.2621 | 2.4758 | 0.000 | 1.0000 | **5.3476** |
| Water Pumping | 0.75 | 14 (0.08) | 2.12 | 0.3384 | 2.3233 | 4.588 | 1.4588 | **5.3888** |
| Telecom | 0.6 | 14 (0.08) | 2.12 | 0.2860 | 2.4279 | 19.212 | 2.9212 | **9.0215** |

**Step 3. Contested?** Total requested = 1640.000 kW > supply 486.902 kW, so the excess to remove is **1153.098 kW**. Negotiation opens.

**Step 4. Rounds.** Each agent's phase-1 floor: its hard floor if reserve ≥ 30%, otherwise its full critical min (it *holds firm*). In each round every agent that can still concede takes the share (1/u_i) / Σ(1/u_j) of the remaining excess, capped at 25% of its phase slack and at its floor. All agents decide from the same public snapshot.

- Emergency Services holds firm at 120.0 kW: reserve only 26%, near failure.
- Telecom holds firm at 80.0 kW: reserve only 29%, near failure.

| Round | Hospital | Emergency Services | Water Pumping | Telecom | Σ offers | Excess after |
|---:|---:|---:|---:|---:|---:|---:|
| start | 690.0 | 260.0 | 480.0 | 210.0 | 1640.0 | 1153.1 |
| R1 | 545.0 (−145.0) | 225.0 (−35.0) | 370.0 (−110.0) | 177.5 (−32.5) | 1317.5 | 830.6 |
| R2 | 400.0 (−145.0) | 190.0 (−35.0) | 260.0 (−110.0) | 145.0 (−32.5) | 995.0 | 508.1 |
| R3 | 269.9 (−130.1) | 155.0 (−35.0) | 150.0 (−110.0) | 112.5 (−32.5) | 687.4 | 200.5 |
| R4 | 218.6 (−51.3) | 120.0 (−35.0) | 92.7 (−57.3) | 80.0 (−32.5) | 511.3 | 24.4 |
| R5 | 207.1 (−11.5) | 120.0 | 79.8 (−12.9) | 80.0 | 486.9 | 0.0 |

Result: Agreement after 5 round(s): 486.9 kW allocated of 486.9 kW.

**Fairness ledger** (share = conceded ÷ desired; Δcredits = 10 × (share − average share)):

- Hospital conceded 482.9 kW in total (107% of its load vs 94% average), reserve 37% → +1.4 fairness credits
- Emergency Services conceded 140.0 kW in total (70% of its load vs 94% average), reserve 26% → -2.4 fairness credits
- Water Pumping conceded 400.2 kW in total (111% of its load vs 94% average), reserve 34% → +1.7 fairness credits
- Telecom conceded 130.0 kW in total (87% of its load vs 94% average), reserve 29% → -0.7 fairness credits

**Step 5. Validator** (integer watts):

- ✔ C1 agent set: ok
- ✔ C2 integer watts: ok
- ✔ C3 0 <= a_i <= request_i: ok
- ✔ C4 sum <= supply: 486901 W <= 486902 W

Committed atomically: the engine swapped in the new state for step 39.

**Step 6. Physics after the deal** (load first, then reserve covers the gap per the agent's discharge policy):

| Agent | Allocated | Reserve discharge | Served | Status | Reserve after | Credits after | Damage this step |
|---|---:|---:|---:|---|---:|---:|---:|
| Hospital | 207.058 kW | 152.942 kW | 360.000 kW | STRESSED | 33.5% | 4.613 | 90.000 |
| Emergency Services | 120.000 kW | 0.000 kW | 120.000 kW | STRESSED | 26.2% | 0.000 | 43.200 |
| Water Pumping | 79.843 kW | 120.157 kW | 200.000 kW | STRESSED | 30.1% | 6.325 | 63.600 |
| Telecom | 80.000 kW | 0.000 kW | 80.000 kW | STRESSED | 28.6% | 18.500 | 22.260 |

Step damage 219.060; cumulative 1483.009.
<!-- WORKED-EXAMPLE:END -->
