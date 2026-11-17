# memory-and-ui — Tasks

Local tasks only: no AWS change, no Terraform change, no push. Execution notes and stop
conditions are those of `agents-core/tasks.md`. Commit after each task.

## TASK-01 — Postmortems and sync script
REQ-MEM-01, MEM-02. **Test:** sections and sidecars for all 8; script with fake
`aws`/`terraform`: account guard, upload, ingestion wait (complete, failed, timeout),
`--delete` opt-in; shellcheck.

## TASK-02 — Shared models
§1. **Test:** new fields, limits, defaults; existing suites pass.

## TASK-03 — incident-memory agent
REQ-MEM-03 to MEM-05. **Test:** query building, result mapping (metadata and fallback),
`TOP_K`/`MIN_SCORE`, Retrieve error → 503, metrics, no model or Kubernetes import.

## TASK-04 — Orchestrator memory and metrics
REQ-MEM-06, MEM-07, MON-05. **Test:** memory queried alongside classification; result in
record and passed to the specialist; failure/missing URL → empty; correlated incidents not
queried; metrics recorded; prompts mention the hints rule.

## TASK-05 — Approval flow
REQ-APR-01 to APR-05. **Test:** state rules and 409s; content-type and Origin checks;
rendering of each proposal kind from the chaos scenarios (strategic and JSON patch);
dry run; GitHub calls against a mock (branch, file, PR) and a GitHub error.

## TASK-06 — UI
REQ-UI-01 to UI-05. **Test:** `/ui` served with CSP; `GET /incidents` order and limit; the
JavaScript never assigns `innerHTML`/`outerHTML` or uses `eval`; every asset is local;
the six characters are present.

## TASK-07 — Chart
REQ-MEM-05, MON-03, MON-04, MON-06, APR-05, UI-05. **Test:** render tests for the new
Deployment, Service, SA, policies, env, optional secret, ServiceMonitors, dashboards JSON
(valid; each metric exists in code); helm lint.

## TASK-08 — Monitoring Application
REQ-MON-01, MON-02. **Test:** Application manifest checks; `helm template` of the pinned
chart with these values renders and has no Alertmanager, no Ingress, no default password.

## TASK-09 — CI
REQ-CIX-01. **Test:** workflow tests; actionlint + shellcheck.

## TASK-10 — Spec status
Record the task table and what was not verified.
