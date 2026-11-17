"""TASK-13: static checks on .github/workflows/agents-images.yml."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "agents-images.yml"
FULL_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def workflow() -> dict:
    data = yaml.safe_load(WORKFLOW.read_text())
    # PyYAML reads the bare key "on" as True.
    data["on"] = data.pop(True, data.get("on"))
    return data


def steps(job: dict) -> list[dict]:
    return job.get("steps", [])


def test_every_action_is_pinned_to_a_full_sha() -> None:
    for name, job in workflow()["jobs"].items():
        for step in steps(job):
            if "uses" in step:
                assert FULL_SHA.match(step["uses"]), (name, step["uses"])


def test_default_permissions_are_empty() -> None:
    assert workflow()["permissions"] == {}


def test_job_permissions() -> None:
    jobs = workflow()["jobs"]
    assert jobs["test"]["permissions"] == {"contents": "read"}
    assert jobs["build"]["permissions"] == {"contents": "read"}
    assert jobs["push"]["permissions"] == {"contents": "read", "id-token": "write"}
    assert jobs["update-chart"]["permissions"] == {"contents": "write"}


def test_only_the_push_job_uses_aws() -> None:
    for name, job in workflow()["jobs"].items():
        uses_aws = any("configure-aws-credentials" in s.get("uses", "") for s in steps(job))
        assert uses_aws == (name == "push"), name


def test_push_job_assumes_the_ecr_push_role_from_a_variable() -> None:
    push = workflow()["jobs"]["push"]
    step = next(s for s in steps(push) if "configure-aws-credentials" in s.get("uses", ""))
    assert step["with"]["role-to-assume"] == "${{ vars.ECR_PUSH_ROLE_ARN }}"
    assert "gha-apply" not in WORKFLOW.read_text()


def test_pull_requests_never_push() -> None:
    jobs = workflow()["jobs"]
    assert jobs["build"]["if"] == "github.event_name == 'pull_request'"
    build_step = next(s for s in steps(jobs["build"]) if "build-push-action" in s.get("uses", ""))
    assert build_step["with"]["push"] is False
    for name in ("push", "update-chart"):
        assert "github.event_name != 'pull_request'" in jobs[name]["if"]
        assert "github.ref == 'refs/heads/main'" in jobs[name]["if"]


def test_ordering_tests_before_images_and_images_before_tag_commit() -> None:
    jobs = workflow()["jobs"]
    assert jobs["build"]["needs"] == "test" and jobs["push"]["needs"] == "test"
    assert jobs["update-chart"]["needs"] == "push"


def test_images_tagged_with_commit_sha_for_amd64_and_never_latest() -> None:
    text = WORKFLOW.read_text()
    push_step = next(s for s in steps(workflow()["jobs"]["push"])
                     if "build-push-action" in s.get("uses", ""))
    assert push_step["with"]["tags"].endswith(":${{ github.sha }}")
    assert push_step["with"]["platforms"] == "linux/amd64"
    assert ":latest" not in text


def test_path_filter_excludes_gitops_so_the_tag_commit_cannot_loop() -> None:
    on = workflow()["on"]
    for event in ("push", "pull_request"):
        assert not any(p.startswith("gitops") for p in on[event]["paths"])


def test_concurrency_queues_and_never_cancels() -> None:
    concurrency = workflow()["concurrency"]
    assert concurrency["cancel-in-progress"] is False


def test_no_environment_so_the_oidc_subject_stays_the_branch() -> None:
    for job in workflow()["jobs"].values():
        assert "environment" not in job


def test_tag_commit_skips_ci_and_never_force_pushes() -> None:
    text = WORKFLOW.read_text()
    assert "[skip ci]" in text
    assert "--force" not in text and "push -f" not in text
    assert "for attempt in 1 2 3" in text


def test_existing_tag_is_skipped() -> None:
    push = workflow()["jobs"]["push"]
    names = [s.get("id") for s in steps(push)]
    assert "exists" in names
    for step in steps(push):
        if "setup-buildx" in step.get("uses", "") or "build-push" in step.get("uses", ""):
            assert step["if"] == "steps.exists.outputs.found == 'false'"


def test_matrix_maps_cpu_memory_package_to_its_repository() -> None:
    include = workflow()["jobs"]["push"]["strategy"]["matrix"]["include"]
    assert {i["package"]: i["repository"] for i in include} == {
        "orchestrator": "kiroween/orchestrator", "cpu_memory": "kiroween/cpu-memory"}
