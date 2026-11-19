"""The 14 lab scenarios (scenario-lab REQ-SC-01 to SC-08).

Each scenario breaks one thing, on purpose, in its own namespace, in a way one of the four
specialists should be able to find. The manifests are fixed here, never taken from a request.
``__NS__`` and ``__RUN__`` are replaced when a run starts (see render.py): every Deployment and
PersistentVolumeClaim name ends in the run suffix, so a second run is a new workload and the
watcher, whose incident id and cooldown contain the workload name, treats it as a new incident.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

BUSYBOX = "public.ecr.aws/docker/library/busybox:1.36"
PYTHON = "public.ecr.aws/docker/library/python:3.12-slim"
NS = "__NS__"
RUN = "__RUN__"

Obj = dict[str, Any]


@dataclass(frozen=True)
class Phase:
    """Objects to apply. ``wait_for`` names a Deployment (with ``__RUN__``) that must be ready
    before this phase starts, so a working revision exists before it is broken."""

    objects: tuple[Obj, ...]
    wait_for: str | None = None


@dataclass(frozen=True)
class Scenario:
    id: str
    namespace: str
    title: str
    agent: str  # the specialist that should answer, as its service name
    category: str  # the classifier category that routes to it
    breaks: str  # plain words for someone who has never seen Kubernetes
    signals: tuple[str, ...]  # what the watcher should report
    phases: tuple[Phase, ...]
    # True when the crew does not yet answer this one reliably. It stays runnable; the card says so.
    experimental: bool = False


# --- builders --------------------------------------------------------------------------


def _pod_security() -> Obj:
    return {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001,
            "seccompProfile": {"type": "RuntimeDefault"}}


def _container_security() -> Obj:
    return {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]}}


def _resources(cpu_limit: str = "50m", memory_limit: str = "64Mi") -> Obj:
    return {"requests": {"cpu": "5m", "memory": "16Mi"},
            "limits": {"cpu": cpu_limit, "memory": memory_limit}}


def exec_wget(url: str) -> Obj:
    """Ready only while the URL answers: the probe is the client of the scenario."""
    return {"exec": {"command": ["wget", "-q", "-T", "2", "-O", "/dev/null", url]},
            "periodSeconds": 10, "timeoutSeconds": 5}


def http_probe(path: str, port: int = 8080, timeout: int = 1) -> Obj:
    return {"httpGet": {"path": path, "port": port}, "periodSeconds": 10,
            "timeoutSeconds": timeout, "failureThreshold": 3}


def deployment(base: str, image: str, command: list[str], *, app: str | None = None,
               port: int | None = None, readiness: Obj | None = None,
               resources: Obj | None = None, env: list[Obj] | None = None,
               volumes: list[Obj] | None = None, mounts: list[Obj] | None = None,
               strategy: Obj | None = None, progress_deadline: int | None = None,
               container: str = "app") -> Obj:
    app = app or base
    spec: Obj = {
        "replicas": 1,
        "selector": {"matchLabels": {"app": app}},
        "template": {
            "metadata": {"labels": {"app": app}},
            "spec": {
                "automountServiceAccountToken": False,
                "securityContext": _pod_security(),
                "containers": [{
                    "name": container, "image": image, "command": command,
                    "resources": resources or _resources(),
                    "securityContext": _container_security(),
                }],
            },
        },
    }
    main = spec["template"]["spec"]["containers"][0]
    if port is not None:
        main["ports"] = [{"containerPort": port}]
    if readiness is not None:
        main["readinessProbe"] = readiness
    if env:
        main["env"] = env
    if mounts:
        main["volumeMounts"] = mounts
    if volumes:
        spec["template"]["spec"]["volumes"] = volumes
    if strategy:
        spec["strategy"] = strategy
    if progress_deadline:
        spec["progressDeadlineSeconds"] = progress_deadline
    return {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": f"{base}-{RUN}", "namespace": NS, "labels": {"app": app}},
            "spec": spec}


def service(name: str, selector_app: str, port: int = 8080, target_port: int = 8080) -> Obj:
    return {"apiVersion": "v1", "kind": "Service",
            "metadata": {"name": name, "namespace": NS},
            "spec": {"selector": {"app": selector_app},
                     "ports": [{"name": "http", "port": port, "targetPort": target_port}]}}


def configmap(name: str, data: dict[str, str]) -> Obj:
    return {"apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": name, "namespace": NS}, "data": data}


def pvc(base: str, storage_class: str, access: str = "ReadWriteOnce") -> Obj:
    return {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
            "metadata": {"name": f"{base}-{RUN}", "namespace": NS},
            "spec": {"storageClassName": storage_class, "accessModes": [access],
                     "resources": {"requests": {"storage": "1Gi"}}}}


def netpol(name: str, server_app: str, allowed_app: str) -> Obj:
    return {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
            "metadata": {"name": name, "namespace": NS},
            "spec": {"podSelector": {"matchLabels": {"app": server_app}},
                     "policyTypes": ["Ingress"],
                     "ingress": [{"from": [{"podSelector": {"matchLabels": {"app": allowed_app}}}],
                                  "ports": [{"protocol": "TCP", "port": 8080}]}]}}


def tmp_volume(name: str = "tmp") -> tuple[list[Obj], list[Obj]]:
    return ([{"name": name, "emptyDir": {"sizeLimit": "1Mi"}}],
            [{"name": name, "mountPath": f"/{name}"}])


def busybox_server(base: str, app: str | None = None) -> Obj:
    volumes, mounts = tmp_volume("www")
    return deployment(
        base, BUSYBOX,
        ["sh", "-c", "echo ok > /www/index.html && exec httpd -f -p 8080 -h /www"],
        app=app, port=8080, readiness=http_probe("/"), volumes=volumes, mounts=mounts,
        container="server")


def busybox_client(base: str, url: str, app: str | None = None) -> Obj:
    return deployment(base, BUSYBOX, ["sh", "-c", "while true; do sleep 3600; done"],
                      app=app, readiness=exec_wget(url), container="client")


def python_server(base: str, *, path: str = "/", tag: str = PYTHON) -> Obj:
    volumes, mounts = tmp_volume("tmp")
    return deployment(
        base, tag, ["python", "-m", "http.server", "8080", "--directory", "/tmp"],  # noqa: S108
        port=8080, readiness=http_probe(path), resources=_resources("100m", "64Mi"),
        volumes=volumes, mounts=mounts, container="web")


def python_program(base: str, code: str, **kw: Any) -> Obj:
    return deployment(base, PYTHON, ["python", "-c", code], **kw)


# --- the programs the cpu-memory scenarios run ------------------------------------------

ALLOCATE_FAST = """\
import time
chunks = []
while True:
    chunks.append(b"x" * (8 << 20))  # writing every byte keeps the pages resident
    print(f"allocated {len(chunks) * 8} MiB", flush=True)
    time.sleep(0.5)
"""

LEAK_SLOWLY = """\
import time
cache = []
while True:
    cache.append(b"x" * (1 << 20))
    print(f"request cache holds {len(cache)} MiB and is never trimmed", flush=True)
    time.sleep(1)
"""

CPU_BOUND_SERVER = """\
import http.server, time

def work():
    return sum(i * i for i in range(3_000_000))

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        wall, cpu = time.monotonic(), time.process_time()
        body = str(work()).encode()
        print(f"answered in {time.monotonic() - wall:.2f}s wall, "
              f"{time.process_time() - cpu:.2f}s cpu", flush=True)
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

http.server.ThreadingHTTPServer(("", 8080), Handler).serve_forever()
"""

CONFIG_READER = """\
if [ ! -f /config/app.conf ]; then
  echo "FATAL: /config/app.conf not found. /config holds: $(ls /config)" >&2
  exit 1
fi
sleep 3600
"""


# --- the 14 scenarios -----------------------------------------------------------------------


def _scenarios() -> tuple[Scenario, ...]:
    p = Phase
    pvc_volume = lambda claim: [{"name": "data", "persistentVolumeClaim": {"claimName": claim}}]  # noqa: E731
    pvc_mount = [{"name": "data", "mountPath": "/data"}]
    writer_cmd = ["sh", "-c", "while true; do date >> /data/log; sleep 60; done"]

    def volume_user(base: str, claim: str) -> Obj:
        return deployment(base, BUSYBOX, writer_cmd, volumes=pvc_volume(claim), mounts=pvc_mount,
                          strategy={"type": "Recreate"})

    healthy_web = python_server("web")
    broken_image = python_server("web", tag="public.ecr.aws/docker/library/python:3.12.99-gone")
    shop_ok = python_server("shop")
    shop_stuck = python_server("shop", path="/ready")
    for shop in (shop_ok, shop_stuck):
        shop["spec"]["strategy"] = {"type": "RollingUpdate",
                                    "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}}
        shop["spec"]["progressDeadlineSeconds"] = 120

    return (
        Scenario(
            "oom-killed", "lab-01", "A container eats all its memory", "cpu-memory", "cpu_memory",
            "A program keeps asking for more memory than its limit allows, so Kubernetes kills "
            "it over and over.", ("OOMKilled",),
            (p((python_program("leaker", ALLOCATE_FAST, resources=_resources("100m", "64Mi")),)),)),
        Scenario(
            "cpu-throttling", "lab-02", "A busy server starved of CPU", "cpu-memory", "cpu_memory",
            "A web server does real work for every request, but it is allowed almost no CPU, so "
            "it answers too slowly and fails its health check.", ("Unhealthy",),
            (p((python_program("crunch", CPU_BOUND_SERVER, port=8080,
                               readiness=http_probe("/"),
                               resources={"requests": {"cpu": "10m", "memory": "32Mi"},
                                          "limits": {"cpu": "20m", "memory": "64Mi"}},
                               container="server"),)),)),
        Scenario(
            "memory-leak", "lab-03", "A slow memory leak", "cpu-memory", "cpu_memory",
            "A program holds on to a little more memory every second and never lets go, until "
            "it runs out and is killed.", ("OOMKilled",),
            (p((python_program("grower", LEAK_SLOWLY, resources=_resources("50m", "48Mi")),)),)),
        Scenario(
            "netpol-blocks-client", "lab-04", "A firewall rule blocks the client", "network",
            "network",
            "A network rule only lets pods with the label app=trusted talk to the server. The "
            "client has a different label, so its requests are silently dropped.",
            ("Unhealthy",),
            (p((netpol("allow-trusted", "server", "trusted"), service("server", "server"),
                busybox_server("server"), busybox_client("client", "http://server:8080/"))),)),
        Scenario(
            "service-selector-mismatch", "lab-05", "A Service that points at nothing", "network",
            "network",
            "The Service looks for pods labelled app=web, but the server's pods are labelled "
            "app=web-v2, so the Service has no endpoints and refuses every connection.",
            ("Unhealthy",),
            (p((service("web", "web"), busybox_server("web-v2", app="web-v2"),
                busybox_client("client", "http://web:8080/"))),)),
        Scenario(
            "wrong-target-port", "lab-06", "A Service sends traffic to the wrong port", "network",
            "network",
            "The server listens on port 8080, but its Service forwards to port 9090, where "
            "nothing is listening, so connections are refused.", ("Unhealthy",),
            (p((service("web", "web", 8080, 9090), busybox_server("web"),
                busybox_client("client", "http://web:8080/"))),)),
        Scenario(
            "dns-missing-service", "lab-07", "A client calls a Service that does not exist",
            "network", "network",
            "The client looks up payments-api, but the only Service in the namespace is called "
            "payments, so the name does not resolve.", ("Unhealthy",),
            (p((service("payments", "payments"), busybox_server("payments"),
                busybox_client("client", "http://payments-api:8080/"))),)),
        Scenario(
            "bad-image-tag", "lab-08", "A rollout with an image that does not exist",
            "deploy-rollout", "deploy_rollout",
            "A working version is running. Then it is updated to an image tag that was never "
            "published, so the new pod cannot start while the old one keeps serving.",
            ("ErrImagePull", "ImagePullBackOff"),
            (p((healthy_web,)), p((broken_image,), wait_for=f"web-{RUN}"))),
        Scenario(
            "crashloop-missing-config-key", "lab-09", "A program that cannot find its settings",
            "deploy-rollout", "deploy_rollout",
            "The program reads a settings file called app.conf, but its ConfigMap only has a "
            "key named application.conf, so it exits at once and Kubernetes keeps restarting it.",
            ("CrashLoopBackOff",),
            (p((configmap("app-config", {"application.conf": "mode=prod\n"}),
                deployment("reporter", BUSYBOX, ["sh", "-c", CONFIG_READER],
                           volumes=[{"name": "config", "configMap": {"name": "app-config"}}],
                           mounts=[{"name": "config", "mountPath": "/config"}]))),)),
        Scenario(
            "missing-secret", "lab-10", "A container that needs a Secret that is not there",
            "deploy-rollout", "deploy_rollout",
            "The container takes its database password from a Secret called db-credentials, "
            "which was never created, so Kubernetes cannot even start it.",
            ("CreateContainerConfigError",),
            (p((deployment("api", BUSYBOX, ["sh", "-c", "sleep 3600"],
                           env=[{"name": "DB_PASSWORD",
                                 "valueFrom": {"secretKeyRef": {"name": "db-credentials",
                                                                "key": "password"}}}]),)),)),
        Scenario(
            "rollout-stuck-readiness", "lab-11", "An update that never becomes ready",
            "deploy-rollout", "deploy_rollout",
            "A working version is running. The update changes the health check to a page that "
            "does not exist, so the new pod never counts as ready and the rollout stalls.",
            ("Unhealthy",),
            (p((shop_ok,)), p((shop_stuck,), wait_for=f"shop-{RUN}")),
            experimental=True),
        Scenario(
            "pvc-missing-storageclass", "lab-12",
            "A disk request for a storage type that does not exist",
            "storage", "storage",
            "A volume claim asks for a StorageClass called does-not-exist, so no disk is ever "
            "created and the pod waiting for it cannot start.", ("ProvisioningFailed",),
            (p((pvc("data", "does-not-exist"), volume_user("writer", f"data-{RUN}"))),)),
        Scenario(
            "rwx-on-ebs", "lab-13", "A shared disk that block storage cannot give",
            "storage", "storage",
            "A volume claim asks for a disk that many pods can write to at once (ReadWriteMany), "
            "but the cluster's disks are single-attach block storage, so it cannot be created.",
            ("ProvisioningFailed",),
            (p((pvc("shared", "gp2", "ReadWriteMany"), volume_user("worker", f"shared-{RUN}"))),)),
        Scenario(
            "missing-configmap-mount", "lab-14", "A pod that mounts a ConfigMap that is not there",
            "storage", "storage",
            "The pod mounts a ConfigMap called app-settings as a folder, but that ConfigMap was "
            "never created, so the folder cannot be mounted and the pod never starts.",
            ("FailedMount",),
            (p((deployment("viewer", BUSYBOX, ["sh", "-c", "sleep 3600"],
                           volumes=[{"name": "settings", "configMap": {"name": "app-settings"}}],
                           mounts=[{"name": "settings", "mountPath": "/settings"}]),)),)),
    )


SCENARIOS: tuple[Scenario, ...] = _scenarios()
BY_ID: dict[str, Scenario] = {s.id: s for s in SCENARIOS}
