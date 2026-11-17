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
from .detect import (
    EVENT_REASONS,
    WAITING_REASONS,
    Signal,
    event_signal,
    signal_id,
    signal_to_incident,
    termination_signal,
    terminations,
    waiting_signals,
)

logger = logging.getLogger(__name__)

# agents is excluded so the agents never investigate their own pods.
DEFAULT_EXCLUDED = ["kube-system", "kube-node-lease", "argocd", "agents"]


@dataclass(frozen=True)
class WatcherSettings:
    orchestrator_url: str = "http://orchestrator:8080"
    reasons: frozenset[str] = frozenset({"OOMKilled"})
    waiting_reasons: frozenset[str] = WAITING_REASONS
    pod_event_reasons: frozenset[str] = EVENT_REASONS["Pod"]
    pvc_event_reasons: frozenset[str] = EVENT_REASONS["PersistentVolumeClaim"]
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
            waiting_reasons=frozenset(env_list(env, "WAITING_REASONS", sorted(WAITING_REASONS))),
            pod_event_reasons=frozenset(
                env_list(env, "POD_EVENT_REASONS", sorted(EVENT_REASONS["Pod"]))),
            pvc_event_reasons=frozenset(env_list(
                env, "PVC_EVENT_REASONS", sorted(EVENT_REASONS["PersistentVolumeClaim"]))),
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
        get_pod: Callable[[str, str], dict[str, Any] | None] | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self._get_pod = get_pod
        # The pod and event streams run in two threads and share the dedup state.
        self._lock = threading.Lock()
        self._now = now
        self._oldest = (started_at or now()) - timedelta(seconds=settings.startup_grace_s)
        self._cooldown = Cooldown(settings.cooldown_s, clock)
        self._seen = SeenIds()

    def handle(self, pod: dict[str, Any]) -> list[str]:
        """Process one pod (terminations and waiting states); returns submitted ids."""
        signals = [termination_signal(t) for t in terminations(pod, set(self.settings.reasons))]
        signals += waiting_signals(pod, self.settings.waiting_reasons, self.settings.reasons)
        return [iid for s in signals if (iid := self._submit(s))]

    def handle_event(self, event: dict[str, Any]) -> list[str]:
        """Process one Event; returns submitted ids."""
        reasons = {"Pod": self.settings.pod_event_reasons,
                   "PersistentVolumeClaim": self.settings.pvc_event_reasons}
        signal = event_signal(event, reasons, self._lookup_pod)
        iid = self._submit(signal) if signal else None
        return [iid] if iid else []

    def _lookup_pod(self, namespace: str, name: str) -> dict[str, Any] | None:
        if self._get_pod is None:
            return None
        try:
            return self._get_pod(namespace, name)
        except Exception:  # the pod may be gone; fall back to the pod as its own workload
            return None

    def _submit(self, s: Signal) -> str | None:
        if s.namespace in self.settings.excluded_namespaces:
            return None
        iid = signal_id(s)
        with self._lock:
            if iid in self._seen:
                return None
            self._seen.add(iid)
            if s.at is not None and s.at < self._oldest:
                logger.debug("ignoring %s from before startup: %s", s.reason, iid)
                return None
            key = (s.namespace, s.workload_kind, s.workload_name, s.container, s.reason)
            if not self._cooldown.allow(key):
                logger.info("suppressed repeat %s for %s/%s", s.reason, s.namespace,
                            s.workload_name)
                return None
        if self.client.submit(signal_to_incident(s, self._now())):
            logger.info("submitted incident %s (%s) for %s/%s", iid, s.reason, s.namespace,
                        s.workload_name)
            return iid
        return None


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
    handle: Callable[[dict[str, Any]], Any],
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
                    handle(pod)
                heartbeat()
            for event_type, pod in watch_pods(resource_version):
                heartbeat()
                version = (pod.get("metadata") or {}).get("resourceVersion")
                if version:
                    resource_version = version
                if event_type in ("ADDED", "MODIFIED"):
                    handle(pod)
            heartbeat()
        except ApiGone:
            logger.info("watch version expired; re-listing pods")
            resource_version = None
        except Exception:
            logger.exception("watch failed; reconnecting")
            sleep(5)


def kubernetes_sources(
    resource: str = "pods",
) -> tuple[Callable[..., Any], Callable[..., Any]]:
    """list/watch callables for ``pods`` or ``events`` across all namespaces."""
    from kubernetes import client, watch
    from kubernetes.client.rest import ApiException

    from kiroween_shared.k8s.client import load_api_client

    api = load_api_client()
    core = client.CoreV1Api(api)
    list_all = {"pods": core.list_pod_for_all_namespaces,
                "events": core.list_event_for_all_namespaces}[resource]

    def list_objects() -> tuple[list[dict[str, Any]], str]:
        result = list_all()
        items = [api.sanitize_for_serialization(o) for o in result.items]
        return items, result.metadata.resource_version

    def watch_objects(resource_version: str) -> Iterable[tuple[str, dict[str, Any]]]:
        try:
            for event in watch.Watch().stream(
                list_all, resource_version=resource_version, timeout_seconds=300
            ):
                if event.get("type") == "ERROR":
                    raise ApiGone()
                yield event["type"], api.sanitize_for_serialization(event["object"])
        except ApiException as exc:
            if exc.status == 410:
                raise ApiGone() from exc
            raise

    return list_objects, watch_objects


def kubernetes_get_pod() -> Callable[[str, str], dict[str, Any] | None]:
    from kubernetes import client

    from kiroween_shared.k8s.client import load_api_client

    api = load_api_client()
    core = client.CoreV1Api(api)

    def get_pod(namespace: str, name: str) -> dict[str, Any] | None:
        return api.sanitize_for_serialization(core.read_namespaced_pod(name, namespace))

    return get_pod


def main() -> None:
    from kiroween_shared.server import configure_logging, create_app, serve

    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    settings = WatcherSettings.from_env()
    pod_beat, event_beat = Heartbeat(), Heartbeat()
    app = create_app("orchestrator-watcher",
                     ready=lambda: pod_beat.recent() and event_beat.recent())
    threading.Thread(target=serve, args=(app, settings.health_port), daemon=True).start()

    watcher = Watcher(settings, OrchestratorClient(settings.orchestrator_url),
                      get_pod=kubernetes_get_pod())
    list_events, watch_events = kubernetes_sources("events")
    threading.Thread(target=run, name="events", daemon=True,
                     args=(watcher.handle_event, list_events, watch_events,
                           event_beat.touch)).start()
    list_pods, watch_pods = kubernetes_sources("pods")
    logger.info("watching pods for %s and %s, events for %s and %s",
                sorted(settings.reasons), sorted(settings.waiting_reasons),
                sorted(settings.pod_event_reasons), sorted(settings.pvc_event_reasons))
    run(watcher.handle, list_pods, watch_pods, pod_beat.touch)
