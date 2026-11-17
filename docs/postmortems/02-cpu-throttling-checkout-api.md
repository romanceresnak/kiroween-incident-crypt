# PM-2026-047: checkout-api latency from CPU throttling

- **Date:** 2026-05-12
- **Severity:** SEV-2
- **Signal:** CPU throttling, readiness probe timeouts (Unhealthy), p99 latency alert
- **Namespace / workload:** `shop` / Deployment `checkout-api`, container `api`

## Summary

Checkout p99 latency rose from 180 ms to 2.4 s during a promotion. The pods were not
crashing and were not out of memory. They were spending most of each 100 ms CFS period
throttled, because their CPU limit (500m) was far below what the request load needed.

## Impact

For 55 minutes, about 7% of checkouts timed out at the edge (HTTP 504) and were retried
by clients. Revenue impact was estimated at 1.1% of the promotion's first hour.

## Root cause

`checkout-api` had `resources.limits.cpu: 500m` with `requests.cpu: 250m`. The HPA
scaled on CPU *utilisation of the request*, so it reported 190% and added pods, but every
new pod was throttled the same way, and the HPA hit its `maxReplicas: 6`. The limit, not
the replica count, was the bottleneck. Readiness probes with a 1 s timeout then failed on
throttled pods, which removed them from the Service and pushed more load onto the rest.

## Evidence

- `container_cpu_cfs_throttled_periods_total / container_cpu_cfs_periods_total` was 0.78
  for every `api` container.
- `kubectl describe hpa checkout-api`: current 190% / target 70%, replicas 6 of max 6.
- Events: `Readiness probe failed: context deadline exceeded` on 4 of 6 pods.
- Node CPU usage was 41%; the nodes had spare capacity.

## Fix

Raised the CPU limit of container `api` from 500m to 1500m and the request from 250m to
500m, and raised the HPA `maxReplicas` from 6 to 10. Throttling dropped below 5% within
two minutes and p99 latency returned to 210 ms.

## Prevention

- Dashboard panel and alert on the throttling ratio above 25% for 5 minutes.
- Readiness probe timeout raised to 3 s so a briefly throttled pod is not ejected.
- Before a promotion, check that `maxReplicas` x limit covers the expected peak.
