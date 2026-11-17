"""TASK-13: tools/set_image_tag.py."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
VALUES = REPO / "gitops" / "charts" / "agents" / "values.yaml"
SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"

spec = importlib.util.spec_from_file_location("set_image_tag", REPO / "tools" / "set_image_tag.py")
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def changed_lines(before: str, after: str) -> list[tuple[str, str]]:
    a, b = before.splitlines(), after.splitlines()
    assert len(a) == len(b)
    return [(x, y) for x, y in zip(a, b, strict=True) if x != y]


def test_only_the_two_tag_lines_change_in_the_real_values_file() -> None:
    before = VALUES.read_text()
    after, changed = mod.update(before, SHA, ["orchestrator", "cpu-memory"])
    assert changed
    diffs = changed_lines(before, after)
    assert [new for _, new in diffs] == [
        f"    tag: {SHA} # ci-managed:orchestrator",
        f"    tag: {SHA} # ci-managed:cpu-memory",
    ]


def test_everything_else_is_byte_identical() -> None:
    before = VALUES.read_text()
    after, _ = mod.update(before, SHA, ["orchestrator", "cpu-memory"])
    strip = [line for line in before.splitlines(True) if "ci-managed:" not in line]
    keep = [line for line in after.splitlines(True) if "ci-managed:" not in line]
    assert strip == keep
    assert before.endswith("\n") == after.endswith("\n")


def test_idempotent() -> None:
    once, _ = mod.update(VALUES.read_text(), SHA, ["orchestrator", "cpu-memory"])
    twice, changed = mod.update(once, SHA, ["orchestrator", "cpu-memory"])
    assert twice == once and changed is False


def test_one_component_leaves_the_other_alone() -> None:
    text, _ = mod.update(VALUES.read_text(), OTHER, ["orchestrator", "cpu-memory"])
    after, _ = mod.update(text, SHA, ["orchestrator"])
    assert f"tag: {SHA} # ci-managed:orchestrator" in after
    assert f"tag: {OTHER} # ci-managed:cpu-memory" in after


def test_crlf_line_endings_are_kept() -> None:
    text = "images:\r\n  a:\r\n    tag: unset # ci-managed:orchestrator\r\n"
    after, _ = mod.update(text, SHA, ["orchestrator"])
    assert after == f"images:\r\n  a:\r\n    tag: {SHA} # ci-managed:orchestrator\r\n"


@pytest.mark.parametrize("sha", ["latest", "abc123", SHA.upper(), SHA + "0", ""])
def test_rejects_anything_but_a_full_sha(sha: str) -> None:
    with pytest.raises(ValueError, match="SHA"):
        mod.update(VALUES.read_text(), sha, ["orchestrator"])


def test_missing_marker_is_an_error() -> None:
    with pytest.raises(ValueError, match="no ci-managed tag line for: watcher"):
        mod.update(VALUES.read_text(), SHA, ["watcher"])


def test_cli_writes_file_and_reports(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "values.yaml"
    shutil.copy(VALUES, target)
    assert mod.main(["x", str(target), SHA, "orchestrator", "cpu-memory"]) == 0
    assert capsys.readouterr().out.strip() == "changed"
    assert mod.main(["x", str(target), SHA, "orchestrator", "cpu-memory"]) == 0
    assert capsys.readouterr().out.strip() == "unchanged"
    assert mod.main(["x", str(target), "bad", "orchestrator"]) == 1


def test_markers_match_the_chart_component_names() -> None:
    text = VALUES.read_text()
    assert "# ci-managed:orchestrator" in text and "# ci-managed:cpu-memory" in text
