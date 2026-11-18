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
PENDING = {"docs/DEMO-SCRIPT.md"}


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


def test_readme_cost_table_covers_all_three_saved_incidents() -> None:
    for name in ("grafana-oom-real.json", "netpol-first-run-real.json",
                 "netpol-second-run-real.json"):
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
         "submission"]

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
def test_every_commit_the_writeup_cites_exists() -> None:
    hashes = set(re.findall(r"`([0-9a-f]{7})`", WRITEUP))
    assert len(hashes) >= 15
    assert [h for h in sorted(hashes) if not commit_exists(h)] == []


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
}


@needs_writeup
def test_kiro_caught_it_lists_each_problem_with_its_commit() -> None:
    section = WRITEUP.split("## Kiro caught it")[1].split("## The real Grafana")[0]
    rows = {int(m.group(1)): m.group(0) for m in re.finditer(r"^\| (\d+) \|.*$", section, re.M)}
    assert sorted(rows) == sorted(CAUGHT)
    for number, (phrase, commit) in CAUGHT.items():
        assert phrase in rows[number], number
        assert f"`{commit}`" in rows[number], number
        assert commit_exists(commit)


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
    patch = (REPO / "gitops" / "remediations" / "chaos-netpol"
             / "networkpolicy-allow-trusted.yaml").read_text()
    stamp = "Approved by Roman Ceresnak at 2026-11-18T07:53:38.681317+00:00"
    assert stamp in patch and stamp in " ".join(WRITEUP.split())
    assert "netpol-trusted" in patch and "netpol-client" in patch
    for rev in ("0c4d20b", "c2ba64d"):
        assert commit_exists(rev)
    assert "pull/2" in WRITEUP


@needs_writeup
def test_writeup_paths_and_links_exist_and_placeholders_are_marked() -> None:
    assert missing_paths(WRITEUP, WRITEUP_PATH.parent) == []
    assert WRITEUP.count("TODO(user)") == 2
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
