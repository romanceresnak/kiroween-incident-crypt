"""TASK-12: chaos/oom script and manifests. Uses a fake kubectl; never touches a cluster."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

OOM = Path(__file__).parents[1] / "oom"
SCRIPT = OOM / "oom.sh"
RIGHT = "arn:aws:eks:eu-west-1:255834079310:cluster/kiroween-prod"


def fake_kubectl(tmp_path: Path, context: str) -> tuple[dict[str, str], Path]:
    log = tmp_path / "kubectl.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "kubectl"
    exe.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{log}"\n'
        'if [[ "$1 $2" == "config current-context" ]]; then\n'
        f'  echo "{context}"\n'
        "fi\n"
    )
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    return env, log


def run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["/bin/bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, check=False
    )


def calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("command", ["up", "down"])
@pytest.mark.parametrize("context", ["arn:aws:eks:eu-west-1:1:cluster/other", "minikube", ""])
def test_wrong_context_aborts_before_any_change(tmp_path: Path, command: str, context: str) -> None:
    env, log = fake_kubectl(tmp_path, context)
    result = run(env, command)
    assert result.returncode == 1
    assert "refusing to run" in result.stderr
    assert calls(log) == ["config current-context"]


def test_up_applies_only_the_manifests(tmp_path: Path) -> None:
    env, log = fake_kubectl(tmp_path, RIGHT)
    assert run(env, "up").returncode == 0
    assert calls(log) == ["config current-context", f"apply -f {OOM / 'manifests.yaml'}"]


def test_down_deletes_only_the_chaos_namespace(tmp_path: Path) -> None:
    env, log = fake_kubectl(tmp_path, RIGHT)
    assert run(env, "down").returncode == 0
    assert calls(log)[-1] == "delete namespace chaos-oom --ignore-not-found --wait=true"


def test_expected_cluster_is_configurable(tmp_path: Path) -> None:
    env, _ = fake_kubectl(tmp_path, "arn:aws:eks:eu-west-1:1:cluster/staging")
    assert run({**env, "EXPECTED_CLUSTER": "staging"}, "up").returncode == 0


def test_usage_on_unknown_command(tmp_path: Path) -> None:
    env, log = fake_kubectl(tmp_path, RIGHT)
    assert run(env, "sideways").returncode == 2
    assert calls(log) == []


def test_manifests_stay_in_chaos_namespace_and_oom_at_64mi() -> None:
    docs = [d for d in yaml.safe_load_all((OOM / "manifests.yaml").read_text()) if d]
    assert {d["kind"] for d in docs} == {"Namespace", "Deployment"}
    namespace = next(d for d in docs if d["kind"] == "Namespace")
    assert namespace["metadata"]["name"] == "chaos-oom"
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    assert deployment["metadata"]["namespace"] == "chaos-oom"
    assert deployment["metadata"]["name"] == "oom-demo"
    (container,) = deployment["spec"]["template"]["spec"]["containers"]
    assert container["resources"]["limits"]["memory"] == "64Mi"
    assert container["image"].startswith("public.ecr.aws/")


def test_allocation_loop_actually_exceeds_the_limit() -> None:
    deployment = next(d for d in yaml.safe_load_all((OOM / "manifests.yaml").read_text())
                      if d and d["kind"] == "Deployment")
    program = deployment["spec"]["template"]["spec"]["containers"][0]["args"][0]
    assert 'b"x" * (8 << 20)' in program  # touched memory, not a zero-filled buffer
    assert "while True" in program


def test_script_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)
