# replay — Requirements

> EARS format, as in the earlier specs. Citations: `mui:` is `memory-and-ui`, `pol:` is
> `polish`, `sub:` is `submission`.

## Scope

The Devpost form asks for a URL to the working app. The live system has none by design
(`mui:REQ-UI-05`: the UI can open pull requests and has no login, so it is reachable only
through `kubectl port-forward`). `sub` left that as a decision. This spec takes option 2: a
public, read-only **replay** of the real UI, built from the real incident records, with no
connection to the cluster.

In scope:
- A replay mode of the existing `/ui`, so the replay is the real UI code and not a copy.
- The saved records in `docs/demo-results/` as its only data.
- A static build, and a GitHub Pages deployment from a GitHub Actions workflow.
- DOM tests, build tests and workflow tests.
- The `README.md` and `docs/DEVPOST.md` text that links it.

Out of scope: any change to the live behaviour, the cluster, AWS or Terraform; enabling Pages in
the repository settings (a manual step); a way to make the replay write anything.

The five records (all real, all saved as returned by the orchestrator):

| File | What it is |
|---|---|
| `grafana-oom-real.json` | Grafana out of memory, submitted by hand, `cpu-memory` |
| `netpol-first-run-real.json` | NetworkPolicy, run 1, approved, pull request #2 |
| `netpol-second-run-real.json` | NetworkPolicy, run 2, learned postmortem found, pull request #3 |
| `badimage-real.json` | Bad image tag, `deploy-rollout`, left unapproved |
| `pvc-real.json` | Missing StorageClass, `storage`, left unapproved |

## A. Replay mode (RM)

**REQ-RM-01 (State-driven)** While `window.KIROWEEN_REPLAY` is defined when `app.js` starts, the
UI shall take every incident from the replay data source, shall not request `/incidents`, and
shall send no request other than a GET of a file under `data/`.

**REQ-RM-02 (Ubiquitous)** Without `window.KIROWEEN_REPLAY`, `app.js` shall behave exactly as
before: the existing DOM tests pass unchanged.

**REQ-RM-03 (Ubiquitous)** The replay shall use the repository's `index.html`, `style.css`,
`app.js` and `art/` as they are, with only additions made at build time. There shall be no
second copy of the rendering code.

**REQ-RM-04 (Ubiquitous)** The feed shall list the five records in a fixed order, newest
story first, with each record's real timestamps.

**REQ-RM-05 (Ubiquitous)** A banner at the top of the page shall read, exactly: "Replay of real
runs on Amazon EKS -- the live system sits behind a login". It shall not be
dismissable. (Changed on 18 Nov by `scenario-lab`: it first read "has no public endpoint by
design", which stopped being true when the live system got a public address with a login.)

**REQ-RM-06 (Ubiquitous)** The header indicator shall say it is a replay, not "live".

**REQ-RM-07 (Unwanted behaviour)** If the data cannot be loaded, the page shall say so in the
indicator and shall not fall back to the live API.

## B. Timeline (TL)

**REQ-TL-01 (Event-driven)** When an incident is selected, the replay shall play its stages in
order: `detected`, `classified`, `investigating`, `awaiting_approval`, and then `approved` only
for a record that has a recorded approval. The first incident plays when the page opens.

**REQ-TL-02 (Ubiquitous)** Each stage shall show only what the record had by then:

| Stage | Status shown | Shown |
|---|---|---|
| `detected` | `accepted` | the incident only |
| `classified` | `processing` | and the classification |
| `investigating` | `processing` | and the similar past incidents |
| `awaiting_approval` | `awaiting_approval` | and the investigation, evidence, proposal and cost; no approval |
| `approved` | the recorded status | the record as saved, with the approval, the pull request and the learned postmortem |

**REQ-TL-03 (State-driven)** While a stage plays, the crew cards that did the work shall be
animated and no others: `detected` and `classified` the Necromancer; `investigating` the
Archivist and the incident's specialist; `awaiting_approval` and `approved` none.

**REQ-TL-04 (Ubiquitous)** The page shall say that the stage boundaries are illustrative,
because the saved records keep only the creation and last-update times, and shall show the
recorded duration: for a record without an approval, creation to last update; for an approved
record, creation to approval.

**REQ-TL-05 (Event-driven)** A "Restart" control shall replay the selected incident from
`detected`, and a "Skip to the end" control shall show its last stage. Selecting another
incident shall restart the replay for that one.

**REQ-TL-06 (Ubiquitous)** Stage lengths shall be configurable, so that tests do not wait.

## C. Disabled decisions (DD)

**REQ-DD-01 (State-driven)** While an incident is at `awaiting_approval`, the Approve and Reject
buttons shall be present and disabled, shall have no handler, and shall send nothing.

**REQ-DD-02 (Ubiquitous)** Next to them, a note shall say the replay cannot approve. For a
record with a recorded pull request, the note shall link to that real pull request (for the
second NetworkPolicy run, <https://github.com/romanceresnak/kiroween-incident-crypt/pull/3>); for
a record without one, it shall say the incident was left unapproved on the real cluster.

**REQ-DD-03 (Ubiquitous)** Links shall be made only to `https://github.com/` addresses, with
`rel="noopener noreferrer"`, as the live UI does.

## D. Build and data (BD)

**REQ-BD-01 (Ubiquitous)** `tools/replay/build.py` (standard library only) shall write a static
site to a directory: `index.html`, `style.css`, `app.js`, `replay.js`, `replay.css`, `art/`,
`data/` with the five records and `manifest.json`. (No `.nojekyll`: an Actions deployment does
not run Jekyll, and the upload action drops dotfiles by default.)

**REQ-BD-02 (Ubiquitous)** The data files shall be byte-for-byte the files in
`docs/demo-results/`.

**REQ-BD-03 (Unwanted behaviour)** If a record is missing, is not valid JSON, lacks a required
key or has a mismatching `incident_id`, or an anchor the build inserts at cannot be found in
`index.html`, the build shall exit non-zero, say why, and write nothing.

**REQ-BD-04 (Ubiquitous)** Every URL in the built page shall be relative, so the site works under
`/<repository>/` on GitHub Pages. The built page shall name no external origin.

**REQ-BD-05 (Ubiquitous)** The built `index.html` shall carry a Content-Security-Policy meta tag
that allows only same-origin scripts, styles, images and connections.

**REQ-BD-06 (Unwanted behaviour)** The build shall refuse to publish a record that contains
something that looks like a credential: an AWS access key ID, a GitHub token, or a private key.

## E. Deployment (DP)

**REQ-DP-01 (Event-driven)** A workflow `.github/workflows/replay-pages.yml` shall build and
deploy the site when the UI, the replay tooling, the records or the workflow change on `main`,
and on manual dispatch. It shall not run on pull requests, and shall not use
`pull_request_target`.

**REQ-DP-02 (Ubiquitous)** Every action shall be pinned to a full commit SHA with its version in
a comment. The workflow's default permissions shall be empty; the build job shall have
`contents: read`; the deploy job shall have `pages: write` and `id-token: write` and nothing else.

**REQ-DP-03 (Ubiquitous)** The workflow shall use no secret, no repository variable and no AWS
credential, and shall call no AWS action.

**REQ-DP-04 (Ubiquitous)** The deploy job shall use the `github-pages` environment, and runs
shall queue and never cancel each other.

**REQ-DP-05 (Ubiquitous)** Checkouts shall not persist credentials.

## F. Tests (TS)

**REQ-TS-01** DOM tests (jsdom) shall run the built page and cover REQ-RM-01, RM-05, RM-06,
RM-07, REQ-TL-01 to TL-05, REQ-DD-01 to DD-03, text safety, and that the live mode is unchanged.

**REQ-TS-02** Python tests shall cover the build (REQ-BD-01 to BD-06) and the workflow
(REQ-DP-01 to DP-05).

**REQ-TS-03** Each test shall be checked by deliberately breaking the code it covers.

## G. Documents (DC)

**REQ-DC-01** `README.md` shall have a "Live demo (replay)" link to the Pages address, saying it is
a replay of saved records and that the live system sits behind a login.

**REQ-DC-02** `docs/DEVPOST.md` shall give the Pages address as the working-app URL, say what it
is, and drop the open decision about the working-app URL.

**REQ-DC-03** The write-up, blog and Devpost text shall count the new spec.

## Decisions

- **The replay is the real UI.** A replay mode inside `app.js`, not a rewritten page, so a fix to
  the UI is a fix to the replay, and what judges see is the code that ran on the cluster.
- **Stage timing is illustrative, and says so.** The records keep two timestamps, not a
  timeline. The replay shows the stages in the order the orchestrator runs them and prints the
  recorded total beside them, instead of inventing per-stage times.
- **No new way to write.** The replay has no POST, no form and no storage. Approve and Reject
  exist so a viewer sees the real screen, and are disabled.
- **The records are published as saved.** They contain pod names, the approver's name and S3
  addresses with the AWS account ID, all already public in the repository. They contain no
  credential, and the build checks for one.
- **Enabling Pages is the operator's step.** It is a repository setting, which this spec does
  not change. The Pages address
  (<https://romanceresnak.github.io/kiroween-incident-crypt/>) follows GitHub's rule for a
  project site and is not verified until the first deployment succeeds.
