"""deploy-rollout specialist: image pulls, bad config, stuck rollouts, Argo CD (specialists §1)."""

from __future__ import annotations

import re
from typing import Any

from kiroween_shared.k8s.client import KubeError
from kiroween_shared.k8s.extra_tools import deploy_tools, rollout_history
from kiroween_shared.models import ProposalDraft
from kiroween_shared.proposals import ChangeRule, Context, incident_workload, parse_path
from kiroween_shared.specialist import SpecialistSpec

DIAGNOSIS_PROMPT = """\
You are the deploy-rollout specialist in a Kubernetes incident response system. You
diagnose why a workload does not run its new version, with read-only tools:
- ImagePullBackOff or ErrImagePull: get_pod and list_events for the pull error (not found,
  manifest unknown, access denied), then get_rollout_history for an earlier revision whose
  image was ready.
- CrashLoopBackOff or CreateContainerConfigError from bad configuration: get_pod_logs with
  previous=True, list_events (a missing ConfigMap, a missing key, a missing Secret), and
  list_configmap_keys to check that a referenced key exists. Secret values are never
  readable; reason from the event messages.
- A stuck or failed rollout: get_workload_status (Progressing=False with
  ProgressDeadlineExceeded, unavailable replicas), get_rollout_history.
- An Argo CD sync or health failure: get_argocd_application (sync and health status,
  conditions, the last operation's message).

The only change you may propose (set_field):
- Deployment  spec.template.spec.containers[name=<container>].image: set the image back to
  the image this Deployment ran in an earlier revision, as shown by get_rollout_history.
  The target is the incident's Deployment.
Configuration, Secret and environment problems, StatefulSets, DaemonSets and Argo CD
Applications are investigate_further: say exactly which ConfigMap, key or setting a
human should change.
"""

IMAGE_FIELD = re.compile(r"spec\.template\.spec\.containers\[name=[A-Za-z0-9._-]+\]\.image")


def image_from_an_earlier_revision(ctx: Context, draft: ProposalDraft,
                                   observed: Any) -> str | None:
    """Only an image this workload already ran is acceptable (REQ-PRO-05)."""
    container = parse_path(draft.field or "")[-2][1]
    target = draft.target
    if target is None:
        return "set_field needs a target"
    try:
        history = rollout_history(ctx.kube, target.namespace, target.name)
    except KubeError as exc:
        return f"could not read the rollout history ({exc.reason})"
    if not history:
        return "the Deployment has no ReplicaSet history"
    newest = max(h["revision"] or 0 for h in history)
    earlier = {h["images"].get(container) for h in history if (h["revision"] or 0) < newest}
    earlier.discard(None)
    if draft.proposed not in earlier:
        return (f"{draft.proposed!r} is not an image this Deployment ran in an earlier "
                "revision")
    return None


RULES = [
    ChangeRule("Deployment", IMAGE_FIELD, image_from_an_earlier_revision,
               target=incident_workload),
]

SPEC = SpecialistSpec(name="deploy-rollout", diagnosis_prompt=DIAGNOSIS_PROMPT,
                      tools=deploy_tools, rules=RULES)
