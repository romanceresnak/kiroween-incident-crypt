#!/usr/bin/env python3
"""How often does the classifier send each lab scenario to the specialist its card names?

    uv run --project agents/orchestrator python tools/lab/measure_routing.py \\
        --runs 3 --prompt current --prompt /tmp/old-prompt.txt

It replays the incidents saved by run_scenarios.py (docs/demo-results/lab) through the real
orchestrator classifier on Amazon Bedrock, under each prompt text given, and prints the
category chosen per scenario. It calls Bedrock (a few cents per run of 14) and touches no
cluster. Needs AWS credentials for eu-west-1.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "docs" / "demo-results" / "lab"
MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"


def load_records(directory: Path = RESULTS) -> list[tuple[str, str, dict[str, Any]]]:
    """(scenario id, expected category, the incident) for every saved scenario."""
    found = []
    for path in sorted(directory.glob("*.json")):
        if path.name == "summary.json":
            continue
        data = json.loads(path.read_text())
        record = data.get("record")
        if record:
            found.append((data["summary"]["scenario"], data["summary"]["category_expected"],
                          record["incident"]))
    return found


def tally(results: dict[str, list[str]], expected: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Per scenario: the categories chosen, how many matched the card, and the rate."""
    out = {}
    for scenario, chosen in results.items():
        hits = sum(1 for c in chosen if c == expected[scenario])
        out[scenario] = {"chosen": dict(Counter(chosen)), "hits": hits, "runs": len(chosen),
                         "expected": expected[scenario]}
    return out


def render(table: dict[str, dict[str, Any]]) -> str:
    lines = [f"{'scenario':<30}{'expected':<16}{'hit':>5}  chosen"]
    for scenario, row in table.items():
        lines.append(f"{scenario:<30}{row['expected']:<16}{row['hits']}/{row['runs']:<3} "
                     f"{row['chosen']}")
    total = sum(r["hits"] for r in table.values())
    runs = sum(r["runs"] for r in table.values())
    lines.append(f"{'TOTAL':<46}{total}/{runs}")
    return "\n".join(lines)


async def classify_all(prompt: str | None, runs: int,
                       records: list[tuple[str, str, dict[str, Any]]]) -> dict[str, list[str]]:
    from kiroween_orchestrator import agent as agent_module
    from kiroween_orchestrator.agent import Orchestrator
    from kiroween_shared.bedrock import make_model
    from kiroween_shared.config import Settings
    from kiroween_shared.metrics import PriceTable
    from kiroween_shared.models import Incident

    if prompt is not None:
        agent_module.CLASSIFY_PROMPT = prompt
    settings = Settings(agent_name="orchestrator", model_id=MODEL)
    orchestrator = Orchestrator(model=make_model(settings), settings=settings,
                                prices=PriceTable.from_json("{}"), specialists={})
    chosen: dict[str, list[str]] = {}
    for scenario, _, incident in records:
        # Classification sees only the incident, never what the specialist later found.
        clean = Incident.model_validate({**incident, "similar_incidents": []})
        for _ in range(runs):
            classification, _usage = await orchestrator.classify(clean)
            chosen.setdefault(scenario, []).append(classification.category)
    return chosen


def read_prompt(spec: str) -> str | None:
    if spec == "current":
        return None
    return Path(spec).read_text()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--prompt", action="append", default=[],
                        help="'current' or a file with a prompt text; repeatable")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--dir", type=Path, default=RESULTS)
    args = parser.parse_args(argv)
    records = load_records(args.dir)
    if not records:
        print(f"no saved scenarios in {args.dir}", file=sys.stderr)
        return 2
    expected = {s: c for s, c, _ in records}
    for spec in args.prompt or ["current"]:
        chosen = asyncio.run(classify_all(read_prompt(spec), args.runs, records))
        print(f"\n== prompt: {spec} ({args.runs} runs each)")
        print(render(tally(chosen, expected)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
