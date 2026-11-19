"""Pod watcher: list, then watch, and post OOMKilled incidents (REQ-WAT)."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
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
    container_resources,
    container_started,
    event_signal,
    pod_claims,
    pod_ready,
    signal_id,
    signal_to_incident,
    termination_signal,
    terminations,
    usage_millicores_of,
    waiting_signals,
)

logger = logging.getLogger(__name__)

# agents is excluded so the agents never investigate their own pods; monitoring so the
# Prometheus stack's own restarts are not incidents (memory-and-ui REQ-MON-06).
DEFAULT_EXCLUDED = ["kube-system", "kube-node-lease", "argocd", "agents", "monitoring"]


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
    # Unhealthy events are re-checked before they are submitted (hardening REQ-WSU-01).
    unhealthy_startup_grace_s: int = 60
    unhealthy_recheck_s: int = 30
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
            unhealthy_startup_grace_s=env_int(env, "UNHEALTHY_STARTUP_GRACE_SECONDS", 60),
            unhealthy_recheck_s=env_int(env, "UNHEALTHY_RECHECK_SECONDS", 30),
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
        get_pod_metrics: Callable[[str, str], dict[str, Any] | None] | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self._get_pod = get_pod
        self._get_pod_metrics = get_pod_metrics
        # The pod and event streams run in two threads and share the dedup state.
        self._lock = threading.Lock()
        self._now = now
        self._oldest = (started_at or now()) - timedelta(seconds=settings.startup_grace_s)
        self._cooldown = Cooldown(settings.cooldown_s, clock)
        self._seen = SeenIds()
        # signal id -> (signal, when to re-check). Unhealthy signals wait here.
        self._pending: dict[str, tuple[Signal, datetime]] = {}

    def handle(self, pod: dict[str, Any]) -> list[str]:
        """Process one pod (terminations and waiting states); returns submitted ids."""
        claims = pod_claims(pod)
        signals = [replace(termination_signal(t), depends_on=claims)
                   for t in terminations(pod, set(self.settings.reasons))]
        signals += waiting_signals(pod, self.settings.waiting_reasons, self.settings.reasons)
        return [iid for s in signals if (iid := self._submit(s))]

    def handle_event(self, event: dict[str, Any]) -> list[str]:
        """Process one Event; returns submitted ids."""
        reasons = {"Pod": self.settings.pod_event_reasons,
                   "PersistentVolumeClaim": self.settings.pvc_event_reasons}
        read: dict[tuple[str, str], tuple[str, dict[str, Any] | None]] = {}

        def lookup(namespace: str, name: str) -> dict[str, Any] | None:
            if (namespace, name) not in read:
                read[(namespace, name)] = self._read_pod(namespace, name)
            return read[(namespace, name)][1]

        signal = event_signal(event, reasons, lookup)
        if signal is None:
            return []
        if signal.reason == "Unhealthy" and signal.pod:
            self._defer(signal, lookup(signal.namespace, signal.pod))
            return []
        iid = self._submit(signal)
        return [iid] if iid else []

    def _defer(self, s: Signal, pod: dict[str, Any] | None) -> None:
        """Hold an Unhealthy signal until the pod has had time to start (REQ-WSU-01, 03)."""
        if s.namespace in self.settings.excluded_namespaces:
            return
        if pod is not None and pod_ready(pod):
            logger.debug("dropping Unhealthy for Ready pod %s/%s", s.namespace, s.pod)
            return
        iid = signal_id(s)
        seen_at = s.at or self._now()
        started = (container_started(pod, s.container) if pod else None) or seen_at
        due = max(seen_at + timedelta(seconds=self.settings.unhealthy_recheck_s),
                  started + timedelta(seconds=self.settings.unhealthy_startup_grace_s))
        with self._lock:
            if iid in self._seen or iid in self._pending:
                return
            if len(self._pending) >= 1000:
                logger.warning("too many pending re-checks; dropping %s", iid)
                return
            self._pending[iid] = (s, due)
        logger.info("Unhealthy for %s/%s: re-check at %s", s.namespace, s.pod, due.isoformat())

    def tick(self) -> list[str]:
        """Re-check due Unhealthy signals; submit those whose pod is still not Ready."""
        now = self._now()
        with self._lock:
            due = [(iid, s) for iid, (s, at) in self._pending.items() if at <= now]
            for iid, _ in due:
                del self._pending[iid]
        submitted = []
        for _, s in due:
            state, pod = self._read_pod(s.namespace, s.pod or "")
            if state == "gone":
                logger.info("Unhealthy pod %s/%s is gone; no incident", s.namespace, s.pod)
                continue
            if pod is not None and pod_ready(pod):
                logger.info("pod %s/%s became Ready; no incident", s.namespace, s.pod)
                continue
            # Still not Ready, or it could not be read: report it rather than lose it.
            if (iid := self._submit(self._with_resources(s, pod))) is not None:
                submitted.append(iid)
        return submitted

    def _with_resources(self, s: Signal, pod: dict[str, Any] | None) -> Signal:
        """Attach the container's CPU limit, request and usage to a probe signal (REQ-WT-03).

        The event text cannot tell a starved server from a network fault: both are a refused or
        slow probe. The limit comes from the pod already read; usage is best effort."""
        if pod is None:
            return s
        usage = None
        if self._get_pod_metrics is not None and s.pod:
            try:
                usage = usage_millicores_of(self._get_pod_metrics(s.namespace, s.pod),
                                            s.container)
            except Exception as exc:
                logger.debug("no CPU usage for %s/%s: %s", s.namespace, s.pod, exc)
        resources = container_resources(pod, s.container, usage)
        return replace(s, resources=resources) if resources is not None else s

    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def _read_pod(self, namespace: str, name: str) -> tuple[str, dict[str, Any] | None]:
        """("ok", pod), ("gone", None) on 404, or ("unknown", None)."""
        if self._get_pod is None:
            return "unknown", None
        try:
            pod = self._get_pod(namespace, name)
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                return "gone", None
            logger.warning("could not read pod %s/%s: %s", namespace, name, exc)
            return "unknown", None
        return ("ok", pod) if pod is not None else ("gone", None)

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


def kubernetes_get_pod_metrics() -> Callable[[str, str], dict[str, Any] | None]:
    """Read one pod's PodMetrics from metrics-server (read-only: get on metrics.k8s.io pods)."""
    from kubernetes import client

    from kiroween_shared.k8s.client import load_api_client

    custom = client.CustomObjectsApi(load_api_client())

    def get_pod_metrics(namespace: str, name: str) -> dict[str, Any] | None:
        return custom.get_namespaced_custom_object(
            "metrics.k8s.io", "v1beta1", namespace, "pods", name)

    return get_pod_metrics


def main() -> None:
    from kiroween_shared.server import configure_logging, create_app, serve

    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    settings = WatcherSettings.from_env()
    pod_beat, event_beat = Heartbeat(), Heartbeat()
    app = create_app("orchestrator-watcher",
                     ready=lambda: pod_beat.recent() and event_beat.recent())
    threading.Thread(target=serve, args=(app, settings.health_port), daemon=True).start()

    token = os.environ.get("ORCHESTRATOR_TOKEN", "")
    if not token:
        logger.warning("ORCHESTRATOR_TOKEN is not set: an orchestrator that requires a login "
                       "will refuse every incident")
    watcher = Watcher(settings, OrchestratorClient(settings.orchestrator_url, token=token),
                      get_pod=kubernetes_get_pod(), get_pod_metrics=kubernetes_get_pod_metrics())
    list_events, watch_events = kubernetes_sources("events")
    def recheck_forever() -> None:
        while True:
            try:
                watcher.tick()
            except Exception:
                logger.exception("Unhealthy re-check failed")
            time.sleep(5)

    threading.Thread(target=recheck_forever, name="recheck", daemon=True).start()
    threading.Thread(target=run, name="events", daemon=True,
                     args=(watcher.handle_event, list_events, watch_events,
                           event_beat.touch)).start()
    list_pods, watch_pods = kubernetes_sources("pods")
    logger.info("watching pods for %s and %s, events for %s and %s",
                sorted(settings.reasons), sorted(settings.waiting_reasons),
                sorted(settings.pod_event_reasons), sorted(settings.pvc_event_reasons))
    run(watcher.handle, list_pods, watch_pods, pod_beat.touch)
