"""scenario-lab TASK-05: the only write calls are the runner's, and the orchestrator never
reaches them (REQ-RN-03, RN-06). Normal tests."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

from kiroween_orchestrator.lab.kube import (
    KubernetesWriter,
    RefusedError,
    check_namespace,
)

PACKAGE = Path(__file__).parents[1] / "src" / "kiroween_orchestrator"
LAB = PACKAGE / "lab"
WRITE_PREFIXES = ("create_", "patch_", "replace_", "delete_", "connect_")
# The five kinds of REQ-SC-03 and nothing else.
ALLOWED_WRITES = {
    "create_namespaced_deployment", "create_namespaced_service", "create_namespaced_config_map",
    "create_namespaced_persistent_volume_claim", "create_namespaced_network_policy",
    "patch_namespaced_deployment",
    "delete_collection_namespaced_deployment", "delete_collection_namespaced_pod",
    "delete_collection_namespaced_config_map",
    "delete_collection_namespaced_persistent_volume_claim",
    "delete_collection_namespaced_network_policy", "delete_namespaced_service",
}


def is_kube_write(name: str) -> bool:
    """A Kubernetes client write: a verb, then the API's own naming (create_namespaced_pod,
    delete_collection_..., patch_node, connect_get_..._exec). Web-framework methods such as
    delete_cookie do not look like that."""
    name = name.lower()
    verb = name.startswith(WRITE_PREFIXES)
    api_shaped = any(part in name for part in ("_namespaced_", "_collection_", "_node", "_pod",
                                               "_exec", "_attach", "_namespace"))
    return (verb and api_shaped) or name.endswith(("_exec", "_attach"))


def write_calls(source: str) -> set[str]:
    return {node.attr for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Attribute) and is_kube_write(node.attr)}


def test_the_runner_writes_only_the_five_kinds_and_deletes_only_by_label() -> None:
    assert write_calls((LAB / "kube.py").read_text()) == ALLOWED_WRITES


def test_no_other_module_in_the_package_makes_a_write_call() -> None:
    for path in sorted(PACKAGE.rglob("*.py")):
        if path == LAB / "kube.py":
            continue
        assert write_calls(path.read_text()) == set(), path.relative_to(PACKAGE)


def test_nothing_touches_secrets_namespaces_roles_or_exec() -> None:
    """Looks at attribute and call names, not at prose in comments."""
    names = {n.attr.lower() for n in ast.walk(ast.parse((LAB / "kube.py").read_text()))
             if isinstance(n, ast.Attribute)}
    for word in ("secret", "role", "exec", "attach", "portforward", "proxy", "token_review",
                 "custom_object", "node", "cluster_role"):
        assert not [n for n in names if word in n], word
    assert not [n for n in names if re.search(r"_namespace(?!d)", n) and "read_" not in n]
    assert "read_namespace" in names  # the one namespace call is a read


def test_the_writer_can_only_be_used_for_the_lab_namespaces() -> None:
    for n in range(1, 15):
        assert check_namespace(f"lab-{n:02d}") == f"lab-{n:02d}"
    for bad in ("kube-system", "agents", "default", "lab-00", "lab-15", "lab-1", "lab-01\n",
                "xlab-01", "", "monitoring"):
        with pytest.raises(RefusedError):
            check_namespace(bad)


class Recorder:
    """Stands in for the three API clients and records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        def call(*args, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append((name, args))
            return type("List", (), {"items": []})()
        return call


def writer() -> tuple[KubernetesWriter, Recorder]:
    rec = Recorder()
    return KubernetesWriter(rec, rec, rec), rec


LAB_OBJECT = {"apiVersion": "v1", "kind": "Service",
              "metadata": {"name": "s", "namespace": "lab-01",
                           "labels": {"kiroween.io/lab": "true"}}}


def test_the_writer_refuses_an_object_outside_the_lab() -> None:
    w, rec = writer()
    outside = [
        {**LAB_OBJECT, "metadata": {**LAB_OBJECT["metadata"], "namespace": "kube-system"}},
        {**LAB_OBJECT, "kind": "Secret"},
        {**LAB_OBJECT, "kind": "Namespace"},
        {**LAB_OBJECT, "kind": "ClusterRoleBinding"},
        {**LAB_OBJECT, "kind": "Pod"},
        {**LAB_OBJECT, "metadata": {"name": "s", "namespace": "lab-01", "labels": {}}},
        {**LAB_OBJECT, "metadata": {"name": "s", "namespace": "lab-01"}},
        {"kind": "Service"},
    ]
    for obj in outside:
        with pytest.raises(RefusedError):
            w.apply(obj)
    assert rec.calls == []


def test_the_writer_creates_and_deletes_with_the_lab_label_only() -> None:
    w, rec = writer()
    w.apply(LAB_OBJECT)
    assert rec.calls == [("create_namespaced_service", ("lab-01", LAB_OBJECT))]
    rec.calls.clear()
    w.delete_all("lab-02")
    names = [c[0] for c in rec.calls]
    assert "delete_collection_namespaced_deployment" in names
    assert all(c[1][0] == "lab-02" for c in rec.calls if c[1])


def test_deletion_always_selects_by_the_lab_label() -> None:
    source = (LAB / "kube.py").read_text()
    tree = ast.parse(source)
    deletes = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute) and n.func.attr.startswith(
                   "delete_collection_")]
    assert len(deletes) == 5
    for call in deletes:
        keys = {k.arg for k in call.keywords}
        assert "kwargs" in ast.dump(call) or "label_selector" in keys, ast.dump(call)
    assert 'label_selector": SELECTOR' in source


def test_the_orchestrator_process_never_imports_the_runner_or_the_writer() -> None:
    """The service account that serves the login holds no write permission, and the code that
    could write is not even loaded there (REQ-RN-03)."""
    code = (
        "import sys, kiroween_orchestrator.api\n"
        "bad = [m for m in sys.modules if m in ('kiroween_orchestrator.lab.runner',"
        " 'kiroween_orchestrator.lab.kube', 'kiroween_orchestrator.lab.server')]\n"
        "print(bad)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,  # noqa: S603
                            check=True)
    assert result.stdout.strip() == "[]"


def test_the_orchestrator_side_modules_import_nothing_from_the_runner_side() -> None:
    for name in ("client.py", "routes.py", "catalog.py"):
        source = (LAB / name).read_text()
        for bad in ("runner", "kube", "server"):
            assert not re.search(rf"from\s+\.{bad}\b|import\s+{bad}\b|lab\.{bad}\b", source), (
                name, bad)
        assert "kubernetes" not in source, name
