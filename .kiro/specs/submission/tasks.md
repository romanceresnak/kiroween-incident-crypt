# submission — Tasks

Local tasks only: no AWS, Terraform or cluster change, no push. Execution notes and stop
conditions are those of `agents-core/tasks.md`. Commit after each task.

## TASK-01 — Kiro hooks and the dispatcher script
REQ-HK-01 to HK-06.
**Test:** hook files (schema, matchers, no overlap); the script against fake tools for every
area and every path form; hostile and out-of-scope paths; failure propagation; shellcheck.

## TASK-02 — LICENSE and README
REQ-LIC-01, LIC-02, REQ-RD-01 to RD-03; the document checks for them.
**Test:** licence text and `.kiro` tracking; README sections in order; Mermaid well formed;
cost figures equal the JSON; every path and command target cited exists.

## TASK-03 — Kiro write-up
REQ-WRU-01 to WRU-05. **Test:** every cited commit exists; spec counts match the files;
every "Kiro caught it" entry has a commit; the not-used features are named.

## TASK-04 — Demo script
REQ-DEM-01 to DEM-04. **Test:** timings add up to at most 170 s; every command's script or
path exists.

## TASK-05 — Blog draft and social post
REQ-BLG-01, REQ-SOC-01. **Test:** hashtag first, at most 280 characters, Kiro named; the blog
states the hackathon.

## TASK-06 — Devpost text
REQ-DVP-01 to DVP-04. **Test:** sections, category, all five criteria, the no-public-URL
decision is stated.

## TASK-07 — Status
Record the task table, the operator's checklist and what was not verified.

---

## Status (18 Nov 2026)

All seven tasks are done and committed. Nothing is pushed: the author pushes.

| Task | Commit | Tests |
|---|---|---|
| Spec | `5963333` | the spec's own counts are asserted by the write-up test |
| TASK-01 hooks and script | `80dee29` | 94 in `tools/tests/test_kiro_hooks.py`; 6 script mutations caught |
| TASK-02 LICENSE and README | `6a55ffc` | README checks in `test_submission_docs.py`; 4 mutations caught |
| TASK-03 write-up | `8427b19` | hashes, counts, MCP, hooks, evidence; 6 mutations caught |
| TASK-04 demo script | `fe521a8` | timings, narration pace, IDs, labels, the one-liner; 4 mutations caught |
| TASK-05 blog and social post | `99cb3c0` | 6 mutations caught |
| TASK-06 Devpost | `d4b3573` | 5 mutations caught |
| TASK-07 status | this commit | placeholder inventory |

Corrections made while executing, each its own commit:

| Commit | What was wrong |
|---|---|
| `27ee2c3` | The README said an approved change is "a merged pull request that Argo CD applies". No Application syncs `gitops/remediations/`; merging records the change and does not apply it. |
| `d1d6b9a` | The README said a second learning run had not been recorded. An approved second incident (pull request #3) existed, and its record shows the learned postmortem returned first, at 0.846. Both records are now saved in `docs/demo-results/`. |
| `b9c9f50` | The commit-existence checks would have failed in CI, which checks out one commit. They skip on a shallow clone. |
| `5c27df3` | The write-up cited `.kiro/agents/`, an empty directory git does not track. Found by running the tests on a `--depth 1` clone. |

Found by the tests while writing: the agents hook also matched chart paths (fixed in
`80dee29`); a misquoted approval time in the write-up; a rehearsal one-liner that printed a raw
float; a 307-character social post; a blog with too few mentions of Kiro itself.

**Final verification, 18 Nov 2026.** Agent packages: shared 171, orchestrator 236, cpu_memory
41, network 21, deploy_rollout 17, storage 21, incident_memory 61 (568); repo-level
(`gitops chaos tools scripts`) 349; UI DOM 34. Total 951. ruff clean in every package and under
the strict config for `tools gitops chaos scripts`; `helm lint` clean; `terraform fmt -check`
clean and `validate` successful; shellcheck clean (run through `uvx --from shellcheck-py`,
version 0.11.0, because it is not installed here). The repo-level suite also passes on a
`git clone --depth 1`, as CI checks out (348 passed and the one history check skipped, before
this commit's two tests). `.kiro` has 31 tracked files and no ignore rule.

### Not verified

- The hooks have never fired inside a Kiro app. Whether `PostFileSave` fires on a save the user
  makes, and how Kiro escapes `{{filePath}}`, are undocumented or unchecked. The `chaos` hook
  was run for real only through `uvx`, not via the hook script, since `shellcheck` is not on
  `PATH`.
- Which Kiro apps were used, and which MCP tools the sessions called. Git cannot say.
- The Mermaid diagram renders in mermaid-cli 11 (a Chrome render, viewed), not in GitHub's
  renderer.
- The demo script has never been recorded or timed. Narration is checked at 2.8 words a second
  at most; the real pace is the narrator's. The UI animation and the Grafana dashboard were not
  viewed in a browser during this spec.
- A third run of the scenario: shot 5's learned hit is one observation, and the rehearsal gate
  exists for that reason.
- Pull request #3's state on GitHub (its branch exists on `origin`; `main` had not merged it).
- The full Official Rules page (`kiroween.devpost.com/rules`) was not read; only the overview
  page was. The form's field names may differ from the section names in `docs/DEVPOST.md`.
- That GitHub detects the licence and shows it in the About section (it should after a push).
- The quickstart and teardown were not run from scratch on a fresh account, and the README's
  `gh api` and `terraform output` commands were not executed.
- Model prices are hand-entered and unchecked against AWS; the cost is three incidents.
- The blog and social post state things about the author's own experience. They are drafts.

### What you still have to do

Placeholders to fill (7 placeholders, each a visible `TODO(user)` in these files; the two in
`docs/KIRO-WRITEUP.md` were filled on 18 Nov from the author's account):
`docs/DEMO-SCRIPT.md` (1: video
URL), `docs/SOCIAL-POST.md` (2: confirm the sentence is yours; the post URL), `docs/DEVPOST.md`
(4: your voice; video URL; blog and social URLs; the "working app" answer).

1. **Push to `main`.** Run a rebase pull first if the bot has committed a new image tag. Check CI
   is green, that the repository is public, and that the About box shows the MIT licence and
   `.kiro` is visible.
2. **Rehearse, then record the video** from `docs/DEMO-SCRIPT.md` (2:50, public on YouTube, no
   music). Run `chaos/netpol/netpol.sh down` afterwards.
3. **Publish the blog** (`docs/BLOG-DRAFT.md`) on a third-party platform, after reading it as
   yourself. It already says it is part of the Kiroween 2026 hackathon.
4. **Post on social** (`docs/SOCIAL-POST.md`), beginning `#KiroweenHackathon26`.
5. **Decide the "URL to your working app" answer** (`docs/DEVPOST.md`, last section).
6. **Read the Official Rules**, then **submit on Devpost** before Friday 4 Dec 2026,
   2:00 PM PT: category Frankenstein, the text from `docs/DEVPOST.md`, the repository URL, the
   video, the blog and social URLs, and the instructions for judges.
7. Afterwards: decide about pull request #3, and about tearing the cluster down (the budget
   alert defaults to 120 USD a month).
