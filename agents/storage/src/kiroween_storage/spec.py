"""storage specialist: PVC provisioning, mounts, EBS CSI and disk pressure (specialists §1)."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from kiroween_shared.k8s.client import KubeError
from kiroween_shared.k8s.extra_tools import storage_tools
from kiroween_shared.models import ProposalDraft
from kiroween_shared.proposals import ChangeRule, Context
from kiroween_shared.quantity import parse_quantity
from kiroween_shared.specialist import SpecialistSpec

MAX_GROWTH = Decimal(4)

DIAGNOSIS_PROMPT = """\
You are the storage specialist in a Kubernetes incident response system. You diagnose
volume problems with read-only tools:
- A PVC stuck Pending: get_pvc (StorageClass, phase), list_events for the
  PersistentVolumeClaim (ProvisioningFailed), list_storage_classes (does the class exist,
  which one is the default), get_ebs_csi_status (provisioning errors in the
  csi-provisioner log).
- A pod Pending with FailedScheduling because of an unbound PVC: get_pod for its volumes,
  then the PVC as above.
- FailedMount or FailedAttachVolume: list_events for the pod, list_volume_attachments,
  get_pv, get_ebs_csi_status.
- Disk pressure: get_node conditions (DiskPressure) and allocatable storage.

Changes you may propose (set_field), always on a PVC the incident is about (the incident's
PVC, or one the incident pod mounts):
- PersistentVolumeClaim  spec.storageClassName: only while the PVC is Pending, and only to
  a StorageClass that exists. This field cannot be changed in place, so the risk must say
  the PVC will be deleted and recreated; that loses no data because it never bound.
- PersistentVolumeClaim  spec.resources.requests.storage: an increase of at most 4 times,
  only when the PVC's StorageClass allows volume expansion.
Mount, attach, CSI driver and node disk problems are investigate_further.
"""


def pvc_of_the_incident(ctx: Context, draft: ProposalDraft) -> str | None:
    target = draft.target
    incident = ctx.incident
    if target is None:
        return "set_field needs a target"
    if incident.workload.kind == "PersistentVolumeClaim" and target.name == incident.workload.name:
        return None
    if incident.pod:
        try:
            pod = ctx.kube.get_pod(incident.namespace, incident.pod)
        except KubeError as exc:
            return f"could not read the incident pod ({exc.reason})"
        claims = {
            (v.get("persistentVolumeClaim") or {}).get("claimName")
            for v in (pod.get("spec") or {}).get("volumes") or []
        }
        if target.name in claims:
            return None
    return "the PVC is neither the incident's nor mounted by the incident pod"


def _storage_classes(ctx: Context) -> dict[str, dict[str, Any]]:
    return {sc["metadata"]["name"]: sc
            for sc in ctx.kube.list_storage_classes().get("items") or []}


def existing_class_while_pending(ctx: Context, draft: ProposalDraft,
                                 observed: Any) -> str | None:
    phase = ((ctx.obj or {}).get("status") or {}).get("phase")
    if phase != "Pending":
        return f"the PVC is {phase}; its StorageClass can only be replaced while it is Pending"
    try:
        classes = _storage_classes(ctx)
    except KubeError as exc:
        return f"could not list StorageClasses ({exc.reason})"
    if draft.proposed not in classes:
        return f"StorageClass {draft.proposed!r} does not exist"
    return None


def bounded_expansion(ctx: Context, draft: ProposalDraft, observed: Any) -> str | None:
    current, proposed = parse_quantity(observed), parse_quantity(draft.proposed)
    if current is None or proposed is None:
        return "the size is not a valid quantity"
    if proposed <= current:
        return "the proposed size is not an increase"
    if proposed > current * MAX_GROWTH:
        return f"the proposed size exceeds {MAX_GROWTH}x the current size"
    class_name = ((ctx.obj or {}).get("spec") or {}).get("storageClassName")
    try:
        storage_class = _storage_classes(ctx).get(class_name or "")
    except KubeError as exc:
        return f"could not list StorageClasses ({exc.reason})"
    if not storage_class or not storage_class.get("allowVolumeExpansion"):
        return f"StorageClass {class_name!r} does not allow volume expansion"
    return None


RULES = [
    ChangeRule("PersistentVolumeClaim", re.compile(r"spec\.storageClassName"),
               existing_class_while_pending, target=pvc_of_the_incident),
    ChangeRule("PersistentVolumeClaim", re.compile(r"spec\.resources\.requests\.storage"),
               bounded_expansion, target=pvc_of_the_incident),
]

SPEC = SpecialistSpec(name="storage", diagnosis_prompt=DIAGNOSIS_PROMPT, tools=storage_tools,
                      rules=RULES)
