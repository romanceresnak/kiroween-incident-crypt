"""replay TASK-03: static checks on .github/workflows/replay-pages.yml (REQ-DP-01 to DP-05)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).parents[2]
PATH = REPO / ".github" / "workflows" / "replay-pages.yml"
TEXT = PATH.read_text()
FULL_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def workflow() -> dict:
    data = yaml.safe_load(TEXT)
    data["on"] = data.pop(True, data.get("on"))  # PyYAML reads the bare key "on" as True
    return data


def steps(job: dict) -> list[dict]:
    return job["steps"]


def uses(job: dict) -> list[str]:
    return [s["uses"] for s in steps(job) if "uses" in s]


def test_every_action_is_pinned_to_a_full_sha_with_its_version_in_a_comment() -> None:
    for name, job in workflow()["jobs"].items():
        for ref in uses(job):
            assert FULL_SHA.match(ref), (name, ref)
    for line in TEXT.splitlines():
        if "uses:" in line and not line.lstrip().startswith("#"):
            assert re.search(r"@[0-9a-f]{40} # v\d+\.\d+\.\d+$", line), line


def test_the_pinned_actions_are_the_ones_the_spec_resolved() -> None:
    refs = {ref for job in workflow()["jobs"].values() for ref in uses(job)}
    assert refs == {
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9",
        "actions/deploy-pages@368f82528645a54fb793d4d04e342629a3f51346",
    }
    # The checkout pin is the one the main workflow already uses.
    main = (REPO / ".github" / "workflows" / "agents-images.yml").read_text()
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in main


def test_default_permissions_are_empty_and_each_job_asks_for_the_least() -> None:
    wf = workflow()
    assert wf["permissions"] == {}
    assert wf["jobs"]["build"]["permissions"] == {"contents": "read"}
    assert wf["jobs"]["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}


def test_it_runs_on_main_changes_and_by_hand_but_never_on_pull_requests() -> None:
    triggers = workflow()["on"]
    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["main"]
    assert set(triggers["push"]["paths"]) == {
        "agents/orchestrator/src/kiroween_orchestrator/ui/**",
        "tools/replay/**",
        "docs/demo-results/**",
        ".github/workflows/replay-pages.yml",
    }
    for forbidden in ("pull_request", "pull_request_target", "workflow_run", "issue_comment"):
        assert forbidden not in triggers
    assert "pull_request" not in "\n".join(
        line for line in TEXT.splitlines() if not line.lstrip().startswith("#"))


def test_the_trigger_paths_exist() -> None:
    for pattern in workflow()["on"]["push"]["paths"]:
        base = pattern.removesuffix("/**")
        assert (REPO / base).exists(), pattern


def test_it_uses_no_secret_variable_aws_credential_or_third_party_action() -> None:
    assert "secrets." not in TEXT and "vars." not in TEXT
    assert "${{ github.token }}" not in TEXT
    assert not re.search(r"aws|AWS|role-to-assume|configure-aws", TEXT.split("jobs:")[1])
    for job in workflow()["jobs"].values():
        assert "env" not in job
        for ref in uses(job):
            assert ref.startswith("actions/"), ref  # GitHub's own actions only


def test_checkouts_do_not_persist_credentials() -> None:
    for job in workflow()["jobs"].values():
        for step in steps(job):
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False


def test_the_deploy_job_uses_the_github_pages_environment_and_runs_after_the_build() -> None:
    jobs = workflow()["jobs"]
    assert jobs["deploy"]["needs"] == "build"
    environment = jobs["deploy"]["environment"]
    assert environment["name"] == "github-pages"
    assert environment["url"] == "${{ steps.deployment.outputs.page_url }}"
    deploy = [s for s in steps(jobs["deploy"]) if s.get("id") == "deployment"]
    assert len(deploy) == 1 and deploy[0]["uses"].startswith("actions/deploy-pages@")
    assert not any("checkout" in ref for ref in uses(jobs["deploy"])), \
        "the deploy job needs no source code"


def test_runs_queue_and_never_cancel_each_other() -> None:
    concurrency = workflow()["concurrency"]
    assert concurrency == {"group": "pages", "cancel-in-progress": False}


def test_the_build_job_runs_the_build_and_uploads_exactly_its_output() -> None:
    build = workflow()["jobs"]["build"]
    runs = [s["run"] for s in steps(build) if "run" in s]
    assert runs == ["python3 tools/replay/build.py --out _site"]
    upload = [s for s in steps(build) if s.get("uses", "").startswith(
        "actions/upload-pages-artifact@")]
    assert len(upload) == 1 and upload[0]["with"] == {"path": "_site"}
    # The output directory is the one git ignores, so a local build is never committed.
    assert "/_site/" in (REPO / ".gitignore").read_text().splitlines()


def test_the_build_needs_only_the_python_on_the_runner() -> None:
    build = workflow()["jobs"]["build"]
    assert workflow()["jobs"]["build"]["runs-on"] == "ubuntu-24.04"
    assert not any("setup-" in ref for ref in uses(build))
    assert not any("pip" in s.get("run", "") or "npm" in s.get("run", "") for s in steps(build))


def test_the_workflow_documents_the_one_manual_step() -> None:
    assert "Settings -> Pages" in TEXT and "Source: GitHub Actions" in TEXT
