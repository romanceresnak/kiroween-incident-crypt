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
