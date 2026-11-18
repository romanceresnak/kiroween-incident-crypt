# replay — Design

## 1. Layout

```
agents/orchestrator/src/kiroween_orchestrator/ui/
  app.js            + six small hooks, all inert unless window.KIROWEEN_REPLAY exists
tools/replay/
  replay.js         the replay engine, loaded before app.js
  replay.css        banner, replay bar, disabled-decision note
  build.py          writes the static site (stdlib only)
tools/ui-tests/replay.test.mjs      DOM tests of the built page
tools/tests/test_replay_build.py    build tests
tools/tests/test_replay_workflow.py workflow tests
.github/workflows/replay-pages.yml
```

`replay.js` and `replay.css` live outside the UI directory so they are not served by, or shipped
in, the orchestrator image. Only the six hooks in `app.js` are.

## 2. Data flow

```
docs/demo-results/*.json  --build.py-->  site/data/*.json + manifest.json
ui/{index.html,style.css,app.js,art/}  --build.py (inserts)-->  site/
page opens -> replay.js sets window.KIROWEEN_REPLAY, fetches data/manifest.json and the
              five records (GET, same origin) -> app.js starts, asks getJSON("/incidents?...")
              -> replay.js answers from memory with the record "as of" the playhead
```

`app.js` never learns that the data is not an API: it asks `getJSON` and renders what it gets.
That is what keeps the replay the real UI.

## 3. The hooks in `app.js`

| # | Where | Change |
|---|---|---|
| 1 | top | `const REPLAY = window.KIROWEEN_REPLAY \|\| null;` |
| 2 | `getJSON` | `if (REPLAY) return REPLAY.getJSON(path);` |
| 3 | `refreshFeed` | the indicator says "replay of saved records", or "replay data could not be loaded" on error |
| 4 | `markBusy` | in replay, the busy set comes from `REPLAY.busyAgents()` |
| 5 | `approvalView` | at `awaiting_approval`, in replay, return `replayDecision(record)` instead of the form |
| 6 | `select` and start-up | in replay, `select` also calls `REPLAY.start(id)`; with no hash, the first incident is selected once the data is ready; `REPLAY.onChange` refreshes the feed and detail |

`replayDecision` builds two disabled buttons, a note, and, when the record has a pull request, a
link through the existing `safeLink`. It touches no storage and registers no handler.

## 4. The engine (`replay.js`)

State: the records in manifest order, and a playhead `{id, stage, timer}`.

Stages and what each shows (`viewAt(record, stage)`, a pure function):

| Stage | `status` | `classification` | `similar_incidents` | `investigation`, `usage_by_agent` | `approval` | `updated_at` |
|---|---|---|---|---|---|---|
| `detected` | `accepted` | null | `[]` | null, `{}` | null | `created_at` |
| `classified` | `processing` | saved | `[]` | null, `{}` | null | `created_at` |
| `investigating` | `processing` | saved | saved | null, `{}` | null | `created_at` |
| `awaiting_approval` | `awaiting_approval` | saved | saved | saved | null | saved if not approved, else `created_at` |
| `approved` | saved | saved | saved | saved | saved | saved |

Incidents that are not playing show their last stage (`approved` if the record has an approval,
else `awaiting_approval`). Busy agents: `detected` and `classified` the `orchestrator`;
`investigating` the `incident-memory` and the specialist for the category; the last two stages
none.

Timing: `stageMs` defaults to 1500, 2000, 5000 and 6000 ms for the first four stages; the last
stage has no timer. `window.KIROWEEN_REPLAY_CONFIG` overrides it, and `autoplay: false` leaves
the playhead to `goto(n)`, which is what the tests use.

Recorded duration: for a record without an approval, `updated_at - created_at`; for an approved
one, `approval.at - created_at`. These are what the saved records can prove. The page prints
them next to the sentence "Stage boundaries are illustrative: the saved records keep only these
two timestamps."

The bar (`#replay-bar`) shows the story label, the stage list with `aria-current="step"`, the
recorded duration, the sentence above, and Restart and Skip buttons. Everything is set with
`textContent`.

## 5. The build (`build.py`)

1. Load the five records named in a table in the script (file, story label, order). Validate:
   readable JSON, required keys, `incident.incident_id` equals the manifest id, a known
   `classification.category`, and no credential-shaped text (AWS access key IDs, GitHub tokens,
   private-key headers).
2. Read the UI's `index.html` and insert, each at an anchor that must occur exactly once:
   the CSP meta after `<meta charset="utf-8">`; `replay.css` after `style.css`; the banner and
   the empty `#replay-bar` after `<body>`; `replay.js` before `app.js`; the title suffix
   "(replay)". Any missing or repeated anchor aborts.
3. Only then write: `index.html`, copies of `style.css`, `app.js`, `replay.js`, `replay.css`,
   `art/*.svg`, `data/<file>` (byte for byte) and `data/manifest.json`.

Failure writes nothing. The CSP meta is
`default-src 'none'; img-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self';
base-uri 'none'; form-action 'none'`.

## 6. The workflow

```
on: push to main (paths: the UI, tools/replay, docs/demo-results, the workflow) + workflow_dispatch
permissions: {}                      # nothing by default
jobs.build   permissions: contents: read
               checkout (persist-credentials: false) -> python3 tools/replay/build.py --out _site
               -> upload-pages-artifact (path: _site)
jobs.deploy  needs: build, permissions: pages: write, id-token: write
               environment: github-pages (url from the deployment output) -> deploy-pages
concurrency: group pages, cancel-in-progress: false
```

Pinned: `actions/checkout` (already used in this repository),
`actions/upload-pages-artifact` v5.0.0 and `actions/deploy-pages` v5.0.1, each by commit SHA
resolved from the GitHub API and checked against its `action.yml`. `configure-pages` is not used:
a static site with relative URLs does not need it.

The `agents-images` workflow also runs on these changes (`tools/**` and `agents/**`), so the
DOM and Python tests run in CI as before.

## 7. Tests

| File | Covers |
|---|---|
| `tools/ui-tests/replay.test.mjs` | the built page in jsdom, with a fake `fetch` that serves the built files and logs every call: no `/incidents`, no POST; banner and indicator; feed order; each stage's content and busy agents; disabled buttons and the PR links; restart and skip; hostile text; a failed load; the live UI unchanged |
| `tools/tests/test_replay_build.py` | the files built; data byte-equal to the records; validation failures write nothing; anchors; relative URLs; CSP; credential scan |
| `tools/tests/test_replay_workflow.py` | SHA pins, permissions, triggers, no secrets or AWS, environment, concurrency, no persisted credentials |

## Trade-offs

- **The playhead lives in the page, not in the data.** The records are real; the pacing is not.
  The page says so.
- **One shared `app.js`.** Six hooks cost a little readability in the live UI, and buy a replay
  that cannot drift from it.
- **No build step beyond Python's standard library.** No bundler, no npm install in the
  workflow, nothing to pin but three actions.
- **Static means stale.** A new real incident does not appear until its record is added to
  `docs/demo-results/` and the table in `build.py`.
