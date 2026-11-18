#!/usr/bin/env python3
"""Build the static replay site (replay REQ-BD-01 to BD-06).

    python3 tools/replay/build.py --out _site

The site is the repository's real UI (index.html, style.css, app.js, art/) plus the replay
engine and the saved incident records. Nothing is written unless every record validates
and every anchor in index.html is found exactly once. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI_DIR = Path("agents/orchestrator/src/kiroween_orchestrator/ui")
REPLAY_DIR = Path("tools/replay")
RESULTS_DIR = Path("docs/demo-results")

# file, story shown in the replay bar. The order is the order of the feed (REQ-RM-04).
RECORDS = [
    ("netpol-second-run-real.json",
     "NetworkPolicy blocks a client, second run: the postmortem learned from the first run is "
     "found, and pull request #3 follows."),
    ("netpol-first-run-real.json",
     "NetworkPolicy blocks a client, first run: approved, pull request #2, and a postmortem "
     "is learned."),
    ("badimage-real.json",
     "A Deployment is updated to an image tag that does not exist. Left unapproved."),
    ("pvc-real.json",
     "A PersistentVolumeClaim names a StorageClass that does not exist. Left unapproved."),
    ("grafana-oom-real.json",
     "Grafana is killed for memory on the same cluster, submitted by hand. Left unapproved."),
]

BANNER = ("Replay of real runs on Amazon EKS -- the live system has no public endpoint "
          "by design")
CSP = ("default-src 'none'; img-src 'self'; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'")

CATEGORIES = {"cpu_memory", "network", "deploy_rollout", "storage"}
STATUSES = {"awaiting_approval", "approved", "rejected"}
TOP_KEYS = ("incident", "status", "classification", "investigation", "usage_by_agent",
            "similar_incidents", "approval", "created_at", "updated_at")

# Anything that looks like a credential keeps a record out of the site (REQ-BD-06).
CREDENTIALS = [
    ("an AWS access key ID", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a GitHub token",
     re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("an AWS secret key", re.compile(r"aws_secret_access_key", re.IGNORECASE)),
]

# (anchor, replacement). Each anchor must occur exactly once in the UI's index.html.
INSERTIONS = [
    ("<title>Kiroween Incident Crypt</title>",
     "<title>Kiroween Incident Crypt (replay)</title>"),
    ('<meta charset="utf-8">',
     '<meta charset="utf-8">\n  <meta http-equiv="Content-Security-Policy" content="' + CSP + '">'),
    ('<link rel="stylesheet" href="style.css">',
     '<link rel="stylesheet" href="style.css">\n  <link rel="stylesheet" href="replay.css">'),
    ("<body>",
     '<body>\n  <div class="replay-banner" role="note">' + BANNER + '</div>\n'
     '  <div id="replay-bar" class="replay-bar" aria-label="Replay controls"></div>'),
    ('<script src="app.js"></script>',
     '<script src="replay.js"></script>\n  <script src="app.js"></script>'),
]


class BuildError(Exception):
    """A reason the site was not built. Nothing has been written."""


def read_record(root: Path, name: str) -> tuple[str, dict]:
    path = root / RESULTS_DIR / name
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BuildError(f"{name}: cannot read it ({exc.strerror or exc})") from exc
    try:
        record = json.loads(text)
    except ValueError as exc:
        raise BuildError(f"{name}: not valid JSON ({exc})") from exc
    for label, pattern in CREDENTIALS:
        if pattern.search(text):
            raise BuildError(f"{name}: contains what looks like {label}; not published")
    return text, record


def need(record: dict, name: str, *path: str) -> object:
    value: object = record
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise BuildError(f"{name}: missing {'.'.join(path)}")
        value = value[key]
    return value


def validate(name: str, record: object) -> str:
    """Check one record and return its incident id."""
    if not isinstance(record, dict):
        raise BuildError(f"{name}: not a JSON object")
    for key in TOP_KEYS:
        if key not in record:
            raise BuildError(f"{name}: missing {key}")
    incident_id = need(record, name, "incident", "incident_id")
    if not isinstance(incident_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", incident_id):
        raise BuildError(f"{name}: bad incident_id")
    for path in (("incident", "reason"), ("incident", "namespace"),
                 ("incident", "workload", "kind"), ("incident", "workload", "name"),
                 ("classification", "rationale"), ("investigation", "outcome"),
                 ("investigation", "diagnosis", "root_cause")):
        need(record, name, *path)
    if need(record, name, "classification", "category") not in CATEGORIES:
        raise BuildError(f"{name}: unknown category")
    if record["status"] not in STATUSES:
        raise BuildError(f"{name}: status {record['status']!r} is not a settled status")
    if not isinstance(need(record, name, "investigation", "diagnosis", "evidence"), list):
        raise BuildError(f"{name}: evidence is not a list")
    if not isinstance(record["similar_incidents"], list):
        raise BuildError(f"{name}: similar_incidents is not a list")
    approval = record["approval"]
    if record["status"] == "approved":
        if not isinstance(approval, dict):
            raise BuildError(f"{name}: approved without an approval")
        pull_request = approval.get("pull_request")
        url = pull_request.get("url") if isinstance(pull_request, dict) else None
        if url is not None and not str(url).startswith("https://github.com/"):
            raise BuildError(f"{name}: the pull request is not a github.com address")
    elif approval is not None:
        raise BuildError(f"{name}: has an approval but status {record['status']}")
    return incident_id


def assemble_index(html: str) -> str:
    for anchor, _ in INSERTIONS:
        count = html.count(anchor)
        if count != 1:
            raise BuildError(f"index.html: expected one {anchor!r}, found {count}")
    for anchor, replacement in INSERTIONS:
        html = html.replace(anchor, replacement, 1)
    return html


def build(out: Path, root: Path = ROOT) -> list[str]:
    """Validate everything, then write the site. Returns the relative paths written."""
    ui = root / UI_DIR
    replay = root / REPLAY_DIR
    loaded: list[tuple[str, str, str]] = []  # file, id, story
    seen: set[str] = set()
    for name, story in RECORDS:
        _, record = read_record(root, name)
        incident_id = validate(name, record)
        if incident_id in seen:
            raise BuildError(f"{name}: duplicate incident_id {incident_id}")
        seen.add(incident_id)
        loaded.append((name, incident_id, story))

    try:
        index = assemble_index((ui / "index.html").read_text(encoding="utf-8"))
        sources = {
            "style.css": (ui / "style.css").read_bytes(),
            "app.js": (ui / "app.js").read_bytes(),
            "replay.js": (replay / "replay.js").read_bytes(),
            "replay.css": (replay / "replay.css").read_bytes(),
        }
    except OSError as exc:
        raise BuildError(f"cannot read {exc.filename}: {exc.strerror}") from exc
    art = sorted((ui / "art").glob("*.svg"))
    if not art:
        raise BuildError("no art found in the UI directory")

    manifest = {"incidents": [{"id": incident_id, "file": name, "story": story}
                              for name, incident_id, story in loaded]}

    written: list[str] = []
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(index, encoding="utf-8")
    written.append("index.html")
    for name, data in sources.items():
        (out / name).write_bytes(data)
        written.append(name)
    (out / "art").mkdir(exist_ok=True)
    for svg in art:
        shutil.copyfile(svg, out / "art" / svg.name)
        written.append(f"art/{svg.name}")
    (out / "data").mkdir(exist_ok=True)
    for name, _, _ in loaded:
        shutil.copyfile(root / RESULTS_DIR / name, out / "data" / name)  # byte for byte
        written.append(f"data/{name}")
    (out / "data" / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                                encoding="utf-8")
    written.append("data/manifest.json")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the static replay site.")
    parser.add_argument("--out", required=True, type=Path, help="directory to write the site to")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        written = build(args.out, args.root)
    except BuildError as exc:
        print(f"replay build failed: {exc}", file=sys.stderr)
        return 1
    print(f"replay site written to {args.out}: {len(written)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
