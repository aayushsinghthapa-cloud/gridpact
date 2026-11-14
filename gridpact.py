#!/usr/bin/env python3
"""GridPact: one command to run everything. Python 3.9+ standard library only.

    python3 gridpact.py              start the dashboard at http://127.0.0.1:8041
    python3 gridpact.py test         run every test (the hard guarantees)
    python3 gridpact.py run SCENARIO [--seed N] [--policy P]   headless run, prints metrics
    python3 gridpact.py results      regenerate RESULTS.md and CITY.md from real runs
    python3 gridpact.py check        pre-flight report for the demo laptop (share it with your team)
"""
import argparse
import sys
import webbrowser


def main(argv=None):
    # Work from the project folder, so `python3 ~/somewhere/gridpact.py test` also finds tests/ and web/.
    import os
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(prog="gridpact", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="start the dashboard (default)")
    s.add_argument("--port", type=int, default=8041)
    s.add_argument("--no-browser", action="store_true")
    sub.add_parser("test", help="run all tests")
    r = sub.add_parser("run", help="run one scenario headless and print metrics")
    r.add_argument("scenario")
    r.add_argument("--seed", type=int, default=41)
    r.add_argument("--policy", default="all")
    sub.add_parser("results", help="regenerate RESULTS.md")
    sub.add_parser("check", help="pre-flight check of this machine; prints a report to share")
    args = ap.parse_args(argv)

    if args.cmd in (None, "serve"):
        from gridpact.server import serve
        port = getattr(args, "port", 8041)
        if not getattr(args, "no_browser", False):
            webbrowser.open(f"http://127.0.0.1:{port}")
        serve(port=port)
    elif args.cmd == "test":
        import unittest
        suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
        ok = unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful()
        sys.exit(0 if ok else 1)
    elif args.cmd == "run":
        from gridpact.config import load_scenario
        from gridpact.metrics import compare
        sc = load_scenario(args.scenario)
        res = compare(sc, args.seed, with_series=False)
        keys = ["total_damage", "failure_events", "longest_outage_h", "jain_fairness",
                "capacity_violations", "rounds_mean", "rounds_max", "fallback_steps", "compute_ms_mean"]
        print(f"{sc.name}  ({len(sc.agents)} agents, {sc.hours:g} h, seed {args.seed})")
        print(f"{'policy':24s}" + "".join(f"{k:>20s}" for k in keys))
        for p, m in res["policies"].items():
            if args.policy in ("all", p):
                print(f"{m['label']:24s}" + "".join(f"{m[k]:>20}" for k in keys))
        for p, d in res["damage_change_pct"].items():
            print(f"GridPact damage vs {res['policies'][p]['label']}: {d:+.1f}%")
    elif args.cmd == "check":
        sys.exit(preflight())
    elif args.cmd == "results":
        from scripts.generate_results import main as gen
        gen()
        from scripts.generate_city_results import main as gen_city
        gen_city()


def preflight() -> int:
    """Everything that can go wrong at the venue, checked in ~5 seconds.

    Prints a report meant to be pasted back to whoever is helping you. It never
    prints secrets: for the API key it only says whether one is set.
    """
    import importlib.util
    import io
    import os
    import platform
    import socket
    import subprocess
    import time
    import unittest

    lines, ok = [], True

    def add(name, passed, detail=""):
        nonlocal ok
        ok = ok and passed is not False
        mark = "OK  " if passed is True else ("FAIL" if passed is False else "INFO")
        lines.append(f"[{mark}] {name}{': ' + detail if detail else ''}")

    add("Python", sys.version_info >= (3, 9), platform.python_version() + " on " + platform.platform(terse=True))
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5).stdout.strip()
        add("Code version", None, (rev or "not a git checkout") + (" (with local changes)" if dirty else ""))
    except (OSError, subprocess.SubprocessError):
        add("Code version", None, "git not available")

    t0 = time.perf_counter()
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
    res = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    add("Tests", res.wasSuccessful(), f"{res.testsRun} run, {len(res.failures)} failed, {len(res.errors)} errors "
                                       f"in {time.perf_counter() - t0:.1f} s")
    for case, tb in (res.failures + res.errors)[:3]:
        lines.append(f"       {case.id()}: {tb.strip().splitlines()[-1][:160]}")

    try:
        from gridpact.config import list_scenarios, load_scenario
        from gridpact.metrics import compare
        for s in list_scenarios():
            r = compare(load_scenario(s["id"]), 41, with_series=False)
            g = r["policies"]["gridpact"]
            viol = sum(m["capacity_violations"] for m in r["policies"].values())
            add(f"Scenario {s['id']}", viol == 0,
                f"GridPact damage {g['total_damage']:,.0f}, failures {g['failure_events']}, "
                f"vs proportional {r['damage_change_pct']['proportional']:+.1f}%, violations {viol}, "
                f"{g['compute_ms_mean']:.2f} ms/step")
    except Exception as e:  # noqa: BLE001
        add("Scenarios", False, f"{type(e).__name__}: {e}")

    try:
        from gridpact import city
        for d in city.load_city("mumbai")["disasters"]:
            r = city.run_city("mumbai", d["id"], 1.0, 41, members=2)
            P = r["policies"]
            viol = sum(p["violations"] for p in P.values()) + r["forecast_violations"]
            best = min(P[p]["harm"] for p in P if p != "gridpact")
            add(f"Mumbai {d['id']}", viol == 0,
                f"GridPact harm {P['gridpact']['harm'] / 1e6:.2f} M household-h, "
                f"vs best baseline {100 * (P['gridpact']['harm'] - best) / best:+.1f}%, violations {viol}")
    except Exception as e:  # noqa: BLE001
        add("Mumbai city data", False, f"{type(e).__name__}: {e}")

    for name in ("index.html", "app.js", "charts.js", "styles.css", "city.html", "city.js", "city.css",
                 "fonts/plex-sans-400.woff2"):
        if not os.path.exists(os.path.join("web", name)):
            add("Dashboard files", False, f"missing web/{name}")
            break
    else:
        add("Dashboard files", True, "present (no internet needed)")

    with socket.socket() as sock:
        free = sock.connect_ex(("127.0.0.1", 8041)) != 0
    add("Port 8041", True if free else None, "free" if free else "in use (already running? or use --port 9000)")

    llm_on = os.environ.get("GRIDPACT_LLM") == "1"
    sdk = importlib.util.find_spec("anthropic") is not None
    key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    add("LLM narration (optional)", None,
        f"GRIDPACT_LLM={'1' if llm_on else 'off'}, anthropic SDK {'installed' if sdk else 'not installed'}, "
        f"API key {'set' if key else 'not set'}")
    if llm_on and sdk and key:
        from gridpact import narration
        from gridpact.config import load_scenario
        from gridpact.simulation import Simulation
        sc = load_scenario("cyclone_landfall")
        sim = Simulation(sc, 41)
        for _ in range(30):
            sim.step()
        out = narration.narrate(sc, sim.records[-1])
        add("LLM narration live call", out.get("enabled", False), (out.get("model", "") + " | " + out["text"])[:300])

    print("===== GridPact pre-flight report (copy everything between the lines) =====")
    print("\n".join(lines))
    print("RESULT:", "READY" if ok else "PROBLEMS FOUND")
    print("==========================================================================")
    return 0 if ok else 1


if __name__ == "__main__":
    main()
