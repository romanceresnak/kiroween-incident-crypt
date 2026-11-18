# submission — Design

## 1. Hooks

```
.kiro/hooks/
  agents-ruff-pytest.json      trigger PostFileSave  matcher (^|/)agents/<pkg>/.*\.(py|toml)$
  infra-terraform-check.json   ...                   (^|/)infra/.*\.(tf|tfvars)$
  charts-helm-lint-tests.json  ...                   (^|/)gitops/charts/.*\.(ya?ml|tpl|json|py)$
  chaos-shellcheck.json        ...                   (^|/)chaos/.*\.sh$
  ui-dom-tests.json            ...                   (^|/)(tools/ui-tests/[^/]*\.(mjs|js|json)|agents/orchestrator/.*/ui/.*)$
scripts/kiro-hook.sh <agents|infra|chart|chaos|ui> <file>
```

One file per hook, so each can be disabled on its own. Each action is
`scripts/kiro-hook.sh <area> "{{filePath}}"` with a timeout (300 s for the areas that may
install dependencies on first use).

`kiro-hook.sh`:

```
rel = path with the repository root removed; quotes and "\ " undone
rel outside the repository or containing ".." -> ignored, exit 0
agents : rel = agents/<pkg>/...  pkg ~ ^[a-z][a-z0-9_]*$ and agents/<pkg>/pyproject.toml exists
         cd agents/<pkg> && uv run --frozen ruff check . && uv run --frozen pytest -q
infra  : terraform -chdir=infra fmt -check -recursive -diff && terraform -chdir=infra validate
         (needs infra/.terraform: otherwise exit 1 with the init command)
chart  : rel = gitops/charts/<name>/...  name ~ ^[a-z0-9-]+$
         helm lint gitops/charts/<name> && uv run --frozen --project agents/shared pytest -q
         gitops/charts/<name>/tests   (tests only if the directory exists)
chaos  : rel = chaos/**/*.sh and the file exists -> shellcheck <file>
ui     : rel under tools/ui-tests/ or agents/orchestrator/**/ui/
         cd tools/ui-tests; npm ci --ignore-scripts only if node_modules is missing; npm test
```

Commands mirror the CI job (`.github/workflows/agents-images.yml`, job `test`), so a green
hook predicts a green CI. A missing tool exits 127 with what to install. A failed check
exits with the tool's code. Nothing is written to the repository.

Why `{{filePath}}` in double quotes, plus tolerance in the script: the documentation shows
the variable unquoted and does not say whether Kiro escapes it. This machine's path contains
spaces, so the script accepts a raw, quoted or backslash-escaped value.

## 2. The documents

Hand-written from facts gathered on 18 Nov 2026; nothing generated. What each draws on:

| Document | Sources |
|---|---|
| `README.md` | the specs, the chart values, `infra/README.md`, `docs/demo-results/`, the cluster logs for the learning loop |
| `docs/KIRO-WRITEUP.md` | `git log`, the commit bodies, the spec files, `.kiro/settings/mcp.json`, the Devpost rules |
| `docs/DEMO-SCRIPT.md` | the polish spec's demo steps, measured timings (investigation about 21 s; watcher grace 60 s) |
| `docs/BLOG-DRAFT.md`, `docs/SOCIAL-POST.md` | the write-up |
| `docs/DEVPOST.md` | the Devpost rules and judging criteria |

Placeholders the operator must fill are written as `<!-- TODO(user): ... -->`, so they are
visible when edited and findable by a test. There are only these: the video URL, the blog
URL, the social post URL and the confirmation of which Kiro surfaces were used.

## 3. Tests (`tools/tests/test_kiro_hooks.py`, `test_submission_docs.py`)

- Hooks: schema, matchers against absolute and relative sample paths (every file goes to
  exactly the hooks it should), and the script against fake `uv`, `terraform`, `helm`,
  `shellcheck` and `npm` that log their working directory and arguments.
- Documents: the checks in REQ-DC-01, each reading the real repository (`git cat-file` for
  hashes, the spec files for counts, the JSON for costs).

CI already runs `tools/tests` and shellchecks `scripts/*.sh`, so both are covered.

## Decisions

- One dispatcher script, not five inline commands: the commands are testable, shellchecked,
  and the same everywhere.
- The write-up is checked, not trusted: hashes and counts are asserted against git and the
  spec files, so a later rebase that rewrites hashes fails a test instead of leaving a dead
  reference. (Hashes were rewritten once already, by a pull with rebase.)
- The README's cost is one incident. It says so, and gives the formula so a reader can
  recompute it from any incident's `usage_by_agent`.
