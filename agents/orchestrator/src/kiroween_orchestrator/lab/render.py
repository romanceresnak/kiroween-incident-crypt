"""Turn a scenario into the objects a run applies, and refuse anything unsafe
(scenario-lab REQ-SC-02 to SC-07).

The runner applies only what ``render`` returns, and ``render`` validates every object, so a
mistake in a scenario is an error here and never a surprise in the cluster.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from .scenarios import BY_ID, NS, RUN, Obj, Scenario

LABEL_LAB = "kiroween.io/lab"
LABEL_SCENARIO = "kiroween.io/lab-scenario"
LABEL_RUN = "kiroween.io/lab-run"
SELECTOR = f"{LABEL_LAB}=true"

RUN_ID = re.compile(r"[0-9a-f]{6}")
NAME = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?")
IMAGE_PREFIX = "public.ecr.aws/docker/library/"
KINDS: dict[str, str] = {
    "Deployment": "apps/v1", "Service": "v1", "ConfigMap": "v1",
    "PersistentVolumeClaim": "v1", "NetworkPolicy": "networking.k8s.io/v1",
}
SUFFIXED = frozenset({"Deployment", "PersistentVolumeClaim"})
VOLUME_TYPES = frozenset({"emptyDir", "configMap", "persistentVolumeClaim"})
STORAGE_CLASSES = frozenset({"gp2", "does-not-exist"})
FORBIDDEN_POD_KEYS = frozenset({"hostNetwork", "hostPID", "hostIPC", "hostAliases", "nodeName",
                                "serviceAccountName", "serviceAccount", "initContainers",
                                "ephemeralContainers", "runtimeClassName"})
MAX_CPU_M, MAX_MEMORY_MI, MAX_OBJECTS = 200, 128, 8
# The only scenario that may name a Secret, and only to show that it is missing.
SECRET_REFERENCE_ALLOWED = frozenset({"missing-secret"})


class RenderError(ValueError):
    pass


@dataclass(frozen=True)
class RenderedPhase:
    objects: tuple[Obj, ...]
    wait_for: str | None


def millicores(value: str) -> int:
    match = re.fullmatch(r"(\d+)(m?)", value)
    if not match:
        raise RenderError(f"bad CPU quantity {value!r}")
    return int(match.group(1)) * (1 if match.group(2) else 1000)


def mebibytes(value: str) -> int:
    match = re.fullmatch(r"(\d+)(Mi|Gi)", value)
    if not match:
        raise RenderError(f"bad memory quantity {value!r}")
    return int(match.group(1)) * (1024 if match.group(2) == "Gi" else 1)


def substitute(value: Any, namespace: str, run: str) -> Any:
    if isinstance(value, str):
        return value.replace(NS, namespace).replace(RUN, run)
    if isinstance(value, list):
        return [substitute(v, namespace, run) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, namespace, run) for k, v in value.items()}
    return value


def _labels(obj: Obj, scenario: Scenario, run: str) -> None:
    ours = {LABEL_LAB: "true", LABEL_SCENARIO: scenario.id, LABEL_RUN: run}
    obj["metadata"].setdefault("labels", {}).update(ours)
    if obj["kind"] == "Deployment":
        obj["spec"]["template"]["metadata"].setdefault("labels", {}).update(ours)


def _check_container(scenario: Scenario, where: str, container: Obj) -> None:
    image = container.get("image", "")
    if not str(image).startswith(IMAGE_PREFIX):
        raise RenderError(f"{where}: image {image!r} is not from {IMAGE_PREFIX}")
    resources = container.get("resources", {})
    for kind in ("requests", "limits"):
        for resource in ("cpu", "memory"):
            if resource not in resources.get(kind, {}):
                raise RenderError(f"{where}: no {kind}.{resource}")
    limits, requests = resources["limits"], resources["requests"]
    if millicores(limits["cpu"]) > MAX_CPU_M or mebibytes(limits["memory"]) > MAX_MEMORY_MI:
        raise RenderError(f"{where}: limits exceed {MAX_CPU_M}m CPU / {MAX_MEMORY_MI}Mi memory")
    if (millicores(requests["cpu"]) > millicores(limits["cpu"])
            or mebibytes(requests["memory"]) > mebibytes(limits["memory"])):
        raise RenderError(f"{where}: a request is above its limit")
    security = container.get("securityContext", {})
    if (security.get("allowPrivilegeEscalation") is not False
            or security.get("readOnlyRootFilesystem") is not True
            or security.get("capabilities") != {"drop": ["ALL"]}
            or security.get("privileged") or "add" in security.get("capabilities", {})):
        raise RenderError(f"{where}: the container is not locked down")
    for entry in container.get("env", []):
        ref = entry.get("valueFrom", {})
        if set(ref) - {"secretKeyRef", "configMapKeyRef"} or (
                "secretKeyRef" in ref and scenario.id not in SECRET_REFERENCE_ALLOWED):
            raise RenderError(f"{where}: env {entry.get('name')} refers to a Secret")
    if container.get("envFrom"):
        raise RenderError(f"{where}: envFrom is not allowed")


def _check_pod(scenario: Scenario, where: str, pod: Obj) -> None:
    if set(pod) & FORBIDDEN_POD_KEYS:
        raise RenderError(f"{where}: forbidden pod setting {sorted(set(pod) & FORBIDDEN_POD_KEYS)}")
    if pod.get("automountServiceAccountToken") is not False:
        raise RenderError(f"{where}: the pod must not mount a service account token")
    security = pod.get("securityContext", {})
    if (security.get("runAsNonRoot") is not True or security.get("runAsUser", 0) == 0
            or security.get("seccompProfile") != {"type": "RuntimeDefault"}):
        raise RenderError(f"{where}: the pod is not locked down")
    for volume in pod.get("volumes", []):
        kinds = set(volume) - {"name"}
        if len(kinds) != 1 or not kinds <= VOLUME_TYPES:
            raise RenderError(f"{where}: volume {volume.get('name')} is not allowed")
    containers = pod.get("containers", [])
    if not containers:
        raise RenderError(f"{where}: no containers")
    for container in containers:
        _check_container(scenario, f"{where}/{container.get('name')}", container)


def _check_object(scenario: Scenario, obj: Obj, run: str) -> None:
    kind = obj.get("kind")
    meta = obj.get("metadata", {})
    name = meta.get("name", "")
    where = f"{scenario.id}: {kind}/{name}"
    if kind not in KINDS:
        raise RenderError(f"{where}: kind {kind!r} is not allowed")
    if obj.get("apiVersion") != KINDS[kind]:
        raise RenderError(f"{where}: wrong apiVersion {obj.get('apiVersion')!r}")
    if meta.get("namespace") != scenario.namespace:
        raise RenderError(f"{where}: namespace {meta.get('namespace')!r}, not {scenario.namespace}")
    if not NAME.fullmatch(name) or len(name) > 63:
        raise RenderError(f"{where}: bad name")
    if (kind in SUFFIXED) != name.endswith(f"-{run}"):
        raise RenderError(f"{where}: a {kind} name must end in the run suffix -{run}")
    if kind == "Deployment":
        _check_pod(scenario, where, obj["spec"]["template"]["spec"])
        if obj["spec"].get("replicas", 1) > 2:
            raise RenderError(f"{where}: too many replicas")
    if kind == "PersistentVolumeClaim":
        spec = obj["spec"]
        if spec.get("storageClassName") not in STORAGE_CLASSES:
            raise RenderError(f"{where}: storage class {spec.get('storageClassName')!r}")
        if mebibytes(spec["resources"]["requests"]["storage"]) > 1024:
            raise RenderError(f"{where}: more than 1Gi")
        if not set(spec["accessModes"]) <= {"ReadWriteOnce", "ReadWriteMany"}:
            raise RenderError(f"{where}: access mode")
    if kind == "Service" and obj["spec"].get("type", "ClusterIP") != "ClusterIP":
        raise RenderError(f"{where}: only ClusterIP Services")


def _no_placeholders(value: Any, where: str) -> None:
    text = str(value)
    if "__NS__" in text or "__RUN__" in text:
        raise RenderError(f"{where}: an unreplaced placeholder")


def render(scenario: Scenario | str, run: str) -> tuple[RenderedPhase, ...]:
    """The phases of a run, with the namespace and run suffix filled in, labelled and checked."""
    if isinstance(scenario, str):
        if scenario not in BY_ID:
            raise RenderError(f"unknown scenario {scenario!r}")
        scenario = BY_ID[scenario]
    if not RUN_ID.fullmatch(run):
        raise RenderError(f"bad run id {run!r}")
    phases: list[RenderedPhase] = []
    total = 0
    for phase in scenario.phases:
        objects: list[Obj] = []
        for raw in phase.objects:
            obj = substitute(copy.deepcopy(raw), scenario.namespace, run)
            _check_object(scenario, obj, run)
            _labels(obj, scenario, run)
            objects.append(obj)
        total += len(objects)
        wait_for = substitute(phase.wait_for, scenario.namespace, run) if phase.wait_for else None
        _no_placeholders(objects, scenario.id)
        phases.append(RenderedPhase(tuple(objects), wait_for))
    if total > MAX_OBJECTS * len(scenario.phases):
        raise RenderError(f"{scenario.id}: too many objects")
    return tuple(phases)


def to_yaml(scenario: Scenario | str, run: str) -> list[str]:
    """One multi-document YAML string per phase, for a person to read or apply by hand."""
    import yaml

    return [yaml.safe_dump_all(phase.objects, sort_keys=False)
            for phase in render(scenario, run)]
