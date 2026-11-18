# polish — Tasks

Execution notes and stop conditions are those of `agents-core/tasks.md`. Commit after each
task; do not push. Pre-approved: Terraform changes that only add or update IAM policies in
place (plan first; apply only with 0 destroy and 0 replace; verify with the simulator).

## TASK-01 — UI animation
REQ-ANI-01 to ANI-05. Already delivered in `b1e6824` and `3105f04` and pushed.
**Done when:** the DOM tests (28) still pass. No new commit.

## TASK-02 — incident-memory: category filter, fallback, configurable minimum score
REQ-RET-02 to RET-06, REQ-LRN-08 (the `origin` field).
`SimilarIncident.origin`; `Memory.similar(incident, category)`; `POST /similar?category=`;
`MIN_SCORE` validation and metrics. Pick the default `MIN_SCORE` from live scores if AWS
access is available, otherwise record the provisional value and why.
**Test:** Stubber requests with and without the filter; fallback after a ValidationException;
code-side category check (other category, `general`, no category); `unknown` unfiltered;
score drop; metrics; invalid `MIN_SCORE`; origin from metadata and from the key.

## TASK-03 — orchestrator: classify first, then look up by category
REQ-RET-01, REQ-RET-06, REQ-IAM-03 (min score).
`IncidentService` and `MemoryClient` take the category; chart `incidentMemory.minScore`.
**Test:** the lookup receives the classified category; it runs after classification; unknown
passes no category; existing memory tests updated on purpose.

## TASK-04 — learning: render, write, ingest, record
REQ-LRN-01 to LRN-07.
`Learning` model, `Approval.learning`, `learning.py`, service integration, metrics, `main`
wiring.
**Test:** document and sidecar match the seeded format; hostile text cannot add a heading;
object keys and order; ingestion token; every failure path leaves the approval intact
(S3 error, ingestion error, conflict, timeout, not configured); rejection writes nothing.

## TASK-05 — UI: mark learned postmortems, show the learning outcome
REQ-LRN-08. **Test:** DOM tests for the badges and for the outcome lines.

## TASK-06 — chart: learning settings
REQ-IAM-03. Orchestrator env, values (`learning.*`, data source ID), render tests.

## TASK-07 — Terraform: orchestrator IAM for learning
REQ-IAM-01, IAM-02. Needs a valid AWS session.
`terraform fmt -check`, `validate`, `plan`; apply only if the plan has IAM policy creates or
in-place updates only; then the simulator checks.

## TASK-08 — Spec status
Record the task table and what was not verified.
