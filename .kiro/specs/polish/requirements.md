# polish — Requirements

> EARS format, as in `agents-core`. Earlier specs are cited as `core:`, `spx:`, `hard:` and
> `mui:` (memory-and-ui).

## Scope

Three improvements after the first live runs (17 Nov 2026).

| # | Item | State when this spec started |
|---|---|---|
| 1 | Character animation at `/ui` while an agent works | Already delivered and pushed: `b1e6824` (animation, 16 DOM tests) and `3105f04` (glow behind the artwork). This spec adds no code for it; REQ-ANI records the requirements and where they are met. |
| 2 | Retrieval quality: a live test scored the correct postmortem 0.71 and an unrelated one 0.70, so a score threshold cannot separate them | New |
| 3 | Self-learning memory: an approved incident becomes a postmortem in the Knowledge Base | New |

Facts relied on (read 18 Nov 2026):
- Knowledge Base `VIBAT27SDH`, one S3 data source over the whole bucket
  `kiroween-prod-postmortems-255834079310`; the seeded postmortems sit under `postmortems/`.
- `orchestrator-sa` has the Pod Identity role `kiroween-prod-orchestrator`
  (`infra/modules/pod_identity`) with Bedrock invoke only. `incident-memory-sa` has
  `bedrock:Retrieve`.
- Seeded sidecars carry `title`, `category` (`cpu_memory`, `network`, `deploy_rollout`,
  `storage`), `signal` and `fix`.
- The AWS session on the build machine had expired when this spec was written; live
  Retrieve calibration and the Terraform plan need `aws login` first.

Out of scope: any IAM change beyond REQ-IAM-01, other Terraform changes, a public ingress,
removing a learned postmortem (done by deleting the S3 object and re-ingesting).

## 1. UI animation (ANI) — delivered

**REQ-ANI-01** While an agent works on an incident (the existing `busy` class set by
`markBusy`), its character card shall animate; an idle card shall not.

**REQ-ANI-02** Per character: Spider dangles on a thin thread line; Ghost floats slowly
with a slight fade; Zombie bobs with a small side-to-side shuffle; Vampire, Necromancer and
Archivist bob with a soft glow pulse.

**REQ-ANI-03** The animation is CSS keyframes using only transform and opacity, 1.5 to
2.5 s, ease-in-out, infinite, toggled by a class.

**REQ-ANI-04** With `prefers-reduced-motion: reduce` nothing moves and the thread is
hidden; the glow stays.

**REQ-ANI-05** DOM tests cover the class being added and removed and the reduced-motion
behaviour.

Met by: `style.css` and `tools/ui-tests/motion.test.mjs` (28 DOM tests with `ui.test.mjs`).

## 2. Retrieval quality (RET)

**REQ-RET-01 (Event-driven)** When the orchestrator has classified an incident, it shall ask
incident-memory for similar incidents with that category. The lookup therefore runs after
classification, not beside it.

**REQ-RET-02 (Ubiquitous)** When a category is given, incident-memory shall return only
postmortems whose metadata `category` is that category or `general`. The filter is sent to
Retrieve and the returned metadata is checked again in code. A postmortem without a
`category` is excluded.

**REQ-RET-03 (Ubiquitous)** When the category is `unknown` or absent, no category filter
applies.

**REQ-RET-04 (Unwanted behaviour)** If Retrieve rejects the metadata filter, incident-memory
shall retry once without it, with a larger result pool, and filter in code. This is counted
in a metric and is not an error to the caller.

**REQ-RET-05 (Ubiquitous)** The minimum score shall be configurable (`MIN_SCORE`, chart
`incidentMemory.minScore`), validated to be between 0 and 1, with a default chosen from
measured scores. Hits dropped for category and hits dropped for score shall be counted
separately.

**REQ-RET-06 (Ubiquitous)** The category shall travel as an optional query parameter of
`POST /similar`, so an old incident-memory pod and a new orchestrator still work together.

## 3. Self-learning memory (LRN)

**REQ-LRN-01 (Event-driven)** When a human approves an incident that has a change proposal,
the orchestrator shall write `learned/<incident-id>.md` and
`learned/<incident-id>.md.metadata.json` to the postmortem bucket and then start an
ingestion job on the Knowledge Base's data source.

**REQ-LRN-02 (Ubiquitous)** The document has the format of `docs/postmortems`: a title and
the sections Summary, Impact, Root cause, Evidence, Fix, Prevention. The sidecar has `title`,
`category` (the classified category), `signal` (the incident reason), `fix` (at most 600
characters) and `source: learned`.

**REQ-LRN-03 (Ubiquitous)** Code builds the document from validated structured fields. Every
inserted value is collapsed to one line and length-bounded. The text says that the fix is an
approved proposal and has not been confirmed to work, and that impact and prevention were not
recorded.

**REQ-LRN-04 (Ubiquitous)** A rejection, and an incident without a change proposal, writes
nothing.

**REQ-LRN-05 (Unwanted behaviour)** If learning fails in any step (not configured, S3
error, ingestion error, 20 s timeout), the approval shall still succeed with its pull
request. The outcome is recorded on `approval.learning` and counted in
`orchestrator_learned_total{result}`.

**REQ-LRN-06 (Unwanted behaviour)** If an ingestion job is already running, the document
stays written and the outcome says `already_running`.

**REQ-LRN-07 (Ubiquitous)** Learning is off unless the bucket, the Knowledge Base ID and the
data source ID are all configured. Off is a recorded outcome, not an error.

**REQ-LRN-08 (Ubiquitous)** A similar incident carries `origin` (`seeded` or `learned`),
taken from the metadata `source` attribute, else from a `learned/` key. The UI shall mark a
learned one `source: learned` and show the learning outcome on an approved incident.

## IAM and chart (IAM)

**REQ-IAM-01 (Ubiquitous)** The orchestrator role shall get exactly two permissions and no
other role shall change: `s3:PutObject` on `<postmortem bucket>/learned/*` and
`bedrock:StartIngestionJob` on the Knowledge Base ARN.

**REQ-IAM-02 (Ubiquitous)** The change shall be planned before it is applied and applied only
if the plan contains IAM policy additions or in-place updates only, with no destroy and no
replace. The result shall be verified with the IAM policy simulator: the two permissions are
allowed, and reading, deleting, writing outside `learned/`, and ingesting into another
Knowledge Base are not.

**REQ-IAM-03 (Ubiquitous)** The chart shall pass the learning settings to the orchestrator as
environment variables and the minimum score to incident-memory.

## Security

Learned postmortems put text that came from the cluster (event messages, evidence values)
into the corpus the specialists later read as hints. That is a persistent prompt-injection
path. It is limited by: a human approves each incident first (REQ-LRN-01); the document is
built from bounded single-line fields (REQ-LRN-03); the specialists already treat similar
incidents as hints and never as evidence (`mui:REQ-MEM-07`); the role can only write under
`learned/` (REQ-IAM-01); and the UI shows where a hint came from (REQ-LRN-08).
