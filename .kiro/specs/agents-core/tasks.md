# agents-core — Tasks

Ordered so each task builds on the last and can be tested on its own. Tasks 1 to 13 run
locally with no AWS calls and no cluster changes. Tasks 14 to 16 touch the live account,
the GitHub repository and the cluster, and need explicit approval (see Execution notes).
Requirement IDs refer to `requirements.md`; section numbers refer to `design.md`.

## Status (17 Nov 2026)

| Task | Commit | Result |
|---|---|---|
| TASK-01 Scaffold, pin SDK, contract tests | `1257a28` | 5 contract tests pass on strands-agents 1.65.0 |
| TASK-02 Models, config, context, server | `e98eaca` | 27 tests pass |
| TASK-03 Bedrock client and call guard | `84ae8f4` | 7 tests pass |
| TASK-04 Token and cost metrics | `fb12756` | 12 tests pass |
| TASK-05 Read-only Kubernetes tools | `db17627` | 22 tests pass, including the write-call guard |
| TASK-06 cpu-memory specialist | `b40f9a4` | 36 tests pass |
| TASK-07 Orchestrator service | `0654c14` | 19 tests pass |
| TASK-08 Event watcher | `5106e8f` | 17 tests pass |
| TASK-09 Container images | `ea279d0` | Not built: Docker daemon not running |
| TASK-10 Helm chart | `3f4ba06` | helm lint clean, 22 render tests pass |
| TASK-11 Argo CD Applications | `e71d5c1` | 4 tests pass, pinned metrics-server chart renders |
| TASK-12 Chaos scenario | `8b968ff` | shellcheck clean, 13 tests pass |
| TASK-13 Image workflow and tag updater | `cac5136` | actionlint clean, 27 tests pass |
| TASK-14 infra TASK-19 applied | `f10e321` | Plan 0/1/0, applied, live policy checked |
| TASK-15 First deployment | — | Owner pushes to `main` |
| TASK-16 End-to-end acceptance | — | Not started |

## Execution notes

- **Local and reversible (tasks 1 to 13):** write code, run unit tests, build images,
  render the chart. No push, no apply.
- **Needs approval (tasks 14 to 16):** applying `infra` TASK-19, setting a GitHub
  repository variable and pushing to `main`, and running the chaos scenario against the
  cluster. The last one calls Bedrock, so it costs a small amount per incident.
- **Stop and ask when:** a step needs an AWS permission, a Terraform change or a
  repository setting that is not already approved; a Terraform plan would destroy or
  replace anything; a deployed pod fails for a reason other than a task listed here.

---

## TASK-01 — Scaffold the packages and pin the SDK

**Implements:** REQ-PKG-01, REQ-PKG-02

Create `agents/shared`, `agents/orchestrator`, `agents/cpu_memory` with `pyproject.toml`
(Python 3.12), src layout, `uv` lock files, `ruff` and `pytest` config. The agents depend
on `shared` by path. Add one **contract test** per SDK assumption in design §15: a real
`strands.Agent` with a stub model, checking that a `@tool` registers,
`structured_output_model` returns a parsed object on `AgentResult.structured_output`, and
`metrics.accumulated_usage` has `inputTokens` and `outputTokens`.

**Test:** `uv sync` succeeds in each package; `ruff check` and `pytest` pass; the three
contract tests pass. If one fails, the design section that relied on it is corrected
before any later task starts.

## TASK-02 — Shared models, configuration, context, server helper

**Implements:** REQ-BED-02, REQ-BED-03, REQ-MET-04, REQ-MET-05, REQ-ORC-02, REQ-ORC-14

`models.py` (design §3), `config.py` with the `eu.` prefix check, `context.py`
(incident_id contextvar), `server.py` (`/healthz`, `/readyz`, JSON logging, metrics
server on 9090).

**Test:** unit tests for model validation, a model ID without `eu.` aborts startup, a proposal with
`requires_approval=False` cannot be constructed, `/metrics` is served only on 9090.

## TASK-03 — Bedrock client and call guard

**Implements:** REQ-BED-01, REQ-BED-04, REQ-BED-05, REQ-BED-06, REQ-BED-07

`bedrock.py`: `make_model`, retry config, `ModelUnavailable`, and the per-invocation call
counter hook.

**Test:** with a fake model, the 16th call raises `CallBudgetExceeded`;
`AccessDeniedException` becomes `ModelUnavailable` naming the model with no retry;
a throttling error is retried and then fails. No AWS call is made.

## TASK-04 — Token and cost metrics

**Implements:** REQ-MET-01, REQ-MET-02, REQ-MET-03, REQ-MET-04

`metrics.py`: counters, price table loader, `record_usage`.

**Test:** known usage produces the expected token and cost values with the right
labels; an unpriced model records tokens only and warns once; a call outside an
incident uses `incident_id="none"`.

## TASK-05 — Read-only Kubernetes access and tools

**Implements:** REQ-K8S-01 to REQ-K8S-06

`k8s/client.py`, `redact.py`, `tools.py` (design §4.3).

**Test:** the AST guard test fails when a `patch_*` call is added to the module (checked by
temporarily adding one); `get_pod` output never contains an env value or a Secret
reference value; oversized logs are truncated; 403, 404 and unavailable metrics return
structured errors and do not raise.

## TASK-06 — cpu-memory specialist

**Implements:** REQ-CPU-01 to REQ-CPU-11

`validate.py`, `agent.py`, `api.py`.

**Test:** table-driven tests for every row of the validation table in design §7, including
a proposal for a different workload, a `proposed` of 5x the current value, and a `current`
that differs from the pod spec; an insufficient-evidence case returns
`investigate_further` with `low` confidence; a log line containing "ignore previous
instructions" does not change the validated output.

## TASK-07 — Orchestrator service

**Implements:** REQ-ORC-01 to REQ-ORC-14

`store.py`, `service.py`, `api.py`, `agent.py`, `specialists.py`.

**Test:** 422 on invalid input with zero model calls; duplicate `incident_id` is not
processed twice; `cpu_memory` routes to the specialist; `network` ends as `no_specialist`;
a skipped tool call triggers the forced delegation; a delegation timeout gives `failed`;
a full queue gives 503; the 501st record evicts the oldest.

## TASK-08 — Event watcher

**Implements:** REQ-WAT-01 to REQ-WAT-09

`watcher/detect.py`, `dedupe.py`, `client.py`, `main.py`.

**Test:** an OOMKilled status makes one incident with a stable ID; the same termination
seen twice does not make a second; a restart within the cooldown is suppressed; an old
termination is ignored at startup; a ReplicaSet-owned pod resolves to its Deployment; a
closed stream reconnects; an unreachable orchestrator is retried and then dropped.

## TASK-09 — Container images

**Implements:** REQ-CI-07, REQ-CHT-06

`agents/orchestrator/Dockerfile` and `agents/cpu_memory/Dockerfile`: repository-root
context, slim Python 3.12 base, dependencies from the lock file, non-root user `10001`.

**Test:** `docker build --platform linux/amd64` succeeds for both; the container starts and
`/healthz` answers; `docker run --read-only` works with a `/tmp` mount; the image runs as
a non-root user. Skipped with a recorded reason if Docker is not available locally.

## TASK-10 — Helm chart with RBAC and network policies

**Implements:** REQ-CHT-01 to REQ-CHT-09, REQ-MET-06, REQ-NET-01 to REQ-NET-08

`gitops/charts/agents` (design §8, §9) and the chart tests.

**Test:** `helm lint` and `helm template` pass; the render test fails on any non-read
verb, wildcard or `secrets` rule; the network policy test fails on an unlisted ingress
source or a policy selecting pods outside `agents`; the ServiceAccount names equal the
Pod Identity ones from `infra`.

## TASK-11 — Argo CD Applications

**Implements:** REQ-CHT-10, REQ-MSV-01, REQ-MSV-02, REQ-MSV-04

`gitops/apps/agents.yaml` and `gitops/apps/metrics-server.yaml`.

**Test:** both validate with `kubectl apply --dry-run=client`; the metrics-server chart
version is the exact pin; the metrics-server chart renders (`helm template` with the
Application's values) without error.

## TASK-12 — Chaos scenario

**Implements:** REQ-CHO-01 to REQ-CHO-06

`chaos/oom/oom.sh` and `manifests.yaml`.

**Test:** `shellcheck` is clean; with the wrong kubectl context the script exits before
any change; `kubectl apply --dry-run=client` accepts the manifests; the memory limit is
64Mi.

## TASK-13 — Image workflow and tag updater

**Implements:** REQ-CI-01 to REQ-CI-16

`.github/workflows/agents-images.yml` and `tools/set_image_tag.py`.

**Test:** `actionlint` is clean; every action is pinned to a full SHA; the PR path has no
credentials and no push; the `update-chart` job has no AWS step; `set_image_tag.py` changes
only the two marked lines, is idempotent, and leaves the rest of `values.yaml` byte for
byte identical.

## TASK-14 — Apply infra TASK-19 (needs approval)

**Implements:** `infra` REQ-GHA-08 (amended)

Plan the `ecr:DescribeImages` change, show it, and apply it after approval.

**Test:** the plan is one in-place policy change; after apply, the simulator allows
`ecr:DescribeImages` on `kiroween/orchestrator` and still denies `CreateRepository`.

## TASK-15 — First deployment (needs approval)

**Implements:** REQ-CI-02, REQ-CI-05, REQ-CI-11, REQ-CHT-10, REQ-MSV-03, REQ-E2E-06

Set the repository variable `ECR_PUSH_ROLE_ARN`, push the branch to `main`, and watch the
workflow. Confirm the images, the tag commit, and the Argo CD rollout.

**Test:** both images exist in ECR tagged with the commit SHA; `values.yaml` carries that
SHA; the three Deployments run it; `kubectl top nodes` works; the APIService
`v1beta1.metrics.k8s.io` is `Available`; every agent pod is Ready under the network
policies. Re-running the workflow for the same commit succeeds without pushing.

## TASK-16 — End-to-end acceptance (needs approval)

**Implements:** REQ-E2E-01 to REQ-E2E-07

Run `chaos/oom up`, wait for the incident, read the record, check the metrics, query the
EKS audit log, run the network negative tests, then `chaos/oom down`.

**Test:** every requirement in section 13 of `requirements.md` passes, each with the
command and its output recorded.
