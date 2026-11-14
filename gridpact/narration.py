"""Optional LLM narration of one step's negotiation. OFF by default.

The core system NEVER depends on this module:
  * it runs only when GRIDPACT_LLM=1 is set,
  * the `anthropic` package is imported lazily, inside narrate(), so the
    simulator still runs with the standard library alone,
  * it only READS a finished, committed step record and paraphrases the
    deterministic log. Its output never feeds back into any allocation.
  * any failure (no package, no key, no network at the venue) returns a
    plain message instead of raising.

Enable:  pip install anthropic   then   GRIDPACT_LLM=1 python3 gridpact.py
Credentials come from ANTHROPIC_API_KEY (or an `ant auth login` profile).
"""
from __future__ import annotations

import os

MODEL = os.environ.get("GRIDPACT_LLM_MODEL", "")  # required when narration is on

SYSTEM = (
    "You narrate a power-sharing negotiation between city utility agents during a disaster, "
    "for hackathon judges watching a live demo. Using ONLY the facts in the step record you "
    "are given, explain in 3-4 plain-English sentences why each agent acted as it did "
    "(conceded, held firm, dug into reserves, failed). Never invent numbers; quote numbers "
    "exactly as given. No preamble, no lists, no markdown."
)


def enabled() -> bool:
    return os.environ.get("GRIDPACT_LLM") == "1"


def _facts(scenario, record: dict) -> str:
    names = {a.id: a.name for a in scenario.agents}
    lines = [f"Scenario: {scenario.name}. Time {record['hour']:.2f} h. "
             f"Supply {record['supply_kw']:.1f} kW. Contested: {record['contested']}. "
             f"Rounds: {record['rounds']}. Fallback: {record['fallback'] or 'none'}."]
    if record["events"]:
        lines.append("Events this step: " + "; ".join(record["events"]))
    for aid, a in record["agents"].items():
        lines.append(f"{names[aid]}: requested {a['request_max_kw']:.1f} kW (needs {a['request_min_kw']:.1f} kW "
                     f"from grid to survive), got {a['alloc_kw']:.1f} kW, reserve {a['reserve_frac']:.0%}, "
                     f"status {a['status']}, urgency bid {a['bid']:.2f}, credits {a['credits']:.1f}"
                     + (f", degraded by failure of {', '.join(names[s] for s in a['cascade_from'])}"
                        if a["cascade_from"] else ""))
    lines.append("Negotiation log:")
    lines += [f"- {l['text']}" for l in record["log"]]
    return "\n".join(lines)


def narrate(scenario, record: dict) -> dict:
    if not enabled():
        return {"enabled": False, "text": "Narration is off. Start with GRIDPACT_LLM=1 to enable it."}
    try:
        import anthropic  # lazy: only needed when narration is on
    except ImportError:
        return {"enabled": False, "text": "Narration needs the anthropic package: pip install anthropic"}

    try:
        client = anthropic.Anthropic(timeout=30.0, max_retries=1)
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=2000,
            output_config={"effort": "low"},          # short paraphrase; no deep reasoning needed
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",                      # if declined, the API retries on a fallback model
            system=SYSTEM,
            messages=[{"role": "user", "content": _facts(scenario, record)}],
        )
    except anthropic.AuthenticationError:
        return {"enabled": False, "text": "Narration unavailable: no valid Anthropic credentials."}
    except anthropic.APIConnectionError:
        return {"enabled": False, "text": "Narration unavailable: no network (the simulator is unaffected)."}
    except anthropic.APIStatusError as e:
        return {"enabled": False, "text": f"Narration unavailable: API error {e.status_code}."}
    except Exception as e:  # noqa: BLE001 - narration must NEVER break the demo
        # e.g. no credentials configured at all: the SDK raises TypeError before any request.
        return {"enabled": False, "text": f"Narration unavailable ({type(e).__name__}). "
                                          "Set ANTHROPIC_API_KEY, or run without GRIDPACT_LLM."}

    if response.stop_reason == "refusal":
        return {"enabled": True, "text": "The narrator declined this step; the log above is authoritative."}
    text = " ".join(b.text for b in response.content if b.type == "text").strip()
    return {"enabled": True, "text": text or "No narration returned.", "model": response.model}
