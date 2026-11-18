# scenario-lab — Tasks

Execution notes and stop conditions are those of `agents-core/tasks.md`: stop for an unapproved
AWS permission, a Terraform plan that replaces or destroys, a step that fails twice, or an
ambiguous requirement. Pre-approved: Terraform plans with only creates and in-place updates.
Commit after each task; do not push; do not commit `Claude outputs/`. Normal tests only.

## TASK-01 — Terraform: public access, controller role, budget
REQ-PA-01 to PA-07, REQ-LC-02, REQ-TF-01 to TF-03. New modules `public_access` and
`lb_controller`; VPC and EKS outputs; `us-east-1` provider alias; budget default 400.
Then `terraform plan`, check it, apply it, and record the summary.
**Test:** `terraform fmt -check`, `validate`, a Python test of the module text (listener port,
SG rules, WAF rules, policy actions), and the plan summary.

## TASK-02 — GitOps: controller, binding, ALB policy
REQ-LC-01, LC-03 to LC-06. Argo Application for the controller; agents Application sync option;
chart `publicAccess.yaml`, values from the Terraform outputs.
**Test:** `gitops/tests/test_public_access.py`, `helm lint`.

## TASK-03 — Orchestrator login
REQ-AU-01 to AU-13. `auth.py`, login page, middleware, same-origin fix, watcher token, chart env.
**Test:** `agents/orchestrator/tests/test_auth.py`, the existing orchestrator tests, a chart test.

## TASK-04 — Scenario catalog
REQ-SC-01 to SC-08. `lab/scenarios.py`, `render.py`, `catalog.py`.
**Test:** `test_lab_scenarios.py`.

## TASK-05 — Runner, limits, Lab API
REQ-RN-04 to RN-07, REQ-LM-01 to LM-06, REQ-AP-01 to AP-05. `limits.py`, `runner.py`, `kube.py`,
`server.py`, `client.py`, routes in the orchestrator.
**Test:** `test_lab_runner.py`, `test_lab_kube_guard.py`, `test_lab_api.py`.

## TASK-06 — Chart: namespaces, RBAC, runner
REQ-RN-01 to RN-03, RN-08, REQ-WT-02. `lab.yaml`, values, NetworkPolicies, the orchestrator and
runner env.
**Test:** `gitops/tests/test_lab_chart.py`.

## TASK-07 — Watcher, routing, sandbox approvals
REQ-WT-01, WT-03, REQ-SB-01 to SB-05.
**Test:** `test_watcher_lab.py`, `test_lab_approvals.py`, the existing routing tests.

## TASK-08 — UI
REQ-UI-01 to UI-06, REQ-AU-11, AU-12. `lab.html`, `lab.js`, nav, logout, the login page's script.
**Test:** `tools/ui-tests/lab.test.mjs`, `login.test.mjs`, the replay build and DOM tests.

## TASK-09 — Run the 14 scenarios on the cluster
REQ-TS-02. A harness renders each scenario with the real code and applies it with `kubectl`
(the runner is not deployed before the author pushes), records the signals and incidents, and
removes everything. Fix what does not behave; list what is left.
**Test:** the results saved in `docs/demo-results/lab/`, and a test that the file lists 14.

## TASK-10 — Documents and status
REQ-DC-01 to DC-04, REQ-PA-07. Devpost, README, What's next, the spec counts, the status.
**Test:** the document tests.

---

## Status, 18 Nov 2026

All ten tasks are committed locally. **Nothing is pushed**, so no image contains the lab code yet.

| Task | Commit | Tests |
|---|---|---|
| Spec | `4720466` | the spec tests |
| TASK-01 Terraform: CloudFront, VPC origin, internal ALB, WAF, controller role, budget 400 | `6e5cb1c` | 17, and **applied**: 13 added, 1 changed (budget 120 to 400), 0 destroyed |
| TASK-02 GitOps: controller app, `TargetGroupBinding`, ALB `NetworkPolicy` | `e0d5949` | `test_public_access.py` and 2 rewritten chart tests |
| TASK-03 Login | `41e47d9` | `test_auth.py`, 60 |
| TASK-04 The 14 scenarios and the renderer | `a1be313` | 131 |
| TASK-05 Runner and Lab API | `cf8a696` | 34 runner, 9 kube guard, 24 API |
| TASK-06 Chart: namespaces, quota, runner, RBAC | `16a20ec` | `test_lab_chart.py`, 21, and 9 chart tests updated |
| TASK-07 Sandbox PRs, no lab learning, `Unhealthy` may be `cpu_memory` | `a072dce` | 48 approvals, 37 watcher |
| TASK-08 The Lab page, nav, log out, safer redirect | `a9c4449` | 20 lab and 11 login jsdom tests, 5 UI |
| TASK-09 The live run | `fbfee86` | 17 harness, about 20 pinning the saved results |
| Replay banner follows the login | `bec8a38` | 41 and 22 |
| TASK-10 Documents | `98d90d9` | 67 document tests, 5 of them new |

### Corrections found on the way

- **A test hung for 1 h 50 min.** It called `reset()` inside `apply()`, so the cleanup ran inline
  and waited on a lock the test already held. The runner was right. The test now defers the
  cleanup, and every test run has a hard timeout.
- **Reset and Log out would have returned 415.** They posted no JSON header. A DOM test that
  records requests found it (`a9c4449`).
- **The sign-in redirect accepted `/ui/../auth/logout`.** Segments `.`, `..`, empty and
  percent-encoded are refused now.
- **`$` in a regular expression accepts a trailing newline.** A test found it; `fullmatch` is used.
- **A classifier prompt line did not work.** It was meant to send a CPU-starved server to the
  Vampire. Three wordings scored 36 of 42 each on the real classifier, so it was reverted
  (`fbfee86`, `routing.txt`).
- **The Terraform plan also wanted to update the Pod Identity add-on** from `v1.4.1-eksbuild.3`
  to `v1.5.0-eksbuild.3` (it uses `most_recent`). The apply was limited with `-target` to the new
  modules and the budget, so the add-on was **not** changed.

### Not verified

- The runner, the login, the Lab API and the Lab page have never run on the cluster.
- CloudFront to ALB to pod, end to end: the `TargetGroupBinding` and the controller have never run,
  and the controller policy is a minimal subset of the official one.
- That the WAF common rule set lets the UI's JSON requests through.
- The Lab and login pages were never viewed in a browser.
- The harness did not use the runner or Argo CD. The 14 lab namespaces, quotas and limit ranges
  already exist on the cluster, created by the harness, so the first sync may need adoption.
- 2 of 14 scenarios go to the wrong specialist.
- The 15-minute cleanup and the hourly limit were tested with fakes only.
- Timing under load, quota behaviour with two quick runs, and the GitHub token's permission to
  create `lab-remediations`.
- Cost per incident uses prices typed in by hand. `awscrt` was added for one measurement only and
  is not a project dependency.
