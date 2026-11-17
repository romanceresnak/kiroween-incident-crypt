"""specialists TASK-11: chaos/netpol, chaos/badimage and chaos/pvc. Uses a fake kubectl;
never touches a cluster."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

CHAOS = Path(__file__).parents[1]
RIGHT = "arn:aws:eks:eu-west-1:255834079310:cluster/kiroween-prod"
SCENARIOS = {"netpol": "chaos-netpol", "badimage": "chaos-badimage", "pvc": "chaos-pvc"}
BAD_IMAGE = "public.ecr.aws/docker/library/python:0.0.0-does-not-exist"


def script(name: str) -> Path:
    return CHAOS / name / f"{name}.sh"


def manifests(name: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((CHAOS / name / "manifests.yaml").read_text()) if d]


def fake_kubectl(tmp_path: Path, context: str, fail_on: str = "") -> tuple[dict[str, str], Path]:
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
        + (f'if [[ "$*" == *"{fail_on}"* ]]; then exit 1; fi\n' if fail_on else "")
    )
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    return env, log


def run(name: str, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["/bin/bash", str(script(name)), *args], env=env, capture_output=True, text=True,
        check=False,
    )


def calls(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


# --- script behaviour, shared by all three -------------------------------------------


@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.parametrize("command", ["up", "down"])
@pytest.mark.parametrize("context", ["arn:aws:eks:eu-west-1:1:cluster/other", "minikube", ""])
def test_wrong_context_aborts_before_any_change(
    tmp_path: Path, name: str, command: str, context: str
) -> None:
    env, log = fake_kubectl(tmp_path, context)
    result = run(name, env, command)
    assert result.returncode == 1
    assert "refusing to run" in result.stderr
    assert calls(log) == ["config current-context"]


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_down_deletes_only_the_chaos_namespace(tmp_path: Path, name: str) -> None:
    env, log = fake_kubectl(tmp_path, RIGHT)
    assert run(name, env, "down").returncode == 0
    assert calls(log) == ["config current-context",
                          f"delete namespace {SCENARIOS[name]} --ignore-not-found --wait=true"]


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_up_and_down_are_repeatable(tmp_path: Path, name: str) -> None:
    env, log = fake_kubectl(tmp_path, RIGHT)
    for _ in range(2):
        assert run(name, env, "up").returncode == 0
    for _ in range(2):
        assert run(name, env, "down").returncode == 0
    lines = calls(log)
    # Every change is an apply/set/delete scoped to the scenario; nothing is created
    # imperatively, so a second run converges instead of failing on "already exists".
    assert not [c for c in lines if c.startswith(("create ", "run ", "expose "))]
    for c in lines:
        if c.startswith(("set ", "rollout ")) or " set " in c or " rollout " in c:
            assert c.startswith(f"-n {SCENARIOS[name]} "), c


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_expected_cluster_is_configurable(tmp_path: Path, name: str) -> None:
    env, _ = fake_kubectl(tmp_path, "arn:aws:eks:eu-west-1:1:cluster/staging")
    assert run(name, {**env, "EXPECTED_CLUSTER": "staging"}, "up").returncode == 0


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_usage_on_unknown_command(tmp_path: Path, name: str) -> None:
    env, log = fake_kubectl(tmp_path, RIGHT)
    assert run(name, env, "sideways").returncode == 2
    assert run(name, env).returncode == 2
    assert calls(log) == []


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_script_is_executable(name: str) -> None:
    assert os.access(script(name), os.X_OK)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_manifests_stay_in_their_namespace_and_are_hardened(name: str) -> None:
    docs = manifests(name)
    namespaces = [d for d in docs if d["kind"] == "Namespace"]
    assert [n["metadata"]["name"] for n in namespaces] == [SCENARIOS[name]]
    assert namespaces[0]["metadata"]["labels"] == {"kiroween/chaos": "true"}
    for doc in docs:
        if doc["kind"] != "Namespace":
            assert doc["metadata"]["namespace"] == SCENARIOS[name], doc["metadata"]["name"]
        if doc["kind"] == "Deployment":
            pod = doc["spec"]["template"]["spec"]
            assert pod["securityContext"]["runAsNonRoot"] is True
            for c in pod["containers"]:
                assert c["image"].startswith("public.ecr.aws/"), c["image"]
                assert c["resources"]["limits"] and c["resources"]["requests"]
                assert c["securityContext"]["allowPrivilegeEscalation"] is False
                assert c["securityContext"]["readOnlyRootFilesystem"] is True


def test_scenario_namespaces_are_not_excluded_by_the_watcher() -> None:
    values = yaml.safe_load((CHAOS.parent / "gitops/charts/agents/values.yaml").read_text())
    assert not set(SCENARIOS.values()) & set(values["watcher"]["excludedNamespaces"])


# --- netpol ---------------------------------------------------------------------------


def test_netpol_up_applies_only_the_manifests(tmp_path: Path) -> None:
    env, log = fake_kubectl(tmp_path, RIGHT)
    assert run("netpol", env, "up").returncode == 0
    assert calls(log) == ["config current-context",
                          f"apply -f {CHAOS / 'netpol' / 'manifests.yaml'}"]


def test_netpol_policy_admits_no_existing_pod_and_blocks_the_client() -> None:
    docs = manifests("netpol")
    (policy,) = [d for d in docs if d["kind"] == "NetworkPolicy"]
    assert policy["metadata"]["name"] == "allow-trusted"
    assert policy["spec"]["podSelector"]["matchLabels"] == {"app": "netpol-server"}
    # The field the network specialist is expected to change.
    allowed = policy["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"]["app"]
    assert allowed == "netpol-trusted"
    pod_apps = {d["spec"]["template"]["metadata"]["labels"]["app"]
                for d in docs if d["kind"] == "Deployment"}
    assert pod_apps == {"netpol-server", "netpol-client"}
    assert allowed not in pod_apps
    port = policy["spec"]["ingress"][0]["ports"][0]["port"]
    (service,) = [d for d in docs if d["kind"] == "Service"]
    assert service["spec"]["selector"] == {"app": "netpol-server"}
    assert service["spec"]["ports"][0]["targetPort"] == port


def test_netpol_client_readiness_depends_on_the_server() -> None:
    client = next(d for d in manifests("netpol")
                  if d["kind"] == "Deployment" and d["metadata"]["name"] == "netpol-client")
    probe = client["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]
    assert "http://netpol-server:8080/" in probe["exec"]["command"]
    assert probe["timeoutSeconds"] > 2  # wget gives up first, so the probe fails, not hangs


# --- badimage -------------------------------------------------------------------------


def test_badimage_up_rolls_out_revision_one_before_breaking_it(tmp_path: Path) -> None:
    env, log = fake_kubectl(tmp_path, RIGHT)
    assert run("badimage", env, "up").returncode == 0
    assert calls(log) == [
        "config current-context",
        f"apply -f {CHAOS / 'badimage' / 'manifests.yaml'}",
        "-n chaos-badimage rollout status deployment/badimage-demo --timeout=180s",
        f"-n chaos-badimage set image deployment/badimage-demo web={BAD_IMAGE}",
    ]


def test_badimage_does_not_break_anything_if_revision_one_never_rolls_out(
    tmp_path: Path,
) -> None:
    env, log = fake_kubectl(tmp_path, RIGHT, fail_on="rollout status")
    assert run("badimage", env, "up").returncode != 0
    assert not [c for c in calls(log) if "set image" in c]


def test_badimage_revision_one_is_a_working_image_in_the_same_repository() -> None:
    (deployment,) = [d for d in manifests("badimage") if d["kind"] == "Deployment"]
    assert deployment["metadata"]["name"] == "badimage-demo"
    (container,) = deployment["spec"]["template"]["spec"]["containers"]
    assert container["name"] == "web"
    good = container["image"]
    assert good.rsplit(":", 1)[0] == BAD_IMAGE.rsplit(":", 1)[0]
    assert good != BAD_IMAGE
    # Kept so the specialist can see revision 1 in the ReplicaSet history.
    assert deployment["spec"]["revisionHistoryLimit"] >= 2


# --- pvc ------------------------------------------------------------------------------


def test_pvc_up_applies_only_the_manifests(tmp_path: Path) -> None:
    env, log = fake_kubectl(tmp_path, RIGHT)
    assert run("pvc", env, "up").returncode == 0
    assert calls(log) == ["config current-context", f"apply -f {CHAOS / 'pvc' / 'manifests.yaml'}"]


def test_pvc_names_a_missing_storage_class_and_is_mounted() -> None:
    docs = manifests("pvc")
    (claim,) = [d for d in docs if d["kind"] == "PersistentVolumeClaim"]
    assert claim["metadata"]["name"] == "data"
    assert claim["spec"]["storageClassName"] == "does-not-exist"
    (deployment,) = [d for d in docs if d["kind"] == "Deployment"]
    volumes = deployment["spec"]["template"]["spec"]["volumes"]
    assert {"name": "data", "persistentVolumeClaim": {"claimName": "data"}} in volumes
    assert not [d for d in docs if d["kind"] == "StorageClass"]
