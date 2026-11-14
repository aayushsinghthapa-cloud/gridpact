# GridPact on Mumbai: real data

The four core scenarios use hand-set loads in kW. This page asks the obvious follow-up: **does
the protocol still work on a real city, with real numbers?** Open it at
`http://127.0.0.1:8041/city.html` (the "Mumbai, real data" link in the dashboard header).

The page runs the *same* engine, validator, physics and baselines. Only the inputs change. They
are built from [data/cities/mumbai.json](data/cities/mumbai.json), where every number is tagged:

| Tag | Meaning |
|---|---|
| **measured** | a published figure, with its source |
| **derived** | arithmetic on published figures; the page prints the formula |
| **assumption** | our modelling choice, stated openly and editable in the data file |

## The city

| | Value | Source |
|---|---|---|
| Population | 12,442,373 (24 wards, ward by ward) | MCGM Census 2011 table |
| Households | 2,779,943 | MCGM Census 2011 table |
| Ward boundaries | 24 BMC wards, simplified to ~25 m | DataMeet Municipal_Spatial_Data |
| Record demand | 4,129 MW, Fri 9 Jun 2023, 15:30–15:45 | Free Press Journal |
| Daily trough and peak | 2,140 MW at 05:00, 3,944 MW at 16:00 (Oct 2024) | Maharashtra SLDC (read via search index; provisional) |
| Suburban licensee's share | Adani 2,070 of 4,041 MW | Mumbai Live |

## The five agents

| Agent | Normal load | How it's built |
|---|---:|---|
| Hospitals | 36.8 MW | 17,190 public beds × 2,753 kWh/yr (NCDC survey, upper end) + 31,000 private beds × 8,860 kWh/yr (500-bed private hospital) ÷ 8,760 h |
| Water pumping | 50 MW | BMC pumping stations, combined demand (Mid-day, 2024) |
| Mobile network | 14.8 MW | 3,705 tower sites (BMC count, 2013; lower bound) × 4 kW (TRAI) |
| Fire and police | 4.2 MW | 34 fire stations × 40 kW + 94 police stations × 30 kW (per-station loads assumed) |
| Homes and businesses | the rest | city demand − the four sectors above, following the daily curve |

The critical share of each load, backup hours and harm weights are **assumptions**, listed on the
page next to each sector. The critical sectors together are **106 MW, 2.6% of the record peak.**
That one fact shapes everything below.

## The disasters

| Disaster | Kind | What is recorded | What we assumed |
|---|---|---|---|
| Grid islanding failure, 12 Oct 2020 | replay | 2,200 MW lost in Mumbai at 10:05 with over 2,800 MW of demand; trip sequence from the POSOCO flash report; restoration from noon, 2–15 h | the 85%-restored-by-14:00 shape |
| Monsoon deluge, 26 Jul 2005 | recorded pattern on today's grid | 944 mm in 24 h; suburban licensee shut feeders on a state safety directive (over 150 substations) | shutdown at 19:00 ("from the evening"); today's Adani share as the area switched off |
| Cyclone Tauktae, 17 May 2021 | recorded storm | 102 km/h at Colaba by 11:00, 114 km/h record gust around 14:00, Adani feeders tripped, 2,426 streetlights out | **the size of the loss (12%)**: we found no published MW figure |
| 2020 failure at the record peak | forecast | 2020's loss and restoration pace; the 2023 record peak | that the 2020 trip sequence repeats at 15:30 |

**Forecast ensemble.** The loss size and restoration time are uncertain even for recorded events.
Each run therefore simulates 24 more versions of the day: the loss drawn within ±15% and the
restoration time within ±30%, each with its own supply noise. Results are reported as the median
and the 10th–90th percentile range. The draws depend only on the seed, so forecasts are
reproducible.

## Harm, in one unit

Every shortfall is converted to **household-hours without power**. Losing the whole comfort
band of a sector for an hour costs `units × harm.soft`, and losing all of its critical load costs
`units × harm.critical`. The units are beds, residents, stations or households. The weights are
value judgements and are listed on the page. The agents' damage rates are expressed in this unit,
so the harm-per-kW term in every bid compares like with like (DECISIONS.md D-17).

## What running it on real data taught us

The first run was a surprise: **GridPact did 2–4× more harm than strict priority on every Mumbai
disaster.** There were zero violations and zero failures; it was simply making the wrong trade.
It pushed hospitals, water and telecom onto diesel, reservoirs and batteries to free a sliver of
power for 3,000 MW of homes. The cause was two biases that the hand-set scenarios could not show,
because there all agents are similar in size and harm rate:

1. The bid ignored *how much harm a kW does*. A kW cut from water pumping does ~10× the harm of a
   kW cut from homes.
2. Concessions were shares of the excess *in absolute watts*. Next to a 3,000 MW load, a 37 MW
   hospital's share exceeded its whole load.

A sweep over intensity then found a third problem. At 1.25× the 2020 loss, only 137 MW remains.
Homes need 424 MW just to avoid failing, so the negotiation deadlocks, and the fallback handed
all 137 MW to homes, which failed anyway, while hospitals, water and telecom got nothing:

3. The deadlock fallback granted *partial* survival floors and kept concessions that had been
   offered for an agreement that could not exist.

The fixes: the bid includes the agent's own harm rate, and each round's concession is a fraction
of the agent's slack. The fallback grants only whole floors and voids deadlocked concessions
(D-18). The bid and concession fixes use only the agent's own state plus public
numbers. All three are now part of the protocol (DECISIONS.md D-17 and D-18, ALGORITHM.md §3, §5
and §7). It costs 1–4 points of GridPact's
advantage in the core scenarios, where it still wins 10/10 seeds with zero failures.

**The honest result on Mumbai:** when critical services are under 3% of the load, any policy that
protects them first is close to optimal. The shortfall then falls on homes, and no allocation can
avoid that. GridPact matches the best baseline and beats proportional sharing where proportional
starves water pumping and the mobile network (12 Oct 2020). GridPact's distinctive advantage,
which is 70–76% less damage than strict priority in the core scenarios, appears when the critical
utilities themselves compete for too little power.

## Results

<!-- CITY-RESULTS:START -->

*Generated by `scripts/generate_city_results.py` (seed 41, 24 forecast versions per disaster, Python 3.14.4). Harm is in household-hours without power, equivalent.*

### Recorded inputs (intensity 1.0×)

| Disaster | GridPact harm | Strict priority harm | Proportional harm | Equal split harm | GridPact vs best baseline | GridPact vs proportional | Lowest-harm forecast versions | Capacity violations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Grid islanding failure | 10.67 M | 10.63 M | 12.10 M | 10.63 M | +0.4% | -11.8% | 6/24 | 0 |
| Monsoon deluge | 9.58 M | 9.59 M | 9.84 M | 9.59 M | -0.1% | -2.6% | 11/24 | 0 |
| Cyclone Tauktae | 3.34 M | 3.34 M | 3.34 M | 3.34 M | +0.0% | +0.0% | 1/24 | 0 |
| Forecast: 2020 failure at the record peak | 8.35 M | 8.34 M | 8.38 M | 8.34 M | +0.1% | -0.4% | 0/24 | 0 |

**0 capacity violations in 400 runs.**

### Forecast detail (median of the ensemble)

#### Grid islanding failure

| Policy | Harm, median (p10–p90) | Homes dark, household-h | Water reduced, resident-h | Hospital beds on reduced power, bed-h | Mobile degraded, resident-h | Critical failures |
|---|---:|---:|---:|---:|---:|---:|
| GridPact | 10.44 M (7.01 M–13.60 M) | 7.28 M | 0.00 M | 5,326 | 0.00 M | 0 |
| Strict priority | 10.52 M (6.98 M–13.75 M) | 7.29 M | 0.00 M | 0 | 0.00 M | 0 |
| Proportional | 11.85 M (7.29 M–16.00 M) | 7.23 M | 6.75 M | 9,628 | 3.38 M | 0 |
| Equal split | 10.52 M (6.98 M–13.75 M) | 7.29 M | 0.00 M | 0 | 0.00 M | 0 |

#### Monsoon deluge

| Policy | Harm, median (p10–p90) | Homes dark, household-h | Water reduced, resident-h | Hospital beds on reduced power, bed-h | Mobile degraded, resident-h | Critical failures |
|---|---:|---:|---:|---:|---:|---:|
| GridPact | 9.09 M (8.31 M–10.92 M) | 6.63 M | 0.00 M | 0 | 0.00 M | 0 |
| Strict priority | 9.09 M (8.31 M–10.93 M) | 6.64 M | 0.00 M | 0 | 0.00 M | 0 |
| Proportional | 9.07 M (8.25 M–12.26 M) | 6.57 M | 0.69 M | 740 | 0.00 M | 0 |
| Equal split | 9.09 M (8.31 M–10.93 M) | 6.64 M | 0.00 M | 0 | 0.00 M | 0 |

#### Cyclone Tauktae

| Policy | Harm, median (p10–p90) | Homes dark, household-h | Water reduced, resident-h | Hospital beds on reduced power, bed-h | Mobile degraded, resident-h | Critical failures |
|---|---:|---:|---:|---:|---:|---:|
| GridPact | 3.41 M (2.44 M–4.82 M) | 1.79 M | 0.00 M | 0 | 0.00 M | 0 |
| Strict priority | 3.41 M (2.44 M–4.82 M) | 1.79 M | 0.00 M | 0 | 0.00 M | 0 |
| Proportional | 3.47 M (2.44 M–4.82 M) | 1.79 M | 0.00 M | 0 | 0.00 M | 0 |
| Equal split | 3.41 M (2.44 M–4.82 M) | 1.79 M | 0.00 M | 0 | 0.00 M | 0 |

#### Forecast: 2020 failure at the record peak

| Policy | Harm, median (p10–p90) | Homes dark, household-h | Water reduced, resident-h | Hospital beds on reduced power, bed-h | Mobile degraded, resident-h | Critical failures |
|---|---:|---:|---:|---:|---:|---:|
| GridPact | 8.33 M (6.16 M–11.07 M) | 5.53 M | 0.00 M | 0 | 0.00 M | 0 |
| Strict priority | 8.33 M (6.16 M–10.92 M) | 5.53 M | 0.00 M | 0 | 0.00 M | 0 |
| Proportional | 8.36 M (6.21 M–11.32 M) | 5.53 M | 0.04 M | 1,364 | 0.00 M | 0 |
| Equal split | 8.33 M (6.16 M–10.92 M) | 5.53 M | 0.00 M | 0 | 0.00 M | 0 |

### Sensitivity to intensity (12 Oct 2020 replay)

| Intensity | Supply left at 10:00 | GridPact vs best baseline | GridPact vs proportional | Homes dark (GridPact) |
|---:|---:|---:|---:|---:|
| 0.50× | 1,820 MW | +0.0% | +0.0% | 3.40 M |
| 0.75× | 1,259 MW | +0.1% | -0.4% | 5.42 M |
| 1.00× | 698 MW | +0.4% | -11.8% | 7.50 M |
| 1.25× | 137 MW | +0.1% | -10.5% | 9.65 M |
| 1.50× | 0 MW | -0.1% | -4.8% | 11.04 M |

*Generated in 4.0 s.*

<!-- CITY-RESULTS:END -->

## Limits

- Supply is one pooled number for the city: no feeders or substations. The map spreads the homes
  shortfall evenly over the wards the historic reports name as affected.
- Census 2011 is the latest census; today's population is larger, so people-counts are conservative.
- The tower count is from 2012–13, so the mobile network's load is a lower bound.
- Service is assumed proportional to power delivered.
- The 2005 and 2021 days replay recorded patterns on today's demand; they do not reconstruct those days' grids.
- Hourly demand between the published daily minimum and maximum is interpolated.

## Rebuilding the data

- Ward map: `python3 scripts/build_city_map.py BMC_Wards.geojson` (DataMeet file) writes `data/cities/mumbai_map.json`.
- Results block above: `python3 scripts/generate_city_results.py` (also run by `python3 gridpact.py results`).
- Every source, with its URL: the "Sources" section at the bottom of the page, or `sources` in the data file.
