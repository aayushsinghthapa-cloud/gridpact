# One-minute demo script

**Before judges arrive:** run `python3 gridpact.py check` on the demo laptop (it should end with
`RESULT: READY`), then `python3 gridpact.py` (works offline). Leave Scenario = *Cyclone
landfall*, Seed = *41*, Speed = *10×*. Have the browser full-screen on the laptop.

Rehearsed numbers below come from a real run with Substation 2 injected at 06:30. If you click
at a slightly different time the numbers shift a little, but the results view re-runs every
policy with *your* exact clicks, so whatever it shows is real.

| Time | Do | Say |
|---|---|---|
| 0:00 | Point at the four cards. | "In a disaster, a city's hospital, water pumps, emergency services and telecom all fight over whatever power is left. In GridPact each one is an autonomous agent with its own private state: backup reserves, a damage curve that escalates, and fairness credits." |
| 0:08 | Press **Play** (Space). The supply crashes at 05:00. | "Every 15 simulated minutes they bid, concede and agree on a split. The white line is supply; the hatched band is what they asked for but can't all get. That's the shortfall they're negotiating." |
| 0:18 | At about **06:15** press **Pause**, then click **Substation 2 fails: −30% supply**, then **Step** (→). | "Let's make it worse, live." *(The card outlines ring; the log pins "Response to Substation 2 fails…")* "Hospital and Water have reserves, so they concede and run critical load on backup. Emergency and Telecom are below 30% reserve, so they hold firm. Agreement in 4 rounds." |
| 0:32 | Point at the guarantees strip. | "A neutral validator only checks physics: never more than supply, never more than asked. It never picks winners. Zero capacity violations, every step committed atomically, and the whole run is deterministic from the seed." |
| 0:40 | Press **Play**, let it finish, click **See results**. | "Same storm, same seed, same injected failure, run four ways." |
| 0:48 | Point at the headline and the bars. | "Lowest damage of all four: about 22% less than proportional, 76% less than strict priority, and zero failure events against 2 to 8 for the baselines. Strict priority starves telecom, and that cascades back into the hospital." |
| 0:58 | Stop. | "Every number here came from the run you just watched." |

## If a judge asks to dig deeper

- **"Show me the math"** → [ALGORITHM.md](ALGORITHM.md): the worked example of a real contested step, round by round.
- **"Does it scale?"** → switch Scenario to *Metro stress test (24 utilities)*, press Play.
- **"Can it break?"** → open a terminal and run `python3 gridpact.py test` (52 tests, ~3 s).
- **"What if an agent lies?"** → [RESULTS.md](RESULTS.md) "What if an agent lies?". Lying helps the liar, which we admit. It can never break capacity.
- Hard questions → [JUDGE_QA.md](JUDGE_QA.md).

## Fallbacks during the demo

- Browser didn't open: go to `http://127.0.0.1:8041`.
- Port in use: `python3 gridpact.py serve --port 9000`.
- Something odd mid-run: press **Restart**. Same seed, same run.
