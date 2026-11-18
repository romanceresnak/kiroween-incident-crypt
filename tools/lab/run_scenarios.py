#!/usr/bin/env python3
"""Run the lab scenarios on the real cluster and record what the crew did (scenario-lab TASK-09).

    uv run --project agents/orchestrator python tools/lab/run_scenarios.py --dry-run
    uv run --project agents/orchestrator python tools/lab/run_scenarios.py --apply \\
        --out docs/demo-results/lab

The lab-runner is not deployed until the change is pushed, so this applies exactly what the
runner would (the same renderer, the same objects, the same labels) with kubectl, one batch at
a time, watches the orchestrator for the incident each scenario produces, saves the record, and
removes what it created. It never approves anything: an approval opens a pull request.

Safety: it refuses any context but the project's cluster, touches only lab-01 to lab-14, and
deletes only objects that carry the lab label.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from kiroween_orchestrator.lab.render import SELECTOR, render
from kiroween_orchestrator.lab.scenarios import SCENARIOS, Scenario

CLUSTER = "kiroween-prod"
LAB_NAMESPACE = re.compile(r"lab-(0[1-9]|1[0-4])")
SETTLED = frozenset({"awaiting_approval", "completed", "failed", "no_specialist", "approved",
                     "rejected"})
KINDS_TO_DELETE = "deployments,pods,services,configmaps,persistentvolumeclaims,networkpolicies"
REPO = Path(__file__).resolve().parents[2]


class Refused(RuntimeError):
    pass


def guard_namespace(namespace: str) -> str:
    if not LAB_NAMESPACE.fullmatch(namespace):
        raise Refused(f"{namespace!r} is not a lab namespace")
    return namespace


def guard_context(context: str) -> str:
    if not context.endswith(f":cluster/{CLUSTER}") and context != CLUSTER:
        raise Refused(f"kubectl context {context!r} is not the {CLUSTER} cluster")
    return context


def kubectl(context: str, *args: str, stdin: str | None = None,
            timeout: int = 120) -> subprocess.CompletedProcess[str]:
    exe = shutil.which("kubectl")
    if not exe:
        raise Refused("kubectl is not installed")
    return subprocess.run([exe, "--context", context, *args], input=stdin,  # noqa: S603
                          capture_output=True, text=True, timeout=timeout, check=False)


def current_context() -> str:
    exe = shutil.which("kubectl")
    if not exe:
        raise Refused("kubectl is not installed")
    out = subprocess.run([exe, "config", "current-context"],  # noqa: S603
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def lab_objects_from_chart() -> list[dict[str, Any]]:
    """The namespaces, quotas and limit ranges the chart creates, so this adopts what Argo CD
    will apply instead of inventing its own."""
    import yaml

    helm = shutil.which("helm")
    if not helm:
        raise Refused("helm is not installed")
    out = subprocess.run(  # noqa: S603
        [helm, "template", "agents", str(REPO / "gitops/charts/agents"), "--namespace", "agents",
         "--show-only", "templates/lab.yaml", "--set", "images.orchestrator.tag=unset"],
        capture_output=True, text=True, check=True).stdout
    keep = {"Namespace", "ResourceQuota", "LimitRange"}
    return [d for d in yaml.safe_load_all(out) if d and d["kind"] in keep]


def ensure_namespaces(context: str) -> int:
    objects = lab_objects_from_chart()
    names = {o["metadata"].get("namespace") or o["metadata"]["name"] for o in objects}
    for name in names:
        guard_namespace(name)
    result = kubectl(context, "apply", "-f", "-", stdin=json.dumps({"apiVersion": "v1",
                                                                    "kind": "List",
                                                                    "items": objects}))
    if result.returncode != 0:
        raise Refused(f"could not create the lab namespaces: {result.stderr.strip()[:300]}")
    return len(names)


def apply_phases(context: str, scenario: Scenario, run: str, log: Callable[[str], None]) -> None:
    guard_namespace(scenario.namespace)
    for index, phase in enumerate(render(scenario, run), start=1):
        if phase.wait_for:
            log(f"{scenario.id}: waiting for {phase.wait_for}")
            kubectl(context, "-n", scenario.namespace, "rollout", "status",
                    f"deployment/{phase.wait_for}", "--timeout=90s", timeout=120)
        items = list(phase.objects)
        result = kubectl(context, "apply", "-f", "-", stdin=json.dumps(
            {"apiVersion": "v1", "kind": "List", "items": items}))
        if result.returncode != 0:
            raise Refused(f"{scenario.id} phase {index}: {result.stderr.strip()[:400]}")
        log(f"{scenario.id}: applied phase {index}")


def clean(context: str, namespace: str) -> None:
    guard_namespace(namespace)
    kubectl(context, "-n", namespace, "delete", KINDS_TO_DELETE, "-l", SELECTOR,
            "--wait=false", "--ignore-not-found")


def fetch(base: str, path: str) -> Any:
    with urllib.request.urlopen(base + path, timeout=20) as response:  # noqa: S310
        return json.loads(response.read())


def seconds_between(start: str, end: str) -> float:
    return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 1)


def summarise(scenario: Scenario, run: str, started: str, records: list[dict[str, Any]]
              ) -> dict[str, Any]:
    """What happened to one scenario, and whether it matches its card."""
    mine = sorted((r for r in records if r["incident"]["namespace"] == scenario.namespace
                   and r["created_at"] >= started), key=lambda r: r["created_at"])
    primaries = [r for r in mine if not r.get("related_to")]
    reasons = sorted({r["incident"]["reason"] for r in mine})
    primary = primaries[0] if primaries else None
    category = (primary.get("classification") or {}).get("category") if primary else None
    usage = (primary or {}).get("usage_by_agent") or {}
    cost = round(sum(u.get("cost_usd", 0) for u in usage.values()), 4)
    expected_signal_seen = bool(set(reasons) & set(scenario.signals))
    if primary is None:
        verdict = "no incident"
    elif not expected_signal_seen:
        verdict = "different signal"
    elif category != scenario.category:
        verdict = "different agent"
    elif primary["status"] not in SETTLED:
        verdict = "not finished"
    else:
        verdict = "as described"
    outcome = ((primary or {}).get("investigation") or {}).get("outcome")
    proposal = (((primary or {}).get("investigation") or {}).get("proposal") or {})
    return {
        "scenario": scenario.id, "namespace": scenario.namespace, "run": run,
        "agent_expected": scenario.agent, "category_expected": scenario.category,
        "signals_expected": list(scenario.signals), "signals_observed": reasons,
        "incidents": len(mine), "correlated": len(mine) - len(primaries),
        "category_observed": category, "status": primary["status"] if primary else None,
        "outcome": outcome, "proposal_field": proposal.get("field"),
        "proposal_proposed": proposal.get("proposed"),
        "seconds_to_decision": (seconds_between(primary["created_at"], primary["updated_at"])
                                if primary else None),
        "seconds_to_detect": (seconds_between(started, primary["created_at"])
                              if primary else None),
        "cost_usd": cost, "verdict": verdict,
        "incident_id": primary["incident"]["incident_id"] if primary else None,
    }


def run_batch(context: str, base: str, batch: Sequence[Scenario], out: Path, timeout_s: int,
              log: Callable[[str], None]) -> list[dict[str, Any]]:
    runs: dict[str, tuple[str, str]] = {}
    for scenario in batch:
        run = secrets.token_hex(3)
        started = datetime.fromisoformat(time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))
        runs[scenario.id] = (run, started.isoformat())
        apply_phases(context, scenario, run, log)
    deadline = time.monotonic() + timeout_s
    records: list[dict[str, Any]] = []
    quiet_since: float | None = None
    while time.monotonic() < deadline:
        time.sleep(10)
        listing = fetch(base, "/incidents?limit=200")
        records = [fetch(base, f"/incidents/{r['incident']['incident_id']}")
                   for r in listing if LAB_NAMESPACE.fullmatch(r["incident"]["namespace"])]
        pending = []
        for scenario in batch:
            summary = summarise(scenario, *runs[scenario.id], records)
            if summary["status"] not in SETTLED:
                pending.append(scenario.id)
        log(f"waiting: {len(pending)} of {len(batch)} not settled {pending}")
        if not pending:
            quiet_since = quiet_since or time.monotonic()
            if time.monotonic() - quiet_since >= 30:  # let related signals arrive
                break
    results = []
    for scenario in batch:
        run, started = runs[scenario.id]
        summary = summarise(scenario, run, started, records)
        primary = next((r for r in sorted(records, key=lambda r: r["created_at"])
                        if r["incident"]["namespace"] == scenario.namespace
                        and r["created_at"] >= started and not r.get("related_to")), None)
        (out / f"{scenario.id}.json").write_text(json.dumps(
            {"summary": summary, "record": primary}, indent=2) + "\n")
        results.append(summary)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="really run the scenarios")
    parser.add_argument("--dry-run", action="store_true", help="print what would be applied")
    parser.add_argument("--only", nargs="*", help="scenario ids (default: all)")
    parser.add_argument("--batch", type=int, default=5, help="scenarios at a time")
    parser.add_argument("--timeout", type=int, default=420, help="seconds per batch")
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--out", type=Path, default=REPO / "docs/demo-results/lab")
    args = parser.parse_args(argv)
    chosen = [s for s in SCENARIOS if not args.only or s.id in args.only]
    unknown = set(args.only or []) - {s.id for s in SCENARIOS}
    if unknown:
        print(f"unknown scenarios: {sorted(unknown)}", file=sys.stderr)
        return 2
    if args.dry_run or not args.apply:
        for s in chosen:
            for i, phase in enumerate(render(s, "000000"), start=1):
                names = [f"{o['kind']}/{o['metadata']['name']}" for o in phase.objects]
                print(f"{s.namespace} {s.id} phase {i}: {names}")
        print("dry run: nothing was applied")
        return 0
    context = guard_context(current_context())
    args.out.mkdir(parents=True, exist_ok=True)
    log = lambda text: print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)  # noqa: E731
    log(f"context {context}; {ensure_namespaces(context)} lab namespaces ready")
    kubectl_exe = shutil.which("kubectl")
    forward = subprocess.Popen(  # noqa: S603
        [kubectl_exe or "kubectl", "--context", context, "-n", "agents", "port-forward",
         "svc/orchestrator", f"{args.port}:8080"], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    time.sleep(4)
    base = f"http://127.0.0.1:{args.port}"
    results: list[dict[str, Any]] = []
    try:
        for start in range(0, len(chosen), args.batch):
            batch = chosen[start:start + args.batch]
            log(f"batch: {[s.id for s in batch]}")
            try:
                results += run_batch(context, base, batch, args.out, args.timeout, log)
            finally:
                for s in batch:
                    clean(context, s.namespace)
                log("cleaned the batch")
            time.sleep(15)
    finally:
        forward.terminate()
    (args.out / "summary.json").write_text(json.dumps(
        {"cluster": CLUSTER, "scenarios": results,
         "total_cost_usd": round(sum(r["cost_usd"] for r in results), 4)}, indent=2) + "\n")
    for r in results:
        print(f"{r['verdict']:<18} {r['scenario']:<30} {r['category_observed']} "
              f"{r['signals_observed']} {r['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
