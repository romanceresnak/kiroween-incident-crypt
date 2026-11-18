"""replay TASK-01: tools/replay/build.py (REQ-BD-01 to BD-06, REQ-RM-03, RM-04)."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
SCRIPT = REPO / "tools" / "replay" / "build.py"

spec = importlib.util.spec_from_file_location("replay_build", SCRIPT)
assert spec and spec.loader
build_mod = importlib.util.module_from_spec(spec)
sys.modules["replay_build"] = build_mod
spec.loader.exec_module(build_mod)

NAMES = [name for name, _ in build_mod.RECORDS]
EXPECTED_FILES = {
    "index.html", "style.css", "app.js", "replay.js", "replay.css",
    "art/archivist.svg", "art/ghost.svg", "art/necromancer.svg", "art/spider.svg",
    "art/vampire.svg", "art/zombie.svg", "data/manifest.json",
    *(f"data/{name}" for name in NAMES),
}


def files_under(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def built(tmp_path: Path) -> Path:
    out = tmp_path / "site"
    build_mod.build(out)
    return out


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    """A throwaway copy of the inputs, so a test can break one."""
    root = tmp_path / "repo"
    for rel in (build_mod.UI_DIR, build_mod.REPLAY_DIR):
        shutil.copytree(REPO / rel, root / rel, ignore=shutil.ignore_patterns("__pycache__"))
    (root / build_mod.RESULTS_DIR).mkdir(parents=True)
    for name in NAMES:
        shutil.copyfile(REPO / build_mod.RESULTS_DIR / name, root / build_mod.RESULTS_DIR / name)
    return root


def edit_record(root: Path, name: str, change) -> None:
    path = root / build_mod.RESULTS_DIR / name
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


# ----------------------------------------------------------------------- the output


def test_the_site_has_exactly_the_files_the_design_lists(built: Path) -> None:
    assert files_under(built) == EXPECTED_FILES


def test_there_is_no_nojekyll_or_kiro_or_hidden_file(built: Path) -> None:
    assert not [p for p in files_under(built) if "/." in "/" + p or ".kiro" in p]


def test_the_data_files_are_byte_for_byte_the_saved_records(built: Path) -> None:
    for name in NAMES:
        assert (built / "data" / name).read_bytes() == (
            REPO / "docs" / "demo-results" / name).read_bytes(), name


def test_the_ui_files_are_the_real_ones_with_no_second_copy(built: Path) -> None:
    ui = REPO / build_mod.UI_DIR
    for name in ("style.css", "app.js"):
        assert (built / name).read_bytes() == (ui / name).read_bytes(), name
    for svg in (ui / "art").glob("*.svg"):
        assert (built / "art" / svg.name).read_bytes() == svg.read_bytes()
    for name in ("replay.js", "replay.css"):
        assert (built / name).read_bytes() == (
            REPO / build_mod.REPLAY_DIR / name).read_bytes()


def test_the_manifest_lists_the_records_in_the_feed_order(built: Path) -> None:
    manifest = json.loads((built / "data" / "manifest.json").read_text())["incidents"]
    assert [entry["file"] for entry in manifest] == NAMES
    ids = [json.loads((built / "data" / name).read_text())["incident"]["incident_id"]
           for name in NAMES]
    assert [entry["id"] for entry in manifest] == ids
    assert len(set(ids)) == 5
    # The story that carries the learned memory is first, as the demo script opens on it.
    assert manifest[0]["file"] == "netpol-second-run-real.json"
    assert all(entry["story"] for entry in manifest)


def test_index_html_is_the_real_one_plus_the_insertions(built: Path) -> None:
    real = (REPO / build_mod.UI_DIR / "index.html").read_text()
    html = (built / "index.html").read_text()
    assert "<title>Kiroween Incident Crypt (replay)</title>" in html
    # Taking the insertions out gives the real page back.
    restored = html
    for anchor, replacement in build_mod.INSERTIONS:
        assert replacement in restored
        restored = restored.replace(replacement, anchor, 1)
    assert restored == real


def test_the_banner_text_is_exact_and_the_scripts_load_in_order(built: Path) -> None:
    html = (built / "index.html").read_text()
    assert ('<div class="replay-banner" role="note">Replay of real runs on Amazon EKS -- the '
            'live system sits behind a login</div>') in html
    assert html.index("replay-banner") < html.index("replay-bar") < html.index('id="live"')
    scripts = re.findall(r'<script src="([^"]+)"></script>', html)
    assert scripts == ["replay.js", "app.js"]
    assert html.index("replay.css") > html.index("style.css")


def test_every_url_in_the_page_is_relative_and_no_origin_is_named(built: Path) -> None:
    html = (built / "index.html").read_text()
    for url in re.findall(r'(?:href|src)="([^"]*)"', html):
        assert not url.startswith(("/", "http:", "https:", "//", "data:")), url
    assert not re.search(r"https?://", html)
    assert not re.search(r"https?://", (built / "replay.css").read_text())


def test_the_csp_allows_only_the_same_origin(built: Path) -> None:
    html = (built / "index.html").read_text()
    match = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', html)
    assert match and match.group(1) == build_mod.CSP
    policy = dict(d.strip().split(" ", 1) for d in match.group(1).split(";"))
    assert policy["default-src"] == "'none'"
    for directive in ("img-src", "style-src", "script-src", "connect-src"):
        assert policy[directive] == "'self'", directive
    assert "unsafe" not in match.group(1) and "*" not in match.group(1)
    assert policy["base-uri"] == "'none'" and policy["form-action"] == "'none'"
    # The CSP is the first thing in the head after the charset.
    assert html.index("Content-Security-Policy") < html.index("<link")


# --------------------------------------------------------------------- the failures


def test_a_build_into_a_clean_directory_via_the_command_line_works(tmp_path: Path) -> None:
    out = tmp_path / "cli"
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--out", str(out)],
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert files_under(out) == EXPECTED_FILES
    assert "17 files" in result.stdout


def test_the_command_line_needs_an_output_directory() -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False)
    assert result.returncode == 2 and "--out" in result.stderr


def run_failing(fake_root: Path, tmp_path: Path, capsys) -> str:
    out = tmp_path / "site"
    code = build_mod.main(["--out", str(out), "--root", str(fake_root)])
    assert code == 1, "the build should have failed"
    assert not out.exists() or not list(out.iterdir()), "a failed build must write nothing"
    return capsys.readouterr().err


def test_a_missing_record_fails_and_writes_nothing(fake_root, tmp_path, capsys) -> None:
    (fake_root / build_mod.RESULTS_DIR / "pvc-real.json").unlink()
    assert "pvc-real.json: cannot read it" in run_failing(fake_root, tmp_path, capsys)


def test_a_record_that_is_not_json_fails(fake_root, tmp_path, capsys) -> None:
    (fake_root / build_mod.RESULTS_DIR / "pvc-real.json").write_text("{not json")
    assert "not valid JSON" in run_failing(fake_root, tmp_path, capsys)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.pop("investigation"), "missing investigation"),
        (lambda d: d["incident"].pop("reason"), "missing incident.reason"),
        (lambda d: d["incident"].update(incident_id="bad id!"), "bad incident_id"),
        (lambda d: d["classification"].update(category="nonsense"), "unknown category"),
        (lambda d: d.update(status="processing"), "not a settled status"),
        (lambda d: d["investigation"]["diagnosis"].update(evidence="x"), "not a list"),
        (lambda d: d.update(similar_incidents=None), "not a list"),
        (lambda d: d.update(approval={"decision": "approved"}), "has an approval but status"),
    ],
)
def test_an_invalid_record_fails_with_the_reason(fake_root, tmp_path, capsys, change,
                                                 message) -> None:
    edit_record(fake_root, "pvc-real.json", change)
    assert message in run_failing(fake_root, tmp_path, capsys)


def test_an_approved_record_needs_an_approval_and_a_github_pull_request(
        fake_root, tmp_path, capsys) -> None:
    edit_record(fake_root, "netpol-first-run-real.json", lambda d: d.update(approval=None))
    assert "approved without an approval" in run_failing(fake_root, tmp_path, capsys)


def test_a_pull_request_outside_github_fails(fake_root, tmp_path, capsys) -> None:
    def change(d):
        d["approval"]["pull_request"]["url"] = "https://example.com/pull/2"

    edit_record(fake_root, "netpol-first-run-real.json", change)
    assert "not a github.com address" in run_failing(fake_root, tmp_path, capsys)


def test_two_records_with_one_incident_id_fail(fake_root, tmp_path, capsys) -> None:
    first = json.loads((fake_root / build_mod.RESULTS_DIR / "netpol-first-run-real.json")
                       .read_text())
    edit_record(fake_root, "netpol-second-run-real.json",
                lambda d: d["incident"].update(incident_id=first["incident"]["incident_id"]))
    assert "duplicate incident_id" in run_failing(fake_root, tmp_path, capsys)


CREDENTIALS = {
    "an AWS access key ID": "AKIA" + "ABCDEFGHIJKLMNOP",
    "a GitHub token": "ghp_" + "a1B2c3D4e5" * 4,
    "a private key": "-----BEGIN RSA " + "PRIVATE KEY-----",
    "an AWS secret key": "aws_secret_" + "access_key",
}


@pytest.mark.parametrize("label", sorted(CREDENTIALS))
def test_a_record_that_looks_like_it_holds_a_credential_is_not_published(
        fake_root, tmp_path, capsys, label) -> None:
    edit_record(fake_root, "grafana-oom-real.json",
                lambda d: d["incident"].update(message="oops " + CREDENTIALS[label]))
    assert f"contains what looks like {label}" in run_failing(fake_root, tmp_path, capsys)


def test_the_real_records_contain_nothing_that_looks_like_a_credential() -> None:
    for name in NAMES:
        text = (REPO / "docs" / "demo-results" / name).read_text()
        for label, pattern in build_mod.CREDENTIALS:
            assert not pattern.search(text), (name, label)


@pytest.mark.parametrize("anchor", [a for a, _ in build_mod.INSERTIONS])
def test_a_missing_anchor_fails_and_writes_nothing(fake_root, tmp_path, capsys,
                                                   anchor) -> None:
    page = fake_root / build_mod.UI_DIR / "index.html"
    page.write_text(page.read_text().replace(anchor, "", 1))
    assert "expected one" in run_failing(fake_root, tmp_path, capsys)


@pytest.mark.parametrize("anchor", [a for a, _ in build_mod.INSERTIONS])
def test_a_repeated_anchor_fails_too(fake_root, tmp_path, capsys, anchor) -> None:
    page = fake_root / build_mod.UI_DIR / "index.html"
    page.write_text(page.read_text().replace(anchor, anchor + "\n" + anchor, 1))
    assert "found 2" in run_failing(fake_root, tmp_path, capsys)


def test_a_missing_engine_file_fails(fake_root, tmp_path, capsys) -> None:
    (fake_root / build_mod.REPLAY_DIR / "replay.js").unlink()
    assert "cannot read" in run_failing(fake_root, tmp_path, capsys)


def test_the_build_reads_only_the_repository_and_imports_only_the_standard_library() -> None:
    source = SCRIPT.read_text()
    stdlib = {"argparse", "json", "re", "shutil", "sys", "pathlib", "__future__"}
    imported = set(re.findall(r"^(?:import|from) (\w+)", source, re.M))
    assert imported <= stdlib, imported - stdlib
    assert "urllib" not in source and "socket" not in source and "subprocess" not in source
