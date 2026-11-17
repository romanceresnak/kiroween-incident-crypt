# PM-2026-066: CoreDNS forwarding loop broke name resolution cluster-wide

- **Date:** 2026-07-09
- **Severity:** SEV-1
- **Signal:** Many readiness failures (Unhealthy) with `no such host`, CoreDNS CrashLoopBackOff
- **Namespace / workload:** `kube-system` / Deployment `coredns`; affected: most namespaces

## Summary

A change to the CoreDNS `Corefile` added a `forward` to a resolver that itself forwarded
back to the cluster DNS. CoreDNS's `loop` plugin detected the loop and the pods exited,
then crash-looped. Every lookup of a Service name failed, so many unrelated workloads
reported readiness failures at the same moment.

## Impact

For 24 minutes, services that resolve dependencies by name failed or degraded. External
traffic was partly served from caches. Root cause took longer to find because the first
alerts came from application teams, not from DNS.

## Root cause

The `coredns` ConfigMap was edited to forward `corp.internal` to `10.40.0.2`. That
resolver forwarded unknown zones to the VPC resolver, which forwarded `cluster.local` back
to CoreDNS. The `loop` plugin is designed to stop CoreDNS in that situation.

## Evidence

- CoreDNS logs: `[FATAL] plugin/loop: Loop (127.0.0.1:53 -> :53) detected for zone "."`.
- `kubectl -n kube-system get pods -l k8s-app=kube-dns`: CrashLoopBackOff on both
  replicas.
- Application events across namespaces: `Readiness probe failed: dial tcp: lookup
  orders on 172.20.0.10:53: no such host`.
- `kube-dns` EndpointSlice had zero ready endpoints.

## Fix

Reverted the `forward corp.internal 10.40.0.2` block in the `coredns` ConfigMap through the
GitOps repo and restarted the CoreDNS Deployment. Resolution recovered within a minute;
dependent workloads became Ready on their next probe.

## Prevention

- CoreDNS config changes go through a staging cluster first, with a resolution test for
  `kubernetes.default.svc.cluster.local` and an external name.
- Alert on `kube-dns` ready endpoints < 2.
- Triage hint: when many unrelated workloads fail at once with DNS errors, check CoreDNS
  before any single workload.
