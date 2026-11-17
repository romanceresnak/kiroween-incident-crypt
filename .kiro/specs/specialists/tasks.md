# specialists — Tasks

Local tasks only: no AWS change, no Terraform change, no push. Execution notes and stop
conditions are those of `agents-core/tasks.md`. Commit after each task.

## TASK-01 — Shared proposals framework and model changes
REQ-PRO-01 to PRO-04, PRO-06, REQ-WTX-04, REQ-ORX-02.
`proposals.py` (path resolution, `ChangeRule`, `validate_change`); `ProposalDraft` gains
`set_field` and an optional container; `Incident.pod`/`container` optional;
`Classification.confidence`.
**Test:** path resolution, every validation step, protected namespaces, and all existing
shared and cpu-memory tests still pass.

## TASK-02 — Read-only client and tools for the three agents
REQ-TLS-01, TLS-02.
`ReadOnlyKube` read methods and `extra_tools.py`.
**Test:** the AST write guard still passes over the package; ConfigMap tools return keys
only; the Corefile is returned; API errors become results; outputs are bounded.

## TASK-03 — Shared specialist runtime
REQ-SPC-01, SPC-03.
`specialist.py`.
**Test:** a scripted investigation end to end through the runtime, the HTTP API, 503 on an
unavailable model, 422 on a bad incident.

## TASK-04 — network specialist
REQ-SPC-02, PRO-05 (network), ACC-01.
**Test:** each allowed change, each rejection, injection in an event message, missing
objects; `uv sync --frozen`, ruff, pytest.

## TASK-05 — deploy-rollout specialist
Same, for deploy-rollout.

## TASK-06 — storage specialist
Same, for storage.

## TASK-07 — Orchestrator delegation to all specialists
REQ-ORX-01 to ORX-04.
**Test:** routing per category, missing URL gives `no_specialist`, low confidence and
disallowed category give `unknown`, the delegation agent sees one tool.

## TASK-08 — Watcher signals
REQ-WTX-01 to WTX-03.
**Test:** each waiting reason, each event reason, PVC incidents, stable ids, cooldown,
startup grace for events, two streams sharing one watcher.

## TASK-09 — Chart
REQ-CHX-01 to CHX-03, REQ-TLS-03, TLS-04.
**Test:** chart render tests for RBAC (exact rules, no secrets, no wildcards), network
policies (ingress only from the orchestrator), deployments hidden until tagged, tag markers.

## TASK-10 — CI
REQ-CIX-01.
**Test:** workflow tests for the five-package matrix and the five tags; actionlint.

## TASK-11 — Chaos scenarios
REQ-CHS-01 to CHS-04.
**Test:** shellcheck; fake-kubectl tests for context guard, up, down, idempotency, and the
manifests' shape.

## TASK-12 — Spec status
Record the task table and what was not verified.

## Status

All tasks done. Nothing pushed. Final run: shared 135, orchestrator 88, cpu_memory 36,
network 17, deploy_rollout 15, storage 18, repo-level (gitops, chaos, tools) 134; ruff clean
in every package; helm lint, actionlint and shellcheck clean.

| Task | Commit | Result |
|---|---|---|
| TASK-01 Proposals framework, model changes | `131329e` | shared 111 pass |
| TASK-02 Read-only client methods and tools | `1c2fc49` | shared 130 pass |
| TASK-03 Shared specialist runtime | `e69d4e9` | shared 135 pass |
| TASK-04 network | `3b96c3f` | 17 pass |
| TASK-05 deploy_rollout | `5979844` | 15 pass |
| TASK-06 storage | `7f7650b` | 18 pass |
| TASK-07 Orchestrator delegation | `5c053fc` | orchestrator 63 pass (36 existing + 27) |
| TASK-08 Watcher signals | `c4accf3` | orchestrator 88 pass (+25) |
| TASK-09 Chart | `60ecb17` | helm lint; repo-level 86 pass |
| TASK-10 CI | `1896c76` | actionlint + shellcheck; repo-level 89 pass |
| TASK-11 Chaos scenarios | `ab26c91` | shellcheck; repo-level 134 pass (chaos 58) |
| TASK-12 Status | this commit | — |

Changes from the design made during execution:
- RBAC grants only what the tools call. `ingressclasses`, `csidrivers`, `csinodes` and
  `controllerrevisions` (design §8) are not granted because no tool reads them.
- The watcher skips a `CrashLoopBackOff` whose last termination is `OOMKilled`, so one OOM
  is one incident (routed to cpu_memory), not two.
- The watcher excludes the `agents` namespace by default and in the chart.
- `chaos/badimage` uses `public.ecr.aws/docker/library/python` (a non-root server on 8080)
  instead of nginx, which runs as root on port 80 and would not pass `runAsNonRoot`.
- The chart test for unset tags now sets every tag to `unset` explicitly. It had failed
  since CI wrote real tags into `values.yaml`.

Not verified:
- No Docker image for the three new packages has been built (no Docker daemon locally).
- Real Bedrock was never called; every agent test uses a scripted model.
- The watcher's event stream, the new RBAC and the network policies have not run on the
  cluster. Readiness and Pod Identity under default-deny for the new pods are unproven.
- That the PV controller emits `ProvisioningFailed` for a missing StorageClass on this
  cluster, and that a fix to `gp2` (in-tree `kubernetes.io/aws-ebs`) provisions on 1.37.
  `spec.storageClassName` is immutable on an existing PVC, so the approved change must be
  delivered as delete-and-recreate of the PVC, not an in-place edit.
- The chaos manifests have not been applied to a cluster.
