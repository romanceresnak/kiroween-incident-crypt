"""network specialist: DNS, NetworkPolicy, Service and Ingress problems (specialists §1)."""

from __future__ import annotations

import re
from typing import Any

from kiroween_shared.k8s.client import KubeError
from kiroween_shared.k8s.extra_tools import network_tools
from kiroween_shared.models import ProposalDraft
from kiroween_shared.proposals import (
    LABEL_KEY_SEGMENT,
    ChangeRule,
    Context,
    label_value,
    parse_path,
    port_value,
)
from kiroween_shared.specialist import SpecialistSpec

DIAGNOSIS_PROMPT = """\
You are the network specialist in a Kubernetes incident response system. You diagnose
connectivity problems with read-only tools:
- DNS failures: get_coredns_status (CoreDNS pod states, recent errors, the Corefile).
- A NetworkPolicy blocking traffic: list_network_policies in the namespace, then compare
  each policy's podSelector and its ingress "from" selectors with the labels of the pods
  involved (list_pods, get_pod). Remember that labels in one matchLabels must all match.
- A Service selector or port mismatch: get_service, list_pods with the Service's selector,
  get_endpoints.
- Endpoints with no ready address: get_endpoints, then the readiness of the selected pods.
- Ingress errors: list_ingresses, then the backend Service and its endpoints.

Changes you may propose (set_field):
- Service  spec.selector.<key>  (write spec.selector["<key>"] when the key contains a dot
  or a slash): a label value.
- Service  spec.ports[<index>].targetPort: a port number or a port name.
- NetworkPolicy  spec.ingress[<i>].from[<j>].podSelector.matchLabels.<key>: a label value
  that a pod in the namespace actually carries, so the policy admits that workload.
Anything else, including CoreDNS and Ingress problems, is investigate_further.
"""


def label_carried_by_a_pod(ctx: Context, draft: ProposalDraft, observed: Any) -> str | None:
    """A policy fix must admit a real workload, not open the policy."""
    error = label_value(ctx, draft, observed)
    if error:
        return error
    key = parse_path(draft.field or "")[-1]
    try:
        pods = ctx.kube.list_pods(ctx.incident.namespace, f"{key}={draft.proposed}")
    except KubeError as exc:
        return f"could not list pods to check the label ({exc.reason})"
    if not pods.get("items"):
        return f"no pod in {ctx.incident.namespace} has the label {key}={draft.proposed}"
    return None


RULES = [
    ChangeRule("Service", re.compile(r"spec\.selector" + LABEL_KEY_SEGMENT), label_value),
    ChangeRule("Service", re.compile(r"spec\.ports\[\d+\]\.targetPort"), port_value),
    ChangeRule(
        "NetworkPolicy",
        re.compile(r"spec\.ingress\[\d+\]\.from\[\d+\]\.podSelector\.matchLabels"
                   + LABEL_KEY_SEGMENT),
        label_carried_by_a_pod,
    ),
]

SPEC = SpecialistSpec(name="network", diagnosis_prompt=DIAGNOSIS_PROMPT, tools=network_tools,
                      rules=RULES)
