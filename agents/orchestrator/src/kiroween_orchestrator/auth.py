"""Login for the orchestrator's UI and API (scenario-lab REQ-AU-01 to AU-13).

One account, from the Kubernetes Secret ``orchestrator-auth``. Sessions, lockouts and the
failure counters live in memory: a restart logs everyone out and forgets the counters.
Nothing here logs, returns or stores a credential.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import re
import secrets
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

COOKIE = "kiroween_session"
LOGIN_DIR = Path(__file__).parent / "ui_login"
# The six crew portraits live with the UI; the login page serves the same files from its own
# open path, because /ui/ needs a session.
ART_DIR = Path(__file__).parent / "ui" / "art"
# The judge's account decides, and submits incidents for, the lab namespaces only.
# Matched with fullmatch, never "$", which also accepts a trailing newline.
LAB_NAMESPACE = re.compile(r"lab-(0[1-9]|1[0-4])")
OPEN_PATHS = frozenset({"/healthz", "/readyz", "/login"})
OPEN_PREFIXES = ("/login-assets/",)
FALSE = {"false", "0", "no", "off"}

AuthKind = Literal["open", "token", "session"]


def is_lab_namespace(namespace: str | None) -> bool:
    return bool(namespace and LAB_NAMESPACE.fullmatch(namespace))


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def same_secret(supplied: str, expected: str) -> bool:
    """Constant-time comparison of two strings of any length."""
    return hmac.compare_digest(_digest(supplied), _digest(expected))


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "")
    try:
        value = int(raw) if raw.strip() else default
    except ValueError:
        return default
    return value if value >= 0 else default


@dataclass(frozen=True)
class AuthConfig:
    required: bool = True
    username: str = ""
    password: str = ""
    internal_token: str = ""
    # Judges decide lab incidents only. Off lets the one account decide real incidents too.
    lab_only: bool = True
    session_ttl_s: int = 8 * 3600
    trusted_hops: int = 0
    public_hosts: frozenset[str] = frozenset()
    max_failures: int = 5
    window_s: int = 900
    lock_s: int = 900
    global_max: int = 100
    global_pause_s: int = 300

    @property
    def configured(self) -> bool:
        return bool(self.username and self.password)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> AuthConfig:
        """Login is required unless AUTH_REQUIRED is explicitly false (fail closed)."""
        hosts = frozenset(h.strip().lower() for h in env.get("PUBLIC_HOSTS", "").split(",")
                          if h.strip())
        return cls(
            required=env.get("AUTH_REQUIRED", "true").strip().lower() not in FALSE,
            username=env.get("AUTH_USERNAME", ""),
            password=env.get("AUTH_PASSWORD", ""),
            internal_token=env.get("AUTH_INTERNAL_TOKEN", ""),
            lab_only=env.get("AUTH_LAB_ONLY", "true").strip().lower() not in FALSE,
            trusted_hops=_int(env, "TRUSTED_PROXY_HOPS", 0),
            public_hosts=hosts,
            session_ttl_s=_int(env, "SESSION_TTL_SECONDS", 8 * 3600) or 8 * 3600,
        )


@dataclass
class _Failures:
    times: deque[float] = field(default_factory=deque)
    locked_until: float = 0.0


@dataclass(frozen=True)
class LoginResult:
    status: Literal["ok", "bad", "locked", "paused", "unconfigured"]
    token: str | None = None
    retry_after_s: int = 0


class Auth:
    MAX_SESSIONS = 1000
    MAX_TRACKED = 10_000

    def __init__(self, config: AuthConfig, clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self._clock = clock
        self._sessions: dict[str, float] = {}
        self._failures: dict[str, _Failures] = {}
        self._global: deque[float] = deque()
        self._paused_until = 0.0

    # --- who is asking ----------------------------------------------------------------

    def client_ip(self, forwarded_for: str | None, peer: str | None) -> str:
        """The address the trusted proxies appended, never the client-supplied leftmost one."""
        hops = self.config.trusted_hops
        if hops > 0 and forwarded_for:
            parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
            if len(parts) >= hops:
                return parts[-hops]
        return peer or "unknown"

    def allowed_origins(self, host: str | None, forwarded_host: str | None) -> set[str]:
        allowed = set(self.config.public_hosts)
        if host:
            allowed.add(host.lower())
        if forwarded_host and self.config.trusted_hops > 0:
            allowed.add(forwarded_host.split(",")[0].strip().lower())
        return allowed

    # --- credentials ------------------------------------------------------------------

    def check_credentials(self, username: str, password: str) -> bool:
        # Both comparisons always run; & does not short-circuit.
        user_ok = same_secret(username, self.config.username)
        password_ok = same_secret(password, self.config.password)
        return bool(user_ok & password_ok)

    def token_ok(self, supplied: str) -> bool:
        expected = self.config.internal_token
        return bool(expected) and same_secret(supplied, expected)

    def login(self, ip: str, username: str, password: str) -> LoginResult:
        cfg, now = self.config, self._clock()
        if not cfg.configured:
            return LoginResult("unconfigured")
        if now < self._paused_until:
            return LoginResult("paused", retry_after_s=math.ceil(self._paused_until - now))
        record = self._failures.get(ip)
        if record is not None and now < record.locked_until:
            return LoginResult("locked", retry_after_s=math.ceil(record.locked_until - now))
        if self.check_credentials(username, password):
            self._failures.pop(ip, None)
            return LoginResult("ok", token=self._new_session(now))
        self._record_failure(ip, now)
        return LoginResult("bad")

    def _record_failure(self, ip: str, now: float) -> None:
        cfg = self.config
        record = self._failures.setdefault(ip, _Failures())
        while record.times and now - record.times[0] > cfg.window_s:
            record.times.popleft()
        record.times.append(now)
        if len(record.times) >= cfg.max_failures:
            record.locked_until = now + cfg.lock_s
            logger.warning("login locked for %s for %d s", ip, cfg.lock_s)
        while self._global and now - self._global[0] > cfg.window_s:
            self._global.popleft()
        self._global.append(now)
        if len(self._global) >= cfg.global_max:
            self._paused_until = now + cfg.global_pause_s
            self._global.clear()
            logger.warning("logins paused for %d s after %d failures", cfg.global_pause_s,
                           cfg.global_max)
        if len(self._failures) > self.MAX_TRACKED:
            def live(f: _Failures) -> bool:
                return f.locked_until > now or bool(f.times and now - f.times[-1] <= cfg.window_s)

            self._failures = {k: v for k, v in self._failures.items() if live(v)}

    # --- sessions ---------------------------------------------------------------------

    def _new_session(self, now: float) -> str:
        if len(self._sessions) >= self.MAX_SESSIONS:
            self._sessions = {t: e for t, e in self._sessions.items() if e > now}
            while len(self._sessions) >= self.MAX_SESSIONS:
                self._sessions.pop(min(self._sessions, key=self._sessions.__getitem__))
        token = secrets.token_urlsafe(32)  # 256 bits
        self._sessions[token] = now + self.config.session_ttl_s
        return token

    def session_valid(self, token: str | None) -> bool:
        if not token:
            return False
        expires = self._sessions.get(token)
        if expires is None:
            return False
        if self._clock() >= expires:
            self._sessions.pop(token, None)
            return False
        return True

    def logout(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)


# --- HTTP ---------------------------------------------------------------------------------


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


def is_open(method: str, path: str) -> bool:
    return (path in OPEN_PATHS or path.startswith(OPEN_PREFIXES)
            or (method in ("GET", "HEAD") and path == "/")
            or (method == "POST" and path == "/auth/login"))


def bearer(header: str | None) -> str | None:
    if header and header[:7].lower() == "bearer ":
        return header[7:].strip() or None
    return None


def json_response(status: int, detail: str, **headers: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status,
                        headers={"Cache-Control": "no-store", **headers})


def check_same_origin(request: Request, auth: Auth | None = None) -> None:
    """Only JSON from this site (REQ-AU-08, REQ-APR-02).

    A page on another site can make a browser send a "simple" request. application/json forces
    a preflight this app never answers; the Origin check refuses the rest. Behind CloudFront
    the Origin is the public host, which equals Host when the origin request policy forwards
    it, and is also accepted from PUBLIC_HOSTS or X-Forwarded-Host when proxy hops are trusted.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="send application/json")
    origin = request.headers.get("origin")
    if origin is None:
        return
    from urllib.parse import urlsplit

    netloc = urlsplit(origin).netloc.lower()
    host = request.headers.get("host")
    if auth is None:
        allowed = {host.lower()} if host else set()
    else:
        allowed = auth.allowed_origins(host, request.headers.get("x-forwarded-host"))
    if netloc not in allowed:
        raise HTTPException(status_code=403, detail="cross-origin request refused")


def install(app: FastAPI, auth: Auth) -> None:
    """The login routes and the middleware that protects everything else."""

    @app.middleware("http")
    async def require_login(request: Request,
                            call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.auth_kind = "open"
        path, method = request.url.path, request.method
        cfg = auth.config
        if not cfg.required or is_open(method, path):
            return await call_next(request)
        if not cfg.configured:
            return json_response(503, "login is not configured")
        if method == "POST" and path == "/incidents":
            supplied = bearer(request.headers.get("authorization"))
            if supplied is not None and auth.token_ok(supplied):
                request.state.auth_kind = "token"
                return await call_next(request)
        if auth.session_valid(request.cookies.get(COOKIE)):
            request.state.auth_kind = "session"
            return await call_next(request)
        if method in ("GET", "HEAD") and (path == "/ui" or path.startswith("/ui/")):
            return RedirectResponse(f"/login?next={quote(path, safe='/')}", status_code=302,
                                    headers={"Cache-Control": "no-store"})
        return json_response(401, "login required")

    @app.get("/login", include_in_schema=False)
    async def login_page() -> FileResponse:
        return FileResponse(LOGIN_DIR / "login.html", media_type="text/html")

    # Mounted first: the longer prefix has to match before /login-assets does.
    app.mount("/login-assets/art", StaticFiles(directory=ART_DIR), name="login-art")
    app.mount("/login-assets", StaticFiles(directory=LOGIN_DIR), name="login-assets")

    @app.post("/auth/login")
    async def login(body: LoginRequest, request: Request) -> JSONResponse:
        check_same_origin(request, auth)
        ip = auth.client_ip(request.headers.get("x-forwarded-for"),
                            request.client.host if request.client else None)
        result = auth.login(ip, body.username, body.password)
        if result.status == "unconfigured":
            return json_response(503, "login is not configured")
        if result.status in ("locked", "paused"):
            return json_response(429, "too many attempts, try again later",
                                 **{"Retry-After": str(result.retry_after_s)})
        if result.status == "bad" or result.token is None:
            logger.info("failed login from %s", ip)
            return json_response(401, "wrong user name or password")
        response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        response.set_cookie(COOKIE, result.token, max_age=auth.config.session_ttl_s,
                            httponly=True, secure=True, samesite="strict", path="/")
        return response

    @app.post("/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        check_same_origin(request, auth)
        auth.logout(request.cookies.get(COOKIE))
        response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return response

    @app.get("/auth/me")
    async def me() -> dict[str, str]:
        return {"user": auth.config.username}
