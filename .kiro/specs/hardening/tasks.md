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

## Status

All tasks done. Nothing pushed. Final run: shared 160, orchestrator 126, cpu_memory 40,
network 21, deploy_rollout 17, storage 21, repo-level (gitops, chaos, tools) 136; ruff
clean in every package; helm lint clean.

| Task | Commit | Result |
|---|---|---|
| Spec documents | `9af33ee` | — |
| TASK-01 Models and shared outcome logic | `1525a77` | shared 160 pass (+25) |
| TASK-02 Specialists use the outcome | `6b056c8` | cpu_memory 40, network 21, deploy_rollout 17, storage 21 |
| TASK-03 Watcher startup noise, depends_on | `6f54673` | orchestrator 102 pass (+14, 4 updated) |
| TASK-04 Orchestrator correlation | `8bf0d21` | orchestrator 121 pass (+19, 2 updated) |
| TASK-05 Chart | `d8267e8` | helm lint; repo-level 136 pass |
| TASK-06 Regression tests (17 Nov cases) | `131d4c3` | 5 pass; all 5 fail with both fixes off |
| TASK-07 Status | this commit | — |

Existing tests changed on purpose:
- Four watcher tests assumed `Unhealthy` is submitted at once; they now advance a clock and
  call `tick()`.
- The two queue-limit tests set `correlation_window_s=0`; their same-workload incidents are
  now correlated instead of queued.
- The undeployed-specialist chart test unsets three tags explicitly (CI wrote real tags in
  `dce39ff`).

Not verified:
- Nothing has run on the cluster. The images for this change are built by CI after push.
- Real Bedrock: whether the model picks `transient` on its own. The code checks (evidence
  contradiction, live re-read) hold either way, but only for the listed state signals.
- The `Incident` schema gained `depends_on` and the models reject unknown fields. During
  the rollout, a new watcher posting to an old orchestrator pod, or a new orchestrator
  calling an old specialist pod, gets HTTP 422 and that incident is lost. Run the chaos
  scenarios only after every agents pod runs the new tag.
- Correlation and pending re-checks are in memory. An orchestrator restart forgets open
  incidents; a watcher restart forgets pending re-checks (events younger than the 300 s
  startup grace are replayed by the re-list).
- `spec.storageClassName` is immutable, so the pvc fix still needs the PVC recreated.
