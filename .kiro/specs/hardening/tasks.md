# hardening — Tasks

Local tasks only: no AWS change, no Terraform change, no push. Execution notes and stop
conditions are those of `agents-core/tasks.md`. Commit after each task.

## TASK-01 — Models and shared outcome logic
REQ-OUT-01 to OUT-05, REQ-WSU-06 (field), REQ-COR-04 (fields).
`models.py` changes; `outcome.py`; `run_investigation` calls `decide`; common prompt.
**Test:** the `Investigation` invariant; `decide` for each branch; evidence about another
object does not match; OOMKilled/CrashLoopBackOff are exempt; a read error is not healthy;
all existing shared tests pass.

## TASK-02 — Specialists use the outcome
REQ-OUT-06, REQ-REG-02.
cpu_memory calls `decide`; prompts for all four mention the outcomes.
**Test:** per package: model says `transient` → no proposal; live subject healthy →
`transient`; the netpol false diagnosis (REQ-REG-02); existing tests pass.

## TASK-03 — Watcher startup noise and depends_on
REQ-WSU-01 to WSU-06.
**Test:** Ready pod dropped; re-check timing (grace and delay); Ready by re-check dropped;
still not Ready submitted once; pod gone dropped; dropped signal not marked seen; one
pending per id; `depends_on` filled; existing watcher tests pass.

## TASK-04 — Orchestrator correlation
REQ-COR-01 to COR-06.
**Test:** each match rule; window boundary; closed primaries skipped; oldest wins;
correlated incident not classified; idempotent resubmission; `GET /incidents/<id>` shows
`related_incidents`/`related_to`; a `None` proposal ends `completed`; existing tests pass.

## TASK-05 — Chart
REQ-CHH-01. **Test:** render test for the three env vars; helm lint.

## TASK-06 — Regression tests for the 17 Nov cases
REQ-REG-01. **Test:** the three scenario tests.

## TASK-07 — Spec status
Record the task table and what was not verified.
