"""memory-and-ui TASK-06: the static UI at /ui and the incident list."""

from __future__ import annotations

import re
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kiroween_orchestrator.api import UI_DIR, create
from kiroween_orchestrator.service import IncidentService
from kiroween_shared.models import Incident, IncidentRecord

T0 = datetime(2026, 11, 17, 15, 0, tzinfo=UTC)
CHARACTERS = {"necromancer": "orchestrator", "vampire": "cpu-memory", "spider": "network",
              "zombie": "deploy-rollout", "ghost": "storage", "archivist": "incident-memory"}


async def never(*args):  # type: ignore[no-untyped-def]
    raise AssertionError("not used")


def service(n: int = 0) -> IncidentService:
    svc = IncidentService(classify=never, delegate=never)
    for k in range(n):
        incident = Incident.model_validate({
            "incident_id": f"i-{k}", "source": "watcher", "reason": "OOMKilled",
            "namespace": "chaos-oom", "workload": {"kind": "Deployment", "name": f"w{k}"},
            "observed_at": T0})
        at = T0 + timedelta(seconds=k)
        svc.store.put(IncidentRecord(incident=incident, created_at=at, updated_at=at))
    return svc


def test_ui_is_served_with_a_strict_csp() -> None:
    with TestClient(create(service())) as client:
        page = client.get("/ui/")
        script = client.get("/ui/app.js")
        art = client.get("/ui/art/ghost.svg")
        api = client.get("/incidents")
    assert page.status_code == 200 and "Kiroween Incident Crypt" in page.text
    csp = page.headers["content-security-policy"]
    for directive in ["default-src 'self'", "script-src 'self'", "style-src 'self'",
                      "frame-ancestors 'none'", "object-src 'none'"]:
        assert directive in csp
    assert "unsafe" not in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    assert script.status_code == 200 and "javascript" in script.headers["content-type"]
    assert art.status_code == 200 and art.headers["content-type"].startswith("image/svg")
    assert "content-security-policy" not in api.headers


def test_incident_list_is_newest_first_and_limited() -> None:
    with TestClient(create(service(5))) as client:
        ids = [r["incident"]["incident_id"] for r in client.get("/incidents?limit=3").json()]
        assert client.get("/incidents?limit=0").status_code == 422
        assert client.get("/incidents?limit=201").status_code == 422
    assert ids == ["i-4", "i-3", "i-2"]


def test_path_traversal_outside_the_ui_is_refused() -> None:
    with TestClient(create(service())) as client:
        assert client.get("/ui/../api.py").status_code == 404
        assert client.get("/ui/%2e%2e/api.py").status_code == 404


# --- static checks on the UI source (REQ-UI-03, UI-04) ---------------------------------------

JS = (UI_DIR / "app.js").read_text()
HTML = (UI_DIR / "index.html").read_text()
CSS = (UI_DIR / "style.css").read_text()


@pytest.mark.parametrize("pattern", [r"\.innerHTML", r"\.outerHTML", r"insertAdjacentHTML",
                                     r"document\.write", r"\beval\(", r"new Function",
                                     r"setTimeout\(\s*['\"]", r"setInterval\(\s*['\"]"])
def test_script_never_parses_strings_as_html_or_code(pattern: str) -> None:
    assert not re.search(pattern, JS)


def test_no_inline_script_or_style_and_no_external_assets() -> None:
    assert re.findall(r"<script[^>]*>", HTML) == ['<script src="app.js">']
    assert "<style" not in HTML and not re.search(r"\sstyle=", HTML)
    assert not re.search(r"\son[a-z]+\s*=", HTML)  # no inline event handlers
    for text in (HTML, CSS, JS, *(p.read_text() for p in (UI_DIR / "art").glob("*.svg"))):
        assert not re.search(r"(?:src|href)=\"(?:https?:)?//", text)
        assert "@import" not in text and "url(" not in text
        assert "fonts.googleapis" not in text


def test_external_links_are_only_github_pull_requests() -> None:
    assert 'url.startsWith("https://github.com/")' in JS
    assert 'rel: "noopener noreferrer"' in JS


def test_decisions_are_sent_as_json() -> None:
    assert '"Content-Type": "application/json"' in JS
    assert "encodeURIComponent(record.incident.incident_id)" in JS


@pytest.mark.parametrize("character,agent", sorted(CHARACTERS.items()))
def test_the_crew_is_present_with_original_art(character: str, agent: str) -> None:
    svg = UI_DIR / "art" / f"{character}.svg"
    text = svg.read_text()
    assert text.startswith("<svg") and "<title" in text
    assert "<script" not in text and "href" not in text
    assert f'data-agent="{agent}"' in HTML and f"art/{character}.svg" in HTML
    assert character.capitalize() in HTML


def test_ui_files_ship_in_the_wheel(tmp_path: Path) -> None:
    import shutil
    import subprocess

    uv = shutil.which("uv") or "/opt/homebrew/bin/uv"
    if not Path(uv).exists():
        pytest.skip("uv is not installed")
    pkg = Path(__file__).parents[1]
    result = subprocess.run(  # noqa: S603
        [uv, "build", "--wheel", "-q", "-o", str(tmp_path), str(pkg)],
        capture_output=True, text=True, check=False)
    if result.returncode != 0:
        pytest.skip(f"cannot build a wheel here: {result.stderr[-200:]}")
    (wheel,) = tmp_path.glob("*.whl")
    names = set(zipfile.ZipFile(wheel).namelist())
    for f in ["index.html", "app.js", "style.css", *(f"art/{c}.svg" for c in CHARACTERS)]:
        assert f"kiroween_orchestrator/ui/{f}" in names
