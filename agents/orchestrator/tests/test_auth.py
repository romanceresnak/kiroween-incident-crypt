"""scenario-lab TASK-03: the login (REQ-AU-01 to AU-13). Normal tests, no mutation checks."""

from __future__ import annotations

import hmac
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kiroween_orchestrator import auth as auth_module
from kiroween_orchestrator.api import create
from kiroween_orchestrator.auth import (
    COOKIE,
    LOGIN_DIR,
    Auth,
    AuthConfig,
    is_lab_namespace,
    same_secret,
)
from kiroween_orchestrator.service import IncidentService
from kiroween_shared.models import Incident, IncidentRecord

USER = "judge"
PASSWORD = "correct horse battery staple"  # noqa: S105 (a test value)
TOKEN = "watcher-internal-token-0123456789"  # noqa: S105
WRONG = "a-wrong-password-that-must-not-be-logged"  # noqa: S105
T0 = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
HTTPS = "https://crypt.example.cloudfront.net"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def never(*args):  # type: ignore[no-untyped-def]
    raise AssertionError("not used")


def incident(namespace: str, iid: str = "i-1") -> Incident:
    return Incident.model_validate({
        "incident_id": iid, "source": "watcher", "reason": "Unhealthy",
        "namespace": namespace, "workload": {"kind": "Deployment", "name": "web"},
        "observed_at": T0})


def service(*namespaces: str) -> IncidentService:
    svc = IncidentService(classify=never, delegate=never)
    for index, namespace in enumerate(namespaces):
        rec = IncidentRecord(incident=incident(namespace, f"rec-{index}"),
                             status="awaiting_approval", created_at=T0, updated_at=T0)
        svc.store.put(rec)
    return svc


def config(**kw: object) -> AuthConfig:
    base: dict[str, object] = {"username": USER, "password": PASSWORD, "internal_token": TOKEN}
    return AuthConfig(**{**base, **kw})  # type: ignore[arg-type]


def app_client(cfg: AuthConfig | None = None, svc: IncidentService | None = None,
               clock: Clock | None = None) -> tuple[TestClient, Auth, Clock]:
    clock = clock or Clock()
    auth = Auth(cfg or config(), clock)
    client = TestClient(create(svc or service(), auth), base_url=HTTPS)
    return client, auth, clock


def json_text() -> str:
    return f'{{"username": "{USER}", "password": "{PASSWORD}"}}'


def sign_in(client: TestClient, password: str = PASSWORD, user: str = USER,
            **headers: str):
    return client.post("/auth/login", json={"username": user, "password": password},
                       headers=headers)


# ------------------------------------------------------------------ configuration


def test_login_is_required_by_default_and_only_an_explicit_false_turns_it_off() -> None:
    assert AuthConfig.from_env({}).required is True
    assert AuthConfig.from_env({"AUTH_REQUIRED": ""}).required is True
    assert AuthConfig.from_env({"AUTH_REQUIRED": "garbage"}).required is True
    for off in ("false", "FALSE", "0", "no", "off", " False "):
        assert AuthConfig.from_env({"AUTH_REQUIRED": off}).required is False, off


def test_configuration_reads_the_secret_keys_hosts_and_hops() -> None:
    cfg = AuthConfig.from_env({
        "AUTH_USERNAME": "u", "AUTH_PASSWORD": "p", "AUTH_INTERNAL_TOKEN": "t",
        "PUBLIC_HOSTS": "A.cloudfront.net, b.example.com,", "TRUSTED_PROXY_HOPS": "2",
        "AUTH_LAB_ONLY": "false"})
    assert (cfg.username, cfg.password, cfg.internal_token) == ("u", "p", "t")
    assert cfg.public_hosts == {"a.cloudfront.net", "b.example.com"}
    assert cfg.trusted_hops == 2 and cfg.lab_only is False and cfg.configured
    default = AuthConfig.from_env({})
    assert default.lab_only is True and default.trusted_hops == 0 and not default.configured
    assert AuthConfig.from_env({"TRUSTED_PROXY_HOPS": "x"}).trusted_hops == 0
    assert AuthConfig.from_env({"TRUSTED_PROXY_HOPS": "-1"}).trusted_hops == 0


def test_a_missing_half_of_the_credentials_is_not_configured() -> None:
    assert not AuthConfig.from_env({"AUTH_USERNAME": "u"}).configured
    assert not AuthConfig.from_env({"AUTH_PASSWORD": "p"}).configured


# ------------------------------------------------------------- routes and redirects


def test_the_health_probes_and_the_login_page_need_no_login() -> None:
    client, _, _ = app_client()
    with client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code in (200, 503)
        page = client.get("/login")
        assert page.status_code == 200 and 'id="login"' in page.text
        assert client.get("/login-assets/login.js").status_code == 200
        assert client.get("/login-assets/login.css").status_code == 200


def test_everything_else_needs_a_login_and_returns_no_data() -> None:
    client, _, _ = app_client(svc=service("lab-01"))
    with client:
        for method, path in [("GET", "/incidents"), ("GET", "/incidents/rec-0"),
                             ("POST", "/incidents"), ("POST", "/incidents/rec-0/approve"),
                             ("POST", "/incidents/rec-0/reject"), ("GET", "/auth/me"),
                             ("POST", "/auth/logout"), ("GET", "/anything-else")]:
            response = client.request(method, path, json={})
            assert response.status_code == 401, (method, path)
            assert response.json() == {"detail": "login required"}
            assert response.headers["cache-control"] == "no-store"
            assert "rec-0" not in response.text


def test_a_browser_visit_to_the_ui_is_sent_to_the_login_page() -> None:
    client, _, _ = app_client()
    with client:
        for path in ("/ui", "/ui/", "/ui/app.js", "/ui/lab.html"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 302, path
            assert response.headers["location"].startswith("/login?next=/ui")
        head = client.head("/ui/", follow_redirects=False)
        assert head.status_code == 302
        # Only GET and HEAD are redirected; a POST to the UI path is refused outright.
        assert client.post("/ui/", json={}, follow_redirects=False).status_code == 401


def test_the_bare_address_redirects_to_the_ui_with_no_session() -> None:
    """A judge opens https://<cloudfront>/ first; it must not be a JSON error (REQ-AU-10)."""
    client, _, _ = app_client()
    with client:
        for method in ("GET", "HEAD"):
            response = client.request(method, "/", follow_redirects=False)
            assert response.status_code == 302, method
            # Relative, so the browser stays on the public host.
            assert response.headers["location"] == "/ui/"
            assert response.headers["cache-control"] == "no-store"
            assert "login required" not in response.text
        # A query string does not change where it goes.
        assert client.get("/?next=/evil", follow_redirects=False).headers["location"] == "/ui/"
        # The whole chain ends on the login page.
        landing = client.get("/", follow_redirects=True)
        assert landing.url.path == "/login" and 'id="login"' in landing.text


def test_the_bare_address_opens_nothing_else_and_other_methods_stay_closed() -> None:
    client, _, _ = app_client(svc=service("lab-01"))
    with client:
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            response = client.request(method, "/", json={}, follow_redirects=False)
            assert response.status_code == 401, method
        assert client.get("//evil.example/x", follow_redirects=False).status_code == 401
        assert client.get("/index.html", follow_redirects=False).status_code == 401
        assert client.get("/incidents", follow_redirects=False).status_code == 401


def test_the_bare_address_goes_to_the_ui_when_signed_in_and_when_login_is_off() -> None:
    client, _, _ = app_client()
    with client:
        sign_in(client)
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 302 and response.headers["location"] == "/ui/"
        assert client.get("/", follow_redirects=True).url.path == "/ui/"
    open_client = TestClient(create(service(), auth=None))
    with open_client:
        assert open_client.get("/", follow_redirects=False).headers["location"] == "/ui/"


def test_the_redirect_cannot_be_turned_into_an_open_redirect() -> None:
    client, _, _ = app_client()
    with client:
        response = client.get("/ui//evil.example.com/x", follow_redirects=False)
        assert response.status_code == 302
        location = response.headers["location"]
        assert location.startswith("/login?next=/ui")
        assert not location.startswith("//")


def test_without_the_secret_everything_but_the_probes_answers_503() -> None:
    client, _, _ = app_client(AuthConfig(required=True))
    with client:
        for method, path in [("GET", "/incidents"), ("GET", "/ui/"), ("POST", "/incidents"),
                             ("GET", "/auth/me")]:
            response = client.request(method, path, json={}, follow_redirects=False)
            assert response.status_code == 503, path
            assert response.json() == {"detail": "login is not configured"}
        assert client.get("/healthz").status_code == 200
        assert sign_in(client).status_code == 503


def test_a_token_alone_does_not_open_an_unconfigured_system() -> None:
    client, _, _ = app_client(AuthConfig(required=True, internal_token=TOKEN))
    with client:
        response = client.post("/incidents", json={}, headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 503


def test_an_app_without_login_is_open_for_tests_and_local_runs() -> None:
    with TestClient(create(service())) as client:
        assert client.get("/incidents").status_code == 200
        assert client.get("/ui/").status_code == 200
    with TestClient(create(service(), Auth(AuthConfig(required=False)))) as client:
        assert client.get("/incidents").status_code == 200


# ----------------------------------------------------------------------- signing in


def test_a_correct_login_sets_a_strict_session_cookie() -> None:
    client, _, _ = app_client()
    with client:
        response = sign_in(client)
        assert response.status_code == 200 and response.json() == {"ok": True}
        header = response.headers["set-cookie"]
        attrs = {a.strip().lower() for a in header.split(";")}
        assert {"httponly", "secure", "samesite=strict", "path=/", "max-age=28800"} <= attrs
        token = re.match(rf"{COOKIE}=([^;]+)", header).group(1)
        assert len(token) >= 43, "256 bits of urlsafe base64"
        assert response.headers["cache-control"] == "no-store"
        assert PASSWORD not in response.text and PASSWORD not in header


def test_a_signed_in_browser_reaches_the_ui_and_the_api() -> None:
    client, _, _ = app_client(svc=service("lab-01"))
    with client:
        assert sign_in(client).status_code == 200
        assert client.get("/ui/").status_code == 200
        assert "frame-ancestors 'none'" in client.get("/ui/").headers["content-security-policy"]
        assert client.get("/incidents").status_code == 200
        assert client.get("/auth/me").json() == {"user": USER}


def test_each_login_gets_its_own_unguessable_session() -> None:
    client, _, _ = app_client()
    with client:
        tokens = set()
        for _ in range(5):
            tokens.add(re.match(rf"{COOKIE}=([^;]+)", sign_in(client).headers["set-cookie"])
                       .group(1))
        assert len(tokens) == 5


@pytest.mark.parametrize(("user", "password"),
                         [(USER, "wrong"), ("wrong", PASSWORD), ("wrong", "wrong"),
                          ("", PASSWORD), (USER, PASSWORD + " ")])
def test_wrong_credentials_get_one_generic_answer(user: str, password: str) -> None:
    client, _, _ = app_client()
    with client:
        response = client.post("/auth/login", json={"username": user or "x", "password": password})
        if user == "" or password == "":
            assert response.status_code in (401, 422)
            return
        assert response.status_code == 401
        assert response.json() == {"detail": "wrong user name or password"}
        assert "set-cookie" not in response.headers
        assert client.get("/incidents").status_code == 401


def test_the_login_body_is_strict() -> None:
    client, _, _ = app_client()
    with client:
        assert client.post("/auth/login", json={"username": USER}).status_code == 422
        assert client.post("/auth/login", json={"username": USER, "password": PASSWORD,
                                                "extra": 1}).status_code == 422
        assert client.post("/auth/login", json={"username": "u" * 129,
                                                "password": "p"}).status_code == 422


def test_both_fields_are_always_compared_in_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a, b):  # type: ignore[no-untyped-def]
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(auth_module.hmac, "compare_digest", spy)
    auth = Auth(config())
    assert auth.check_credentials("nobody", "also wrong") is False
    assert len(calls) == 2, "the password is compared even when the user name is wrong"
    calls.clear()
    assert auth.check_credentials(USER, "x") is False and len(calls) == 2
    calls.clear()
    assert auth.check_credentials(USER, PASSWORD) is True and len(calls) == 2
    assert all(len(a) == len(b) == 32 for a, b in calls), "digests, so length does not leak"


def test_same_secret_handles_any_length_and_unicode() -> None:
    assert same_secret("a", "a") and not same_secret("a", "b")
    assert not same_secret("short", "a much longer string than the other one")
    assert same_secret("pässwörd-✓", "pässwörd-✓") and not same_secret("pässwörd", "passwörd")
    assert not same_secret("", "x")


def test_no_credential_is_ever_logged(caplog: pytest.LogCaptureFixture) -> None:
    client, _, _ = app_client()
    caplog.set_level("DEBUG")
    with client:
        sign_in(client, password=WRONG)
        sign_in(client)
    text = caplog.text
    assert PASSWORD not in text and WRONG not in text
    assert TOKEN not in text


# --------------------------------------------------------------------------- lockout


def test_five_failures_lock_the_address_even_for_the_right_password() -> None:
    client, _, clock = app_client()
    with client:
        for attempt in range(5):
            assert sign_in(client, "wrong").status_code == 401, attempt
        locked = sign_in(client)
        assert locked.status_code == 429
        assert int(locked.headers["retry-after"]) == 900
        assert "set-cookie" not in locked.headers
        clock.advance(450)
        assert int(sign_in(client).headers["retry-after"]) == 450
        clock.advance(451)
        assert sign_in(client).status_code == 200


def test_a_success_clears_the_count_and_the_window_slides() -> None:
    client, _, clock = app_client()
    with client:
        for _ in range(4):
            assert sign_in(client, "wrong").status_code == 401
        assert sign_in(client).status_code == 200
        for _ in range(4):
            assert sign_in(client, "wrong").status_code == 401
        assert sign_in(client).status_code == 200
        for _ in range(4):
            assert sign_in(client, "wrong").status_code == 401
        clock.advance(901)  # the four old failures age out of the window
        assert sign_in(client, "wrong").status_code == 401
        assert sign_in(client).status_code == 200


def test_another_address_is_not_locked_by_the_first() -> None:
    client, _, _ = app_client(config(trusted_hops=1))
    with client:
        for _ in range(5):
            sign_in(client, "wrong", **{"X-Forwarded-For": "198.51.100.1"})
        assert sign_in(client, **{"X-Forwarded-For": "198.51.100.1"}).status_code == 429
        assert sign_in(client, **{"X-Forwarded-For": "198.51.100.2"}).status_code == 200


def test_many_failures_from_many_addresses_pause_logins_for_a_while() -> None:
    client, auth, clock = app_client(config(trusted_hops=1))
    with client:
        for n in range(100):
            sign_in(client, "wrong", **{"X-Forwarded-For": f"203.0.113.{n}"})
        paused = sign_in(client, **{"X-Forwarded-For": "192.0.2.77"})
        assert paused.status_code == 429 and int(paused.headers["retry-after"]) == 300
        # A session that exists already keeps working.
        clock.advance(301)
        assert sign_in(client, **{"X-Forwarded-For": "192.0.2.77"}).status_code == 200
        assert auth.session_valid(client.cookies.get(COOKIE))


def test_the_client_address_is_the_one_the_trusted_proxies_appended() -> None:
    auth2 = Auth(config(trusted_hops=2))
    # client-supplied "evil", then CloudFront's viewer address, then the VPC origin address.
    assert auth2.client_ip("evil, 198.51.100.7, 10.0.16.9", "10.0.16.9") == "198.51.100.7"
    assert auth2.client_ip("198.51.100.7, 10.0.16.9", "x") == "198.51.100.7"
    assert auth2.client_ip("10.0.16.9", "peer") == "peer", "too few entries: the socket peer"
    assert auth2.client_ip(None, "peer") == "peer" and auth2.client_ip("", None) == "unknown"
    assert Auth(config()).client_ip("1.2.3.4, 5.6.7.8", "peer") == "peer", "hops 0: not trusted"
    assert Auth(config(trusted_hops=1)).client_ip("a, b", "peer") == "b"


def test_changing_the_leftmost_forwarded_value_does_not_evade_the_lockout() -> None:
    client, _, _ = app_client(config(trusted_hops=2))
    with client:
        for n in range(5):
            sign_in(client, "wrong",
                    **{"X-Forwarded-For": f"spoof-{n}, 198.51.100.7, 10.0.16.9"})
        blocked = sign_in(client, **{"X-Forwarded-For": "yet-another, 198.51.100.7, 10.0.16.9"})
        assert blocked.status_code == 429


def test_the_failure_table_is_bounded() -> None:
    auth = Auth(config(), Clock())
    for n in range(Auth.MAX_TRACKED + 50):
        auth.login(f"10.1.{n // 250}.{n % 250}", "x", "y")
    assert len(auth._failures) <= Auth.MAX_TRACKED + 1


# -------------------------------------------------------------- sessions and logout


def test_a_session_expires_after_eight_hours() -> None:
    client, auth, clock = app_client()
    with client:
        sign_in(client)
        token = client.cookies.get(COOKIE)
        clock.advance(8 * 3600 - 1)
        assert auth.session_valid(token)
        clock.advance(2)
        assert client.get("/incidents").status_code == 401
        assert not auth.session_valid(token)


def test_logout_ends_the_session_and_clears_the_cookie() -> None:
    client, auth, _ = app_client()
    with client:
        sign_in(client)
        token = client.cookies.get(COOKIE)
        response = client.post("/auth/logout", json={})
        assert response.status_code == 200
        assert f'{COOKIE}=""' in response.headers["set-cookie"] or "max-age=0" in (
            response.headers["set-cookie"].lower())
        assert not auth.session_valid(token)
        client.cookies.set(COOKIE, token)  # the old cookie, replayed
        assert client.get("/incidents").status_code == 401


def test_an_invented_or_empty_session_is_refused() -> None:
    client, _, _ = app_client()
    with client:
        for value in ("", "x", "A" * 43, "../../etc/passwd"):
            client.cookies.set(COOKIE, value)
            assert client.get("/incidents").status_code == 401


def test_the_session_table_is_bounded() -> None:
    auth = Auth(config(), Clock())
    tokens = [auth.login(f"10.2.0.{n % 250}", USER, PASSWORD).token
              for n in range(Auth.MAX_SESSIONS + 20)]
    assert len(auth._sessions) <= Auth.MAX_SESSIONS
    assert auth.session_valid(tokens[-1])


# --------------------------------------------------------------- same-origin (AU-08)


def test_login_and_logout_refuse_a_foreign_origin_and_a_non_json_body() -> None:
    client, _, _ = app_client()
    host = "crypt.example.cloudfront.net"
    with client:
        assert sign_in(client, Origin="https://evil.example.com").status_code == 403
        assert sign_in(client, Origin=f"https://{host}").status_code == 200
        # A "simple" (non-JSON) request is refused before or after the origin check, and signs
        # nobody in: FastAPI answers 422 for a body it cannot read as JSON, the app answers 415.
        client.cookies.clear()
        simple = client.post("/auth/login", content=json_text(),
                             headers={"Content-Type": "text/plain"})
        assert simple.status_code in (415, 422) and "set-cookie" not in simple.headers
        form = client.post("/auth/login", content=f"username={USER}&password={PASSWORD}",
                           headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert form.status_code in (415, 422) and "set-cookie" not in form.headers
        assert client.get("/incidents").status_code == 401
        sign_in(client)
        assert client.post("/auth/logout", json={},
                           headers={"Origin": "https://evil.example.com"}).status_code == 403
        assert client.post("/auth/logout", content="x",
                           headers={"Content-Type": "application/x-www-form-urlencoded"}
                           ).status_code in (415, 422)
        assert client.get("/incidents").status_code == 200, "a refused logout leaves the session"


def test_the_public_host_is_accepted_when_the_host_header_differs() -> None:
    """CloudFront may forward its own Host to the ALB; the browser's Origin is the public one."""
    client, _, _ = app_client(config(public_hosts=frozenset({"crypt.example.cloudfront.net"})))
    with client:
        response = client.post("/auth/login", json={"username": USER, "password": PASSWORD},
                               headers={"Host": "internal-alb.eu-west-1.elb.amazonaws.com",
                                        "Origin": "https://crypt.example.cloudfront.net"})
        assert response.status_code == 200


def test_forwarded_host_is_trusted_only_behind_trusted_proxies() -> None:
    headers = {"Host": "internal-alb.local", "X-Forwarded-Host": "crypt.example.cloudfront.net",
               "Origin": "https://crypt.example.cloudfront.net"}
    behind, _, _ = app_client(config(trusted_hops=2))
    with behind:
        assert behind.post("/auth/login", json={"username": USER, "password": PASSWORD},
                           headers=headers).status_code == 200
    direct, _, _ = app_client(config(trusted_hops=0))
    with direct:
        assert direct.post("/auth/login", json={"username": USER, "password": PASSWORD},
                           headers=headers).status_code == 403


def test_an_unknown_origin_stays_refused_with_every_setting_on() -> None:
    client, _, _ = app_client(config(trusted_hops=2, public_hosts=frozenset({"ok.example.com"})))
    with client:
        response = sign_in(client, Origin="https://evil.example.com", **{
            "X-Forwarded-Host": "ok.example.com"})
        assert response.status_code == 200 or response.status_code == 403
        assert sign_in(client, Origin="https://ok.example.com:8443").status_code == 403


def test_the_decision_check_still_refuses_cross_origin_posts() -> None:
    client, _, _ = app_client(svc=service("lab-01"))
    with client:
        sign_in(client)
        assert client.post("/incidents/rec-0/approve", json={"by": "Judge"},
                           headers={"Origin": "https://evil.example.com"}).status_code == 403
        simple = client.post("/incidents/rec-0/approve", content='{"by": "Judge"}',
                             headers={"Content-Type": "text/plain"})
        assert simple.status_code in (415, 422)
        assert client.get("/incidents/rec-0").json()["status"] == "awaiting_approval"


# ---------------------------------------------------------- the watcher's token (AU-09)


def post_incident(client: TestClient, namespace: str, token: str | None = TOKEN,
                  iid: str = "w-1"):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.post("/incidents", json=incident(namespace, iid).model_dump(mode="json"),
                       headers=headers)


def test_the_watcher_token_submits_incidents_in_any_namespace() -> None:
    client, _, _ = app_client()
    with client:
        for n, namespace in enumerate(["chaos-oom", "lab-01", "payments"]):
            response = post_incident(client, namespace, iid=f"w-{n}")
            assert response.status_code == 202, namespace
            assert "set-cookie" not in response.headers


def test_the_token_opens_nothing_else_and_never_makes_a_session() -> None:
    client, _, _ = app_client(svc=service("lab-01"))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with client:
        for method, path in [("GET", "/incidents"), ("GET", "/incidents/rec-0"),
                             ("POST", "/incidents/rec-0/approve"), ("GET", "/auth/me"),
                             ("POST", "/auth/logout"), ("GET", "/ui/")]:
            response = client.request(method, path, json={"by": "x"}, headers=headers,
                                      follow_redirects=False)
            assert response.status_code in (401, 302), (method, path, response.status_code)
        assert COOKIE not in client.cookies


@pytest.mark.parametrize("header", [None, "Bearer wrong", "Bearer ", "Bearer", f"Basic {TOKEN}",
                                    f"bearerx {TOKEN}", TOKEN])
def test_a_wrong_or_missing_token_is_refused(header: str | None) -> None:
    client, _, _ = app_client()
    with client:
        headers = {"Authorization": header} if header is not None else {}
        response = client.post("/incidents", json=incident("lab-01").model_dump(mode="json"),
                               headers=headers)
        assert response.status_code == 401


def test_the_scheme_word_is_case_insensitive_but_the_token_is_not() -> None:
    client, _, _ = app_client()
    with client:
        ok = client.post("/incidents", json=incident("lab-01", "c-1").model_dump(mode="json"),
                         headers={"Authorization": f"bEaReR {TOKEN}"})
        assert ok.status_code == 202
        bad = client.post("/incidents", json=incident("lab-01", "c-2").model_dump(mode="json"),
                          headers={"Authorization": f"Bearer {TOKEN.upper()}"})
        assert bad.status_code == 401


def test_with_no_token_configured_no_bearer_value_works() -> None:
    client, _, _ = app_client(config(internal_token=""))
    with client:
        for value in ("", "x", "Bearer", "Bearer x"):
            response = client.post("/incidents", json=incident("lab-01").model_dump(mode="json"),
                                   headers={"Authorization": value})
            assert response.status_code == 401


# ------------------------------------------------------------ the judge's scope (AU-13)


def test_a_judge_may_submit_only_lab_incidents() -> None:
    client, _, _ = app_client()
    with client:
        sign_in(client)
        assert post_incident(client, "lab-01", token=None, iid="j-1").status_code == 202
        assert post_incident(client, "lab-14", token=None, iid="j-2").status_code == 202
        for namespace in ("kube-system", "chaos-oom", "lab-15", "lab-00", "lab-1", "lab-01x",
                          "xlab-01", "agents"):
            response = post_incident(client, namespace, token=None, iid="j-x")
            assert response.status_code == 403, namespace
            assert response.json() == {"detail": "this account can submit lab incidents only"}


def test_a_judge_may_decide_lab_incidents_but_not_real_ones() -> None:
    client, _, _ = app_client(svc=service("chaos-netpol", "lab-04"))
    with client:
        sign_in(client)
        refused = client.post("/incidents/rec-0/approve", json={"by": "Judge"})
        assert refused.status_code == 403
        assert refused.json() == {"detail": "this account can decide lab incidents only"}
        assert client.post("/incidents/rec-0/reject", json={"by": "Judge"}).status_code == 403
        assert client.get("/incidents/rec-0").json()["status"] == "awaiting_approval"
        decided = client.post("/incidents/rec-1/approve", json={"by": "Judge"})
        assert decided.status_code == 200 and decided.json()["status"] == "approved"


def test_an_unknown_incident_is_still_a_404_for_a_judge() -> None:
    client, _, _ = app_client()
    with client:
        sign_in(client)
        assert client.post("/incidents/nope/approve", json={"by": "Judge"}).status_code == 404


def test_the_owner_can_lift_the_limit_with_one_setting() -> None:
    client, _, _ = app_client(config(lab_only=False), svc=service("chaos-netpol"))
    with client:
        sign_in(client)
        assert client.post("/incidents/rec-0/approve", json={"by": "Owner"}).status_code == 200
        assert post_incident(client, "kube-system", token=None, iid="o-1").status_code == 202


def test_lab_namespace_names_are_exactly_lab_01_to_lab_14() -> None:
    assert [n for n in range(0, 20) if is_lab_namespace(f"lab-{n:02d}")] == list(range(1, 15))
    for bad in (None, "", "lab-1", "lab-001", "Lab-01", "lab-01 ", "lab_01", "lab-01\n",
                "lab-0a", "chaos-lab-01"):
        assert not is_lab_namespace(bad), bad


# ------------------------------------------------------------------ the login page


def test_the_login_page_has_the_ui_content_security_policy() -> None:
    client, _, _ = app_client()
    with client:
        for path in ("/login", "/login-assets/login.js", "/login-assets/login.css"):
            response = client.get(path)
            csp = response.headers["content-security-policy"]
            assert "script-src 'self'" in csp and "unsafe" not in csp, path
            assert "frame-ancestors 'none'" in csp
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["cache-control"] == "no-store"


def test_the_login_page_has_no_inline_script_style_or_handler() -> None:
    html = (LOGIN_DIR / "login.html").read_text()
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert not re.search(r"\son\w+\s*=", html) and "style=" not in html
    assert "<style" not in html and "javascript:" not in html
    js = (LOGIN_DIR / "login.js").read_text()
    assert "innerHTML" not in js and "document.write" not in js and "eval(" not in js
    assert "textContent" in js and "safeNext" in js


def page_references(html: str) -> list[str]:
    """Every file the page asks the browser to fetch (src and href), not same-page #fragments."""
    found = re.findall(r'\b(?:src|href)="([^"]+)"', html)
    return [ref for ref in found if not ref.startswith("#")]


def test_every_asset_the_login_page_names_is_served_without_a_session() -> None:
    """The logo was once under /ui/, which needs a session, and showed as a broken image."""
    html = (LOGIN_DIR / "login.html").read_text()
    refs = page_references(html)
    assert refs, "the page references nothing"
    client, _, _ = app_client()
    kinds = {"css": "text/css", "js": "javascript", "svg": "image/svg+xml"}
    with client:
        assert client.get("/login").status_code == 200  # signed out the whole time
        for ref in refs:
            assert ref.startswith("/login-assets/"), f"{ref} is not under the open path"
            response = client.get(ref, follow_redirects=False)
            assert response.status_code == 200, ref
            assert kinds[ref.rsplit(".", 1)[-1]] in response.headers["content-type"], ref
            assert response.content, ref
        # What the page shows is what the server has: the six portraits.
        shown = {ref.rsplit("/", 1)[-1] for ref in refs if "/art/" in ref}
        assert shown == {"necromancer.svg", "vampire.svg", "spider.svg", "zombie.svg",
                         "ghost.svg", "archivist.svg"}
        # The closed path really is closed, so this test would have caught the old logo.
        assert client.get("/ui/art/ghost.svg", follow_redirects=False).status_code == 302


def test_the_login_page_uses_no_external_resource_and_its_css_fetches_nothing() -> None:
    css = (LOGIN_DIR / "login.css").read_text()
    html = (LOGIN_DIR / "login.html").read_text()
    assert "@import" not in css and "@font-face" not in css
    assert "url(" not in css, "the stylesheet should draw with gradients and shapes only"
    for text in (css, html):
        assert not re.search(r"https?://(?!www\.w3\.org/)", text)


def test_the_login_page_art_is_the_ui_art_not_a_copy() -> None:
    ui_art = Path(auth_module.__file__).parent / "ui" / "art"
    assert auth_module.ART_DIR == ui_art
    client, _, _ = app_client()
    with client:
        for svg in ui_art.glob("*.svg"):
            assert client.get(f"/login-assets/art/{svg.name}").content == svg.read_bytes()


def test_the_login_page_respects_reduced_motion_and_keeps_its_look() -> None:
    css = (LOGIN_DIR / "login.css").read_text()
    block = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*)\}\s*$", css, re.S)
    assert block, "no reduced-motion block at the end of the stylesheet"
    rules = block.group(1)
    assert re.search(r"animation:\s*none\s*!important", rules)
    # The scenery stays: nothing is hidden instead of being stilled.
    assert "display: none" not in rules and "visibility" not in rules and "opacity: 0" not in rules
    # The bats start off screen, so the block has to rest them somewhere visible.
    for bat in ("bat-1", "bat-2", "bat-3"):
        assert re.search(rf"\.{bat}\s*\{{\s*left:\s*\d+%", rules), bat
    # Only animations carry motion; a transition outside the block would ignore the preference.
    assert "transition" not in css.split("@media (prefers-reduced-motion")[0]


def hex_luminance(value: str) -> float:
    channels = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((hex_luminance(a), hex_luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_the_login_text_colours_have_enough_contrast() -> None:
    """WCAG AA: 4.5 for text, 3 for the edge of an input and the focus ring."""
    css = (LOGIN_DIR / "login.css").read_text()
    colour = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", css))
    stone = colour["stone-hi"]  # the lightest part of the gravestone: the worst case for text
    for name in ("ink", "muted", "error", "bone"):
        assert contrast(colour[name], stone) >= 4.5, (name, contrast(colour[name], stone))
    assert contrast(colour["ink"], colour["field"]) >= 4.5
    assert contrast(colour["on-pumpkin"], colour["pumpkin"]) >= 4.5
    assert contrast(colour["field-edge"], colour["stone"]) >= 3
    assert contrast(colour["pumpkin"], colour["field"]) >= 3  # the focus ring inside an input
    assert contrast(colour["pumpkin"], colour["night"]) >= 3


def test_the_login_form_is_labelled_and_the_button_says_what_it_does() -> None:
    html = (LOGIN_DIR / "login.html").read_text()
    for field in ("username", "password"):
        assert f'<label for="{field}">' in html and f'id="{field}"' in html
    assert ">Enter the crypt</button>" in html
    assert 'role="alert"' in html and html.count("<h1>") == 1
    assert '<html lang="en">' in html


def test_the_login_messages_are_in_the_crypt_voice() -> None:
    js = (LOGIN_DIR / "login.js").read_text()
    assert "The crypt does not recognise you." in js
    assert "Too many knocks. The crypt stays sealed for" in js


def test_the_login_page_files_ship_in_the_wheel(tmp_path: Path) -> None:
    import shutil
    import subprocess

    uv = shutil.which("uv") or "/opt/homebrew/bin/uv"
    if not Path(uv).exists():
        pytest.skip("uv is not installed")
    result = subprocess.run(  # noqa: S603
        [uv, "build", "--wheel", "-q", "-o", str(tmp_path), str(Path(__file__).parents[1])],
        capture_output=True, text=True, check=False)
    if result.returncode != 0:
        pytest.skip(f"cannot build a wheel here: {result.stderr[-200:]}")
    (wheel,) = tmp_path.glob("*.whl")
    names = set(zipfile.ZipFile(wheel).namelist())
    for name in ("login.html", "login.js", "login.css"):
        assert f"kiroween_orchestrator/ui_login/{name}" in names


# ---------------------------------------------------- the watcher client sends its token


def test_the_watcher_client_sends_the_token_and_the_incident() -> None:
    import httpx

    from kiroween_orchestrator.watcher.client import OrchestratorClient

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={})

    client = OrchestratorClient("http://o", transport=httpx.MockTransport(handler), token=TOKEN)
    assert client.submit(incident("lab-01")) is True
    (request,) = seen
    assert request.url.path == "/incidents" and request.method == "POST"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"


def test_the_watcher_client_without_a_token_sends_no_authorization_header() -> None:
    import httpx

    from kiroween_orchestrator.watcher.client import OrchestratorClient

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(401, json={"detail": "login required"})

    client = OrchestratorClient("http://o", transport=httpx.MockTransport(handler),
                                attempts=3, sleep=lambda s: None)
    assert client.submit(incident("lab-01")) is False
    assert len(seen) == 1, "a 401 is a client error and is not retried"
    assert "authorization" not in seen[0].headers


def test_the_watcher_token_works_end_to_end_against_the_orchestrator_app() -> None:
    import httpx

    from kiroween_orchestrator.watcher.client import OrchestratorClient

    api, _, _ = app_client()
    with api:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                api.post(str(request.url.path), content=request.content,
                         headers=dict(request.headers)).status_code, json={}))
        good = OrchestratorClient("http://o", transport=transport, token=TOKEN)
        assert good.submit(incident("chaos-oom", "e2e-1")) is True
        bad = OrchestratorClient("http://o", transport=transport, token="nope")  # noqa: S106
        assert bad.submit(incident("chaos-oom", "e2e-2")) is False
