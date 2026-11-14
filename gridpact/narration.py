"""Optional LLM narration. OFF by default; the core never depends on it.

Filled in during Phase C. With narration disabled, the dashboard shows the
deterministic negotiation log, which already explains every decision.
"""
import os


def enabled() -> bool:
    return os.environ.get("GRIDPACT_LLM") == "1"


def narrate(scenario, record: dict) -> dict:
    return {"enabled": False, "text": "Narration is off (set GRIDPACT_LLM=1 to enable)."}
