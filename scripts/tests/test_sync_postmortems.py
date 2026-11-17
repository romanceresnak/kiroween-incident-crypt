"""memory-and-ui TASK-01: seeded postmortems and scripts/sync-postmortems.sh.

The script runs against fake `aws` and `terraform` executables; nothing reaches AWS.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
POSTMORTEMS = REPO / "docs" / "postmortems"
SCRIPT = REPO / "scripts" / "sync-postmortems.sh"
SECTIONS = ["Summary", "Impact", "Root cause", "Evidence", "Fix", "Prevention"]
CATEGORIES = {"cpu_memory", "network", "deploy_rollout", "storage"}
EXPECTED_SIGNALS = {"OOMKilled", "CPUThrottling", "Unhealthy", "ImagePullBackOff",
                    "CrashLoopBackOff", "ProvisioningFailed", "DiskPressure"}


def documents() -> list[Path]:
    return sorted(POSTMORTEMS.glob("*.md"))


# --- postmortems (REQ-MEM-01) ---------------------------------------------------------


def test_eight_postmortems_each_with_a_sidecar() -> None:
    docs = documents()
    assert len(docs) == 8
    sidecars = sorted(POSTMORTEMS.glob("*.md.metadata.json"))
    assert [p.name for p in sidecars] == [f"{d.name}.metadata.json" for d in docs]


@pytest.mark.parametrize("doc", documents(), ids=lambda p: p.stem)
def test_postmortem_has_every_section_in_order(doc: Path) -> None:
    text = doc.read_text()
    headings = re.findall(r"^## (.+)$", text, re.M)
    assert headings == SECTIONS
    assert text.startswith("# PM-2026-")
    for section in SECTIONS:  # no empty section
        body = text.split(f"## {section}\n", 1)[1].split("\n## ", 1)[0]
        assert len(body.split()) >= 20, (doc.name, section)


@pytest.mark.parametrize("doc", documents(), ids=lambda p: p.stem)
def test_sidecar_matches_the_document(doc: Path) -> None:
    attrs = json.loads(Path(f"{doc}.metadata.json").read_text())["metadataAttributes"]
    assert set(attrs) == {"title", "category", "signal", "fix"}
    assert doc.read_text().splitlines()[0] == f"# {attrs['title']}"
    assert attrs["category"] in CATEGORIES
    assert 40 <= len(attrs["fix"]) <= 600
    assert len(json.dumps(attrs)) < 1024


def test_the_eight_scenarios_are_covered() -> None:
    signals = {json.loads(Path(f"{d}.metadata.json").read_text())["metadataAttributes"]["signal"]
               for d in documents()}
    assert signals == EXPECTED_SIGNALS
    names = " ".join(d.name for d in documents())
    for topic in ["oomkilled", "cpu-throttling", "networkpolicy", "coredns",
                  "imagepullbackoff", "crashloop-bad-configmap", "pvc-missing-storageclass",
                  "disk-pressure"]:
        assert topic in names, topic


def test_postmortems_name_no_real_infrastructure() -> None:
    for doc in documents():
        text = doc.read_text()
        assert "255834079310" not in text
        assert not re.search(r"arn:aws:", text)


# --- sync script (REQ-MEM-02) --------------------------------------------------------------

FAKE_AWS = r"""#!/usr/bin/env bash
echo "aws $*" >> "$FAKE_LOG"
case "$1 $2" in
  "sts get-caller-identity") echo "$FAKE_ACCOUNT" ;;
  "s3 sync") exit "${FAKE_SYNC_EXIT:-0}" ;;
  "bedrock-agent list-data-sources") echo "$FAKE_SOURCES" ;;
  "bedrock-agent start-ingestion-job") echo "JOB123" ;;
  "bedrock-agent get-ingestion-job")
    case "$*" in
      *ingestionJob.status*)
        status="$(head -n 1 "$FAKE_STATUSES")"
        if [[ "$(wc -l < "$FAKE_STATUSES")" -gt 1 ]]; then
          tail -n +2 "$FAKE_STATUSES" > "$FAKE_STATUSES.tmp" \
            && mv "$FAKE_STATUSES.tmp" "$FAKE_STATUSES"
        fi
        echo "$status" ;;
      *ingestionJob.statistics*) echo '{"numberOfNewDocumentsIndexed": 8}' ;;
      *ingestionJob.failureReasons*) echo "embedding model access denied" ;;
    esac ;;
  *) echo "unexpected aws call: $*" >&2; exit 99 ;;
esac
"""

FAKE_TERRAFORM = r"""#!/usr/bin/env bash
echo "terraform $*" >> "$FAKE_LOG"
case "$*" in
  *postmortem_bucket_name*) echo "kiroween-prod-postmortems-255834079310" ;;
  *knowledge_base_id*) echo "VIBAT27SDH" ;;
esac
"""


def setup(tmp_path: Path, *, account: str = "255834079310", sources: str = "DS1",
          statuses: tuple[str, ...] = ("IN_PROGRESS", "COMPLETE")) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("aws", FAKE_AWS), ("terraform", FAKE_TERRAFORM)):
        exe = bin_dir / name
        exe.write_text(body)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "statuses").write_text("\n".join(statuses) + "\n")
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path),
            "FAKE_LOG": str(tmp_path / "calls.log"), "FAKE_ACCOUNT": account,
            "FAKE_SOURCES": sources, "FAKE_STATUSES": str(tmp_path / "statuses"),
            "POLL_SECONDS": "0"}


def run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["/bin/bash", str(SCRIPT), *args], env=env,  # noqa: S603
                          capture_output=True, text=True, check=False)


def calls(env: dict[str, str]) -> list[str]:
    log = Path(env["FAKE_LOG"])
    return log.read_text().splitlines() if log.exists() else []


def test_uploads_ingests_and_waits_for_completion(tmp_path: Path) -> None:
    env = setup(tmp_path)
    result = run(env)
    assert result.returncode == 0, result.stderr
    log = calls(env)
    sync = next(c for c in log if c.startswith("aws s3 sync"))
    assert f"{POSTMORTEMS} s3://kiroween-prod-postmortems-255834079310/postmortems/" in sync
    assert "--include *.md --include *.md.metadata.json" in sync and "--delete" not in sync
    assert any("start-ingestion-job --knowledge-base-id VIBAT27SDH --data-source-id DS1" in c
               for c in log)
    assert sum("ingestionJob.status" in c for c in log) == 2
    assert all("--region eu-west-1" in c for c in log if c.startswith("aws "))
    assert "Ingestion complete" in result.stdout


def test_wrong_account_aborts_before_any_change(tmp_path: Path) -> None:
    env = setup(tmp_path, account="111122223333")
    result = run(env)
    assert result.returncode == 1 and "refusing to run" in result.stderr
    assert calls(env) == [c for c in calls(env) if "sts get-caller-identity" in c]


def test_failed_ingestion_exits_1_with_the_reason(tmp_path: Path) -> None:
    env = setup(tmp_path, statuses=("STARTING", "FAILED"))
    result = run(env)
    assert result.returncode == 1 and "embedding model access denied" in result.stderr


def test_timeout_exits_1(tmp_path: Path) -> None:
    env = setup(tmp_path, statuses=("IN_PROGRESS",))
    result = run(env, "--timeout", "3")
    assert result.returncode == 1 and "still IN_PROGRESS after 3s" in result.stderr


def test_delete_is_opt_in(tmp_path: Path) -> None:
    env = setup(tmp_path)
    assert run(env, "--delete").returncode == 0
    assert "--delete" in next(c for c in calls(env) if c.startswith("aws s3 sync"))


def test_env_overrides_terraform(tmp_path: Path) -> None:
    env = {**setup(tmp_path), "POSTMORTEM_BUCKET": "other-bucket", "KNOWLEDGE_BASE_ID": "KB2"}
    assert run(env).returncode == 0
    log = calls(env)
    assert not [c for c in log if c.startswith("terraform")]
    assert any("s3://other-bucket/postmortems/" in c for c in log)


@pytest.mark.parametrize("sources", ["", "DS1\tDS2"])
def test_needs_exactly_one_data_source(tmp_path: Path, sources: str) -> None:
    env = setup(tmp_path, sources=sources)
    result = run(env)
    assert result.returncode == 1 and "exactly one data source" in result.stderr
    assert not [c for c in calls(env) if "start-ingestion-job" in c]


def test_failed_upload_does_not_start_ingestion(tmp_path: Path) -> None:
    env = {**setup(tmp_path), "FAKE_SYNC_EXIT": "1"}
    assert run(env).returncode != 0
    assert not [c for c in calls(env) if "start-ingestion-job" in c]


@pytest.mark.parametrize("args", [["--bogus"], ["--timeout"], ["--timeout", "x"]])
def test_usage_errors(tmp_path: Path, args: list[str]) -> None:
    env = setup(tmp_path)
    assert run(env, *args).returncode == 2
    assert calls(env) == []


def test_script_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)
