#!/usr/bin/env bash
# Run the check that belongs to a file Kiro just saved (submission REQ-HK-01 to HK-05).
# Called by the hooks in .kiro/hooks/*.json; you can also run it by hand:
#
#   scripts/kiro-hook.sh <agents|infra|chart|chaos|ui> <file>
#
# The commands are the ones CI runs (.github/workflows/agents-images.yml), so a green
# hook predicts a green build. A path outside its area, or one that no longer exists,
# is ignored with exit 0. Exit codes: 0 ok or ignored; 2 bad call; 127 tool missing;
# anything else is the failing tool's own code.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() {
  echo "usage: $0 <agents|infra|chart|chaos|ui> <file>" >&2
  exit 2
}

ignore() {
  echo "ignored: $1"
  exit 0
}

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing tool: $1 ($2)" >&2
    exit 127
  fi
}

[[ $# -ge 2 ]] || usage
area="$1"
shift
# The path is untrusted. Kiro may pass it quoted, backslash-escaped or split at spaces,
# so join what is left, then undo one layer of quotes and "\ ".
path="$*"
path="${path#[\"\']}"
path="${path%[\"\']}"
path="${path//\\ / }"
[[ -n "$path" ]] || usage

rel="${path#"$ROOT"/}"
case "/$rel/" in
  *"/../"*) ignore "$path contains '..'" ;;
esac
case "$rel" in
  /*) ignore "$path is outside the repository" ;;
esac

case "$area" in
  agents)
    if [[ "$rel" =~ ^agents/([a-z][a-z0-9_]*)/ ]]; then
      pkg="${BASH_REMATCH[1]}"
    else
      ignore "$rel is not inside an agent package"
    fi
    [[ -f "agents/$pkg/pyproject.toml" ]] || ignore "agents/$pkg is not a package"
    need uv "https://docs.astral.sh/uv/"
    echo "== agents/$pkg: ruff and pytest =="
    cd "agents/$pkg"
    uv run --frozen ruff check .
    uv run --frozen pytest -q
    ;;
  infra)
    [[ "$rel" =~ ^infra/ ]] || ignore "$rel is not under infra/"
    need terraform "https://developer.hashicorp.com/terraform/install"
    if [[ ! -d infra/.terraform ]]; then
      echo "infra is not initialised. Run: terraform -chdir=infra init -backend=false" >&2
      exit 1
    fi
    echo "== infra: terraform fmt -check and validate =="
    terraform -chdir=infra fmt -check -recursive -diff
    terraform -chdir=infra validate
    ;;
  chart)
    if [[ "$rel" =~ ^gitops/charts/([a-z0-9-]+)/ ]]; then
      chart="${BASH_REMATCH[1]}"
    else
      ignore "$rel is not inside a chart"
    fi
    [[ -f "gitops/charts/$chart/Chart.yaml" ]] || ignore "gitops/charts/$chart is not a chart"
    need helm "https://helm.sh/docs/intro/install/"
    echo "== gitops/charts/$chart: helm lint and chart tests =="
    helm lint "gitops/charts/$chart"
    if [[ -d "gitops/charts/$chart/tests" ]]; then
      need uv "https://docs.astral.sh/uv/"
      uv run --frozen --project agents/shared pytest -q "gitops/charts/$chart/tests"
    fi
    ;;
  chaos)
    [[ "$rel" =~ ^chaos/.+\.sh$ ]] || ignore "$rel is not a chaos script"
    [[ -f "$rel" ]] || ignore "$rel no longer exists"
    need shellcheck "https://www.shellcheck.net/"
    echo "== $rel: shellcheck =="
    shellcheck "$rel"
    ;;
  ui)
    [[ "$rel" =~ ^(tools/ui-tests/|agents/orchestrator/.+/ui/) ]] || ignore "$rel is not a UI file"
    need npm "https://nodejs.org/"
    echo "== tools/ui-tests: npm test (jsdom) =="
    cd tools/ui-tests
    if [[ ! -d node_modules ]]; then
      npm ci --ignore-scripts --no-audit --no-fund
    fi
    npm test
    ;;
  *)
    usage
    ;;
esac
