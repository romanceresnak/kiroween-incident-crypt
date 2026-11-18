# replay — Tasks

Local tasks only: no AWS, Terraform or cluster change, no push, and no change to the repository's
settings. Stop conditions as in `agents-core/tasks.md`. Commit after each task.

The build comes first so that every DOM test runs on the page the build assembles, not on a
second assembly written inside the tests.

## TASK-01 — The build
REQ-BD-01 to BD-06, REQ-RM-03, RM-04.
`tools/replay/build.py`, the manifest, the data copy, the insertions into the real `index.html`.
`replay.js` and `replay.css` start as one-line placeholders that TASK-02 replaces; the build copies
whatever is there.
**Test:** `tools/tests/test_replay_build.py`. Mutation-checked.

## TASK-02 — Replay mode in the UI, and the engine
REQ-RM-01, RM-02, RM-05 to RM-07, REQ-TL-01 to TL-06, REQ-DD-01 to DD-03.
`app.js` hooks, `tools/replay/replay.js`, `tools/replay/replay.css`.
**Test:** `tools/ui-tests/replay.test.mjs` on the built page, and the existing UI tests unchanged.
Mutation-checked.

## TASK-03 — The Pages workflow
REQ-DP-01 to DP-05. `.github/workflows/replay-pages.yml`; the SHAs resolved from the GitHub API.
**Test:** `tools/tests/test_replay_workflow.py`, and `actionlint` if it can be run.

## TASK-04 — Documents and status
REQ-DC-01 to DC-03. README link, Devpost working-app text, the spec counts in the write-up, blog
and Devpost text, and the status section.
**Test:** the document tests, including the new spec in the counts.
