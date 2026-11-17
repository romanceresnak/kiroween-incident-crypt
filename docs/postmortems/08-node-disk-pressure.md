# PM-2026-091: node disk pressure evicted pods after container logs filled the root volume

- **Date:** 2026-10-28
- **Severity:** SEV-2
- **Signal:** Evicted pods, node condition DiskPressure, FailedScheduling (untolerated taint)
- **Namespace / workload:** node `ip-10-0-2-47`; noisy workload `media` / Deployment
  `thumbnailer`

## Summary

A debug flag left on in `thumbnailer` made it log every request at about 30 MB per
minute. Its logs and a growing `emptyDir` scratch volume filled the node's 20 GiB root
volume. The kubelet set `DiskPressure`, tainted the node with
`node.kubernetes.io/disk-pressure:NoSchedule` and evicted pods, including unrelated ones.

## Impact

Eleven pods were evicted from one node over 15 minutes; most rescheduled elsewhere within
a minute. Two single-replica services had short outages (under 2 minutes each).

## Root cause

The node group used 20 GiB root volumes, and container logs, images and `emptyDir` share
that volume. `thumbnailer` had no `ephemeral-storage` limit and no `sizeLimit` on its
scratch `emptyDir`, so nothing stopped one pod from filling the disk.

## Evidence

- Node condition `DiskPressure=True`; taint `node.kubernetes.io/disk-pressure`.
- Kubelet events: `The node was low on resource: ephemeral-storage`, `Evicted` pods.
- `thumbnailer` scratch `emptyDir` at 11 GiB; its container log at 6 GiB.
- Other pods' events: `FailedScheduling ... untolerated taint {node.kubernetes.io/
  disk-pressure}`.

## Fix

Turned the debug flag off, set `resources.limits.ephemeral-storage: 2Gi` on container
`thumbnailer` and `sizeLimit: 1Gi` on its scratch volume via the GitOps repo, and drained
and replaced the affected node. The node group root volume was raised to 50 GiB in the
next infrastructure change.

## Prevention

- Every container has an `ephemeral-storage` limit; every `emptyDir` has a `sizeLimit`.
- Alert at 80% node filesystem usage, before the kubelet's eviction threshold.
- Triage hint: evictions across unrelated workloads on one node point to node pressure;
  find the pod that consumes the resource rather than fixing each evicted pod.
