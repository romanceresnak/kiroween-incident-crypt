"""scenario-lab TASK-09: the saved results of running the 14 scenarios on the real cluster, and
the tool that measures routing. Normal tests.

The results are evidence, so these tests read them and check that what the documents will say
about them is true. They run no cluster and call no model.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from kiroween_orchestrator.agent import CLASSIFY_PROMPT
from kiroween_orchestrator.lab.scenarios import BY_ID, SCENARIOS

ROOT = Path(__file__).parents[3]
LAB_RESULTS = ROOT / "docs" / "demo-results" / "lab"
SCRIPT = ROOT / "tools" / "lab" / "measure_routing.py"
spec = importlib.util.spec_from_file_location("measure_routing", SCRIPT)
assert spec and spec.loader
measure = importlib.util.module_from_spec(spec)
sys.modules["measure_routing"] = measure
spec.loader.exec_module(measure)

SUMMARY = json.loads((LAB_RESULTS / "summary.json").read_text())
ROWS = {r["scenario"]: r for r in SUMMARY["scenarios"]}


def saved(scenario: str) -> dict:
    return json.loads((LAB_RESULTS / f"{scenario}.json").read_text())


# ------------------------------------------------------------------- the saved evidence


def test_all_fourteen_scenarios_were_run_and_each_has_its_own_file() -> None:
    assert SUMMARY["cluster"] == "kiroween-prod"
    assert [r["scenario"] for r in SUMMARY["scenarios"]] == [s.id for s in SCENARIOS]
    for scenario in SCENARIOS:
        assert (LAB_RESULTS / f"{scenario.id}.json").is_file(), scenario.id
        row = ROWS[scenario.id]
        assert row["namespace"] == scenario.namespace
        assert row["agent_expected"] == scenario.agent
        assert row["category_expected"] == scenario.category
        assert row["signals_expected"] == list(scenario.signals)
    extra = {p.stem for p in LAB_RESULTS.glob("*.json")} - {s.id for s in SCENARIOS} - {"summary"}
    assert extra == set()


def test_every_scenario_produced_an_incident_in_its_own_namespace() -> None:
    for scenario in SCENARIOS:
        row, data = ROWS[scenario.id], saved(scenario.id)
        assert row["incidents"] >= 1 and row["verdict"] != "no incident", scenario.id
        record = data["record"]
        assert record["incident"]["namespace"] == scenario.namespace
        assert record["incident"]["incident_id"] == row["incident_id"]
        assert record["incident"]["source"] == "watcher"
        assert record["investigation"]["diagnosis"]["root_cause"]
        assert record["investigation"]["diagnosis"]["evidence"]
        assert data["summary"] == row


def test_twelve_behaved_as_described_and_two_were_sent_to_the_network_specialist() -> None:
    different = {s for s, r in ROWS.items() if r["verdict"] != "as described"}
    assert different == {"cpu-throttling", "rollout-stuck-readiness"}
    for scenario in different:
        assert ROWS[scenario]["verdict"] == "different agent"
        assert ROWS[scenario]["category_observed"] == "network"
        assert ROWS[scenario]["signals_observed"] == ["Unhealthy"]
        assert ROWS[scenario]["category_expected"] != "network"
    for scenario, row in ROWS.items():
        if scenario not in different:
            assert row["category_observed"] == row["category_expected"], scenario
            assert set(row["signals_observed"]) & set(row["signals_expected"]), scenario


def test_the_diagnosis_of_the_two_misrouted_scenarios_was_still_right() -> None:
    """Routing was the problem, not the investigation."""
    throttled = saved("cpu-throttling")["record"]["investigation"]["diagnosis"]["root_cause"]
    assert "20m" in throttled and "CPU" in throttled
    stuck = saved("rollout-stuck-readiness")["record"]["investigation"]["diagnosis"]["root_cause"]
    assert "/ready" in stuck and "404" in stuck


def test_which_scenarios_ended_with_something_to_approve_is_what_the_docs_will_say() -> None:
    approvable = {s for s, r in ROWS.items() if r["status"] == "awaiting_approval"}
    assert approvable == {"oom-killed", "memory-leak", "netpol-blocks-client",
                          "service-selector-mismatch", "wrong-target-port", "bad-image-tag",
                          "pvc-missing-storageclass"}
    diagnosis_only = {s for s, r in ROWS.items() if r["status"] == "completed"}
    assert diagnosis_only == {"cpu-throttling", "dns-missing-service",
                              "crashloop-missing-config-key", "missing-secret",
                              "rollout-stuck-readiness", "rwx-on-ebs", "missing-configmap-mount"}
    assert approvable | diagnosis_only == set(BY_ID)
    for scenario in diagnosis_only:
        proposal = saved(scenario)["record"]["investigation"]["proposal"]
        assert proposal is None or proposal["action"] == "investigate_further", scenario


def test_nothing_was_approved_so_no_pull_request_or_memory_came_from_the_run() -> None:
    for scenario in SCENARIOS:
        record = saved(scenario.id)["record"]
        assert record["approval"] is None, scenario.id
        assert record["status"] in {"awaiting_approval", "completed"}


def test_cost_and_timing_are_the_sum_and_the_range_the_docs_quote() -> None:
    total = round(sum(r["cost_usd"] for r in SUMMARY["scenarios"]), 4)
    assert total == SUMMARY["total_cost_usd"] == 1.2566
    for row in SUMMARY["scenarios"]:
        assert 0.04 < row["cost_usd"] < 0.17, row["scenario"]
        assert 0 < row["seconds_to_decision"] < 40, row["scenario"]
        assert 0 < row["seconds_to_detect"] < 100, row["scenario"]


def test_the_saved_files_hold_no_credential() -> None:
    patterns = [r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", r"gh[pousr]_[A-Za-z0-9]{30,}",
                r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"(?i)aws_secret_access_key"]
    for path in LAB_RESULTS.glob("*"):
        text = path.read_text()
        for pattern in patterns:
            assert not re.search(pattern, text), (path.name, pattern)


# ---------------------------------------------------- the routing measurement, on record


def test_the_routing_measurement_is_saved_and_says_what_it_found() -> None:
    text = (LAB_RESULTS / "routing.txt").read_text()
    assert text.count("TOTAL") == 3 and text.count("36/42") == 3
    assert "cpu-throttling and rollout-stuck-readiness are sent to" in text
    assert "The prompt\nchanges were not kept." in text
    for scenario in ("cpu-throttling", "rollout-stuck-readiness"):
        assert re.search(rf"{scenario}\s+\w+\s+0/3", text)


def test_the_prompt_in_the_code_is_the_one_that_was_measured_as_old() -> None:
    """The prompt wording that did not help is not shipped."""
    assert "cpu_memory: OOMKilled containers, memory pressure, CPU throttling, resource limits\n" \
        in CLASSIFY_PROMPT
    assert "CPU limit" not in CLASSIFY_PROMPT and "HTTP error" not in CLASSIFY_PROMPT


# ----------------------------------------------------------------- the measuring tool


def test_the_tool_loads_every_saved_scenario_with_its_expected_category() -> None:
    records = measure.load_records()
    assert [r[0] for r in records] == sorted(s.id for s in SCENARIOS)
    for scenario, expected, incident in records:
        assert expected == BY_ID[scenario].category
        assert incident["namespace"] == BY_ID[scenario].namespace


def test_the_tool_counts_hits_per_scenario_and_in_total() -> None:
    table = measure.tally({"a": ["network", "network", "storage"], "b": ["storage"] * 3},
                          {"a": "network", "b": "storage"})
    assert table["a"] == {"chosen": {"network": 2, "storage": 1}, "hits": 2, "runs": 3,
                          "expected": "network"}
    assert table["b"]["hits"] == 3
    text = measure.render(table)
    assert "TOTAL" in text and "5/6" in text and "2/3" in text


def test_the_tool_refuses_an_empty_directory(tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    assert measure.main(["--dir", str(tmp_path)]) == 2
    assert "no saved scenarios" in capsys.readouterr().err


def test_the_tool_reads_a_prompt_file_or_keeps_the_current_one(tmp_path: Path) -> None:
    assert measure.read_prompt("current") is None
    path = tmp_path / "p.txt"
    path.write_text("text")
    assert measure.read_prompt(str(path)) == "text"


def test_the_tool_never_touches_a_cluster() -> None:
    source = SCRIPT.read_text()
    for word in ("kubectl", "subprocess", "apply", "delete", "port-forward"):
        assert word not in source, word
