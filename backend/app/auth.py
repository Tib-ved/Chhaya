"""Sign-in with Google, limited to an allowlist of addresses, and the check on every request.

Nothing here logs or returns an email address, a token or a cookie in an error.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from itsdangerous import BadData, URLSafeTimedSerializer
from starlette.requests import HTTPConnection
from starlette.responses import Response

from app.config import Settings, allowed_emails, sign_in_problem
from app.db import Store
from app.security_headers import EVERY_RESPONSE, HTTPS_ONLY, page_policy

logger = logging.getLogger(__name__)

COOKIE_NAME = "chhaya_session"
COOKIE_SALT = "chhaya-session-v1"
MAX_COOKIE_CHARS = 2048  # a real one is about 100
MAX_NEXT_CHARS = 200
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
LOCAL_HOST = re.compile(r"(localhost|127\.0\.0\.1)(:[0-9]{1,5})?")
MAX_SIGN_IN_BYTES = 16384
MAX_CONCURRENT_VERIFICATIONS = 4
MAX_SIGN_INS_PER_MINUTE = 10  # from one address; a person needs one or two
MAX_TRACKED_CLIENTS = 10000
SIGNED_OUT = "signed_out_sessions"  # collection: one document per session that signed out
SIGNED_OUT_REFRESH_SECONDS = 30  # how soon a sign-out on another instance is seen here
LOGIN_PAGE = Path(__file__).parent / "static" / "login.html"
LOGIN_PAGE_POLICY = page_policy(
    LOGIN_PAGE,
    script_src="https://accounts.google.com/gsi/client",
    style_src="https://accounts.google.com/gsi/style",
    frame_src="https://accounts.google.com/gsi/",
    connect_src="https://accounts.google.com/gsi/",
)
NO_STORE = {"Cache-Control": "no-store"}
# Exact paths that need no session. The Twilio pair is guarded by Twilio's signature and a
# one-time call token; the rest is what signing in itself needs, and the health checks.
OPEN_PATHS = frozenset({
    "/login", "/auth/config", "/auth/google", "/auth/logout",
    "/health", "/healthz", "/twilio/status", "/twilio/stream", "/twilio/operations/status",
})
INTERNAL_PREFIX = "/internal/"  # for machines: each route there demands the internal secret


def verify_with_google(token: str, client_id: str) -> dict:
    """Check signature, expiry and audience with Google's library. Blocking: it fetches
    Google's certificates over the network, so it must never run on the event loop."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(token, google_requests.Request(), client_id)


def safe_next_path(value: object) -> str | None:
    """`value` if it is a path on this site that is safe to send a browser to, else None."""
    if not isinstance(value, str) or not 0 < len(value) <= MAX_NEXT_CHARS:
        return None
    if not value.startswith("/") or value.startswith("//"):
        return None
    if "\\" in value or "://" in value:
        return None
    if any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value):
        return None  # control characters and spaces: browsers strip some of them from a URL
    return value


class AttemptLimiter:
    """At most `limit` attempts per client in any `window` seconds. Used from the event loop
    only, so it needs no lock."""

    def __init__(self, limit: int, window: float = 60.0) -> None:
        self.limit = limit
        self.window = window
        self._attempts: dict[str, deque[float]] = {}

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        attempts = self._attempts.get(client)
        if attempts is None:
            if len(self._attempts) >= MAX_TRACKED_CLIENTS:
                self._forget_idle(now)
            attempts = self._attempts[client] = deque()
        while attempts and now - attempts[0] >= self.window:
            attempts.popleft()
        if len(attempts) >= self.limit:
            return False
        attempts.append(now)
        return True

    def _forget_idle(self, now: float) -> None:
        for client in [c for c, seen in self._attempts.items() if not seen or now - seen[-1] >= self.window]:
            del self._attempts[client]
        while len(self._attempts) >= MAX_TRACKED_CLIENTS:  # still full: the oldest goes
            del self._attempts[next(iter(self._attempts))]


class Auth:
    """Who may use the app: the allowlist, the session cookie and the internal secret."""

    def __init__(
        self, settings: Settings, env: Mapping[str, str] | None = None, store: Store | None = None
    ) -> None:
        problem = sign_in_problem(settings)
        if problem:
            # check_settings says the same at start-up; this holds for any other way in.
            raise RuntimeError(problem)
        self.allowed = allowed_emails(settings)
        self.enabled = bool(self.allowed)
        self.client_id = settings.google_client_id.strip()
        self.max_age = settings.session_hours * 3600
        self.on_cloud_run = bool((os.environ if env is None else env).get("K_SERVICE"))
        self._secret = settings.internal_secret.encode()
        # Its own secret: knowing the internal secret is not enough to forge a session.
        self._serializer = URLSafeTimedSerializer(
            settings.session_secret.strip(), salt=COOKIE_SALT,
            signer_kwargs={"key_derivation": "hmac", "digest_method": hashlib.sha256},
        )
        # Sessions that signed out, by id, with the time each would have expired anyway. Kept
        # in the store as well, so that a restart or another instance still refuses them.
        self._store = store if self.enabled else None
        self._signed_out: dict[str, float] = {}
        self._refreshing = False
        self._refresh_due = 0.0
        if self._store is not None:
            self._load_signed_out()  # before the first request is answered

    def secret_matches(self, offered: str) -> bool:
        """Constant-time comparison; an empty configured secret matches nothing."""
        return bool(self._secret) and hmac.compare_digest(offered.encode(), self._secret)

    def sign(self, email: str) -> str:
        return self._serializer.dumps({"email": email, "sid": secrets.token_urlsafe(16)})

    def _session(self, cookies: Mapping[str, str]) -> tuple[str, str] | None:
        """(address, session id) of a cookie that verifies and is young enough, or None."""
        value = cookies.get(COOKIE_NAME)
        if not value or len(value) > MAX_COOKIE_CHARS:
            return None
        try:
            payload = self._serializer.loads(value, max_age=self.max_age)
        except BadData:  # bad signature, expired, from the future, or not decodable
            return None
        if not isinstance(payload, dict):
            return None
        email, session_id = payload.get("email"), payload.get("sid")
        if not isinstance(email, str) or not isinstance(session_id, str) or not session_id:
            return None
        return email, session_id

    def sign_out(self, cookies: Mapping[str, str]) -> None:
        """End the session in `cookies` for good: a copy of the cookie stops working too."""
        session = self._session(cookies) if self.enabled else None
        if session is None:
            return
        session_id, expires_at = session[1], time.time() + self.max_age
        self._signed_out[session_id] = expires_at
        if self._store is None:
            return
        try:
            self._store.set(SIGNED_OUT, session_id, {"sid": session_id, "expires_at": expires_at})
        except Exception as error:
            # Refused here until a restart; another instance would still accept it.
            logger.error("could not store a sign-out (%s)", type(error).__name__)

    def _load_signed_out(self) -> None:
        """Take in the sign-outs in the store and drop the ones whose session has expired.
        Never forgets one made here: a sign-out during the read is not in what was read."""
        self._refresh_due = time.monotonic() + SIGNED_OUT_REFRESH_SECONDS
        try:
            rows = self._store.query(SIGNED_OUT)
        except Exception as error:
            logger.warning("could not read the signed-out sessions (%s)", type(error).__name__)
            return
        now = time.time()
        for row in rows:
            session_id, expires_at = row.get("sid"), row.get("expires_at")
            if not isinstance(session_id, str) or not isinstance(expires_at, (int, float)):
                continue
            if expires_at > now:
                self._signed_out[session_id] = float(expires_at)
                continue
            try:
                self._store.delete(SIGNED_OUT, session_id)
            except Exception:
                pass  # it is asked again at the next refresh
        for session_id in [s for s, expires_at in self._signed_out.items() if expires_at <= now]:
            self._signed_out.pop(session_id, None)

    async def refresh_signed_out(self) -> None:
        """Now and then, off the event loop. Requests meanwhile use the list in hand."""
        if self._store is None or self._refreshing or time.monotonic() < self._refresh_due:
            return
        self._refreshing = True
        try:
            await asyncio.to_thread(self._load_signed_out)
        finally:
            self._refreshing = False

    def email_from_cookies(self, cookies: Mapping[str, str]) -> str | None:
        """The signed-in address, or None. The cookie must verify, be young enough, not have
        been signed out, and name an address that is on the allowlist now."""
        session = self._session(cookies) if self.enabled else None
        if session is None:
            return None
        email, session_id = session
        if session_id in self._signed_out or email not in self.allowed:
            return None
        return email

    def cookie_is_secure(self, connection: HTTPConnection) -> bool:
        """Always, except for local development over plain http. The scheme is not used: behind
        Cloud Run's proxy it is http. The Host header alone never decides in a deployment."""
        if self.on_cloud_run:
            return True
        return LOCAL_HOST.fullmatch(connection.headers.get("host", "").lower()) is None

    def client_address(self, connection: HTTPConnection) -> str:
        """Who is calling, for counting attempts. On Cloud Run the last entry of
        X-Forwarded-For is written by Google; anything before it is the caller's own claim."""
        if self.on_cloud_run:
            forwarded = connection.headers.get("x-forwarded-for", "").rsplit(",", 1)[-1].strip()
            if forwarded:
                return forwarded
        return connection.client.host if connection.client else "unknown"


class SignInMiddleware:
    """Refuses every HTTP request and WebSocket handshake that has neither a valid session nor
    the internal secret, except the open paths. With sign-in disabled it refuses nothing.
    Either way it adds the security headers to every HTTP response."""

    def __init__(self, app, auth: Auth) -> None:
        self.app = app
        self.auth = auth
        fixed = {**EVERY_RESPONSE, **(HTTPS_ONLY if auth.on_cloud_run else {})}
        self._headers = [(name.lower().encode(), value.encode()) for name, value in fixed.items()]

    def _with_headers(self, send):
        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                extra = [pair for pair in self._headers if pair[0] not in present]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        return send_with_headers

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            send = self._with_headers(send)
        if not self.auth.enabled or scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] not in ("http", "websocket"):
            return  # nothing else is served
        await self.auth.refresh_signed_out()
        path = scope.get("path")
        if isinstance(path, str) and (
            path in OPEN_PATHS or path.startswith(INTERNAL_PREFIX) or self._authorised(scope)
        ):
            await self.app(scope, receive, send)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})  # before accept: refused
        else:
            await self._refusal(scope)(scope, receive, send)

    def _authorised(self, scope) -> bool:
        connection = HTTPConnection(scope)
        if self.auth.secret_matches(connection.headers.get("x-internal-secret", "")):
            return True
        return self.auth.email_from_cookies(connection.cookies) is not None

    @staticmethod
    def _refusal(scope) -> Response:
        accept = HTTPConnection(scope).headers.get("accept", "")
        if scope.get("method") != "GET" or "text/html" not in accept:
            return JSONResponse({"detail": "sign in required"}, status_code=401, headers=NO_STORE)
        path = scope.get("path")
        query = scope.get("query_string", b"").decode("latin-1")
        wanted = safe_next_path(f"{path}?{query}" if query else path) if isinstance(path, str) else None
        # A relative Location: the Host header plays no part in where the browser goes.
        location = "/login" if wanted is None else "/login?next=" + quote(wanted, safe="")
        return Response(status_code=302, headers={"Location": location, **NO_STORE})


async def _read_limited(request: Request, limit: int) -> bytes | None:
    """The body, or None if it is (or claims to be) longer than `limit`."""
    declared = request.headers.get("content-length")
    if declared is not None and not (
        declared.isascii() and declared.isdigit() and len(declared) <= 9 and int(declared) <= limit
    ):
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return None
    return bytes(body)


def _credential(body: bytes | None) -> str | None:
    try:
        parsed = json.loads(body) if body else None
    except ValueError:  # includes bytes that are not UTF-8
        return None
    token = parsed.get("credential") if isinstance(parsed, dict) else None
    return token if isinstance(token, str) and token else None


def _check_claims(claims: object, auth: Auth) -> tuple[int, str] | str:
    """The lower-cased address of an acceptable token, or (status, reason for the log)."""
    if not isinstance(claims, dict):
        return 401, "token did not verify"
    if claims.get("iss") not in GOOGLE_ISSUERS:
        return 401, "wrong issuer"
    if claims.get("email_verified") is not True:
        return 401, "email not verified"
    email = claims.get("email")
    if not isinstance(email, str) or not email.strip():
        return 401, "no email in token"
    email = email.strip().lower()
    if email not in auth.allowed:
        return 403, "not on the allowlist"
    return email


def register_auth_routes(app: FastAPI, auth: Auth, verify: Callable[[str, str], dict]) -> None:
    # Verification blocks on the network. Its own small pool keeps it off the event loop and
    # away from the threads that calls in progress use; when every slot is taken, a further
    # sign-in is refused at once instead of queueing.
    pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_VERIFICATIONS, thread_name_prefix="sign-in")
    slots = threading.BoundedSemaphore(MAX_CONCURRENT_VERIFICATIONS)
    # One caller cannot take all the slots, or keep the server asking Google about junk.
    attempts = AttemptLimiter(MAX_SIGN_INS_PER_MINUTE)

    def refuse(status: int, reason: str) -> JSONResponse:
        logger.warning("sign-in rejected: %s", reason)
        detail = "this account is not allowed" if status == 403 else "invalid sign-in"
        return JSONResponse({"detail": detail}, status_code=status, headers=NO_STORE)

    @app.get("/login", include_in_schema=False)
    def login_page() -> FileResponse:
        return FileResponse(LOGIN_PAGE, media_type="text/html", headers={**NO_STORE, **LOGIN_PAGE_POLICY})

    @app.get("/auth/config")
    def auth_config() -> dict:
        return {"client_id": auth.client_id}

    @app.post("/auth/google")
    async def sign_in(request: Request) -> JSONResponse:
        if not auth.enabled:
            raise HTTPException(status_code=404, detail="sign-in is not enabled")
        if not attempts.allow(auth.client_address(request)):
            logger.warning("sign-in rejected: too many attempts from one address")
            return JSONResponse(
                {"detail": "too many attempts, try again in a minute"}, status_code=429,
                headers={**NO_STORE, "Retry-After": "60"},
            )
        token = _credential(await _read_limited(request, MAX_SIGN_IN_BYTES))
        if token is None:
            raise HTTPException(status_code=400, detail="bad request")
        if not slots.acquire(blocking=False):
            logger.warning("sign-in rejected: too many sign-ins in progress")
            return JSONResponse({"detail": "busy, try again"}, status_code=503, headers=NO_STORE)
        try:
            job = pool.submit(verify, token, auth.client_id)
        except BaseException:
            slots.release()
            raise
        job.add_done_callback(lambda _: slots.release())  # when the thread is really done
        try:
            claims = await asyncio.wrap_future(job)
        except ValueError:
            return refuse(401, "token did not verify")
        except Exception as error:
            # Only the type: a library message can contain parts of the token.
            return refuse(401, f"token could not be checked ({type(error).__name__})")
        outcome = _check_claims(claims, auth)
        if not isinstance(outcome, str):
            return refuse(*outcome)
        response = JSONResponse({"email": outcome}, headers=NO_STORE)
        response.set_cookie(
            COOKIE_NAME, auth.sign(outcome), max_age=auth.max_age, path="/",
            secure=auth.cookie_is_secure(request), httponly=True, samesite="lax",
        )
        logger.info("sign-in accepted")
        return response

    @app.post("/auth/logout")
    def sign_out(request: Request) -> JSONResponse:
        auth.sign_out(request.cookies)
        response = JSONResponse({"ok": True}, headers=NO_STORE)
        response.delete_cookie(
            COOKIE_NAME, path="/", secure=auth.cookie_is_secure(request), httponly=True, samesite="lax"
        )
        return response

    @app.get("/auth/me")
    def me(request: Request) -> JSONResponse:
        email = auth.email_from_cookies(request.cookies)
        if email is None:
            raise HTTPException(status_code=401, detail="sign in required")
        return JSONResponse({"email": email}, headers=NO_STORE)
