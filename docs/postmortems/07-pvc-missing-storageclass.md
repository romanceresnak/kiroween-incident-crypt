# PM-2026-085: analytics-cache stuck Pending on a StorageClass that did not exist

- **Date:** 2026-10-06
- **Severity:** SEV-3
- **Signal:** ProvisioningFailed on the PVC, FailedScheduling on the pod
- **Namespace / workload:** `analytics` / PVC `cache-data`, Deployment `analytics-cache`

## Summary

A new cache service was deployed with a PersistentVolumeClaim that named
`storageClassName: fast-ssd`. That class existed in the old cluster, not in this one. The
claim stayed Pending and the pod that mounts it could not be scheduled. The two signals,
`ProvisioningFailed` on the claim and `FailedScheduling` on the pod, had one cause.

## Impact

The new analytics dashboards were unavailable for their launch window (3 hours), and the
launch announcement had to be moved to the next day. No existing service was affected,
because nothing else used the claim.

## Root cause

The manifests were copied from the previous cluster. This cluster's StorageClasses were
`gp2` (in-tree) and `gp3` (EBS CSI); there was no `fast-ssd` and no default class.
`storageClassName` cannot be changed on an existing claim, so the claim had to be
recreated with the right class.

## Evidence

- PVC events: `ProvisioningFailed: storageclass.storage.k8s.io "fast-ssd" not found`.
- PVC `status.phase: Pending`, no `volumeName`.
- Pod events: `0/3 nodes are available: pod has unbound immediate PersistentVolumeClaims`.
- `kubectl get storageclass`: `gp2`, `gp3`.

## Fix

Changed `spec.storageClassName` of PVC `cache-data` from `fast-ssd` to `gp3` in the GitOps
manifest and recreated the claim (it had never bound, so no data could be lost). The volume
was provisioned in 20 seconds and the pod started.

## Prevention

- A policy check rejects PVCs that name a StorageClass that does not exist in the target
  cluster.
- Mark one StorageClass as default so claims without a class still bind.
- Treat a pod's FailedScheduling caused by an unbound claim as part of the claim's
  incident, not as a separate one.
