# polish — Design

## 1. Animation

Delivered; see `requirements.md`. Nothing to design.

## 2. Retrieval quality

### Order of work in the orchestrator

```
classify(incident)  ->  classification.category
similar(incident, category)   # was: asyncio.gather(classify, similar)
specialist(incident + similar_incidents)
```

The lookup waits for the category, which costs one Retrieve call (about a second) in a
pipeline whose model calls take tens of seconds. `IncidentService(similar=...)` now takes
`(incident, category)`. `MemoryClient.similar(incident, category)` posts to
`/similar?category=<c>` when the category is not `unknown`.

### incident-memory

`POST /similar?category=` (optional, validated against the incident categories).

```
allowed = {category, "general"}  if category in the four specialist categories else None
pool    = 10                      # candidates; top_k applies after filtering

Retrieve(filter = orAll[ equals(category, c), equals(category, "general") ])
  -> ValidationException on the filter -> retry unfiltered, pool = 25, count "filter_fallback"
then in code, always:
  drop hits whose metadata category is not in `allowed`   (counted: dropped{reason=category})
  drop hits below MIN_SCORE                                (counted: dropped{reason=score})
  sort by score, keep TOP_K
```

The code check does not depend on the KB supporting server-side filters on S3 Vectors; the
server-side filter only stops other-category hits from using up the candidate pool. Both
paths are tested with botocore's Stubber, which checks the request against the real service
model.

`MIN_SCORE`: parsed at startup, must be a number from 0 to 1, otherwise the pod fails to
start. The default is set after measuring (`tasks.md`, TASK-02). A score threshold cannot
tell apart 0.71 and 0.70; the category filter does that work. The threshold only removes
weak matches.

`SimilarIncident.origin`: `learned` when metadata `source` is `learned` or the S3 key starts
with `learned/`, else `seeded`.

## 3. Self-learning memory

### Flow

```
approve (service.decide)
  claim the decision, render the PR           (unchanged)
  learner.learn(record, approval)             bounded by 20 s, never raises
     render_postmortem -> markdown + sidecar
     s3.put_object  learned/<id>.md.metadata.json
     s3.put_object  learned/<id>.md
     bedrock-agent.start_ingestion_job(clientToken = uuid5(incident id))
  approval.learning = Learning(...)           shown in the UI
```

The sidecar is written first so that a job that starts between the two writes never indexes
a document without its metadata.

`Learning`: `status` (`written` | `disabled` | `error`), `key`, `ingestion` (`started` |
`already_running` | `failed` | `skipped`), `ingestion_job_id`, `error` (error class only,
at most 200 characters, never a message body).

`Approval.learning: Learning | None`.

### The document

```
# LRN-<incident id>: <reason> on <Kind> <name>

- **Date:** <approval date>
- **Source:** learned from an approved incident (not a reviewed postmortem)
- **Signal:** <reason>
- **Namespace / workload:** <ns> / <Kind> <name>, container <c>

## Summary      the diagnosis root cause; who approved, when; the incident message
## Impact       "Not recorded. ..."
## Root cause   the diagnosis root cause and confidence
## Evidence     one bullet per evidence entry (tool, field, value), at most 8
## Fix          "Approved change, not yet confirmed to work: ..." field, current, proposed,
               rationale, risk, and the pull request URL or "dry run"
## Prevention   "Not recorded. ..."
```

Every inserted value passes `clean(text, limit)`: control characters removed, whitespace and
newlines collapsed to single spaces, cut to the limit. No inserted value can start a new
heading or section.

Sidecar: `title` (at most 200), `category`, `signal`, `fix` (at most 600), `source: learned`.
The `fix` text starts with "Approved fix (not yet confirmed):" so a hint reader is not told
that it worked.

### Configuration

Environment of the orchestrator: `LEARNING_BUCKET`, `LEARNING_PREFIX` (default `learned/`),
`KNOWLEDGE_BASE_ID`, `KNOWLEDGE_BASE_DATA_SOURCE_ID`. All three of bucket, KB ID and data
source ID must be set or learning is `disabled`. The role may call `StartIngestionJob` but
not `ListDataSources`, so the data source ID is configuration (chart value
`orchestrator.learning.dataSourceId`).

### IAM (`infra/modules/pod_identity`)

New variables `postmortem_bucket_name` and `learning_agents` (default `["orchestrator"]`),
a policy document with the two statements, and `aws_iam_role_policy.learn` for each agent in
`learning_agents` (name `learn-postmortems`). The root module passes
`module.bedrock_kb.postmortem_bucket_name`. The bucket ARN is built from the name, so no
output is added and nothing outside IAM changes. The bucket uses S3-managed encryption
(AES256), so no KMS permission is needed. The KB's own role already reads `bucket/*`, which
includes `learned/`.

### UI

`similarView` shows a `source: learned` badge (highlighted) or `source: seeded` for each hit.
`approvalView` shows the learning outcome and ingestion state. Text only, as before.

## Decisions

- Category filtering is done twice, in the query and in code, because I could not confirm
  server-side filtering on S3 Vectors before writing this. If the live test shows it works,
  the code check stays as a guard.
- The learned postmortem records an approved proposal, not a verified outcome. The pull
  request may not have been merged and the fix may not have worked; saying otherwise would
  teach the specialists a claim nobody checked.
- Learning runs inside the approve request with a 20 s limit, not as a background task, so
  its outcome is on the approval the UI shows and tests stay deterministic.
- Learning does not roll back on failure: the approval is a human decision and the pull
  request already exists.
