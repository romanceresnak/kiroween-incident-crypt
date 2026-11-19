# How Kiro was used to build Kiroween Incident Crypt

Kiroween Incident Crypt is a crew of read-only AI agents for an EKS cluster. They detect
broken workloads, diagnose them, recall similar past incidents, and propose a fix as a pull
request that a human approves (see the [README](../README.md)). This document answers the
questions the Kiroween 2026 rules ask about how Kiro was used. Where Kiro did not do
something, it says so.

The repository's history runs from 17 Nov 2026 (first commit) to the time of writing,
18 Nov 2026. One author, Roman Ceresnak. `git log` is the evidence for everything below, and
`tools/tests/test_submission_docs.py` checks that the commits and counts cited here exist.

## At a glance

| Kiro feature | Used? | Where |
|---|---|---|
| Specs (requirements, design, tasks) | Yes, for all work | `.kiro/specs/`: [nine specs](#specs) |
| Steering docs | Yes, three files | `.kiro/steering/` |
| MCP servers | Configured: three | `.kiro/settings/mcp.json` |
| Agent hooks | Yes, five, added at the end | `.kiro/hooks/` |
| Kiro Crew (desktop app) | Yes, the whole project | see [Which Kiro](#which-kiro-ide-cli-web-crew) | see [Which Kiro](#which-kiro-ide-cli-web-crew) |
| Kiro CLI | Only to sign in | see [Which Kiro](#which-kiro-ide-cli-web-crew) |
| Kiro IDE, Kiro web app | No | |
| Custom agents | **No.** No custom agent files exist under `.kiro`. | |
| Property-based testing | **No.** No PBT library is a dependency. | see [Testing](#testing-without-property-based-tests) |
| Powers | **No.** | |

The agents inside the product are [Strands Agents](https://strandsagents.com) that run on
EKS. They are not Kiro custom agents.

## Which Kiro: IDE, CLI, Web, Crew

I built the whole project in Kiro Crew (the desktop app). Every spec ran as its own session,
with subagents and per-command approvals. I used `kiro-cli` only to sign in. I did not use the
IDE or the web app, so I cannot compare the four from experience. This is the author's account;
git does not record which app made a commit.

What the repository does show:
- `.kiro/settings/cli.json` (a file named for the Kiro CLI) enables `toolSearch`.
- The steering documents arrived through pull request #1 (`add-steering-docs`, commit
  `5b88fe5`) and the MCP servers through two commits named "Add Kiro MCP servers"
  (`2cdcbfd`, `51719df`).
- The specs and the code they drove were written and executed in Kiro Crew sessions, with
  file, shell, AWS CLI, kubectl and web tools. That is the author's account, not something the
  git history records.

## Specs

Every piece of work started as three files: `requirements.md` in EARS style with numbered
requirement IDs, `design.md`, and `tasks.md` with one commit per task. Each commit message
names the task, and tests name the requirement they check.

| Spec | Tasks | Requirement IDs | What it built |
|---|---:|---:|---|
| `infra` | 20 | 60 | VPC, EKS 1.37 on Spot, ECR, Knowledge Base on S3 Vectors, Pod Identity roles, GitHub OIDC roles, budget |
| `agents-core` | 16 | 97 | Shared library, orchestrator, cpu-memory specialist, Helm chart, CI |
| `specialists` | 12 | 37 | network, deploy-rollout and storage specialists, routing, watcher signals, chaos scenarios |
| `hardening` | 7 | 23 | Outcome field (`action_required`, `transient`, `no_action`), `Unhealthy` re-check, incident correlation |
| `memory-and-ui` | 10 | 25 | Seeded postmortems, incident memory, approval and real pull request, the UI with six characters, Grafana |
| `polish` | 8 | 23 | Per-character animation, category filter and score threshold, learned postmortems |
| `submission` | 7 | 28 | These hooks and the documents for the hackathon |
| `replay` | 4 | 34 | A public, read-only replay of the real UI on GitHub Pages |
| `scenario-lab` | 10 | 76 | Public HTTPS access with a login, and a Lab of 14 live failure scenarios run by a separate runner |

Counts come from the files (`## TASK-n` headings; distinct `REQ-…` identifiers in
`requirements.md`) and are asserted by a test.

**How the specs were used.** The agent was told where to stop: the execution notes in
`agents-core/tasks.md` list the stop conditions. Work stops for an unapproved AWS permission,
a Terraform plan that would replace or destroy something, a step that fails twice, or an
ambiguous requirement. Everything else proceeds. In practice this meant long runs of tasks
with a checkpoint wherever something touched the real account: `terraform apply` only after a
plan with creates and in-place changes only, as in `ce20d4f` ("0 to add, 3 to change, 0 to
destroy") and `1913353` ("0 to add, 1 to change, 0 to destroy").

**Compared with vibe coding.** The author did not run the same work both ways, so there is no
measured comparison. What the specs changed, concretely:
- Requirements were corrected before code existed. `1c80868` fixes the specs'
  `aws_s3vectors_bucket` to `aws_s3vectors_vector_bucket` (the provider's real name);
  `154a73d` and `0d7f81d` fix Helm v3 and Kubernetes provider v2 syntax in the specs.
- A written requirement is a thing to test. The learning-loop IAM policy has a script that
  asks the IAM policy simulator ten questions (`scripts/verify-learning-iam.sh`) and a test of
  that script with a fake `aws`.
- The specs recorded trade-offs where a reviewer can see them. `agents-core` states that
  delegating over HTTP costs a network hop but keeps each specialist's ServiceAccount
  separate, and that `incident_id` as a metric label has unbounded cardinality, "fine for a
  demo and a problem at volume".

## Steering

Three files in `.kiro/steering/`, written first:

| File | Lines | What it fixes |
|---|---:|---|
| `product.md` | 46 | The six agents, human-in-the-loop, the four demo scenarios, what is out of scope |
| `structure.md` | 33 | One package per agent, `shared/`, Terraform only in `infra/`, `gitops/` holds only manifests |
| `tech.md` | 45 | Strands on Bedrock, EKS Pod Identity, read-only agents, remediation as a pull request, cost metrics labelled by agent and incident, `eu-west-1` |

The rules that mattered most were the security ones in `tech.md`: agents are read-only,
remediation is a pull request, AWS access is Pod Identity with no static keys. They were not
restated per task; they showed up as design constraints. Two places cite `tech.md` by name
(`agents-core/requirements.md`): the choice to delegate over HTTP "breaks the per-agent
ServiceAccount separation in `tech.md`" if done in process, and the `incident_id` metric label
that `tech.md` requires. Most specs do not name the steering file, so the claim is only that
the constraints held, which tests check (no write verbs in RBAC, no write-style client calls,
`requires_approval` always true).

## MCP servers

`.kiro/settings/mcp.json` configures three servers, all started with `uvx`:

| Server | Package | Notes |
|---|---|---|
| `aws-docs` | `awslabs.aws-documentation-mcp-server` | AWS documentation |
| `eks` | `awslabs.eks-mcp-server` | pinned to `eu-west-1` |
| `terraform` | `awslabs.terraform-mcp-server` | provider documentation |

**What happened with them.** The three servers were configured, but the first session could not
start them: the config named the wrong `uvx` path. That session fell back to web research
instead, which fits `f21466e`, where a chart pin came from stale search results. The path was
then fixed. Whether and how the servers were used in later sessions is not recorded in the
repository, so this document does not claim it.

The config now names `/opt/homebrew/bin/uvx` as the command, so it works as is only on a Mac with
Homebrew; on another machine change the path.

The corrections in the history that documentation access would help with are the provider
and chart facts: the S3 Vectors resource name, the Helm and Kubernetes provider syntax and
the Argo CD chart pin (`f21466e`: "10.16.0 → 10.18.0", the first pin having come from stale
search results). The history does not say how each was found, and the first session ran without the servers,
so this is not a claim that MCP found them.

## Hooks

Five hooks in `.kiro/hooks/`, one file each, format `v1`, trigger `PostFileSave`. Each runs
`scripts/kiro-hook.sh <area> "{{filePath}}"`, which runs the commands CI runs for the area:

| Hook | Matches | Runs |
|---|---|---|
| `agents-ruff-pytest` | `agents/<package>/**` Python and `pyproject.toml` | `ruff check .` and `pytest -q` for that package |
| `infra-terraform-check` | `infra/**` `.tf`, `.tfvars` | `terraform fmt -check` and `terraform validate` |
| `charts-helm-lint-tests` | `gitops/charts/**` | `helm lint` and the chart tests |
| `chaos-shellcheck` | `chaos/**/*.sh` | `shellcheck` |
| `ui-dom-tests` | `tools/ui-tests/*`, `agents/orchestrator/**/ui/**` | `npm test` (jsdom) |

**Honest status.** The hooks were written at the end, in the `submission` spec. The
development of the project did not use them, and the checks they run were run by hand and in
CI instead (`.github/workflows/agents-images.yml`). What has been verified: the script, with
94 tests against fake tools, including paths with spaces, quoting, `..`, hostile names and
failures; and each of the five areas run once for real except `shellcheck`, which passed
through `uvx` instead. What has not: the hooks have never fired inside a Kiro app.
Kiro's documentation says `PostFileSave` fires when the *agent* saves or modifies a file;
whether it also fires on a save you make in an editor is not verified here. Whether Kiro
escapes `{{filePath}}` is undocumented, so the script accepts it raw, quoted or escaped.

The routing test found a real bug: `gitops/charts/agents/tests/test_chart.py` contains
`/agents/tests/`, so the agents hook would have fired on chart files. The matcher now
excludes chart paths.

To run one by hand: `scripts/kiro-hook.sh agents agents/orchestrator/src/…/service.py`.

## Testing without property-based tests

Property-based testing was not used. No test generates inputs from a property. What was used
instead, and caught defects:
- **Mutation checks.** After writing a test, the code under it was deliberately broken and
  the test had to fail. Examples from the history: `cb84a75` ("Disabling the grouping fails 7
  of them; keeping the first chunk instead of the best fails 1"), `7de072f` ("Run against the
  previous app.js all 11 fail"), and the hook script (a mutation survived because a fixture
  could not reach the check, so the fixture was fixed).
- **Fake tools.** The CI step that checks for an existing image is tested by running the
  workflow's own script under GitHub's exact shell with a fake `aws` (`313231b`).
- **Live probes** for facts a mock cannot give, such as the duplicate chunks below.

## Kiro caught it

Real problems from `git log`, in the order they happened. "Found by" says what exposed each
one; several were first introduced by an earlier Kiro draft, and are listed because the next
check caught them.

| # | Problem | Found by | Commit | What changed |
|---|---|---|---|---|
| 1 | The Terraform kept `bootstrap_cluster_creator_admin_permissions` on: EKS already creates an admin access entry for the cluster creator, so the explicit `aws_eks_access_entry` would fail with "already exists". | Reasoning before the first apply. `terraform plan` cannot detect it. | `8119229` | Set to `false` in `infra/modules/eks/main.tf`; access only through the explicit entries. |
| 2 | The S3 Vectors resource was named `aws_s3vectors_bucket` in the specs. | Writing the Terraform for it | `1c80868` | Specs corrected to `aws_s3vectors_vector_bucket`. |
| 3 | The network design had no egress rule to the EKS Pod Identity Agent (`169.254.170.23:80`). With default-deny, pods would have been blocked from obtaining AWS credentials. | Writing the chart's NetworkPolicies | `3f4ba06` | Rule added; "(now REQ-NET-09)". |
| 4 | Model availability: newer Bedrock profiles are `ACTIVE`, and `get-foundation-model-availability` says `AUTHORIZED`, but `Converse` fails with `AccessDeniedException`. | A real tool-use `Converse` call per profile | `368732b` | The two models used were chosen from real calls (Haiku 567 ms, Sonnet 772 ms) and the page says the API is "not a reliable signal". |
| 5 | CI failed with "Not authorized to perform sts:AssumeRoleWithWebIdentity". The repository issues **immutable OIDC subjects** (`repo:romanceresnak@25537084/…@1461265969:…`), so name-only trust policies never matched. | A failed CI run (44104222083) | `ce20d4f` | `gha_oidc_subject_prefix`, read from the GitHub API, with validation that rejects a prefix without numeric IDs. Plan: 0 to add, 3 to change, 0 to destroy. |
| 6 | The "image exists?" step aborted with exit 254 on every first push of a commit. GitHub runs steps as `bash -eo pipefail`, so a bare `out=$(aws ecr describe-images …)` ended the script before the not-found branch. | A failed CI run | `313231b` | The substitution moved into the `if`. Three tests run the real step under GitHub's exact shell; against the old script the not-found test reproduces exit 254. |
| 7 | The image push failed on `ecr:BatchGetImage`. buildx reads the target repository's manifest and layers while pushing. | A failed CI run | `1913353` | `BatchGetImage` and `GetDownloadUrlForLayer`, scoped to `kiroween/*`, checked with the IAM policy simulator (denied on other repositories). |
| 8 | Grafana was in `CrashLoopBackOff`. All three containers were OOM-killed: Grafana at its 256Mi limit, 19 s after start; both sidecars at 96Mi after about 65 s. Raising only Grafana would have left the pod at 1 of 3 ready. | The live cluster | `fdb0d1c` | Grafana 256Mi/512Mi, sidecars 192Mi; a test fixed to count the sidecar twice, and new tests on the rendered pod. |
| 9 | The UI showed `[object HTMLDListElement],[object HTMLHeadingElement],…` in three sections and had no Approve or Reject buttons. `replaceChildren` stringifies an array argument. | The live UI | `7de072f` | `nodes()` flattens and refuses to stringify; 11 jsdom tests, all failing on the old `app.js`; a CI step. |
| 10 | The category-filtered result for the netpol client contained one postmortem three times and another twice, because Retrieve returns chunks. | A live Retrieve probe | `cb84a75` | One hit per document, the best chunk; 13 tests. |
| 11 | The Lab page sent Reset and Log out as POSTs with an empty body and no `application/json` header. The server's same-origin check refuses that with 415, so both buttons would have failed on the live system. | DOM tests that record every request | `a9c4449` | Every POST is JSON; a test checks the header. |
| 12 | The sign-in redirect rule accepted `?next=/ui/../auth/logout`. It is not a cross-site redirect, but a crafted link could send a user to any path on the site. | A DOM test of the redirect rule | `a9c4449` | Segments cannot be `.`, `..`, empty or percent-encoded. |
| 13 | A prompt line meant to send a CPU-starved server to the Vampire changed nothing. Replaying the 14 recorded incidents through the real classifier three times each scored 36 of 42 under the old prompt, the new one and a third wording. | A measurement on Bedrock, not a unit test | `fbfee86` | The prompt line is reverted, and the measurement and the limit are written down. |

Smaller ones: `f21466e` (Argo CD chart pin 10.16.0 → 10.18.0), `154a73d` and `0d7f81d`
(Helm and provider syntax in the specs), `b8eb6ba` (the kube-prometheus-stack pin was
verified before use).

Items 5 to 10 were found by running the thing (a CI run, the live cluster, the live UI, a
live query) and item 4 by calling the API. Items 1 to 3 were found before the thing ran,
while writing it. Items 11 to 13 came from the Scenario Lab: two from DOM tests that record every
request, and one from measuring a change against the model instead of trusting it.

## The real Grafana OOM, diagnosed by the crew's own agent

On 17 Nov the Grafana pod of the monitoring stack was OOM-killed on this cluster (item 8).
It was submitted to the orchestrator by hand (`source: manual`), because the watcher does
not watch the `monitoring` namespace. The result is saved in
[`docs/demo-results/grafana-oom-real.json`](demo-results/grafana-oom-real.json). The fix in
`fdb0d1c` had been committed but not yet applied, so the live pod still showed the old
limits.

- The orchestrator classified it `cpu_memory` with high confidence; delegation `tool`.
- The cpu-memory agent returned 13 pieces of evidence, each with the tool, the field and the
  value. Among them: `lastState.reason` = `OOMKilled`, exit code 137, `restartCount` 5, the
  256Mi limit, **234900Ki (about 229 MiB) used right after a restart**, both sidecars also
  OOM-killed under 96Mi, and no ResourceQuota or LimitRange in the way.
- Its root cause: the 256Mi limit leaves about 26 MiB of headroom; the sidecars also die.
- Its proposal: `adjust_resources`, `resources.limits.memory` from `256Mi` to `512Mi` for
  the `grafana` container only, with the rationale that the request should rise to 256Mi and
  the sidecars to at least 128Mi.
- Outcome `action_required`, status `awaiting_approval`, about 21 seconds from submission.
- Cost: $0.1104 (Haiku $0.0046, Sonnet $0.1058).

The main change matches the fix committed earlier (`fdb0d1c`): Grafana 512Mi with a 256Mi
request. The agent had not seen that commit; its evidence is the live pod, which still had the
old limits. It proposed a single change and mentioned the sidecars outside the proposal; the
committed fix raised the sidecars to 192Mi.

## Pull requests #2 and #3: the loop closed, then it remembered

Incident `2e4fd60547e332f6` came from the `chaos/netpol` scenario. The Spider agent found
that the NetworkPolicy `allow-trusted` allowed `app: netpol-trusted` while the blocked client
was labelled `app: netpol-client`. The proposal was a change of
`spec.ingress[0].from[0].podSelector.matchLabels.app`.

1. Roman Ceresnak clicked Approve in the UI (the orchestrator log: "approved by Roman
   Ceresnak (github)"; the header of the file in that pull request says "Approved by Roman
   Ceresnak at 2026-11-18T07:53:38.681317+00:00").
2. The orchestrator opened
   [pull request #2](https://github.com/romanceresnak/kiroween-incident-crypt/pull/2): branch
   `remediation/2e4fd60547e332f6`, commit `0c4d20b` made through the GitHub API, adding
   [`gitops/remediations/chaos-netpol/networkpolicy-allow-trusted.yaml`](../gitops/remediations/chaos-netpol/networkpolicy-allow-trusted.yaml),
   a Kustomize JSON patch with a `test` and a `replace`.
3. The pull request was merged (`c2ba64d`, 18 Nov 08:55). Merging did not change the
   cluster: no Argo CD Application applies `gitops/remediations/`, the chaos workloads are
   not managed by Argo CD, and the patch was not run through `kustomize build`
   (`memory-and-ui/tasks.md` records this). The pull request is the audit trail of the
   approved change. Pull request #3 later wrote the same path (see "Which pull request is on
   `main`" below).
4. The orchestrator wrote `learned/2e4fd60547e332f6.md` and its metadata sidecar to the
   postmortem bucket and started a Knowledge Base ingestion job. The job completed: nine
   documents scanned, one new document indexed.

The learned document says "Approved change, not yet confirmed to work", because an approval
is not proof that the fix worked.

**Run 2, 14 minutes later.** The scenario was run again (incident `009de3634d72afbc`). The
Archivist returned three similar incidents. In run 1 it had returned two, both seeded:

| Run | Similar incidents returned (origin, score) |
|---|---|
| 1 (`2e4fd60547e332f6`) | seeded 0.708 (PM-2026-058), seeded 0.683 (PM-2026-066) |
| 2 (`009de3634d72afbc`) | **learned 0.846 (LRN-2e4fd60547e332f6)**, seeded 0.708, seeded 0.683 |

So the postmortem the crew wrote after run 1 was the top hit in run 2, above the seeded
postmortems. Run 2 was approved by the same person, opened
[pull request #3](https://github.com/romanceresnak/kiroween-incident-crypt/pull/3), wrote
`learned/009de3634d72afbc.md` and started ingestion job `013WGNC1LA`. Both records are saved:
[`netpol-first-run-real.json`](demo-results/netpol-first-run-real.json) and
[`netpol-second-run-real.json`](demo-results/netpol-second-run-real.json). The cost of the
three real incidents is $0.1104 (Grafana), $0.0790 (run 1) and $0.1052 (run 2). Two more
incidents were captured later the same day, from `chaos/badimage` ($0.0609) and `chaos/pvc`
($0.0508); both stopped at `awaiting_approval` and were not approved.

**Which pull request is on `main`.** Both runs proposed the same change to the same path, so
pull request #3 replaced the file that pull request #2 had added. Pull request #2 was merged at
08:55 (`c2ba64d`). Pull request #3 (commit `4fd7a65`, merged as `8cd8ee4` at 11:10, after #2)
changed only the two header lines of
[`networkpolicy-allow-trusted.yaml`](../gitops/remediations/chaos-netpol/networkpolicy-allow-trusted.yaml):
the incident (`009de3634d72afbc` instead of `2e4fd60547e332f6`) and the approval time
(2026-11-18T08:07:41.071878+00:00 instead of 07:53:38.681317+00:00). The patch itself, a `test`
of `netpol-trusted` and a `replace` with `netpol-client`, is identical in both. So the version on
`main` is pull request #3's, and the approval stamp in the file is run 2's. The history of the
file keeps both. Neither version has been applied to the cluster.

**Still not verified:** that the learned hit generalises. It was one observation, for the same
scenario both times, so it shows that retrieval works, not that a different incident would
find a useful postmortem. The 0.5 threshold is a first guess. Pull request #3 had not been
merged when this was written, and neither pull request was applied to the cluster.

## The Scenario Lab, measured

Before shipping the Lab page, all 14 scenarios were run on the real cluster with the same
renderer the runner uses ([`summary.json`](demo-results/lab/summary.json)). Each produced an
incident in its own namespace, in 3 to 86 seconds, and the investigation then took 11 to 26
seconds. The whole run cost $1.26. Nothing was approved, and the cluster was left clean.

- **Twelve behaved as described.** Seven ended with a fix to approve, five as a diagnosis, for
  example a `Secret` or `ConfigMap` that does not exist, which the crew cannot create.
- **Two were sent to the wrong specialist.** `cpu-throttling` (card: Vampire) and
  `rollout-stuck-readiness` (card: Zombie) both went to the Spider. It found the right causes,
  the 20m CPU limit and the `/ready` path that returns 404, and could not propose a resource or
  probe change, so both ended as a diagnosis.
- **A prompt change did not fix it.** Replaying the 14 incidents three times each through the
  real classifier gave 36 of 42 for the old prompt, my one-line change and a third wording
  ([`routing.txt`](demo-results/lab/routing.txt)). The classifier sees one event, and a failed
  health check looks the same whatever caused it. The lines were not kept. A real fix needs more
  input than the event text.
- **More input did fix lab-02, in a replay.** The watcher now reads the container's CPU limit and
  request from the pod and its CPU usage from metrics-server. The limit alone changed nothing (0 of
  3) and the data without the prompt rule changed nothing (0 of 3). With both, and the usage
  **assumed** at the 20m limit, lab-02 went to the Vampire 10 of 10 times and the NetworkPolicy,
  DNS, Service and port scenarios stayed with the Spider
  ([`routing-cpu-hint.txt`](demo-results/lab/routing-cpu-hint.txt)). Nobody has read
  metrics-server for a lab-02 pod yet, so this is not seen on the cluster. The stuck rollout is
  not fixed.

The runner's tests also hung once for 1 hour 50 minutes. The cause was a test, not the runner:
it called `reset()` from inside `apply()`, which in the test ran the cleanup inline and waited
on a lock it already held. The cleanup now runs after the apply, as it does in the service, and
test runs carry a hard timeout.

## What Kiro got wrong, and limits

- I added a prompt line meant to send a CPU-starved server to the Vampire and committed it
  with a note that its effect would be measured, not assumed. The measurement showed it
  changed nothing, and the commit that follows says so and reverts it.
- The first draft of the README said an approved change is "a merged pull request that Argo CD
  applies". The `memory-and-ui` spec says the opposite for remediation files, and the
  README was corrected after reading it.
- The first designs missed the Pod Identity egress rule (3), used the wrong provider resource
  name (2) and, on one occasion, a stale chart pin (`f21466e`).
- The UI shipped with a bug that showed only on the live page (9). The tests that existed
  checked the served files, not what `app.js` does; the fix added jsdom tests that run it.
- The retrieval result looked fine in unit tests with mocked responses and had duplicates on
  the real service (10).
- Open items, stated in the README: that the `badimage` and `pvc` proposals work (they were
  not approved); the untuned similarity threshold (0.5); model prices entered by
  hand; the hooks inside a Kiro app.
