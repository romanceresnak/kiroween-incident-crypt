"""scenario-lab TASK-09: the harness that runs the scenarios on the real cluster. Normal tests.

Nothing here touches a cluster: kubectl is replaced by a recorder.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from kiroween_orchestrator.lab.scenarios import BY_ID, SCENARIOS

SCRIPT = Path(__file__).parents[3] / "tools" / "lab" / "run_scenarios.py"
spec = importlib.util.spec_from_file_location("run_scenarios", SCRIPT)
assert spec and spec.loader
harness = importlib.util.module_from_spec(spec)
sys.modules["run_scenarios"] = harness
spec.loader.exec_module(harness)

CONTEXT = "arn:aws:eks:eu-west-1:255834079310:cluster/kiroween-prod"
START = "2026-11-18T18:00:00+00:00"


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], str | None]] = []

    def __call__(self, context: str, *args: str, stdin: str | None = None, timeout: int = 120):  # type: ignore[no-untyped-def]
        self.calls.append(((context, *args), stdin))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()


@pytest.fixture
def kube(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr(harness, "kubectl", recorder)
    return recorder


# --------------------------------------------------------------------------- the guards


def test_only_the_projects_cluster_is_accepted() -> None:
    assert harness.guard_context(CONTEXT) == CONTEXT
    assert harness.guard_context("kiroween-prod") == "kiroween-prod"
    for bad in ("arn:aws:eks:eu-west-1:1:cluster/other", "minikube", "", "kiroween-prod-2",
                "arn:aws:eks:eu-west-1:255834079310:cluster/kiroween-prod-staging"):
        with pytest.raises(harness.Refused):
            harness.guard_context(bad)


def test_only_lab_namespaces_are_accepted() -> None:
    for n in range(1, 15):
        assert harness.guard_namespace(f"lab-{n:02d}")
    for bad in ("kube-system", "agents", "default", "lab-00", "lab-15", "lab-1", "lab-01\n", ""):
        with pytest.raises(harness.Refused):
            harness.guard_namespace(bad)


def test_cleaning_deletes_only_labelled_objects_in_one_lab_namespace(kube: Recorder) -> None:
    harness.clean(CONTEXT, "lab-04")
    ((args, _),) = kube.calls
    assert args[:4] == (CONTEXT, "-n", "lab-04", "delete")
    assert "-l" in args and args[args.index("-l") + 1] == "kiroween.io/lab=true"
    kinds = args[4].split(",")
    assert "namespaces" not in kinds and "secrets" not in kinds and "--all" not in args
    assert set(kinds) == {"deployments", "pods", "services", "configmaps",
                          "persistentvolumeclaims", "networkpolicies"}


def test_cleaning_a_non_lab_namespace_is_refused_before_kubectl_runs(kube: Recorder) -> None:
    with pytest.raises(harness.Refused):
        harness.clean(CONTEXT, "kube-system")
    assert kube.calls == []


def test_applying_applies_the_rendered_phases_in_order_and_waits_between(kube: Recorder) -> None:
    logs: list[str] = []
    harness.apply_phases(CONTEXT, BY_ID["bad-image-tag"], "a1b2c3", logs.append)
    verbs = [c[0][1] for c in kube.calls]
    assert verbs == ["apply", "-n", "apply"], verbs
    wait = kube.calls[1][0]
    assert wait[2] == "lab-08" and wait[3:5] == ("rollout", "status")
    assert wait[5] == "deployment/web-a1b2c3"
    first, second = (json.loads(c[1]) for c in (kube.calls[0], kube.calls[2]))
    assert first["kind"] == second["kind"] == "List"
    image = lambda doc: doc["items"][0]["spec"]["template"]["spec"]["containers"][0]["image"]  # noqa: E731
    assert image(first).endswith("3.12-slim") and image(second).endswith("-gone")
    assert len(logs) == 3


def test_a_failed_apply_is_an_error_not_a_silent_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(context: str, *args: str, **kw: Any):  # type: ignore[no-untyped-def]
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": "forbidden: quota"})()

    monkeypatch.setattr(harness, "kubectl", fail)
    with pytest.raises(harness.Refused, match="quota"):
        harness.apply_phases(CONTEXT, BY_ID["oom-killed"], "a1b2c3", lambda t: None)


# ------------------------------------------------------------------------- the command


def test_a_dry_run_prints_every_scenario_and_applies_nothing(
        kube: Recorder, capsys: pytest.CaptureFixture[str]) -> None:
    assert harness.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    for scenario in SCENARIOS:
        assert scenario.id in out and scenario.namespace in out
    assert "nothing was applied" in out and kube.calls == []


def test_without_apply_nothing_is_applied_either(kube: Recorder) -> None:
    assert harness.main([]) == 0
    assert kube.calls == []


def test_an_unknown_scenario_is_refused(kube: Recorder,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    assert harness.main(["--dry-run", "--only", "nope"]) == 2
    assert "unknown scenarios" in capsys.readouterr().err and kube.calls == []


def test_apply_refuses_the_wrong_cluster_before_touching_anything(
        kube: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harness, "current_context", lambda: "arn:aws:eks:eu-west-1:1:cluster/x")
    with pytest.raises(harness.Refused, match="not the kiroween-prod cluster"):
        harness.main(["--apply"])
    assert kube.calls == []


def test_the_harness_never_approves_or_posts_to_the_orchestrator() -> None:
    source = SCRIPT.read_text()
    assert "/approve" not in source and "/reject" not in source and "POST" not in source
    assert "urlopen" in source and "data=" not in source


def test_namespaces_come_from_the_chart_and_only_three_kinds_are_applied() -> None:
    objects = harness.lab_objects_from_chart()
    assert {o["kind"] for o in objects} == {"Namespace", "ResourceQuota", "LimitRange"}
    assert len([o for o in objects if o["kind"] == "Namespace"]) == 14


# ----------------------------------------------------------------------- the verdicts


def record(namespace: str, reason: str, *, category: str | None = "network",
           status: str = "awaiting_approval", created: str = "2026-11-18T18:00:30+00:00",
           updated: str = "2026-11-18T18:01:00+00:00", related_to: str | None = None,
           iid: str = "i1", cost: float = 0.05) -> dict[str, Any]:
    return {"incident": {"incident_id": iid, "namespace": namespace, "reason": reason},
            "status": status, "created_at": created, "updated_at": updated,
            "related_to": related_to,
            "classification": {"category": category} if category else None,
            "investigation": {"outcome": "action_required", "proposal": {
                "field": "spec.x", "proposed": "y"}},
            "usage_by_agent": {"network": {"cost_usd": cost}}}


def test_a_scenario_that_does_what_its_card_says_is_as_described() -> None:
    s = BY_ID["netpol-blocks-client"]
    out = harness.summarise(s, "abc123", START, [record("lab-04", "Unhealthy")])
    assert out["verdict"] == "as described" and out["incidents"] == 1 and out["correlated"] == 0
    assert out["seconds_to_detect"] == 30.0 and out["seconds_to_decision"] == 30.0
    assert out["cost_usd"] == 0.05 and out["category_observed"] == "network"
    assert out["proposal_field"] == "spec.x" and out["incident_id"] == "i1"


def test_each_way_a_scenario_can_differ_from_its_card_is_named() -> None:
    s = BY_ID["netpol-blocks-client"]
    assert harness.summarise(s, "r", START, [])["verdict"] == "no incident"
    other_ns = [record("lab-05", "Unhealthy")]
    assert harness.summarise(s, "r", START, other_ns)["verdict"] == "no incident"
    old = [record("lab-04", "Unhealthy", created="2026-11-18T17:00:00+00:00")]
    assert harness.summarise(s, "r", START, old)["verdict"] == "no incident"
    assert harness.summarise(s, "r", START, [record("lab-04", "OOMKilled")])[
        "verdict"] == "different signal"
    assert harness.summarise(s, "r", START, [record("lab-04", "Unhealthy",
                                                    category="storage")])[
        "verdict"] == "different agent"
    assert harness.summarise(s, "r", START, [record("lab-04", "Unhealthy",
                                                    status="processing")])[
        "verdict"] == "not finished"


def test_related_signals_are_counted_and_do_not_hide_the_primary() -> None:
    s = BY_ID["bad-image-tag"]
    records = [record("lab-08", "ImagePullBackOff", iid="b", created="2026-11-18T18:00:40+00:00",
                      related_to="a", status="correlated", category=None),
               record("lab-08", "ErrImagePull", iid="a", category="deploy_rollout")]
    out = harness.summarise(s, "r", START, records)
    assert out["incident_id"] == "a" and out["incidents"] == 2 and out["correlated"] == 1
    assert out["signals_observed"] == ["ErrImagePull", "ImagePullBackOff"]
    assert out["verdict"] == "as described"


def test_any_one_expected_signal_is_enough() -> None:
    s = BY_ID["bad-image-tag"]
    assert set(s.signals) == {"ErrImagePull", "ImagePullBackOff"}
    only_second = [record("lab-08", "ImagePullBackOff", category="deploy_rollout")]
    assert harness.summarise(s, "r", START, only_second)["verdict"] == "as described"


def test_the_summary_of_a_failed_investigation_is_not_called_as_described() -> None:
    s = BY_ID["oom-killed"]
    failed = [record("lab-01", "OOMKilled", category="cpu_memory", status="failed")]
    out = harness.summarise(s, "r", START, failed)
    assert out["status"] == "failed"
    assert out["verdict"] == "as described", "a settled status: the failure is in `status`"
