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
