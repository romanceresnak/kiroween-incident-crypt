"""Pod watcher: list, then watch, and post OOMKilled incidents (REQ-WAT)."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from kiroween_shared.config import env_int, env_list
from kiroween_shared.models import utcnow

from .client import OrchestratorClient
from .dedupe import Cooldown, SeenIds
from .detect import incident_id, terminations, to_incident

logger = logging.getLogger(__name__)

DEFAULT_EXCLUDED = ["kube-system", "kube-node-lease", "argocd"]


@dataclass(frozen=True)
class WatcherSettings:
    orchestrator_url: str = "http://orchestrator:8080"
    reasons: frozenset[str] = frozenset({"OOMKilled"})
    excluded_namespaces: frozenset[str] = field(default_factory=lambda: frozenset(DEFAULT_EXCLUDED))
    cooldown_s: int = 600
    startup_grace_s: int = 300
    health_port: int = 8080

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> WatcherSettings:
        env = os.environ if env is None else env
        return cls(
            orchestrator_url=env.get("ORCHESTRATOR_URL", "http://orchestrator:8080"),
            reasons=frozenset(env_list(env, "WATCH_REASONS", ["OOMKilled"])),
            excluded_namespaces=frozenset(env_list(env, "EXCLUDED_NAMESPACES", DEFAULT_EXCLUDED)),
            cooldown_s=env_int(env, "COOLDOWN_SECONDS", 600),
            startup_grace_s=env_int(env, "STARTUP_GRACE_SECONDS", 300),
            health_port=env_int(env, "API_PORT", 8080),
        )


class Watcher:
    def __init__(
        self,
        settings: WatcherSettings,
        client: OrchestratorClient,
        *,
        started_at: datetime | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.settings = settings
        self.client = client
        self._now = now
        self._oldest = (started_at or now()) - timedelta(seconds=settings.startup_grace_s)
        self._cooldown = Cooldown(settings.cooldown_s, clock)
        self._seen = SeenIds()

    def handle(self, pod: dict[str, Any]) -> list[str]:
        """Process one pod; returns the ids of incidents submitted."""
        namespace = (pod.get("metadata") or {}).get("namespace", "")
        if namespace in self.settings.excluded_namespaces:
            return []
        submitted = []
        for t in terminations(pod, set(self.settings.reasons)):
            iid = incident_id(t)
            if iid in self._seen:
                continue
            self._seen.add(iid)
            if t.finished_at < self._oldest:
                logger.debug("ignoring termination from before startup: %s", iid)
                continue
            key = (t.namespace, t.workload_kind, t.workload_name, t.container, t.reason)
            if not self._cooldown.allow(key):
                logger.info("suppressed repeat %s for %s/%s", t.reason, t.namespace,
                            t.workload_name)
                continue
            if self.client.submit(to_incident(t, self._now())):
                submitted.append(iid)
                logger.info("submitted incident %s for %s/%s", iid, t.namespace,
                            t.workload_name)
        return submitted


class Heartbeat:
    def __init__(self, max_age_s: float = 600.0) -> None:
        self._last = time.monotonic()
        self._max_age = max_age_s

    def touch(self) -> None:
        self._last = time.monotonic()

    def recent(self) -> bool:
        return time.monotonic() - self._last < self._max_age


class ApiGone(Exception):
    """The watch resourceVersion is too old; a full re-list is needed."""


def run(
    watcher: Watcher,
    list_pods: Callable[[], tuple[Iterable[dict[str, Any]], str]],
    watch_pods: Callable[[str], Iterable[tuple[str, dict[str, Any]]]],
    heartbeat: Callable[[], None],
    *,
    should_continue: Callable[[], bool] = lambda: True,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """List once, then watch from the list's resourceVersion (REQ-WAT-07).

    ``list_pods`` returns (pods, resourceVersion). ``watch_pods`` yields (event type, pod)
    and raises ``ApiGone`` when the version has expired.
    """
    resource_version: str | None = None
    while should_continue():
        try:
            if resource_version is None:
                pods, resource_version = list_pods()
                for pod in pods:
                    watcher.handle(pod)
                heartbeat()
            for event_type, pod in watch_pods(resource_version):
                heartbeat()
                version = (pod.get("metadata") or {}).get("resourceVersion")
                if version:
                    resource_version = version
                if event_type in ("ADDED", "MODIFIED"):
                    watcher.handle(pod)
            heartbeat()
        except ApiGone:
            logger.info("watch version expired; re-listing pods")
            resource_version = None
        except Exception:
            logger.exception("watch failed; reconnecting")
            sleep(5)


def kubernetes_sources() -> tuple[Callable[..., Any], Callable[..., Any]]:
    from kubernetes import client, watch
    from kubernetes.client.rest import ApiException

    from kiroween_shared.k8s.client import load_api_client

    api = load_api_client()
    core = client.CoreV1Api(api)

    def list_pods() -> tuple[list[dict[str, Any]], str]:
        result = core.list_pod_for_all_namespaces()
        pods = [api.sanitize_for_serialization(p) for p in result.items]
        return pods, result.metadata.resource_version

    def watch_pods(resource_version: str) -> Iterable[tuple[str, dict[str, Any]]]:
        try:
            for event in watch.Watch().stream(
                core.list_pod_for_all_namespaces,
                resource_version=resource_version,
                timeout_seconds=300,
            ):
                if event.get("type") == "ERROR":
                    raise ApiGone()
                yield event["type"], api.sanitize_for_serialization(event["object"])
        except ApiException as exc:
            if exc.status == 410:
                raise ApiGone() from exc
            raise

    return list_pods, watch_pods


def main() -> None:
    from kiroween_shared.server import configure_logging, create_app, serve

    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    settings = WatcherSettings.from_env()
    beat = Heartbeat()
    app = create_app("orchestrator-watcher", ready=beat.recent)
    threading.Thread(target=serve, args=(app, settings.health_port), daemon=True).start()

    list_pods, watch_pods = kubernetes_sources()
    watcher = Watcher(settings, OrchestratorClient(settings.orchestrator_url))
    logger.info("watching pods for %s", sorted(settings.reasons))
    run(watcher, list_pods, watch_pods, beat.touch)
