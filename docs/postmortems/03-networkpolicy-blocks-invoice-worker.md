# PM-2026-058: NetworkPolicy blocked the billing worker from the ledger service

- **Date:** 2026-06-03
- **Severity:** SEV-2
- **Signal:** Readiness probe failed (Unhealthy): `wget: download timed out`
- **Namespace / workload:** `billing` / Deployment `invoice-worker`, container `worker`;
  policy `ledger-allow-api` on Deployment `ledger`

## Summary

After a label clean-up, `invoice-worker` pods never became Ready. Their readiness probe
calls `http://ledger:8080/healthz`, and every call timed out. The `ledger` pods were
healthy and Ready the whole time. The NetworkPolicy in front of `ledger` admitted only
pods labelled `app: billing-api`, and the worker's label had just been renamed from
`app: billing-api` to `app: invoice-worker`.

## Impact

Invoices were not generated for 2 hours 5 minutes. They were generated late from the
queue; no invoice was lost or duplicated.

## Root cause

The policy `ledger-allow-api` selects `app: ledger` and allows ingress on 8080 only
`from: podSelector: matchLabels: app: billing-api`. The label rename on the worker's pod
template was reviewed as a cosmetic change; nobody checked which NetworkPolicies selected
the old label. With the VPC CNI network policy agent enforcing policies, the packets were
dropped silently, so the client saw timeouts, not connection refused.

## Evidence

- Client pod events: `Readiness probe failed: wget: download timed out` every 10 s.
- `ledger` EndpointSlice: all endpoints `ready: true`. The server side was healthy.
- `kubectl get networkpolicy ledger-allow-api -o yaml`: ingress from
  `podSelector.matchLabels.app: billing-api`.
- No pod in the namespace carried `app: billing-api` any more.
- From a debug pod labelled `app: billing-api`, the same request succeeded in 4 ms.

## Fix

Changed `spec.ingress[0].from[0].podSelector.matchLabels.app` of NetworkPolicy
`ledger-allow-api` from `billing-api` to `invoice-worker`, via a pull request to the
GitOps repo. The worker pods became Ready within one probe period.

## Prevention

- CI check that fails when a pod-template label used by any NetworkPolicy selector
  changes.
- Prefer a dedicated label for policy selection (`ledger-client: "true"`) over `app`.
- Note in the runbook: timeouts with a healthy, Ready server usually mean a policy drop.
  A "connection refused" during the server's own startup is a different, transient
  problem.
