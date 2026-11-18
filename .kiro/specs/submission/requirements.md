# submission — Requirements

> EARS format, as in `agents-core`. Earlier specs are cited as `core:`, `spx:`, `hard:`,
> `mui:` and `pol:`.

## Scope

Everything the Kiroween 2026 hackathon asks for besides the running system: the Kiro hooks,
the README and licence, the write-up on how Kiro was used, the demo script, the blog post
and social post, and the Devpost text. No cluster, AWS or Terraform change.

Sources, read on 18 Nov 2026:
- The official page <https://kiroween.devpost.com/> (deadline 4 Dec 2026, 2:00 PM PT; only
  projects started on or after 30 Oct 2026 are eligible; this repository's first commit is
  17 Nov 2026).
- <https://kiro.dev/docs/hooks/> for the hook schema (IDE 1.0 and CLI V3).
- The repository itself: `git log` (history as of the merge of pull request #2),
  `.kiro/specs/*/tasks.md`, `docs/demo-results/grafana-oom-real.json`, and the cluster's own
  logs and S3 objects for incident `2e4fd60547e332f6`.

What the rules require, and where this spec meets it:

| Rule | Met by |
|---|---|
| Public GitHub repo with a detectable open-source licence | REQ-LIC-01 |
| `/.kiro` at the root, not in `.gitignore` | REQ-LIC-02 |
| Demo video under 3 minutes, on YouTube, no copyrighted music | REQ-DEM-01 to DEM-04 (script only; recording is manual) |
| Write-up on how Kiro was used (IDE/CLI/Web/Crew, custom agents, hooks, specs, steering, MCP, PBT, powers) | REQ-WRU-01 to WRU-05 |
| Text description of features | REQ-DVP-01 to DVP-04 |
| Social post beginning `#KiroweenHackathon26` | REQ-SOC-01 |
| Blog post that says it is part of Kiroween 2026 | REQ-BLG-01 |
| A URL to the working app | **Not met by design**: see the Decisions section. |

Out of scope: recording or uploading the video, publishing the blog or social post,
submitting on Devpost, the GitHub repository settings (About section, topics), and any
change to the running system.

## A. Kiro hooks (HK)

**REQ-HK-01 (Ubiquitous)** The repository shall contain five hook files in `.kiro/hooks/`,
each in the current schema (`version: "v1"`, trigger `PostFileSave`, a regex `matcher` on the
file path, a `command` action), not the deprecated `.kiro.hook` format.

**REQ-HK-02 (Event-driven)** When a file matching a hook's pattern is saved, the hook shall
run:

| Area | Saved file | Check |
|---|---|---|
| agents | `agents/<package>/**` Python or `pyproject.toml` | `ruff check .` and `pytest -q` for that package |
| infra | `infra/**` `.tf` or `.tfvars` | `terraform fmt -check` and `terraform validate` |
| charts | `gitops/charts/**` | `helm lint` and the chart tests |
| chaos | `chaos/**/*.sh` | `shellcheck` |
| ui | `tools/ui-tests/*` or `agents/orchestrator/**/ui/**` | `npm test` (jsdom) |

**REQ-HK-03 (Ubiquitous)** The five areas shall not overlap: a UI file does not start the
agents hook.

**REQ-HK-04 (Ubiquitous)** Every hook command shall call one script,
`scripts/kiro-hook.sh <area> <file>`, which runs the same commands as CI and exits non-zero
when a check fails.

**REQ-HK-05 (Unwanted behaviour)** The script shall treat the file path as untrusted input:
it shall accept a project-relative or absolute path, with spaces, quoted or backslash-escaped;
use a package or chart name only if it is lower-case letters, digits, `_` or `-`; ignore, with
exit 0, a path outside the repository, a path with `..`, an unknown package and a file that
no longer exists; and exit 2 on a bad call and 127 when a tool is missing.

**REQ-HK-06 (Ubiquitous)** The documents shall say that `PostFileSave` fires when the
**agent** saves or modifies a file (the documentation's wording) and how to run a check by
hand.

## B. README and licence (RD, LIC)

**REQ-RD-01** `README.md` shall contain, in this order: what the project is; the crew with
their spooky names; an architecture diagram in Mermaid; the full flow (watcher, classify,
specialist, similar incidents, proposal, human approval, real GitHub pull request, learned
postmortem, found next time); the security model; the cost of an incident; a quickstart;
demo steps; development; teardown; known trade-offs and what is not verified.

**REQ-RD-02** Every number shall come from the repository or from the cluster, and every
command and path shall exist. The cost of an incident shall be the figures in
`docs/demo-results/grafana-oom-real.json`, labelled as one real incident, with how the cost
is computed.

**REQ-RD-03** The README shall separate what has run on the cluster from what has not. The
learning loop's first half (approval, real pull request, learned postmortem, completed
ingestion) has run; the second run that finds the learned postmortem has not been recorded.

**REQ-LIC-01** The repository shall contain `LICENSE` with the MIT licence text, the year
2026 and the holder Roman Ceresnak.

**REQ-LIC-02** `.kiro/specs`, `.kiro/steering`, `.kiro/settings` and `.kiro/hooks` shall be
tracked by git and shall not match any ignore rule.

## C. Kiro write-up (WRU)

**REQ-WRU-01** `docs/KIRO-WRITEUP.md` shall answer each question the rules list, for every
Kiro feature in turn. A feature that was not used shall be named as not used: custom agents,
property-based testing and powers.

**REQ-WRU-02** It shall list every spec with its task count and distinct requirement count,
counted from the files, including this one.

**REQ-WRU-03** It shall describe the three steering files, the three MCP servers
(`aws-docs`, `eks`, `terraform`) as configured, and the five hooks.

**REQ-WRU-04** Its "Kiro caught it" section shall list real problems from `git log`: the
cluster-creator admin bootstrap, GitHub's immutable OIDC subjects, `bash -e` in CI,
`ecr:BatchGetImage` for buildx, the missing Pod Identity Agent egress rule, the wrong S3
Vectors resource name, the model-availability API, the Grafana sidecar OOM, the
`[object HTMLElement]` UI bug and the duplicate retrieval chunks. Each entry shall cite
the commit that exists in the history, say how the problem was found (a CI failure, a live
run, a user report or a check made before applying) and what test or verification followed.

**REQ-WRU-05** It shall include the real Grafana OOM that the cpu-memory agent diagnosed
(`docs/demo-results/grafana-oom-real.json`), stating that the incident was submitted by hand
because the watcher excludes the `monitoring` namespace, and the real pull request #2
(incident `2e4fd60547e332f6`, approved by Roman Ceresnak, merged).

## D. Demo script (DEM)

**REQ-DEM-01** `docs/DEMO-SCRIPT.md` shall be a shot list whose timings add up to at most
170 seconds, with the exact command for every shot that needs one.

**REQ-DEM-02** It shall show the animated crew, an incident's evidence, similar past
incidents, Approve opening a real pull request, the learned memory on the second run, and the
Grafana cost dashboard.

**REQ-DEM-03** It shall stage the second run: it needs at least 10 minutes after the first
incident (watcher cooldown 600 s, correlation window 300 s) and a completed ingestion job,
so the first run is done before recording.

**REQ-DEM-04** It shall use no third-party marks or music.

## E. Blog and social post (BLG, SOC)

**REQ-BLG-01** `docs/BLOG-DRAFT.md` shall be a public-facing post about Kiro and the project
that states it was written as part of the Kiroween 2026 hackathon.

**REQ-SOC-01** `docs/SOCIAL-POST.md` shall hold a post that begins with `#KiroweenHackathon26`,
states the favourite thing about Kiro and how it changed the approach to development, and is
at most 280 characters.

## F. Devpost text (DVP)

**REQ-DVP-01** `docs/DEVPOST.md` shall have the sections Inspiration, What it does, How we
built it, Challenges, Accomplishments, What's next, Category and Built with.

**REQ-DVP-02** The category is Frankenstein, and the text shall argue it from the stitched
parts and the rule's own wording.

**REQ-DVP-03** It shall address the five judging criteria (impact, technological
implementation, design, innovation, presentation) with evidence from the repository.

**REQ-DVP-04** It shall tell the truth about what has not been verified.

## G. Consistency (DC)

**REQ-DC-01** Tests shall check what can be checked: every commit hash cited exists; task and
requirement counts match the spec files; every repository path cited exists; the cost figures
match the JSON; the Mermaid diagram is well formed; the timings add up; the social post and
the licence meet their rules; and the only placeholders left are the marked ones the
operator must fill.

## Decisions

- **No public URL.** The rules ask for "a URL to your working app". The project's security
  model (`mui:REQ-UI-05`) allows the UI only through `kubectl port-forward`; adding a public
  ingress to an incident-response console with unauthenticated approve buttons would break
  it. The Devpost text offers the repository, the demo video and the reproduction steps, and
  the checklist asks the operator to decide.
- **Hooks fire on the agent's saves.** The v1 trigger is documented as firing when the agent
  saves or modifies a file. The hooks help while Kiro edits; for your own edits, run the
  script by hand or rely on CI, which runs the same commands.
- **The write-up claims only what happened.** Property-based testing, custom agents and
  powers were not used. The MCP servers are listed as configured; which of them the sessions
  used is the operator's to confirm.
