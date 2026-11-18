"""polish TASK-07: scripts/verify-learning-iam.sh against a fake `aws`."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "verify-learning-iam.sh"
BUCKET = "kiroween-prod-postmortems-255834079310"
KB = "arn:aws:bedrock:eu-west-1:255834079310:knowledge-base/VIBAT27SDH"

# The fake simulator: allowed only for the two granted permissions, like the real policy.
FAKE_AWS = r"""#!/usr/bin/env bash
echo "aws $*" >> "$FAKE_LOG"
if [[ "$1 $2" == "sts get-caller-identity" ]]; then echo "$FAKE_ACCOUNT"; exit 0; fi
if [[ "$1 $2" != "iam simulate-principal-policy" ]]; then echo "unexpected: $*" >&2; exit 99; fi
action=""; resource=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --action-names) action="$2"; shift 2 ;;
    --resource-arns) resource="$2"; shift 2 ;;
    *) shift ;;
  esac
done
decision="implicitDeny"
case "$action $resource" in
  "s3:PutObject arn:aws:s3:::BUCKET/learned/"*) decision="allowed" ;;
  "bedrock:StartIngestionJob KB") decision="allowed" ;;
esac
if [[ -n "$FAKE_OVERRIDE" && "$action" == "$FAKE_OVERRIDE" ]]; then decision="allowed"; fi
echo "$decision"
""".replace("BUCKET", BUCKET).replace("KB", KB)


def setup(tmp_path: Path, *, account: str = "255834079310", override: str = "") -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "aws"
    exe.write_text(FAKE_AWS)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path),
            "FAKE_LOG": str(tmp_path / "calls.log"), "FAKE_ACCOUNT": account,
            "FAKE_OVERRIDE": override}


def run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["/bin/bash", str(SCRIPT)], env=env,  # noqa: S603
                          capture_output=True, text=True, check=False)


def calls(env: dict[str, str]) -> list[str]:
    log = Path(env["FAKE_LOG"])
    return log.read_text().splitlines() if log.exists() else []


def test_the_expected_policy_passes_every_check(tmp_path: Path) -> None:
    env = setup(tmp_path)
    result = run(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS") == 10 and "FAIL" not in result.stdout
    assert "all checks match" in result.stdout


def test_it_checks_both_grants_and_the_denials_that_matter(tmp_path: Path) -> None:
    out = run(setup(tmp_path)).stdout
    assert re.search(rf"PASS\s+allowed\s+s3:PutObject\s+arn:aws:s3:::{BUCKET}/learned/probe\.md",
                     out)
    assert re.search(r"PASS\s+allowed\s+bedrock:StartIngestionJob\s+\S+VIBAT27SDH", out)
    for denied in ["s3:GetObject", "s3:DeleteObject", "s3:ListBucket", "bedrock:Retrieve",
                   "bedrock:DeleteKnowledgeBase", f"{BUCKET}/postmortems/probe.md",
                   "knowledge-base/NOTTHISONE1"]:
        assert re.search(rf"PASS\s+denied\s+.*{re.escape(denied)}", out), denied


@pytest.mark.parametrize("action", ["s3:DeleteObject", "s3:GetObject", "bedrock:Retrieve"])
def test_a_widened_policy_fails(tmp_path: Path, action: str) -> None:
    result = run(setup(tmp_path, override=action))
    assert result.returncode == 1
    assert f"FAIL  expected denied, got allowed (allowed)  {action}" in result.stdout
    assert "check(s) did not match" in result.stderr


def test_wrong_account_stops_before_any_simulation(tmp_path: Path) -> None:
    env = setup(tmp_path, account="111122223333")
    result = run(env)
    assert result.returncode == 1 and "refusing to run" in result.stderr
    assert calls(env) == [c for c in calls(env) if "sts get-caller-identity" in c]


def test_it_only_reads(tmp_path: Path) -> None:
    env = setup(tmp_path)
    run(env)
    for call in calls(env):
        assert call.startswith(("aws sts get-caller-identity", "aws iam simulate-principal-policy"))
    assert all("--region eu-west-1" in c for c in calls(env))


def test_script_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)
