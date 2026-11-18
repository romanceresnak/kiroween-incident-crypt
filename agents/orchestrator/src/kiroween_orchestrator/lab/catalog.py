"""What the UI and the API show about the scenarios. No manifests."""

from __future__ import annotations

from typing import Any

from .scenarios import SCENARIOS

CREW = {
    "cpu-memory": "Vampire", "network": "Spider", "deploy-rollout": "Zombie", "storage": "Ghost",
}


def public() -> list[dict[str, Any]]:
    return [
        {"id": s.id, "namespace": s.namespace, "title": s.title, "agent": s.agent,
         "crew": CREW[s.agent], "category": s.category, "breaks": s.breaks,
         "signals": list(s.signals)}
        for s in SCENARIOS
    ]
