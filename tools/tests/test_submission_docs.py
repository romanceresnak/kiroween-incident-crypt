"""submission: the hackathon documents say only what the repository can back up (REQ-DC-01).

Each section is added with its task. Everything here reads the real repository.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
README = (REPO / "README.md").read_text()
TOP_LEVEL = {"agents", "infra", "gitops", "chaos", "docs", "tools", "scripts", ".kiro"}
# Documents the README links to that a later task of this spec writes. Each task removes its
# own entry; the last test of this file fails while any is left.
PENDING: set[str] = set()


def git(*args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(REPO), *args],  # noqa: S607
        capture_output=True, text=True, check=False,
    ).stdout


def cited_paths(text: str, base: Path = REPO) -> set[str]:
    """Repository paths a document names, relative to the repository root.

    Links are resolved from the document's own folder; backticked paths from the root.
    """
    found: set[str] = set()
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
        if not re.match(r"[a-z]+:", target):
            resolved = (base / target).resolve()
            found.add(str(resolved.relative_to(REPO.resolve())) if resolved.is_relative_to(
                REPO.resolve()) else target)
    for code in re.findall(r"`([^`\n]+)`", text):
        if ".." in code:
            continue  # an elided path such as agents/orchestrator/.../ui
        if re.fullmatch(r"[\w.\-]+(/[\w.\-]+)+/?", code) and code.split("/")[0] in TOP_LEVEL:
            found.add(code)
    return found


def missing_paths(text: str, base: Path = REPO) -> list[str]:
    return [p for p in sorted(cited_paths(text, base) - PENDING) if not (REPO / p).exists()]


SHALLOW = git("rev-parse", "--is-shallow-repository").strip() == "true"
# CI checks out one commit, so older hashes are not there. The history checks run locally.
needs_history = pytest.mark.skipif(SHALLOW, reason="shallow clone: older commits are absent")


def commit_exists(rev: str) -> bool:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(REPO), "cat-file", "-e", f"{rev}^{{commit}}"],  # noqa: S607
        capture_output=True, check=False,
    ).returncode == 0


# -------------------------------------------------------------------- licence


def test_license_is_mit_for_the_author_in_2026() -> None:
    text = (REPO / "LICENSE").read_text()
    assert text.startswith("MIT License")
    assert "Copyright (c) 2026 Roman Ceresnak" in text
    assert "Permission is hereby granted, free of charge" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text


def test_kiro_directory_is_tracked_and_never_ignored() -> None:
    """The rules disqualify a submission that ignores /.kiro."""
    ignore = (REPO / ".gitignore").read_text().lower()
    assert not [ln for ln in ignore.splitlines() if "kiro" in ln and not ln.startswith("#")]
    for rel in (".kiro/specs/infra/tasks.md", ".kiro/steering/tech.md",
                ".kiro/settings/mcp.json", ".kiro/hooks/chaos-shellcheck.json",
                ".kiro/specs/submission/tasks.md"):
        assert git("ls-files", "--error-unmatch", rel).strip() == rel or (REPO / rel).exists()
        ignored = subprocess.run(  # noqa: S603
            ["git", "-C", str(REPO), "check-ignore", "-q", rel],  # noqa: S607
            check=False,
        )
        assert ignored.returncode == 1, f"{rel} matches an ignore rule"


# --------------------------------------------------------------------- README

SECTIONS = [
    "The crew", "Architecture", "The flow", "Security model", "What an incident costs",
    "Quickstart", "Demo", "Development", "Teardown",
    "Known trade-offs and what is not verified", "Repository layout", "License",
]


def test_readme_sections_exist_in_order() -> None:
    headings = re.findall(r"^## (.+)$", README, re.M)
    assert [h for h in headings if h in SECTIONS] == SECTIONS


def test_readme_names_every_agent_with_its_spooky_name() -> None:
    for agent, name in [("orchestrator", "Necromancer"), ("cpu-memory", "Vampire"),
                        ("network", "Spider"), ("deploy-rollout", "Zombie"),
                        ("storage", "Ghost"), ("incident-memory", "Archivist")]:
        assert re.search(rf"\| `{agent}` \| \*\*{name}\*\* \|", README), agent
        assert (REPO / "agents" / agent.replace("-", "_")).is_dir()


def test_readme_covers_the_whole_flow_in_order() -> None:
    flow = README.split("## The flow")[1].split("## Security model")[0]
    steps = re.findall(r"^\d+\. \*\*(.+?)\.\*\*", flow, re.M)
    assert steps == ["Watcher", "Classify", "Specialist", "Similar incidents", "Proposal",
                     "Human approval", "Real pull request", "Learned postmortem",
                     "Found next time"]


def mermaid_block(text: str) -> str:
    match = re.search(r"```mermaid\n(.*?)```", text, re.S)
    assert match, "no mermaid block"
    return match.group(1)


def test_mermaid_diagram_is_well_formed() -> None:
    """A structural check; the diagram was also rendered with mermaid-cli 11."""
    diagram = mermaid_block(README)
    lines = [ln.strip() for ln in diagram.splitlines() if ln.strip()]
    assert lines[0].startswith("flowchart ")
    assert sum(ln.startswith("subgraph ") for ln in lines) == sum(ln == "end" for ln in lines)
    assert diagram.count('"') % 2 == 0
    defined = set(re.findall(r"^(\w+)(?:\[|\(\[)", "\n".join(lines), re.M))
    defined |= set(re.findall(r"^subgraph (\w+)", "\n".join(lines), re.M))
    for line in lines:
        edge = re.match(r"^(\w+) -->(?:\|[^|]*\|)? (\w+)$", line)
        if edge:
            assert edge.group(1) in defined and edge.group(2) in defined, line
    for agent in ("Necromancer", "Vampire", "Spider", "Zombie", "Ghost", "Archivist", "Watcher"):
        assert agent in diagram


def test_readme_cost_matches_the_real_incident() -> None:
    data = json.loads((REPO / "docs" / "demo-results" / "grafana-oom-real.json").read_text())
    usage = data["usage_by_agent"]
    total_in = sum(u["input_tokens"] for u in usage.values())
    total_out = sum(u["output_tokens"] for u in usage.values())
    total_cost = sum(u["cost_usd"] for u in usage.values())
    assert f"{total_in:,}" in README and f"{total_out:,}" in README
    assert f"${total_cost:.4f}" in README
    assert f"${usage['cpu-memory']['cost_usd']:.4f}" in README
    assert f"${usage['orchestrator']['cost_usd']:.4f}" in README
    assert f"{usage['orchestrator']['input_tokens']:,}" in README
    assert f"{usage['cpu-memory']['output_tokens']:,}" in README
    from datetime import datetime
    made = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
    done = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
    assert round((done - made).total_seconds()) == 21
    assert "about 21 seconds" in README


def test_readme_prices_match_the_chart() -> None:
    values = (REPO / "gitops" / "charts" / "agents" / "values.yaml").read_text()
    for price in ("1.10", "5.50", "3.30", "16.50"):
        assert price in README
        assert price in values.replace("1.1,", "1.10,") or price.rstrip("0") in values


def test_every_path_the_readme_cites_exists() -> None:
    assert missing_paths(README) == []


def demo_result(name: str) -> dict:
    return json.loads((REPO / "docs" / "demo-results" / name).read_text())


def totals(record: dict) -> tuple[int, int, float]:
    usage = record["usage_by_agent"].values()
    return (sum(u["input_tokens"] for u in usage), sum(u["output_tokens"] for u in usage),
            sum(u["cost_usd"] for u in usage))


def test_readme_cost_table_covers_all_five_saved_incidents() -> None:
    for name in ("grafana-oom-real.json", "netpol-first-run-real.json",
                 "netpol-second-run-real.json", "badimage-real.json", "pvc-real.json"):
        tin, tout, cost = totals(demo_result(name))
        row = f"| {tin:,} | {tout:,} | ${cost:.4f} |"
        assert row in README, name


def test_readme_learning_loop_claims_match_the_saved_runs() -> None:
    first = demo_result("netpol-first-run-real.json")
    second = demo_result("netpol-second-run-real.json")
    # Run 1 had nothing learned to find; run 2 found run 1's postmortem first.
    assert {s["origin"] for s in first["similar_incidents"]} == {"seeded"}
    top = second["similar_incidents"][0]
    assert top["origin"] == "learned"
    assert top["source"].endswith(f"learned/{first['incident']['incident_id']}.md")
    assert top["score"] == max(s["score"] for s in second["similar_incidents"])
    assert f"score {top['score']:.3f}" in README
    others = [s for s in second["similar_incidents"] if s["origin"] == "seeded"]
    for hit in others:
        assert f"{hit['score']:.3f}" in README
    # Each run wrote a learned postmortem and started an ingestion.
    for run in (first, second):
        learning = run["approval"]["learning"]
        assert learning["status"] == "written" and learning["ingestion"] == "started"
        assert run["approval"]["pull_request"]["mode"] == "github"
    for number, run in ((2, first), (3, second)):
        url = run["approval"]["pull_request"]["url"]
        assert url.endswith(f"/pull/{number}") and url in README
    from datetime import datetime
    gap = (datetime.fromisoformat(second["created_at"].replace("Z", "+00:00"))
           - datetime.fromisoformat(first["created_at"].replace("Z", "+00:00")))
    assert round(gap.total_seconds() / 60) == 14 and "14 minutes later" in README


def test_readme_badimage_and_pvc_rows_match_the_saved_records() -> None:
    from datetime import datetime

    def parse(text: str) -> datetime:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))

    for name, category, proposed in (("badimage-real.json", "deploy_rollout", "python:3.12-slim"),
                                     ("pvc-real.json", "storage", "gp2")):
        record = demo_result(name)
        assert record["status"] == "awaiting_approval" and record["approval"] is None
        assert record["classification"]["category"] == category
        assert record["classification"]["confidence"] == "high"
        assert record["investigation"]["outcome"] == "action_required"
        assert record["incident"]["source"] == "watcher"
        evidence = len(record["investigation"]["diagnosis"]["evidence"])
        seconds = round((parse(record["updated_at"]) - parse(record["created_at"]))
                        .total_seconds())
        assert (f"classified `{category}` (high), {evidence} pieces of evidence, "
                f"`awaiting_approval` {seconds} s after detection") in README, name
        assert proposed in record["investigation"]["proposal"]["proposed"]
        assert f"`{proposed}`" in README or f"`{proposed}`," in README
    assert "Not approved." in README


def test_the_raw_export_holds_every_incident_that_the_docs_cite() -> None:
    export = REPO / "docs" / "demo-results" / "export"
    listing = json.loads((export / "incidents-list.json").read_text())
    ids = {r["incident"]["incident_id"] for r in listing}
    assert {"2e4fd60547e332f6", "009de3634d72afbc", "1384b03838aa51eb",
            "af99b9217f60475f"} <= ids
    for iid in ids:
        record = json.loads((export / f"{iid}.json").read_text())
        assert record["incident"]["incident_id"] == iid
    # The correlated signals point at an incident that is in the export.
    for record in listing:
        if record["related_to"]:
            assert record["related_to"] in ids
    for name, iid in (("badimage-real.json", "1384b03838aa51eb"),
                      ("pvc-real.json", "af99b9217f60475f"),
                      ("netpol-first-run-real.json", "2e4fd60547e332f6"),
                      ("netpol-second-run-real.json", "009de3634d72afbc")):
        assert demo_result(name) == json.loads((export / f"{iid}.json").read_text())


def test_readme_says_what_the_single_observation_does_not_show() -> None:
    assert "That the learned hit generalises" in README
    assert "pull/2" in README
    assert (REPO / "gitops" / "remediations" / "chaos-netpol"
            / "networkpolicy-allow-trusted.yaml").is_file()


def test_readme_does_not_claim_remediations_are_applied() -> None:
    """No Application syncs gitops/remediations, so the README must not say Argo applies it."""
    apps = "".join(p.read_text() for p in (REPO / "gitops" / "apps").glob("*.y*ml"))
    assert "remediations" not in apps, "an Application now applies them: update the docs"
    assert "Approved changes are not applied" in README
    assert "no Argo CD Application applies `gitops/remediations/`" in README
    assert "that Argo CD applies" not in README
    assert "Merging a pull request did not change the cluster" in README


def test_readme_hook_claim_is_true() -> None:
    assert len(list((REPO / ".kiro" / "hooks").glob("*.json"))) == 5
    assert "Five Kiro hooks" in README


def test_the_commands_in_the_readme_use_real_scripts_and_outputs() -> None:
    outputs = (REPO / "infra" / "outputs.tf").read_text()
    for out in re.findall(r"terraform output -raw (\w+)", README):
        assert f'output "{out}"' in outputs
    for script in re.findall(r"(?<![\w/-])((?:chaos/\w+/|scripts/)[\w-]+\.sh)", README):
        assert (REPO / script).is_file(), script
    for area in ("oom", "netpol", "badimage", "pvc"):
        assert (REPO / "chaos" / area / f"{area}.sh").is_file()


@pytest.mark.parametrize("model", [
    "eu.anthropic.claude-haiku-4-5-20251001-v1:0", "eu.anthropic.claude-sonnet-4-6"])
def test_readme_model_ids_are_the_ones_in_the_chart(model: str) -> None:
    assert model in README
    assert model in (REPO / "gitops" / "charts" / "agents" / "values.yaml").read_text()



# ----------------------------------------------------------------- write-up

WRITEUP_PATH = REPO / "docs" / "KIRO-WRITEUP.md"
WRITEUP = WRITEUP_PATH.read_text() if WRITEUP_PATH.exists() else ""
SPECS = ["infra", "agents-core", "specialists", "hardening", "memory-and-ui", "polish",
         "submission", "replay", "scenario-lab"]

needs_writeup = pytest.mark.skipif(not WRITEUP, reason="docs/KIRO-WRITEUP.md not written yet")


def spec_counts(name: str) -> tuple[int, int]:
    base = REPO / ".kiro" / "specs" / name
    tasks = len(re.findall(r"^## TASK-\d+", (base / "tasks.md").read_text(), re.M))
    reqs = len(set(re.findall(r"REQ-[A-Z]+-\d+", (base / "requirements.md").read_text())))
    return tasks, reqs


@needs_writeup
@pytest.mark.parametrize("name", SPECS)
def test_writeup_spec_counts_match_the_spec_files(name: str) -> None:
    tasks, reqs = spec_counts(name)
    row = re.search(rf"^\| `{name}` \| (\d+) \| (\d+) \|", WRITEUP, re.M)
    assert row, name
    assert (int(row.group(1)), int(row.group(2))) == (tasks, reqs)


@needs_writeup
def test_the_writeup_cites_enough_commits() -> None:
    assert len(set(re.findall(r"`([0-9a-f]{7})`", WRITEUP))) >= 15


@needs_writeup
@needs_history
def test_every_commit_the_writeup_cites_exists() -> None:
    hashes = set(re.findall(r"`([0-9a-f]{7})`", WRITEUP))
    assert [h for h in sorted(hashes) if not commit_exists(h)] == []
    for _, commit in CAUGHT.values():
        assert commit_exists(commit)
    for rev in ("0c4d20b", "c2ba64d"):
        assert commit_exists(rev)


CAUGHT = {
    1: ("bootstrap_cluster_creator_admin_permissions", "8119229"),
    2: ("aws_s3vectors_vector_bucket", "1c80868"),
    3: ("169.254.170.23", "3f4ba06"),
    4: ("AccessDeniedException", "368732b"),
    5: ("immutable OIDC subjects", "ce20d4f"),
    6: ("bash -eo pipefail", "313231b"),
    7: ("ecr:BatchGetImage", "1913353"),
    8: ("OOM-killed", "fdb0d1c"),
    9: ("HTMLDListElement", "7de072f"),
    10: ("three times", "cb84a75"),
    11: ("415", "a9c4449"),
    12: ("../auth/logout", "a9c4449"),
    13: ("36 of 42", "fbfee86"),
}


@needs_writeup
def test_kiro_caught_it_lists_each_problem_with_its_commit() -> None:
    section = WRITEUP.split("## Kiro caught it")[1].split("## The real Grafana")[0]
    rows = {int(m.group(1)): m.group(0) for m in re.finditer(r"^\| (\d+) \|.*$", section, re.M)}
    assert sorted(rows) == sorted(CAUGHT)
    for number, (phrase, commit) in CAUGHT.items():
        assert phrase in rows[number], number
        assert f"`{commit}`" in rows[number], number


@needs_writeup
def test_writeup_answers_every_question_the_rules_ask() -> None:
    for heading in ("## Which Kiro: IDE, CLI, Web, Crew", "## Specs", "## Steering",
                    "## MCP servers", "## Hooks", "## Testing without property-based tests"):
        assert heading in WRITEUP, heading
    assert "Compared with vibe coding" in WRITEUP
    assert "Custom agents | **No.**" in WRITEUP
    assert "Property-based testing | **No.**" in WRITEUP
    assert "Powers | **No.**" in WRITEUP


@needs_writeup
def test_the_features_it_says_were_not_used_were_not_used() -> None:
    agents_dir = REPO / ".kiro" / "agents"
    assert not agents_dir.exists() or not [p for p in agents_dir.rglob("*") if p.is_file()]
    assert not (REPO / ".kiro" / "powers").exists()
    for pyproject in (REPO / "agents").glob("*/pyproject.toml"):
        assert "hypothesis" not in pyproject.read_text(), pyproject


@needs_writeup
def test_writeup_mcp_and_hooks_match_the_configuration() -> None:
    mcp = json.loads((REPO / ".kiro" / "settings" / "mcp.json").read_text())["mcpServers"]
    for name, server in mcp.items():
        package = server["args"][0].removesuffix("@latest")
        assert f"| `{name}` | `{package}` |" in WRITEUP, name
    hooks = sorted(p.stem for p in (REPO / ".kiro" / "hooks").glob("*.json"))
    assert len(hooks) == 5
    for hook in hooks:
        assert f"| `{hook}` |" in WRITEUP, hook
    steering = REPO / ".kiro" / "steering"
    for doc in sorted(steering.glob("*.md")):
        lines = len(doc.read_text().splitlines())
        assert f"| `{doc.name}` | {lines} |" in WRITEUP, doc.name


@needs_writeup
def test_writeup_grafana_and_pull_request_facts_match_the_evidence() -> None:
    data = json.loads((REPO / "docs" / "demo-results" / "grafana-oom-real.json").read_text())
    evidence = data["investigation"]["diagnosis"]["evidence"]
    assert f"returned {len(evidence)} pieces of evidence" in WRITEUP
    assert "234900Ki" in WRITEUP and any("234900Ki" in e["value"] for e in evidence)
    proposal = data["investigation"]["proposal"]
    assert "256Mi" in json.dumps(proposal) and "512Mi" in json.dumps(proposal)
    total = sum(u["cost_usd"] for u in data["usage_by_agent"].values())
    assert f"${total:.4f}" in WRITEUP
    assert "pull/2" in WRITEUP


REMEDIATION = (REPO / "gitops" / "remediations" / "chaos-netpol"
               / "networkpolicy-allow-trusted.yaml")


def header(text: str) -> dict[str, str]:
    """The incident and the approval stamp in a remediation file's comment header."""
    incident = re.search(r"^# Approved remediation for incident (\S+) ", text, re.M)
    stamp = re.search(r"^# (Approved by .+? at (\S+?))\.$", text, re.M)
    assert incident and stamp, "the remediation file has no approval header"
    return {"incident": incident.group(1), "stamp": stamp.group(1), "time": stamp.group(2)}


def body(text: str) -> str:
    return "".join(line for line in text.splitlines(keepends=True) if not line.startswith("#"))


def pull_requests() -> dict[str, dict]:
    """Each real approved run by incident id: its pull request number and the file it sent."""
    runs = {}
    for name in ("netpol-first-run-real.json", "netpol-second-run-real.json"):
        record = demo_result(name)
        pr = record["approval"]["pull_request"]
        number = int(re.search(r"/pull/(\d+)$", pr["url"]).group(1))
        runs[record["incident"]["incident_id"]] = {"number": number, "content": pr["content"]}
    return runs


@needs_writeup
def test_writeup_says_which_pull_requests_version_of_the_remediation_is_on_main() -> None:
    """The stamp, the incident and the pull request number come from the file and the saved
    records, so the next pull request that rewrites the file shows up here as a failure."""
    on_main = header(REMEDIATION.read_text())
    runs = pull_requests()
    assert on_main["incident"] in runs, f"no saved record for {on_main['incident']}"
    text = " ".join(WRITEUP.split())
    number = runs[on_main["incident"]]["number"]
    assert f"the version on `main` is pull request #{number}'s" in text
    assert on_main["time"] in text and on_main["incident"] in text
    # The saved pull request carried exactly the file that is on main.
    assert runs[on_main["incident"]]["content"] == REMEDIATION.read_text()
    # The other run's version is described as the earlier one, with its own stamp.
    (other_id,) = set(runs) - {on_main["incident"]}
    other = header(runs[other_id]["content"])
    assert other["time"] in text and other_id in text
    assert f"pull request #{runs[other_id]['number']}" in text
    assert "changed only the two header lines" in text
    assert "is identical in both" in text
    # "Identical" is checked, not assumed: only the comment header differs between the runs.
    assert body(runs[on_main["incident"]]["content"]) == body(runs[other_id]["content"])
    assert header(runs[on_main["incident"]]["content"]) != other
    assert "netpol-trusted" in body(REMEDIATION.read_text())
    assert "netpol-client" in body(REMEDIATION.read_text())


@needs_writeup
def test_writeup_does_not_credit_the_stamp_on_main_to_the_other_pull_request() -> None:
    on_main = header(REMEDIATION.read_text())
    runs = pull_requests()
    wrong = [r["number"] for incident, r in runs.items() if incident != on_main["incident"]]
    text = " ".join(WRITEUP.split())
    for number in wrong:
        assert f"the version on `main` is pull request #{number}'s" not in text


@needs_writeup
@needs_history
def test_writeup_pull_request_commits_exist_and_merge_order_is_as_stated() -> None:
    for rev in ("0c4d20b", "c2ba64d", "4fd7a65", "8cd8ee4"):
        assert commit_exists(rev), rev
        assert f"`{rev}`" in WRITEUP, rev
    # Pull request #2 was merged before #3, which is why #3's file replaced #2's.
    first = git("log", "-1", "--format=%at", "c2ba64d").strip()
    second = git("log", "-1", "--format=%at", "8cd8ee4").strip()
    assert int(first) < int(second)


@needs_writeup
def test_writeup_paths_and_links_exist_and_no_placeholder_is_left() -> None:
    assert missing_paths(WRITEUP, WRITEUP_PATH.parent) == []
    assert WRITEUP.count("TODO(user)") == 0
    assert "not verified" in WRITEUP.lower()


@needs_writeup
def test_writeup_second_run_facts_match_the_saved_records() -> None:
    first = demo_result("netpol-first-run-real.json")
    second = demo_result("netpol-second-run-real.json")
    top = second["similar_incidents"][0]
    assert f"**learned {top['score']:.3f} (LRN-{first['incident']['incident_id']})**" in WRITEUP
    for hit in first["similar_incidents"]:
        assert f"seeded {hit['score']:.3f}" in WRITEUP
    job = second["approval"]["learning"]["ingestion_job_id"]
    assert f"`{job}`" in WRITEUP
    assert f"`learned/{second['incident']['incident_id']}.md`" in WRITEUP
    assert second["approval"]["pull_request"]["url"] in WRITEUP
    for name in ("grafana-oom-real.json", "netpol-first-run-real.json",
                 "netpol-second-run-real.json"):
        assert f"${totals(demo_result(name))[2]:.4f}" in WRITEUP, name
    assert "Still not verified" in WRITEUP
    assert "Not verified:** that a second" not in WRITEUP


# -------------------------------------------------------------- demo script

DEMO_PATH = REPO / "docs" / "DEMO-SCRIPT.md"
DEMO = DEMO_PATH.read_text() if DEMO_PATH.exists() else ""
needs_demo = pytest.mark.skipif(not DEMO, reason="docs/DEMO-SCRIPT.md not written yet")


def shot_rows() -> list[tuple[int, int, str]]:
    rows = []
    for m in re.finditer(r"^\| (\d+):(\d{2}) \| (\d+) \| ([^|]+) \|", DEMO, re.M):
        rows.append((int(m.group(1)) * 60 + int(m.group(2)), int(m.group(3)), m.group(4).strip()))
    return rows


@needs_demo
def test_demo_timings_are_contiguous_and_under_three_minutes() -> None:
    rows = shot_rows()
    assert len(rows) == 9
    clock = 0
    for start, seconds, name in rows:
        assert start == clock, name
        clock += seconds
    assert clock <= 170, f"{clock} s"
    assert "2:50" in DEMO and clock == 170


def narration() -> dict[int, int]:
    """Words per shot. The alternative lines of shot 5 count once, as the longer one."""
    words: dict[int, int] = {}
    for m in re.finditer(r"^\*\*Shot (\d+) \((\d+) s\)\.\*\* (.+?)(?:\n\n|\Z)", DEMO, re.M | re.S):
        text = m.group(3)
        variants = [len(v.split()) for v in re.findall(r"\*[^*]+\*", text)]
        base = len(re.sub(r"\*[^*]+\*", " ", text).split())
        words[int(m.group(1))] = base + (max(variants) if variants else 0)
    return words


@needs_demo
def test_narration_fits_each_shot_at_a_speakable_pace() -> None:
    words = narration()
    lengths = {i + 1: seconds for i, (_, seconds, _) in enumerate(shot_rows())}
    assert sorted(words) == sorted(lengths)
    for shot, count in words.items():
        assert count <= 2.8 * lengths[shot], (shot, count, lengths[shot])


@needs_demo
def test_demo_shows_everything_the_brief_asks_for() -> None:
    for needle in ("Necromancer then the Spider animating", "Evidence table",
                   "Similar past incidents", "Approve: open the PR", "pull request", "learned",
                   "Kiroween Incident Crypt", "cost per incident", "ingestion job"):
        assert needle in DEMO, needle
    assert "second" in DEMO.lower() and "10 minutes" in DEMO and "600 s" in DEMO


@needs_demo
def test_demo_labels_and_ids_are_the_real_ones() -> None:
    ui = REPO / "agents" / "orchestrator" / "src" / "kiroween_orchestrator" / "ui"
    page = (ui / "index.html").read_text() + (ui / "app.js").read_text()
    for label in ("Approve: open the PR", "Similar past incidents", "Incident feed", "Evidence",
                  "Incidents rise; the crew investigates; only the living approve."):
        assert label in page, label
    values = (REPO / "gitops" / "charts" / "agents" / "values.yaml").read_text()
    for ident in ("VIBAT27SDH", "UCADW10RQV", "kiroween-prod-postmortems-255834079310"):
        assert ident in values and ident in DEMO, ident
    dash = json.loads((REPO / "gitops" / "charts" / "agents" / "dashboards"
                       / "kiroween-agents.json").read_text())
    assert f"/d/{dash['uid']}" in DEMO
    assert "kiroween-prod" in DEMO
    assert (REPO / "chaos" / "netpol" / "netpol.sh").is_file()
    assert "chaos/netpol/netpol.sh up" in DEMO and "chaos/netpol/netpol.sh down" in DEMO


@needs_demo
def test_the_rehearsal_one_liner_works_on_a_real_record() -> None:
    match = re.search(r"python3 -c '([^']+)'", DEMO)
    assert match
    record = demo_result("netpol-second-run-real.json")
    result = subprocess.run(  # noqa: S603
        ["python3", "-c", match.group(1)],  # noqa: S607
        input=json.dumps([record]), capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "approved"
    assert lines[1].startswith("learned 0.846 LRN-2e4fd60547e332f6")
    assert lines[2].startswith("seeded 0.708")


@needs_demo
def test_demo_does_not_overclaim() -> None:
    assert "Do not say the pull request fixed the cluster" in DEMO
    assert "applying it is not automated yet" in DEMO
    assert missing_paths(DEMO, DEMO_PATH.parent) == []
    assert DEMO.count("TODO(user)") == 1
    assert "has not been observed" not in DEMO


# ------------------------------------------------------------ blog and social

BLOG_PATH = REPO / "docs" / "BLOG-DRAFT.md"
BLOG = BLOG_PATH.read_text() if BLOG_PATH.exists() else ""
SOCIAL_PATH = REPO / "docs" / "SOCIAL-POST.md"
SOCIAL = SOCIAL_PATH.read_text() if SOCIAL_PATH.exists() else ""
needs_blog = pytest.mark.skipif(not BLOG, reason="docs/BLOG-DRAFT.md not written yet")
needs_social = pytest.mark.skipif(not SOCIAL, reason="docs/SOCIAL-POST.md not written yet")


@needs_blog
def test_blog_says_it_is_part_of_the_hackathon_and_is_about_kiro_and_the_project() -> None:
    assert "part of my entry to the Kiroween 2026 hackathon" in BLOG
    assert BLOG.startswith("# ")
    assert len(re.findall(r"\bKiro\b", BLOG)) >= 8
    assert "https://github.com/romanceresnak/kiroween-incident-crypt" in BLOG
    assert 700 <= len(BLOG.split()) <= 1700


@needs_blog
def test_blog_numbers_come_from_the_saved_records_and_the_specs() -> None:
    first = demo_result("netpol-first-run-real.json")
    second = demo_result("netpol-second-run-real.json")
    for name in ("grafana-oom-real.json", "netpol-first-run-real.json",
                 "netpol-second-run-real.json"):
        assert f"${totals(demo_result(name))[2]:.4f}" in BLOG, name
    top = second["similar_incidents"][0]
    assert f"{top['score']:.3f}" in BLOG
    for hit in first["similar_incidents"]:
        assert f"{hit['score']:.3f}" in BLOG
    from datetime import datetime

    def parse(text: str) -> datetime:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))

    seconds = (parse(first["approval"]["at"]) - parse(first["created_at"])).total_seconds()
    assert f"took {round(seconds)} seconds" in BLOG
    assert len(first["investigation"]["diagnosis"]["evidence"]) == 6
    assert "six pieces of evidence" in BLOG
    for number in (2, 3):
        assert f"pull/{number})" in BLOG
    tasks = sum(spec_counts(name)[0] for name in SPECS)
    reqs = sum(spec_counts(name)[1] for name in SPECS)
    assert len(SPECS) == 9
    assert f"nine specs, {tasks} tasks and more than {reqs // 10 * 10} requirement IDs" in BLOG


@needs_blog
def test_blog_states_its_limits() -> None:
    text = " ".join(BLOG.split())
    for phrase in ("does not change the cluster yet", "one observation", "What I did not use",
                   "not checked them against the AWS price list", "never run inside a Kiro",
                   "Approved changes are recorded, not applied"):
        assert phrase in text, phrase
    assert "TODO" not in BLOG
    assert missing_paths(BLOG, BLOG_PATH.parent) == []


@needs_social
def test_social_post_follows_the_rules() -> None:
    match = re.search(r"```text\n(.+?)\n```", SOCIAL, re.S)
    assert match
    post = match.group(1)
    assert post.startswith("#KiroweenHackathon26 ")
    assert len(post) <= 280, len(post)
    assert "Kiro" in post
    assert "favourite" in post.lower() or "favorite" in post.lower()
    assert "github.com/romanceresnak/kiroween-incident-crypt" in post
    assert SOCIAL.count("TODO(user)") == 2


# ------------------------------------------------------------------- Devpost

DEVPOST_PATH = REPO / "docs" / "DEVPOST.md"
DEVPOST = DEVPOST_PATH.read_text() if DEVPOST_PATH.exists() else ""
needs_devpost = pytest.mark.skipif(not DEVPOST, reason="docs/DEVPOST.md not written yet")


@needs_devpost
def test_devpost_has_every_section_in_order() -> None:
    headings = re.findall(r"^## (.+)$", DEVPOST, re.M)
    wanted = ["Inspiration", "What it does", "How I built it", "Challenges", "Accomplishments",
              "What is next", "Category", "Built with"]
    assert [h for h in headings if h in wanted] == wanted


@needs_devpost
def test_devpost_category_is_frankenstein_and_quotes_the_rule() -> None:
    category = DEVPOST.split("## Category")[1].split("## Built with")[0]
    assert "**Frankenstein**" in category
    assert "Stitch together a chimera of technologies into one app" in category
    assert "seemingly incompatible elements" in category


@needs_devpost
def test_devpost_answers_all_five_judging_criteria() -> None:
    table = DEVPOST.split("## How this maps to the judging criteria")[1].split("## Links")[0]
    for criterion in ("Potential impact", "Technological implementation", "Design", "Innovation",
                      "Presentation"):
        assert f"| {criterion} |" in table, criterion


@needs_devpost
def test_devpost_numbers_match_the_repository() -> None:
    tasks = sum(spec_counts(name)[0] for name in SPECS)
    reqs = sum(spec_counts(name)[1] for name in SPECS)
    assert f"nine specs, {tasks} tasks and {reqs} requirement IDs" in DEVPOST
    first = demo_result("netpol-first-run-real.json")
    second = demo_result("netpol-second-run-real.json")
    top = second["similar_incidents"][0]
    assert f"score {top['score']:.3f}" in DEVPOST
    for hit in first["similar_incidents"]:
        assert f"{hit['score']:.3f}" in DEVPOST
    for name in ("grafana-oom-real.json", "netpol-first-run-real.json",
                 "netpol-second-run-real.json"):
        assert f"${totals(demo_result(name))[2]:.4f}" in DEVPOST, name
    graf = demo_result("grafana-oom-real.json")["investigation"]["diagnosis"]["evidence"]
    assert f"{len(graf)} pieces of" in DEVPOST
    for number, run in ((2, first), (3, second)):
        assert run["approval"]["pull_request"]["url"] in DEVPOST
        assert run["approval"]["pull_request"]["url"].endswith(f"/pull/{number}")
    assert len(list((REPO / "docs" / "postmortems").glob("*.md"))) == 8
    assert "eight postmortems" in DEVPOST
    ui_tests = len(re.findall(r"^\s*test\(", "".join(
        (REPO / "tools" / "ui-tests" / name).read_text()
        for name in ("ui.test.mjs", "motion.test.mjs")), re.M))
    assert f"{ui_tests} jsdom tests" in DEVPOST


@needs_devpost
def test_devpost_states_the_limits_and_the_url_decision() -> None:
    text = " ".join(DEVPOST.split())
    assert "merging the pull request does not change the cluster yet" in text
    assert "URL to your working app" in text
    assert "no public URL by design" not in text
    assert "More than 900 automated tests" in text
    assert "Best Kiro Crew App" in text and "is not claimed" in text
    assert DEVPOST.count("TODO(user)") == 4


@needs_devpost
def test_devpost_links_exist() -> None:
    assert missing_paths(DEVPOST, DEVPOST_PATH.parent) == []
    assert "https://github.com/romanceresnak/kiroween-incident-crypt" in DEVPOST
    assert "docs/KIRO-WRITEUP.md" in DEVPOST and "#kiro-caught-it" in DEVPOST
    assert "## Kiro caught it" in WRITEUP


# ------------------------------------------------------------- placeholders

EXPECTED_TODOS = {
    "README.md": 0,
    "docs/KIRO-WRITEUP.md": 0,
    "docs/DEMO-SCRIPT.md": 1,
    "docs/BLOG-DRAFT.md": 0,
    "docs/SOCIAL-POST.md": 2,
    "docs/DEVPOST.md": 4,
}


def test_only_the_known_placeholders_remain() -> None:
    """Every TODO(user) is a thing only the author can supply; the list is in tasks.md."""
    found = {name: (REPO / name).read_text().count("TODO(user)") for name in EXPECTED_TODOS}
    assert found == EXPECTED_TODOS
    status = (REPO / ".kiro" / "specs" / "submission" / "tasks.md").read_text()
    assert f"{sum(EXPECTED_TODOS.values())} placeholders" in status


def test_no_document_leaves_a_stray_marker() -> None:
    for name in EXPECTED_TODOS:
        stripped = (REPO / name).read_text().replace("TODO(user)", "")
        for marker in ("TODO", "FIXME", "XXX", "TBD", "lorem"):
            assert marker not in stripped, (name, marker)


# -------------------------------------------------------------------- the replay

PAGES = "https://romanceresnak.github.io/kiroween-incident-crypt/"


def test_the_readme_links_the_replay_and_says_what_it_is() -> None:
    text = " ".join(README.replace("\n> ", "\n").split())
    assert f"[replay]({PAGES})" in text
    assert "read-only [replay]" in text and "No login needed" in text
    assert "no public endpoint" not in text
    assert "by `kubectl port-forward`, never through" not in text
    assert "## Replay" in README and "tools/replay/build.py" in README
    assert "nine specs" in README and "eight specs" not in README


@needs_devpost
def test_devpost_gives_the_replay_as_the_working_app_and_drops_the_open_decision() -> None:
    text = " ".join(DEVPOST.split())
    section = DEVPOST.split('## "URL to your working app"')[1].split("## Prizes")[0]
    assert f"**<{PAGES}>**" in section
    assert "is a replay" in " ".join(section.split())
    assert "no public URL by design" not in text
    assert f"- Public demo, no login (replay): <{PAGES}>" in DEVPOST
    assert "a decision for you" not in text
    assert "Options, none built" not in text
    assert "Expose the UI behind authentication" not in text


def test_the_pages_url_follows_the_repository_and_matches_the_workflow_and_the_build() -> None:
    assert PAGES == "https://romanceresnak.github.io/kiroween-incident-crypt/"
    workflow = (REPO / ".github" / "workflows" / "replay-pages.yml").read_text()
    assert "actions/deploy-pages@" in workflow
    build = (REPO / "tools" / "replay" / "build.py").read_text()
    assert "behind a login" in build
    for doc in (README, DEVPOST, BLOG):
        assert PAGES in doc


@needs_blog
def test_the_blog_names_the_replay_and_says_it_is_one() -> None:
    text = " ".join(BLOG.split())
    assert f"<{PAGES}>" in BLOG
    assert "read-only replay of the real UI" in text and "needs no login" in text
    assert "no public endpoint" not in text


# ------------------------------------------------------------- the Scenario Lab claims

LIVE = "https://d3uh8kq3w5msy5.cloudfront.net"
LAB_RESULTS = REPO / "docs" / "demo-results" / "lab"


def lab_summary() -> dict:
    return json.loads((LAB_RESULTS / "summary.json").read_text())


def test_lab_numbers_in_the_docs_come_from_the_saved_run() -> None:
    summary = lab_summary()
    rows = summary["scenarios"]
    assert len(rows) == 14
    total = f"${summary['total_cost_usd']:.2f}"
    assert total == "$1.26"
    approvable = [r for r in rows if r["status"] == "awaiting_approval"]
    assert len(approvable) == 7
    for text in (README, DEVPOST, BLOG, WRITEUP):
        assert total in text or "1.26 USD" in text
    flat = " ".join(README.split())
    assert "All 14 produced an incident in their own namespace" in flat
    assert "Twelve behaved as their card says. Two did not" in flat
    for row in rows:
        assert f"`{row['scenario']}`" in README, row["scenario"]
        if row["status"] == "awaiting_approval":
            assert f"a fix to approve: `{row['proposal_field']}`" in README


def test_the_two_misrouted_scenarios_are_named_and_match_the_saved_records() -> None:
    rows = {r["scenario"]: r for r in lab_summary()["scenarios"]}
    wrong = sorted(n for n, r in rows.items() if r["category_observed"] != r["category_expected"])
    assert wrong == ["cpu-throttling", "rollout-stuck-readiness"]
    for name in wrong:
        assert rows[name]["category_observed"] == "network"
        assert name in README
    routing = (LAB_RESULTS / "routing.txt").read_text()
    assert routing.count("36/42") >= 3
    assert "36 of 42" in README and "36 of 42" in WRITEUP


def test_the_docs_give_the_live_address_and_the_lab_limits() -> None:
    for text in (README, DEVPOST, BLOG):
        assert LIVE in text
    flat = " ".join(README.split())
    assert "one scenario at a time, ten runs an hour, cleaned up by itself after 15 minutes" in flat
    assert "`lab-remediations`" in README and "`lab-remediations`" in DEVPOST
    assert "nothing is saved as memory" in flat or "nothing is saved as\n" in README
    main_tf = (REPO / "infra" / "variables.tf").read_text()
    assert 'default     = 400' in main_tf or "default = 400" in main_tf


def test_nobody_is_told_the_live_system_has_no_login() -> None:
    for name, text in (("README", README), ("DEVPOST", DEVPOST), ("BLOG", BLOG)):
        flat = " ".join(text.split())
        assert "has no login" not in flat, name
        assert "no public endpoint" not in flat, name
        assert "only reachable through" not in flat, name


def test_the_devpost_testing_steps_name_what_a_judge_can_and_cannot_do() -> None:
    section = DEVPOST.split('## "URL to your working app"')[1].split("## Prizes")[0]
    flat = " ".join(section.split())
    for needle in ("judges-only instructions field", "Scenario Lab", "**Run**", "**Approve**",
                   "**Reset**", "never on `main`", "lab incidents only"):
        assert needle in flat, needle


def test_the_lab_02_update_matches_the_saved_measurement_and_says_what_it_did_not_measure() -> None:
    hint = (LAB_RESULTS / "routing-cpu-hint.txt").read_text()
    flat = " ".join(README.split())
    assert "Update, 19 Nov, for lab-02 only" in flat
    # The numbers the docs quote are the ones in the record.
    assert re.search(r"^cpu-throttling\s+cpu_memory\s+10/10", hint.split("### E:")[1], re.M)
    assert re.search(r"^cpu-throttling\s+cpu_memory\s+0/3", hint.split("### B:")[1], re.M)
    assert "10 of 10" in flat and "(0 of 3)" in flat
    # The assumption is stated where the number is, and in the record itself.
    assert "usage **assumed**" in flat and "has not been seen for a lab-02 pod" in flat
    assert "WHAT WAS NOT MEASURED: CPU usage" in hint
    assert "not yet seen on the cluster" in " ".join(DEVPOST.split())
    assert "not seen on the cluster" in " ".join(WRITEUP.split())
    for doc in (README, WRITEUP):
        assert "routing-cpu-hint.txt" in doc
    # The table is the 18 Nov run and still says what happened then.
    assert "**Spider** (card says Vampire)" in README


def test_the_experimental_scenario_in_the_docs_matches_the_code_and_the_record() -> None:
    import sys

    sys.path.insert(0, str(REPO / "agents" / "orchestrator" / "src"))
    sys.path.insert(0, str(REPO / "agents" / "shared" / "src"))
    from kiroween_orchestrator.lab.scenarios import SCENARIOS

    flagged = [s for s in SCENARIOS if s.experimental]
    assert [s.id for s in flagged] == ["rollout-stuck-readiness"]
    rest = len(SCENARIOS) - len(flagged)
    assert rest == 13
    hint = (LAB_RESULTS / "routing-cpu-hint.txt").read_text()
    row = re.search(r"^rollout-stuck-readiness\s+\w+\s+(\d+)/(\d+)", hint.split("### E:")[1], re.M)
    assert row and (row.group(1), row.group(2)) == ("1", "10")
    for name, text in (("README", README), ("DEVPOST", DEVPOST)):
        flat = " ".join(text.split())
        assert "Experimental" in flat and "An update that never becomes ready" in flat, name
        assert "1 of 10" in flat and f"the other {rest}" in flat, name
    assert "It still runs" in " ".join(README.split())
    assert "it runs" in " ".join(DEVPOST.split())
