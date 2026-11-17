# PM-2026-031: report-renderer OOMKilled during month-end batch

- **Date:** 2026-03-31
- **Severity:** SEV-2
- **Signal:** OOMKilled (exit code 137), CrashLoopBackOff
- **Namespace / workload:** `reports` / Deployment `report-renderer`, container `renderer`

## Summary

The PDF renderer was killed by the kernel OOM killer on every month-end job. The
container memory limit was 256Mi; rendering the largest customer statement (about 9,000
pages) peaked at about 410Mi. The pod restarted 23 times in 40 minutes and entered
CrashLoopBackOff.

## Impact

Month-end statements for 312 customers were delayed by 3 hours 10 minutes. No data was
lost; jobs were retried from the queue after the fix.

## Root cause

The renderer loads every page of a statement into memory before writing the PDF. The
256Mi limit was set two years earlier, when the largest statement was about 1,500 pages.
Statement size grew with the customer, the limit did not. CPU and node memory were fine;
the container hit its own cgroup limit.

## Evidence

- `kubectl get pod` showed `lastState.terminated.reason: OOMKilled`, `exitCode: 137`,
  restart count 23.
- The previous container's log ended with `rendering page 6120/9044` and no error line,
  which is typical for a SIGKILL.
- `resources.limits.memory: 256Mi`, `resources.requests.memory: 128Mi`.
- Grafana `container_memory_working_set_bytes` for the container climbed linearly to the
  limit within 90 seconds of each start.
- Node `MemoryPressure` was `False` the whole time, so this was a container limit, not
  node pressure.

## Fix

Raised the memory limit of container `renderer` from 256Mi to 512Mi and the request from
128Mi to 384Mi, sized from the observed 410Mi peak plus 25% headroom, via a pull request
to the workload's GitOps manifest. Pods stopped restarting and the backlog drained in 25
minutes.

## Prevention

- Stream pages to the PDF writer instead of holding the whole statement in memory
  (tracked as RPT-412); after that, the limit can go back down.
- Alert when a container's working set stays above 85% of its limit for 10 minutes.
- Load-test with the largest real statement before every quarter close.
