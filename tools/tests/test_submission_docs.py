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
PENDING = {"docs/DEMO-SCRIPT.md", "docs/KIRO-WRITEUP.md"}


def git(*args: str) -> str:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(REPO), *args],  # noqa: S607
        capture_output=True, text=True, check=False,
    ).stdout


def cited_paths(text: str) -> set[str]:
    """Repository paths a document names, in links or in backticks."""
    found: set[str] = set()
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
        if not re.match(r"[a-z]+:", target):
            found.add(target)
    for code in re.findall(r"`([^`\n]+)`", text):
        if ".." in code:
            continue  # an elided path such as agents/orchestrator/.../ui
        if re.fullmatch(r"[\w.\-]+(/[\w.\-]+)+/?", code) and code.split("/")[0] in TOP_LEVEL:
            found.add(code)
    return found


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
    paths = cited_paths(README) - PENDING
    missing = [p for p in sorted(paths) if not (REPO / p).exists()]
    assert missing == []


def test_readme_states_the_learning_loop_is_not_fully_verified() -> None:
    assert "has not been recorded yet" in README
    assert "A second incident that finds the **learned** postmortem" in README
    assert "pull/2" in README
    assert (REPO / "gitops" / "remediations" / "chaos-netpol"
            / "networkpolicy-allow-trusted.yaml").is_file()


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

