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


PACKAGES = {
    "orchestrator": "kiroween/orchestrator", "cpu_memory": "kiroween/cpu-memory",
    "network": "kiroween/network", "deploy_rollout": "kiroween/deploy-rollout",
    "storage": "kiroween/storage",
}


def test_matrix_maps_each_package_to_its_repository() -> None:
    include = workflow()["jobs"]["push"]["strategy"]["matrix"]["include"]
    assert {i["package"]: i["repository"] for i in include} == PACKAGES


def test_pr_build_matrix_covers_every_pushed_package() -> None:
    assert workflow()["jobs"]["build"]["strategy"]["matrix"]["package"] == list(PACKAGES)


def test_every_pushed_package_is_tested_and_has_a_dockerfile() -> None:
    steps = workflow()["jobs"]["test"]["steps"]
    loop = next(s["run"] for s in steps if "for pkg in" in s.get("run", ""))
    tested = re.search(r"for pkg in ([^;]+);", loop).group(1).split()
    assert tested == ["shared", *PACKAGES]
    for package in PACKAGES:
        assert (REPO / "agents" / package / "Dockerfile").is_file(), package


def test_update_chart_sets_every_image_and_the_markers_exist() -> None:
    steps = workflow()["jobs"]["update-chart"]["steps"]
    run = next(s["run"] for s in steps if "set_image_tag.py" in s.get("run", ""))
    components = re.search(r'"\$\{SHA\}" ([a-z -]+)\)', run).group(1).split()
    # The chart component names are the ECR repository names without the prefix.
    assert components == [r.removeprefix("kiroween/") for r in PACKAGES.values()]
    values = (REPO / "gitops" / "charts" / "agents" / "values.yaml").read_text()
    for component in components:
        assert f"# ci-managed:{component}" in values, component


# --- "image already exists" step, run for real ------------------------------
#
# GitHub runs run: steps as `bash --noprofile --norc -eo pipefail {0}`. These tests run
# the step's own script that way, with a fake aws CLI, so a bash -e abort shows up here.


def exists_step_script() -> str:
    step = next(s for s in steps(workflow()["jobs"]["push"]) if s.get("id") == "exists")
    return step["run"]


def run_exists_step(tmp_path: Path, *, aws_exit: int, aws_output: str) -> tuple[int, str, str]:
    import stat
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws = bin_dir / "aws"
    aws.write_text(f"#!/usr/bin/env bash\necho {aws_output!r} >&2\nexit {aws_exit}\n")
    aws.chmod(aws.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "step.sh"
    script.write_text(exists_step_script())
    output = tmp_path / "github_output"
    output.write_text("")
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "GITHUB_OUTPUT": str(output),
           "REPOSITORY": "kiroween/orchestrator", "TAG": "0" * 40}
    result = subprocess.run(  # noqa: S603
        ["/bin/bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],  # noqa: S607
        env=env, capture_output=True, text=True, check=False,
    )
    return result.returncode, output.read_text(), result.stderr


def test_exists_step_not_found_continues_with_found_false(tmp_path: Path) -> None:
    # The real CLI exits 254 for ImageNotFoundException; that case broke run 44104222083.
    code, output, _ = run_exists_step(
        tmp_path, aws_exit=254,
        aws_output="An error occurred (ImageNotFoundException) when calling the DescribeImages "
                   "operation: The image with imageId {imageTag:'x'} does not exist",
    )
    assert code == 0
    assert output.strip() == "found=false"


def test_exists_step_found_sets_found_true(tmp_path: Path) -> None:
    code, output, _ = run_exists_step(tmp_path, aws_exit=0, aws_output='{"imageDetails": []}')
    assert code == 0
    assert output.strip() == "found=true"


def test_exists_step_other_error_fails_and_prints_it(tmp_path: Path) -> None:
    message = "An error occurred (AccessDeniedException) when calling the DescribeImages operation"
    code, output, stderr = run_exists_step(tmp_path, aws_exit=254, aws_output=message)
    assert code == 1
    assert output == ""
    assert "AccessDeniedException" in stderr
