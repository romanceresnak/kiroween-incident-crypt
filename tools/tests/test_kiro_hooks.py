"""submission TASK-01: the Kiro hooks and scripts/kiro-hook.sh against fake tools."""

from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
HOOKS_DIR = REPO / ".kiro" / "hooks"
SCRIPT = REPO / "scripts" / "kiro-hook.sh"

TRIGGERS = {
    "SessionStart", "Stop", "UserPromptSubmit", "PreTaskExec", "PostTaskExec",
    "PreToolUse", "PostToolUse", "PostFileCreate", "PostFileSave", "PostFileDelete",
}
EXPECTED = {
    "agents-ruff-pytest": "agents",
    "infra-terraform-check": "infra",
    "charts-helm-lint-tests": "chart",
    "chaos-shellcheck": "chaos",
    "ui-dom-tests": "ui",
}


def load_hooks() -> dict[str, dict]:
    hooks: dict[str, dict] = {}
    for path in sorted(HOOKS_DIR.glob("*.json")):
        doc = json.loads(path.read_text())
        assert doc["version"] == "v1", path.name
        for hook in doc["hooks"]:
            hooks[hook["name"]] = hook
    return hooks


# ---------------------------------------------------------------- the hook files


def test_exactly_the_five_hooks_exist_in_the_current_format() -> None:
    assert not list(HOOKS_DIR.glob("*.kiro.hook")), "the deprecated format must not be used"
    assert set(load_hooks()) == set(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_hook_is_valid_and_calls_the_script_with_its_area(name: str) -> None:
    hook = load_hooks()[name]
    assert hook["trigger"] in TRIGGERS
    assert hook["trigger"] == "PostFileSave"
    assert hook["enabled"] is True
    assert isinstance(hook["timeout"], int) and 0 < hook["timeout"] <= 600
    assert hook["description"]
    re.compile(hook["matcher"])
    action = hook["action"]
    assert action["type"] == "command"
    assert action["command"] == f'scripts/kiro-hook.sh {EXPECTED[name]} "{{{{filePath}}}}"'


def test_the_script_the_hooks_call_exists_and_is_executable() -> None:
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & stat.S_IXUSR


# Every sample path is tried absolute and project-relative. Each must start exactly
# the hooks listed, so a UI file never starts the agents hook and nothing overlaps.
ROUTES = {
    "agents/orchestrator/src/kiroween_orchestrator/service.py": ["agents-ruff-pytest"],
    "agents/shared/pyproject.toml": ["agents-ruff-pytest"],
    "agents/orchestrator/tests/test_ui.py": ["agents-ruff-pytest"],
    "agents/orchestrator/src/kiroween_orchestrator/ui/app.js": ["ui-dom-tests"],
    "agents/orchestrator/src/kiroween_orchestrator/ui/style.css": ["ui-dom-tests"],
    "tools/ui-tests/app.test.mjs": ["ui-dom-tests"],
    "tools/ui-tests/package-lock.json": ["ui-dom-tests"],
    "infra/main.tf": ["infra-terraform-check"],
    "infra/modules/eks/main.tf": ["infra-terraform-check"],
    "gitops/charts/agents/values.yaml": ["charts-helm-lint-tests"],
    "gitops/charts/agents/templates/_helpers.tpl": ["charts-helm-lint-tests"],
    "gitops/charts/agents/tests/test_chart.py": ["charts-helm-lint-tests"],
    "chaos/netpol/netpol.sh": ["chaos-shellcheck"],
    # Not covered by any hook.
    "README.md": [],
    "docs/KIRO-WRITEUP.md": [],
    "gitops/apps/monitoring.yaml": [],
    "tools/ui-tests/node_modules/jsdom/lib/api.js": [],
    "chaos/netpol/README.md": [],
    "scripts/kiro-hook.sh": [],
    "infra/README.md": [],
}


@pytest.mark.parametrize("rel", sorted(ROUTES))
@pytest.mark.parametrize("absolute", [False, True])
def test_every_file_starts_exactly_the_right_hooks(rel: str, absolute: bool) -> None:
    path = f"/Users/someone/My Projects/kiroween-incident-crypt/{rel}" if absolute else rel
    started = sorted(
        name for name, hook in load_hooks().items() if re.search(hook["matcher"], path)
    )
    assert started == sorted(ROUTES[rel])


# ------------------------------------------------------------------ the script

FAKE_TOOL = """#!/usr/bin/env bash
echo "$PWD|$(basename "$0") $*" >> "$FAKE_LOG"
case "$(basename "$0") $*" in
  *"$FAKE_FAIL"*) [[ -n "$FAKE_FAIL" ]] && exit 3 ;;
esac
exit 0
"""


class Sandbox:
    """A throwaway repository (its path contains a space) with fake tools."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "my repo"
        (self.root / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPT, self.root / "scripts" / "kiro-hook.sh")
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.log = tmp_path / "calls.log"
        for tool in ("uv", "terraform", "helm", "shellcheck", "npm"):
            self.add_tool(tool)

    def add_tool(self, name: str) -> None:
        exe = self.bin / name
        exe.write_text(FAKE_TOOL)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)

    def touch(self, rel: str, text: str = "") -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run(self, *args: str, fail: str = "") -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.root),
            "FAKE_LOG": str(self.log),
            "FAKE_FAIL": fail,
        }
        return subprocess.run(  # noqa: S603
            ["/bin/bash", str(self.root / "scripts" / "kiro-hook.sh"), *args],
            env=env, capture_output=True, text=True, check=False, cwd="/",
        )

    def calls(self) -> list[str]:
        if not self.log.exists():
            return []
        lines = self.log.read_text().splitlines()
        return [line.replace(str(self.root), "<root>") for line in lines]


@pytest.fixture
def box(tmp_path: Path) -> Sandbox:
    sandbox = Sandbox(tmp_path)
    sandbox.touch("agents/orchestrator/pyproject.toml")
    sandbox.touch("agents/orchestrator/src/pkg/service.py")
    # A real package whose name the script must still refuse (REQ-HK-05).
    sandbox.touch("agents/Bad Name/pyproject.toml")
    sandbox.touch("gitops/charts/Bad Chart/Chart.yaml")
    sandbox.touch("gitops/charts/agents/Chart.yaml")
    sandbox.touch("gitops/charts/agents/tests/test_chart.py")
    sandbox.touch("gitops/charts/plain/Chart.yaml")
    sandbox.touch("chaos/netpol/netpol.sh", "#!/bin/bash\n")
    sandbox.touch("infra/main.tf")
    (sandbox.root / "infra" / ".terraform").mkdir()
    sandbox.touch("tools/ui-tests/package.json")
    sandbox.touch("tools/ui-tests/node_modules/.keep")
    return sandbox


def test_agents_runs_ruff_then_pytest_inside_the_package(box: Sandbox) -> None:
    result = box.run("agents", "agents/orchestrator/src/pkg/service.py")
    assert result.returncode == 0, result.stderr
    assert box.calls() == [
        "<root>/agents/orchestrator|uv run --frozen ruff check .",
        "<root>/agents/orchestrator|uv run --frozen pytest -q",
    ]


@pytest.mark.parametrize("style", ["relative", "absolute", "quoted", "single", "escaped", "split"])
def test_every_way_kiro_may_pass_the_path_reaches_one_package(box: Sandbox, style: str) -> None:
    rel = "agents/orchestrator/src/pkg/service.py"
    absolute = str(box.root / rel)  # contains a space
    forms = {
        "relative": [rel],
        "absolute": [absolute],
        "quoted": [f'"{absolute}"'],
        "single": [f"'{absolute}'"],
        "escaped": [absolute.replace(" ", "\\ ")],
        "split": absolute.split(" "),  # an unquoted value, split at the space
    }
    result = box.run("agents", *forms[style])
    assert result.returncode == 0, result.stdout + result.stderr
    assert box.calls()[0] == "<root>/agents/orchestrator|uv run --frozen ruff check ."


def test_a_ruff_failure_stops_before_pytest_and_fails_the_hook(box: Sandbox) -> None:
    result = box.run("agents", "agents/orchestrator/src/pkg/service.py", fail="ruff")
    assert result.returncode == 3
    assert box.calls() == ["<root>/agents/orchestrator|uv run --frozen ruff check ."]


def test_a_pytest_failure_fails_the_hook(box: Sandbox) -> None:
    result = box.run("agents", "agents/orchestrator/src/pkg/service.py", fail="pytest")
    assert result.returncode == 3


@pytest.mark.parametrize(
    "path",
    [
        "agents/unknown/src/x.py",          # no pyproject.toml: not a package
        "agents/Bad Name/x.py",             # not a valid package name
        "agents/../etc/x.py",
        "agents/orchestrator/../../x.py",
        "agents/x.py",                      # not inside a package
        "README.md",
        "/etc/passwd",                      # outside the repository
        "../outside/agents/orchestrator/x.py",
    ],
)
def test_paths_outside_the_area_are_ignored_and_run_nothing(box: Sandbox, path: str) -> None:
    result = box.run("agents", path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ignored")
    assert box.calls() == []


def test_a_hostile_name_never_reaches_a_shell(box: Sandbox, tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    result = box.run("agents", f"agents/$(touch {marker})/x.py")
    assert result.returncode == 0 and not marker.exists() and box.calls() == []


def test_infra_runs_fmt_check_then_validate(box: Sandbox) -> None:
    assert box.run("infra", "infra/main.tf").returncode == 0
    assert box.calls() == [
        "<root>|terraform -chdir=infra fmt -check -recursive -diff",
        "<root>|terraform -chdir=infra validate",
    ]


def test_infra_formatting_failure_skips_validate(box: Sandbox) -> None:
    assert box.run("infra", "infra/main.tf", fail="fmt").returncode == 3
    assert [c for c in box.calls() if "validate" in c] == []


def test_infra_that_was_never_initialised_says_how_to_fix_it(box: Sandbox) -> None:
    shutil.rmtree(box.root / "infra" / ".terraform")
    result = box.run("infra", "infra/main.tf")
    assert result.returncode == 1
    assert "terraform -chdir=infra init -backend=false" in result.stderr
    assert box.calls() == []


def test_infra_ignores_files_elsewhere(box: Sandbox) -> None:
    assert box.run("infra", "gitops/x.tf").stdout.startswith("ignored")
    assert box.calls() == []


def test_chart_runs_helm_lint_then_the_chart_tests(box: Sandbox) -> None:
    result = box.run("chart", "gitops/charts/agents/values.yaml")
    assert result.returncode == 0, result.stderr
    assert box.calls() == [
        "<root>|helm lint gitops/charts/agents",
        "<root>|uv run --frozen --project agents/shared pytest -q gitops/charts/agents/tests",
    ]


def test_chart_without_tests_only_lints(box: Sandbox) -> None:
    assert box.run("chart", "gitops/charts/plain/values.yaml").returncode == 0
    assert box.calls() == ["<root>|helm lint gitops/charts/plain"]


def test_chart_lint_failure_skips_the_tests(box: Sandbox) -> None:
    assert box.run("chart", "gitops/charts/agents/values.yaml", fail="helm").returncode == 3
    assert [c for c in box.calls() if "pytest" in c] == []


@pytest.mark.parametrize("path", ["gitops/charts/ghost/values.yaml", "gitops/apps/x.yaml",
                                  "gitops/charts/Agents/x.yaml",
                                  "gitops/charts/Bad Chart/x.yaml"])
def test_chart_ignores_what_is_not_a_chart(box: Sandbox, path: str) -> None:
    assert box.run("chart", path).stdout.startswith("ignored")
    assert box.calls() == []


def test_chaos_runs_shellcheck_on_the_saved_script(box: Sandbox) -> None:
    assert box.run("chaos", "chaos/netpol/netpol.sh").returncode == 0
    assert box.calls() == ["<root>|shellcheck chaos/netpol/netpol.sh"]


def test_chaos_failure_fails_the_hook(box: Sandbox) -> None:
    assert box.run("chaos", "chaos/netpol/netpol.sh", fail="shellcheck").returncode == 3


@pytest.mark.parametrize("path", ["chaos/netpol/README.md", "chaos/netpol/gone.sh", "scripts/x.sh"])
def test_chaos_ignores_other_files_and_deleted_scripts(box: Sandbox, path: str) -> None:
    assert box.run("chaos", path).stdout.startswith("ignored")
    assert box.calls() == []


def test_ui_runs_npm_test_without_reinstalling(box: Sandbox) -> None:
    assert box.run("ui", "tools/ui-tests/app.test.mjs").returncode == 0
    assert box.calls() == ["<root>/tools/ui-tests|npm test"]


def test_ui_installs_from_the_lockfile_once_when_node_modules_is_missing(box: Sandbox) -> None:
    shutil.rmtree(box.root / "tools" / "ui-tests" / "node_modules")
    assert box.run("ui", "agents/orchestrator/src/pkg/ui/app.js").returncode == 0
    assert box.calls() == [
        "<root>/tools/ui-tests|npm ci --ignore-scripts --no-audit --no-fund",
        "<root>/tools/ui-tests|npm test",
    ]


def test_ui_ignores_other_files(box: Sandbox) -> None:
    assert box.run("ui", "agents/orchestrator/src/pkg/service.py").stdout.startswith("ignored")
    assert box.calls() == []


@pytest.mark.parametrize(
    ("area", "path", "tool"),
    [("agents", "agents/orchestrator/x.py", "uv"), ("infra", "infra/main.tf", "terraform"),
     ("chart", "gitops/charts/agents/values.yaml", "helm"),
     ("chaos", "chaos/netpol/netpol.sh", "shellcheck"), ("ui", "tools/ui-tests/a.mjs", "npm")],
)
def test_a_missing_tool_exits_127_and_names_it(
    box: Sandbox, area: str, path: str, tool: str
) -> None:
    (box.bin / tool).unlink()
    result = box.run(area, path)
    assert result.returncode == 127
    assert f"missing tool: {tool}" in result.stderr


@pytest.mark.parametrize("args", [[], ["agents"], ["nonsense", "agents/x.py"], ["agents", ""]])
def test_a_bad_call_exits_2(box: Sandbox, args: list[str]) -> None:
    result = box.run(*args)
    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert box.calls() == []


def test_the_script_is_shellchecked_by_ci() -> None:
    workflow = (REPO / ".github" / "workflows" / "agents-images.yml").read_text()
    assert "shellcheck chaos/*/*.sh scripts/*.sh" in workflow
