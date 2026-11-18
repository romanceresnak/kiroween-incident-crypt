"""scenario-lab TASK-08: the Lab page is served by the UI mount, behind the login, with the
same strict policy (REQ-UI-01, UI-05, AU-12). Normal tests."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from kiroween_orchestrator.api import UI_DIR, create
from kiroween_orchestrator.auth import Auth, AuthConfig
from kiroween_orchestrator.service import IncidentService

USER, PASSWORD = "judge", "correct horse battery staple"  # noqa: S105


async def never(*args):  # type: ignore[no-untyped-def]
    raise AssertionError("not used")


def client() -> TestClient:
    service = IncidentService(classify=never, delegate=never)
    auth = Auth(AuthConfig(username=USER, password=PASSWORD))
    return TestClient(create(service, auth), base_url="https://crypt.example.cloudfront.net")


def test_the_lab_page_and_its_script_are_served_after_sign_in_with_the_ui_policy() -> None:
    with client() as web:
        assert web.get("/ui/lab.html", follow_redirects=False).status_code == 302
        assert web.get("/ui/lab.js", follow_redirects=False).status_code == 302
        web.post("/auth/login", json={"username": USER, "password": PASSWORD})
        page, script = web.get("/ui/lab.html"), web.get("/ui/lab.js")
    assert page.status_code == 200 and "Scenario Lab" in page.text
    assert script.status_code == 200 and "javascript" in script.headers["content-type"]
    csp = page.headers["content-security-policy"]
    for directive in ("default-src 'self'", "script-src 'self'", "style-src 'self'",
                      "frame-ancestors 'none'", "object-src 'none'", "connect-src 'self'"):
        assert directive in csp
    assert "unsafe" not in csp and page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["cache-control"] == "no-store"


def test_the_main_page_links_to_the_lab_and_the_lab_links_back() -> None:
    index = (UI_DIR / "index.html").read_text()
    lab = (UI_DIR / "lab.html").read_text()
    assert 'href="lab.html"' in index and 'href="index.html"' in lab
    assert 'id="logout"' in index and 'id="logout"' in lab
    assert '<script src="lab.js"></script>' in lab


def test_every_css_class_the_lab_uses_is_styled() -> None:
    css = (UI_DIR / "style.css").read_text()
    used: set[str] = set()
    for source in ("lab.html", "lab.js"):
        text = (UI_DIR / source).read_text()
        for match in re.finditer(r'class[=:]\s*"([^"]+)"', text):
            used.update(match.group(1).split())
    # Classes set only to mark a state or an element for the script, not for style.
    script_only = {"lab-incident-link", "lab-run", "lab-crew", "lab-signals", "linklike",
                   "ok", "down", "lab-intro", "lab-status", "lab"}
    for name in sorted(used - script_only):
        assert f".{name}" in css, f"{name} is used but not styled"
    for name in ("topnav", "lab-grid", "lab-card", "lab-card-head", "lab-run", "linklike"):
        assert f".{name}" in css, name


def test_the_pages_share_one_stylesheet_and_no_external_resource() -> None:
    for name in ("index.html", "lab.html"):
        text = (UI_DIR / name).read_text()
        assert 'href="style.css"' in text
        assert not re.search(r'(?:src|href)="(?:https?:)?//', text), name
    assert not re.search(r"url\(\s*['\"]?https?:", (UI_DIR / "style.css").read_text())


def test_the_replay_build_hides_the_navigation_that_needs_a_session() -> None:
    from pathlib import Path

    replay_css = (Path(__file__).parents[3] / "tools" / "replay" / "replay.css").read_text()
    assert ".topnav { display: none; }" in replay_css
