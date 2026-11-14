#!/usr/bin/env python3
"""GridPact: one command to run everything. Python 3.9+ standard library only.

    python3 gridpact.py              start the dashboard at http://127.0.0.1:8041
    python3 gridpact.py test         run every test (the hard guarantees)
    python3 gridpact.py run SCENARIO [--seed N] [--policy P]   headless run, prints metrics
    python3 gridpact.py results      regenerate RESULTS.md from real runs
"""
import argparse
import sys
import webbrowser


def main(argv=None):
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
    elif args.cmd == "results":
        from scripts.generate_results import main as gen
        gen()


if __name__ == "__main__":
    main()
