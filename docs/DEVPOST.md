# Devpost submission text

Paste each section into the matching field of the Kiroween 2026 form
(<https://kiroween.devpost.com/>). Deadline: Friday 4 Dec 2026, 2:00 PM Pacific Time.

> **TODO(user):** the "Inspiration" and "Accomplishments" sections are written in your voice.
> Read them and change anything that is not true for you. Everything factual comes from the
> repository.

## Project name

Kiroween Incident Crypt

## Tagline

A crew of read-only AI agents that finds what broke in your EKS cluster, remembers what broke
before, and proposes the fix as a pull request a human approves.

## Inspiration

On-call work has a slow part and a risky part. The slow part is finding out what is wrong: which
pod, which event, which policy, which limit. The risky part is changing production at night.
I wanted to see how much of the slow part an AI agent can take, with none of the risky part:
agents that can only read, and a person who has to say yes. It is a Halloween hackathon, so
the agents are monsters: a Necromancer, a Vampire, a Spider, a Zombie, a Ghost and an Archivist.

## What it does

A watcher notices broken workloads on Amazon EKS: out-of-memory kills, image pulls that fail,
volumes that will not bind, failing probes. The Necromancer (orchestrator) classifies each
incident and delegates it to a specialist:

| Agent | Handles |
|---|---|
| Vampire (`cpu-memory`) | OOM kills, throttling, limits |
| Spider (`network`) | NetworkPolicies, DNS, Services |
| Zombie (`deploy-rollout`) | image pulls, bad config, stuck rollouts |
| Ghost (`storage`) | PVCs, mounts, the EBS CSI driver |
| Archivist (`incident-memory`) | finds similar past postmortems, with no model call |

The specialist reads the cluster through read-only tools and returns a diagnosis, the
evidence (tool, field, value) and at most one proposed change. The UI shows the crew animating
while they work, the evidence, the similar past incidents and the proposal. A human clicks
**Approve**, and the orchestrator opens a real GitHub pull request with a Kustomize patch.
The approved incident is then written back as a postmortem and the knowledge base re-indexed,
so the next similar incident can find it.

Real results, saved in `docs/demo-results/`:
- A broken NetworkPolicy, twice. Both runs went from submission to approval in about 40
  seconds and opened pull requests
  [#2](https://github.com/romanceresnak/kiroween-incident-crypt/pull/2) and
  [#3](https://github.com/romanceresnak/kiroween-incident-crypt/pull/3). In the second run the
  postmortem learned from the first came back as the top similar incident (score 0.846,
  against 0.708 and 0.683 for the seeded ones).
- A real Grafana out-of-memory on the same cluster, diagnosed by the Vampire with 13 pieces of
  evidence.
- Cost per incident, from Bedrock token counts, for five real incidents: $0.1104, $0.0790,
  $0.1052, $0.0609 and $0.0508.

What it does not do: merging the pull request does not change the cluster yet. Nothing syncs
`gitops/remediations/`. The pull request is the audit trail of an approved change.

## How I built it

Every piece of work was a Kiro spec: requirements with numbered IDs in EARS style, a design,
and tasks with one commit each. There are eight specs, 84 tasks and 327 requirement IDs, and the
tests and commit messages cite them. Three steering files fix the rules that stay fixed: agents
are read-only, remediation is a pull request, AWS access uses EKS Pod Identity. I gave Kiro
stop conditions (an unapproved AWS permission, a Terraform plan that replaces or destroys
something, a step that fails twice, an ambiguous requirement) and let it run the rest. Five
Kiro hooks, written last, run the same checks as CI for the file you save. The whole project was
built in Kiro Crew (the desktop app), one session per spec. Three MCP servers (AWS
documentation, EKS, Terraform) are configured; the first session could not start them, so it
used web research. The full account, including what was not used (custom agents, property-based
testing, powers), is in
[`docs/KIRO-WRITEUP.md`](KIRO-WRITEUP.md).

The system itself:
- **Agents:** Python 3.12 with Strands Agents on Amazon Bedrock. Claude Haiku 4.5 classifies;
  Claude Sonnet 4.6 investigates.
- **Memory:** a Bedrock Knowledge Base on S3 Vectors with Titan Text Embeddings V2, seeded with
  eight postmortems and extended by approved incidents.
- **Platform:** Terraform (VPC, EKS 1.37 on Spot, ECR, Pod Identity roles, GitHub OIDC roles),
  Argo CD, a Helm chart, GitHub Actions that test, build and push images tagged with the commit.
- **Safety:** read-only RBAC, default-deny NetworkPolicies, hardened pods, one least-privilege
  IAM role per agent, proposals validated by code, and an approval step.
- **Observability:** token and cost metrics labelled by agent and incident in Prometheus, and a
  Grafana dashboard.
- **UI:** one page with six original SVG characters, per-character animation, a strict
  content-security policy, and 34 jsdom tests. A replay mode of the same code, with 22 more DOM
  tests, serves the public demo.

## Challenges

- **Mistakes only a real run shows.** CI failed because GitHub issues immutable OIDC subjects
  for newer repositories. A CI step exited with 254 because GitHub runs `bash -e`. The UI
  printed `[object HTMLDListElement],...` and had no Approve button. Retrieval returned one
  postmortem three times. Each now has a test, and the commit says how it was found
  ([`docs/KIRO-WRITEUP.md`](KIRO-WRITEUP.md#kiro-caught-it)).
- **Model access that looks fine and is not.** Newer Bedrock models were listed as active
  and authorised, and then refused the real call, so the models were chosen from real calls.
- **Staying read-only while still being useful.** The agents can only propose. Getting from an
  approved proposal to a change in the cluster needs the workload's manifests in Git, which
  the demo workloads do not have.
- **Time.** The watcher reports one incident per workload every 10 minutes, so a repeat demo
  needs a wait.

## Accomplishments

- The whole loop ran on a real cluster, twice: detection, diagnosis, evidence, similar
  incidents, approval, a real pull request, a learned postmortem, and the learned postmortem
  retrieved next time.
- The crew diagnosed a real out-of-memory in Grafana on its own cluster and matched the fix I
  had committed.
- More than 900 automated tests, many checked by deliberately breaking the code and watching the
  test fail.
- Security that is tested, not only described: a test fails on any write-style Kubernetes
  call, an IAM simulator script checks ten permission decisions, and the UI has a CSP.

## What is next

- Apply approved changes: put the workload manifests in Git under Argo CD, so a merged pull
  request is applied, and check that the patch renders with `kustomize build`.
- Check whether a learned fix worked before it is trusted, and tune the 0.5 similarity
  threshold with more incidents.
- Add login to the approval page, and persist incident state outside memory.
- Approve the image and storage proposals and check that they fix the cluster; both scenarios
  stopped at `awaiting_approval` and were left unapproved.

## Category

**Frankenstein**: "Stitch together a chimera of technologies into one app. Bring together
seemingly incompatible elements to build something unexpectedly powerful."

The parts that do not obviously belong together: an LLM that reasons, a Kubernetes API that must
never be written by it, a vector knowledge base that grows from approved incidents, GitOps
that turns approval into a pull request, per-agent IAM, a Prometheus cost meter for every
model call, and a haunted-crypt UI with animated monsters. The unexpected part is the loop
they make: break, diagnose, approve, learn, remember.

## Built with

Kiro (specs, steering, hooks, MCP) · Python 3.12 · Strands Agents · Amazon Bedrock (Claude
Haiku 4.5, Claude Sonnet 4.6, Titan Text Embeddings V2, Knowledge Bases) · Amazon S3 Vectors ·
Amazon EKS 1.37 · EKS Pod Identity · Amazon ECR · Terraform · Argo CD · Helm · GitHub Actions
(OIDC) · Prometheus · Grafana · FastAPI · JavaScript and SVG · pytest · ruff · shellcheck · jsdom

## How this maps to the judging criteria

| Criterion | Evidence |
|---|---|
| Potential impact | A real task (incident triage) with a safety design for teams that will not let an agent touch production; honest about being proven on a demo cluster, not in production. |
| Technological implementation | Eight specs, three steering files, five hooks, three MCP servers; a "Kiro caught it" table with ten real catches and their commits; mutation-checked tests. |
| Design | A complete flow in one page: crew, feed, evidence, similar incidents, proposal, decision, cost; six original SVG characters, per-character animation, reduced-motion support. |
| Innovation | Learned postmortems from approved incidents, found by the next similar incident; read-only agents with remediation as a pull request; cost per incident. |
| Presentation | A 2:50 script that shows the real run end to end, a blog post and a write-up. |

## Links

- Working app (replay): <https://romanceresnak.github.io/kiroween-incident-crypt/>
- Repository: <https://github.com/romanceresnak/kiroween-incident-crypt> (MIT, `.kiro/` committed)
- Kiro write-up: [`docs/KIRO-WRITEUP.md`](KIRO-WRITEUP.md)
- Demo script: [`docs/DEMO-SCRIPT.md`](DEMO-SCRIPT.md)

> **TODO(user):** the demo video URL (YouTube, public).
>
> **TODO(user):** the blog post URL and the social post URL, for the two bonus prizes.

## "URL to your working app"

**<https://romanceresnak.github.io/kiroween-incident-crypt/>**

The live system runs on an EKS cluster in the author's AWS account and has **no public URL by
design**: its UI has no login and can open pull requests, so it is only reachable through
`kubectl port-forward`. The URL above is a **replay**: the real UI code, run in a read-only mode
over the saved records of five real runs on EKS (`docs/demo-results/`). The crew animates through
each incident's stages, Approve and Reject are disabled with a link to the real pull request, a
banner says it is a replay, and the page sends no request except a GET of the saved files. It has
no secret and no AWS access.

For the instructions field, which only judges see: the replay needs no login. To run the real
system, follow the README quickstart (it needs an AWS account). The tests need no cluster
(`uv run --frozen pytest -q` in each `agents/<package>`).

> **TODO(user):** after you push, enable Pages once (Settings, Pages, Source: GitHub Actions) if
> it is not already on, open the URL, and check it loads before you submit.

## Prizes this submission could be considered for

- Best Frankenstein (the category), and the overall prizes.
- Bonus Blog Prize: needs the published blog URL (`docs/BLOG-DRAFT.md`).
- Social Blitz Prize: needs the published post URL (`docs/SOCIAL-POST.md`).
- Best Demo: no extra step.

Not claimed: Best Startup Project, Best Student Project and Best Ghost of Kiroween Past (it
needs a 2025 registration this repository cannot show). Best Kiro Crew App asks for "your
Kiro Crew app"; this project was built in sessions with Kiro Crew but does not run on it, so it
is not claimed. If you think it qualifies, add the repository URL and a description of the
features in the form.
