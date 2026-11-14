"""Tiny offline HTTP server: serves the dashboard and a JSON API.

Python's built-in http.server is enough: one presenter, one browser tab,
localhost only. No framework means nothing to pip install at the venue.

API (all JSON):
  GET  /api/scenarios                 list of scenarios
  POST /api/session   {scenario, seed}         -> new live run (GridPact)
  POST /api/step      {session, n}             -> next n step records
  POST /api/inject    {session, event_id}      -> queue an event for the next step
  POST /api/compare   {session}                -> all policies on the same seed + event log
  POST /api/narrate   {session, step}          -> optional LLM narration (off by default)
"""
from __future__ import annotations

import json
import mimetypes
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import narration
from .config import ConfigError, list_scenarios, load_scenario
from .metrics import compare
from .simulation import Simulation, StepRolledBack

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
_sessions: dict = {}
_lock = threading.Lock()      # one simulation mutation at a time
MAX_SESSIONS = 20


def scenario_info(sc) -> dict:
    """Public description of a scenario for the dashboard (config only, no run state)."""
    return {
        "id": sc.id, "name": sc.name, "description": sc.description,
        "hours": sc.hours, "step_minutes": sc.step_minutes, "steps": sc.steps,
        "base_supply_kw": sc.base_supply_kw,
        "agents": [{"id": a.id, "name": a.name, "kind": a.kind, "min_kw": a.min_kw,
                    "desired_kw": a.desired_kw, "priority_rank": a.priority_rank,
                    "reserve_capacity_kwh": a.reserve_capacity_kwh,
                    "reserve_label": a.reserve_label}
                   for a in sc.agents],
        "dependencies": [{"source": d.source, "target": d.target, "impact": d.impact, "label": d.label}
                         for d in sc.dependencies],
        "injectable": [{"id": e.id, "label": e.label, "type": e.type} for e in sc.injectable],
        "scheduled": [{"step": s, "label": e.label} for s, e in sc.scheduled],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "GridPact/1.0"

    def log_message(self, fmt, *args):  # keep the terminal quiet during the demo
        pass

    # ---------------- helpers ----------------
    def _json(self, code: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 100_000:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def _session(self, body) -> Simulation:
        sim = _sessions.get(body.get("session"))
        if sim is None:
            raise KeyError("unknown or expired session; start a new run")
        return sim

    # ---------------- routes ----------------
    def do_GET(self):
        if self.path == "/api/scenarios":
            return self._json(200, {"scenarios": list_scenarios(),
                                    "narration": narration.enabled()})
        clean = self.path.split("?", 1)[0]
        path = "index.html" if clean in ("/", "") else clean.lstrip("/")
        target = (WEB_DIR / path).resolve()
        if WEB_DIR not in target.parents or not target.is_file():
            return self._json(404, {"error": "not found"})
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            body = self._body()
            with _lock:
                if self.path == "/api/session":
                    sc = load_scenario(str(body.get("scenario", "cyclone_landfall")))
                    seed = int(body.get("seed", 41))
                    if len(_sessions) >= MAX_SESSIONS:
                        _sessions.pop(next(iter(_sessions)))
                    sid = uuid.uuid4().hex
                    _sessions[sid] = Simulation(sc, seed, "gridpact")
                    return self._json(200, {"session": sid, "seed": seed, "scenario": scenario_info(sc)})
                if self.path == "/api/step":
                    sim = self._session(body)
                    n = max(1, min(int(body.get("n", 1)), 200))
                    out = []
                    for _ in range(n):
                        if sim.done:
                            break
                        out.append(sim.step())
                    return self._json(200, {"records": out, "done": sim.done,
                                            "total_damage": round(sim.state.total_damage, 2)})
                if self.path == "/api/inject":
                    sim = self._session(body)
                    ev = next((e for e in sim.scenario.injectable if e.id == body.get("event_id")), None)
                    if ev is None:
                        return self._json(400, {"error": "unknown event"})
                    if sim.done:
                        return self._json(400, {"error": "run finished"})
                    step = sim.inject(ev)
                    return self._json(200, {"queued_for_step": step, "label": ev.label})
                if self.path == "/api/compare":
                    sim = self._session(body)
                    out = compare(sim.scenario, sim.seed, list(sim.injected))
                    out["injected"] = [{"step": st, "label": e.label} for st, e in sim.injected]
                    return self._json(200, out)
                if self.path == "/api/narrate":
                    sim = self._session(body)
                    step = int(body.get("step", len(sim.records) - 1))
                    if not (0 <= step < len(sim.records)):
                        return self._json(400, {"error": "no such step"})
                    return self._json(200, narration.narrate(sim.scenario, sim.records[step]))
            return self._json(404, {"error": "not found"})
        except StepRolledBack as e:
            return self._json(500, {"error": str(e), "rolled_back": True})
        except (KeyError, ValueError, ConfigError) as e:
            return self._json(400, {"error": str(e)})


def serve(host: str = "127.0.0.1", port: int = 8041) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"GridPact dashboard: http://{host}:{port}   (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
