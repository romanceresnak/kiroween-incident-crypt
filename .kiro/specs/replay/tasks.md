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

---

## Status (18 Nov 2026)

All four tasks are done and committed. Nothing is pushed by the agent.

| Task | Commit | Tests |
|---|---|---|
| Spec | `842eeca` | none (documents) |
| TASK-01 build | `342ae94` | 41 in `tools/tests/test_replay_build.py`; 7 mutations caught |
| TASK-02 replay mode and engine | `357b5f2` | 22 DOM tests in `tools/ui-tests/replay.test.mjs`; the 34 live UI tests unchanged |
| TASK-03 Pages workflow | `ecaeda9` | 12 in `tools/tests/test_replay_workflow.py`; `actionlint` 1.7.12 clean |
| TASK-04 documents | this commit | 6 new document tests |

Before the spec, the real records were captured (`d44a0e2`) and the author's placeholders in the
write-up were filled (`41b6b7c`).

**Mutation checks of TASK-02 were stopped part way** at the author's request. Fourteen mutations
of `replay.js` and `app.js` were planned and none of their results were read. The interruption left
one mutation (`limit=51`) in `app.js`. It was found by diffing against the backup taken before the
run, restored, and the whole `app.js` diff against `HEAD` was read: it holds only the six hooks.
The engine's DOM tests therefore pass, but how well they catch a broken engine is not measured.
TASK-03 and TASK-04 were tested without mutation checks.

**Final verification.** Agent packages 568 (shared 171, orchestrator 236, cpu_memory 41, network 21,
deploy_rollout 17, storage 21, incident_memory 61); repo-level 411; UI DOM 56 (34 live, 22 replay).
Total 1035. ruff clean in every package and under the strict config for `tools gitops chaos
scripts`; `helm lint`, `terraform fmt -check` and `validate`, shellcheck and actionlint clean.

### Facts checked against GitHub (read-only)

The repository is public, its licence is detected as MIT, and GitHub Pages is enabled with
`build_type: workflow` and the address `https://romanceresnak.github.io/kiroween-incident-crypt/`.
The action SHAs were resolved from the API and their `action.yml` inputs read at those SHAs.

### Not verified

- **The replay has never been viewed in a browser.** The Playwright tools were not available in
  this session. The DOM tests run the real built page in jsdom, which checks structure, text and
  behaviour, and cannot show layout, the banner's look, the animations or the narrow screen.
- **The deployment has never run.** The workflow ran on no push yet. Until it succeeds, the Pages
  address serves nothing from this workflow, and the first run may fail on a setting that cannot be
  seen from here.
- The stage lengths (1.5, 2, 5 and 6 seconds) were chosen, not tuned.
- Mutation checks of the engine (see above).
- That an earlier Pages site is not already published from another source. The API reports
  `build_type: workflow` and no status.

### What you still have to do

1. Push, and watch the `replay-pages` run (Actions tab). If Pages is not already enabled for
   Actions it can fail once; enable it in Settings, Pages, Source: GitHub Actions, and re-run.
2. Open <https://romanceresnak.github.io/kiroween-incident-crypt/> and look at it: the banner,
   the animation of the crew, the disabled buttons, the link to pull request #3, and a phone width.
3. Do not commit the untracked `Claude outputs/` folder.
4. The earlier checklist (video, blog, social post, Devpost) is in
   `.kiro/specs/submission/tasks.md`; its placeholder count is now 7.
