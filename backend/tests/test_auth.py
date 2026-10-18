"""Sign-in with Google and the check on every request. Google is never contacted: the token
verifier is a fake passed to create_app."""
import asyncio
import base64
import hashlib
import json
import logging
import re
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from starlette.websockets import WebSocketDisconnect

import app.auth as auth_module
from app.api import create_app
from app.auth import COOKIE_NAME, safe_next_path
from app.config import Settings
from tests.fakes import FakeLiveSession, healthy_call_turns, session_opener
from tests.helpers import queue_one_call

STRONG = "a-long-random-value-not-in-any-repository"
SESSION = "a-second-random-value-that-signs-the-cookie"
SECRET = {"X-Internal-Secret": STRONG}
CLIENT_ID = "client-123.apps.googleusercontent.com"
BASE = "https://chhaya.example.test"
HTML = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
GOOGLE = "https://accounts.google.com"
TOKENS = {
    "tok-a": {"iss": GOOGLE, "email": "a@example.com", "email_verified": True},
    "tok-b-upper": {"iss": "accounts.google.com", "email": " B@EXAMPLE.COM ", "email_verified": True},
    "tok-stranger": {"iss": GOOGLE, "email": "stranger@example.com", "email_verified": True},
    "tok-unverified": {"iss": GOOGLE, "email": "a@example.com", "email_verified": False},
    "tok-verified-as-text": {"iss": GOOGLE, "email": "a@example.com", "email_verified": "true"},
    "tok-evil-issuer": {"iss": "https://evil.example", "email": "a@example.com", "email_verified": True},
    "tok-no-issuer": {"email": "a@example.com", "email_verified": True},
    "tok-no-email": {"iss": GOOGLE, "email_verified": True},
    "tok-odd-email": {"iss": GOOGLE, "email": ["a@example.com"], "email_verified": True},
    "tok-not-claims": ["a@example.com"],
}
PROTECTED = ["/api/rounds/current", "/api/wards/w1/cases", "/dev/call", "/docs", "/openapi.json"]


class Verifier:
    """Stands in for Google's library. Its errors carry the token, as a real one might."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.on_event_loop: list[bool] = []

    def __call__(self, token: str, client_id: str) -> dict:
        self.calls.append((token, client_id))
        try:
            asyncio.get_running_loop()
            self.on_event_loop.append(True)
        except RuntimeError:
            self.on_event_loop.append(False)
        if token == "tok-library-broke":
            raise OSError(f"could not fetch certificates while checking {token}")
        if token not in TOKENS:
            raise ValueError(f"Wrong number of segments in token: {token}")
        return TOKENS[token]


class Rig:
    def __init__(self, store, tz, now, base=BASE, session=None, verifier=None, **overrides):
        self.store = store
        if store.get("wards", "w1") is None:
            self.round_id, self.call_id, self.queue = queue_one_call(store, tz, now)
        else:  # a second app over the same data
            from app.dispatch import RecordingQueue

            self.queue = RecordingQueue()
        self.verifier = verifier or Verifier()
        fields = dict(
            internal_secret=STRONG, session_secret=SESSION, allowed_emails="a@example.com, B@Example.com",
            google_client_id=CLIENT_ID, max_call_seconds=2, public_host="voice.example.test",
        )
        fields.update(overrides)
        self.app = create_app(
            store, self.queue, Settings(_env_file=None, **fields), clock=lambda: now,
            open_session=session_opener(session or FakeLiveSession([])),
            validate_twilio=lambda url, params, signature: False,
            verify_google_token=self.verifier,
        )
        self.client = TestClient(self.app, base_url=base, follow_redirects=False)

    def sign_in(self, token="tok-a"):
        return self.client.post("/auth/google", json={"credential": token})

    def cookie(self, token="tok-a") -> dict:
        """A Cookie header for a fresh session, without leaving it in the client's jar."""
        response = self.sign_in(token)
        assert response.status_code == 200
        value = response.cookies[COOKIE_NAME]
        self.client.cookies.clear()
        return {"Cookie": f"{COOKIE_NAME}={value}"}


@pytest.fixture
def rig(store, tz, now):
    return Rig(store, tz, now)


@pytest.fixture
def open_rig(store, tz, now):
    """Sign-in disabled: the default."""
    return Rig(store, tz, now, allowed_emails="", google_client_id="", internal_secret="s3cret")


def attributes(response) -> dict:
    """The attributes of the one Set-Cookie header, lower-cased; flags map to True."""
    headers = response.headers.get_list("set-cookie")
    assert len(headers) == 1
    name_value, *rest = [part.strip() for part in headers[0].split(";")]
    assert name_value.startswith(COOKIE_NAME + "=")
    found = {"value": name_value.split("=", 1)[1]}
    for part in rest:
        key, _, value = part.partition("=")
        found[key.lower()] = value if value else True
    return found


def asgi(app, method, path, query=b"", headers=(), body=b"", host=b"chhaya.example.test", chunks=None,
         client="203.0.113.5"):
    """One request straight into the app, for paths and bodies a client library would tidy up.
    Returns (status, headers dict, body bytes)."""

    async def run():
        return await asgi_call(app, method, path, query, headers, body, host, chunks, client)

    return asyncio.run(run())


async def asgi_call(app, method, path, query=b"", headers=(), body=b"", host=b"chhaya.example.test", chunks=None,
                    client="203.0.113.5"):
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": path, "raw_path": path.encode("utf-8"), "query_string": query,
        "root_path": "", "headers": [(b"host", host), *headers] if host is not None else list(headers),
        "client": (client, 4321), "server": ("10.0.0.1", 8080),
    }
    pending = list(chunks) if chunks is not None else [body]
    sent = []

    async def receive():
        if pending:
            chunk = pending.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(pending)}
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    start = sent[0]
    payload = b"".join(m.get("body", b"") for m in sent[1:])
    return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}, payload


def refused(client, url, headers=None):
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(url, headers=headers or {}):
            pass
    return error.value.code


# ---- signing in (brief tests 1 to 8)

def test_a_valid_token_signs_in_and_sets_a_strict_cookie(rig):
    response = rig.sign_in("tok-a")
    assert response.status_code == 200
    assert response.json() == {"email": "a@example.com"}
    cookie = attributes(response)
    assert cookie["httponly"] is True and cookie["secure"] is True
    assert cookie["samesite"] == "lax"
    assert cookie["path"] == "/"
    assert cookie["max-age"] == "43200"
    assert "domain" not in cookie
    me = rig.client.get("/auth/me")
    assert (me.status_code, me.json()) == (200, {"email": "a@example.com"})


@pytest.mark.parametrize("base", ["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost", "http://LOCALHOST:8000"])
def test_on_localhost_the_cookie_is_not_secure(store, tz, now, base):
    rig = Rig(store, tz, now, base=base)
    cookie = attributes(rig.sign_in())
    assert "secure" not in cookie
    assert cookie["httponly"] is True and cookie["samesite"] == "lax"
    assert rig.client.get("/auth/me").status_code == 200  # usable over plain http


@pytest.mark.parametrize(
    "host",
    [b"localhost.evil.example", b"evil.example:localhost", b"127.0.0.1.evil.example", b"notlocalhost",
     b"127.0.0.2", b"[::1]:8000", b"", b"localhost:8000:1", b"localhost@evil.example", None],
)
def test_any_other_host_header_gets_a_secure_cookie(rig, host):
    body = json.dumps({"credential": "tok-a"}).encode()
    status, headers, _ = asgi(rig.app, "POST", "/auth/google", body=body, host=host)
    assert status == 200
    assert "; secure" in headers["set-cookie"].lower()


def test_on_cloud_run_the_cookie_is_secure_whatever_the_host_header_says(store, tz, now, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "svc")
    rig = Rig(store, tz, now)
    body = json.dumps({"credential": "tok-a"}).encode()
    for host in (b"localhost", b"localhost:8080", b"127.0.0.1"):
        status, headers, _ = asgi(rig.app, "POST", "/auth/google", body=body, host=host)
        assert status == 200
        assert "; secure" in headers["set-cookie"].lower()
        status, headers, _ = asgi(rig.app, "POST", "/auth/logout", host=host)
        assert "; secure" in headers["set-cookie"].lower()


def test_the_allowlist_ignores_case_both_ways(rig):
    response = rig.sign_in("tok-b-upper")
    assert response.status_code == 200
    assert response.json() == {"email": "b@example.com"}
    assert rig.client.get("/auth/me").json() == {"email": "b@example.com"}


def test_a_verified_address_that_is_not_listed_is_403_without_a_cookie(rig):
    response = rig.sign_in("tok-stranger")
    assert response.status_code == 403
    assert response.json() == {"detail": "this account is not allowed"}
    assert "set-cookie" not in response.headers
    assert rig.client.get("/auth/me").status_code == 401


REJECTED = ["tok-unknown", "tok-unverified", "tok-verified-as-text", "tok-evil-issuer", "tok-no-issuer",
            "tok-no-email", "tok-odd-email", "tok-not-claims", "tok-library-broke"]


@pytest.mark.parametrize("token", REJECTED)
def test_a_token_that_fails_any_check_is_401_without_a_cookie(rig, token):
    response = rig.sign_in(token)
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid sign-in"}
    assert "set-cookie" not in response.headers
    assert rig.client.get("/auth/me").status_code == 401


def test_the_verifier_gets_the_configured_client_id_and_runs_off_the_event_loop(rig):
    rig.sign_in("tok-a")
    rig.sign_in("tok-unknown")
    assert rig.verifier.calls == [("tok-a", CLIENT_ID), ("tok-unknown", CLIENT_ID)]
    assert rig.verifier.on_event_loop == [False, False]


@pytest.mark.parametrize("token", REJECTED + ["tok-stranger"])
def test_error_responses_contain_neither_the_token_nor_an_address(rig, token):
    response = rig.sign_in(token)
    assert response.status_code in (401, 403)
    text = response.text + json.dumps(dict(response.headers))
    assert token not in text
    assert "@" not in text and "example.com" not in text


@pytest.mark.parametrize(
    "content",
    [b"not json", b"", b"[]", b'"tok-a"', b"{}", b'{"credential": 5}', b'{"credential": null}',
     b'{"credential": ""}', b'{"credential": ["tok-a"]}', b'{"token": "tok-a"}', b"\xff\xfe",
     json.dumps({"credential": "tok-a", "pad": "x" * 17000}).encode()],
)
def test_a_malformed_or_oversized_body_is_400_and_is_never_verified(rig, content):
    response = rig.client.post("/auth/google", content=content, headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert "set-cookie" not in response.headers
    assert rig.verifier.calls == []
    assert "tok-a" not in response.text


def test_the_size_limit_is_16_kb_and_holds_without_a_content_length(rig):
    def padded(size):
        prefix = b'{"credential": "tok-a", "pad": "'
        return prefix + b"x" * (size - len(prefix) - 2) + b'"}'

    assert asgi(rig.app, "POST", "/auth/google", body=padded(16384))[0] == 200
    assert asgi(rig.app, "POST", "/auth/google", body=padded(16385))[0] == 400
    big = padded(40000)
    pieces = [big[i:i + 4096] for i in range(0, len(big), 4096)]
    assert asgi(rig.app, "POST", "/auth/google", chunks=pieces)[0] == 400
    lying = [(b"content-length", b"999999")]
    assert asgi(rig.app, "POST", "/auth/google", headers=lying, body=padded(100))[0] == 400
    assert len(rig.verifier.calls) == 1


def test_only_a_few_tokens_are_verified_at_once(store, tz, now):
    release = threading.Event()

    class Slow(Verifier):
        def __call__(self, token, client_id):
            claims = super().__call__(token, client_id)
            release.wait(10)
            return claims

    rig = Rig(store, tz, now, verifier=Slow())
    body = json.dumps({"credential": "tok-a"}).encode()
    limit = auth_module.MAX_CONCURRENT_VERIFICATIONS

    async def scenario():
        try:
            waiting = [asyncio.create_task(asgi_call(rig.app, "POST", "/auth/google", body=body)) for _ in range(limit)]
            for _ in range(500):
                if len(rig.verifier.calls) == limit:
                    break
                await asyncio.sleep(0.01)
            extra = await asgi_call(rig.app, "POST", "/auth/google", body=body)
        finally:
            release.set()
        return extra, await asyncio.gather(*waiting)

    extra, finished = asyncio.run(scenario())
    assert extra[0] == 503 and "set-cookie" not in extra[1]
    assert len(rig.verifier.calls) == limit
    assert [status for status, _, _ in finished] == [200] * limit
    assert asgi(rig.app, "POST", "/auth/google", body=body)[0] == 200  # the slots were given back


# ---- the session (brief tests 9 to 13)

def test_a_tampered_cookie_is_refused(rig):
    header = rig.cookie()
    value = header["Cookie"].split("=", 1)[1]
    payload, rest = value.split(".", 1)
    middle = len(value) - 20  # inside the signature; the last character has spare bits
    forged = [value[:middle] + ("A" if value[middle] != "A" else "B") + value[middle + 1:], "x" + value, payload + ".", payload,
              "e30." + rest, ""]
    assert rig.client.get("/auth/me", headers=header).status_code == 200
    for bad in forged:
        response = rig.client.get("/auth/me", headers={"Cookie": f"{COOKIE_NAME}={bad}"})
        assert response.status_code == 401, bad
        assert rig.client.get("/api/rounds/current", headers={"Cookie": f"{COOKIE_NAME}={bad}"}).status_code == 401


def test_a_cookie_signed_with_another_secret_is_refused(store, tz, now, rig):
    other = Rig(store, tz, now, session_secret=SESSION + "-other")
    header = other.cookie()
    assert other.client.get("/auth/me", headers=header).status_code == 200
    assert rig.client.get("/auth/me", headers=header).status_code == 401
    assert rig.client.get("/api/rounds/current", headers=header).status_code == 401


def test_an_expired_session_is_refused(rig, monkeypatch):
    start = 1_800_000_000
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: start)
    header = rig.cookie()
    for later, expected in ((43200, 200), (43201, 401)):
        monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self, t=start + later: t)
        assert rig.client.get("/auth/me", headers=header).status_code == expected
        assert rig.client.get("/api/rounds/current", headers=header).status_code == expected


def test_removing_an_address_from_the_allowlist_ends_its_session(store, tz, now, rig):
    header = rig.cookie("tok-b-upper")
    assert rig.client.get("/api/rounds/current", headers=header).status_code == 200
    shorter = Rig(store, tz, now, allowed_emails="a@example.com")
    assert shorter.client.get("/auth/me", headers=header).status_code == 401
    assert shorter.client.get("/api/rounds/current", headers=header).status_code == 401
    body = {"district_id": "d1", "hazard": "heat", "severity": "red"}
    assert shorter.client.post("/admin/alerts/replay", json=body, headers=header).status_code == 401
    assert refused(shorter.client, "/browser/stream", headers=header) == 1008


def test_logout_clears_the_cookie(rig):
    rig.sign_in()
    assert rig.client.get("/auth/me").status_code == 200
    response = rig.client.post("/auth/logout")
    assert (response.status_code, response.json()) == (200, {"ok": True})
    cookie = attributes(response)
    assert cookie["value"] in ("", '""') and cookie["max-age"] == "0"
    assert cookie["path"] == "/" and cookie["httponly"] is True and cookie["secure"] is True
    assert COOKIE_NAME not in rig.client.cookies
    assert rig.client.get("/auth/me").status_code == 401


def test_a_copy_of_the_cookie_stops_working_after_logout(store, tz, now, rig):
    header = rig.cookie()
    kept = rig.cookie()  # another session of the same person, e.g. on a phone
    assert rig.client.get("/api/rounds/current", headers=header).status_code == 200
    assert rig.client.post("/auth/logout", headers=header).status_code == 200
    body = {"district_id": "d1", "hazard": "heat", "severity": "red"}
    for client in (rig.client, Rig(store, tz, now).client):  # here, and after a restart
        assert client.get("/auth/me", headers=header).status_code == 401
        assert client.get("/api/rounds/current", headers=header).status_code == 401
        assert client.post("/admin/alerts/replay", json=body, headers=header).status_code == 401
        assert refused(client, "/browser/stream", headers=header) == 1008
        assert client.get("/auth/me", headers=kept).status_code == 200


def test_one_address_may_only_try_to_sign_in_so_often(rig):
    limit = auth_module.MAX_SIGN_INS_PER_MINUTE
    for _ in range(limit):
        assert rig.sign_in("tok-junk").status_code == 401
    response = rig.sign_in("tok-a")
    assert response.status_code == 429 and response.headers["retry-after"] == "60"
    assert "set-cookie" not in response.headers and len(rig.verifier.calls) == limit
    # someone else is not held up
    body = json.dumps({"credential": "tok-a"}).encode()
    assert asgi(rig.app, "POST", "/auth/google", body=body, client="198.51.100.7")[0] == 200


def test_on_cloud_run_attempts_are_counted_by_the_address_google_reports(store, tz, now, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "chhaya")
    rig = Rig(store, tz, now)
    limit = auth_module.MAX_SIGN_INS_PER_MINUTE
    body = json.dumps({"credential": "tok-junk"}).encode()

    def attempt(forwarded):
        return asgi(rig.app, "POST", "/auth/google", headers=[(b"x-forwarded-for", forwarded)], body=body)[0]

    # the caller's own claim (everything before the last entry) does not give a fresh count
    assert [attempt(b"10.%d.0.1, 203.0.113.9" % n) for n in range(limit)] == [401] * limit
    assert attempt(b"10.99.0.1, 203.0.113.9") == 429
    assert attempt(b"203.0.113.9, 198.51.100.7") == 401


def test_the_attempt_counter_forgets_after_a_minute_and_stays_small(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(auth_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(auth_module, "MAX_TRACKED_CLIENTS", 3)
    limiter = auth_module.AttemptLimiter(2)
    assert [limiter.allow("a") for _ in range(3)] == [True, True, False]
    clock[0] += 60
    assert limiter.allow("a")
    for client in "bcdef":
        assert limiter.allow(client)
    assert len(limiter._attempts) <= 3


def test_logout_works_without_a_session(rig):
    response = rig.client.post("/auth/logout")
    assert (response.status_code, response.json()) == (200, {"ok": True})


# ---- enforcement (brief tests 14 to 21)

@pytest.mark.parametrize("path", PROTECTED)
def test_protected_paths_are_refused_without_a_session(rig, path):
    plain = rig.client.get(path)
    assert plain.status_code == 401
    assert plain.json() == {"detail": "sign in required"}
    page = rig.client.get(path, headers=HTML)
    assert page.status_code == 302
    assert page.headers["location"] == "/login?next=" + quote(path, safe="")
    assert "set-cookie" not in plain.headers and "set-cookie" not in page.headers


@pytest.mark.parametrize("path", PROTECTED)
def test_protected_paths_open_with_a_session(rig, path):
    rig.sign_in()
    assert rig.client.get(path).status_code == 200
    assert rig.client.get(path, headers=HTML).status_code == 200


@pytest.mark.parametrize("path", PROTECTED)
def test_protected_paths_open_with_the_secret_and_not_with_a_wrong_one(rig, path):
    assert rig.client.get(path, headers=SECRET).status_code == 200
    for wrong in ("wrong", "", STRONG + "x", STRONG[:-1], "dev-secret"):
        assert rig.client.get(path, headers={"X-Internal-Secret": wrong}).status_code == 401, wrong


def test_only_a_get_for_a_page_is_redirected(rig):
    assert rig.client.post("/api/cases/x/visit", json={"outcome": "x"}, headers=HTML).status_code == 401
    assert rig.client.get("/api/rounds/current", headers={"Accept": "application/json"}).status_code == 401
    for method in ("HEAD", "OPTIONS", "PUT", "DELETE", "PATCH"):
        assert rig.client.request(method, "/api/rounds/current", headers=HTML).status_code == 401, method


def test_an_unknown_path_is_refused_before_it_is_looked_up(rig):
    assert rig.client.get("/nothing/here").status_code == 401
    rig.sign_in()
    assert rig.client.get("/nothing/here").status_code == 404


@pytest.mark.parametrize("path", ["/login", "/auth/config", "/health", "/healthz"])
def test_open_paths_need_no_session(rig, path):
    response = rig.client.get(path, headers=HTML)
    assert response.status_code == 200


def test_health_and_healthz_answer_alike(rig):
    assert rig.client.get("/health").json() == rig.client.get("/healthz").json() == {"ok": True}


def test_the_client_id_is_served_to_the_login_page(rig):
    assert rig.client.get("/auth/config").json() == {"client_id": CLIENT_ID}


def test_twilio_status_reaches_its_own_check(rig):
    response = rig.client.post("/twilio/status?call_id=x", data={"CallStatus": "busy"})
    assert response.status_code == 403
    assert response.json() == {"detail": "bad signature"}


@pytest.mark.parametrize(
    "path",
    ["/twilio/status/", "/twilio/statusX", "/TWILIO/status", "/twilio/status/x", "/twilio", "/twilio/",
     "//twilio/status", "/twilio//status", "/twilio/status ", "/x/../twilio/status", "/twilio/status;x",
     "/twilio/stream/", "/login/", "/Login", "/health/", "/healthz/x", "/auth/config/", "/auth/google/",
     "/auth/logout/", "/auth", "/auth/", "/internal", "/internalx/rounds/x/dispatch", "/INTERNAL/rounds/x/dispatch",
     "/x/internal/rounds/x/dispatch", "//internal/rounds/x/dispatch"],
)
def test_near_misses_of_open_paths_are_not_open(rig, path):
    for method in ("GET", "POST"):
        status, headers, body = asgi(rig.app, method, path, query=b"call_id=x")
        assert status == 401, (method, path)
        assert json.loads(body) == {"detail": "sign in required"}


def test_internal_paths_are_left_to_the_route_and_a_session_is_not_enough(rig):
    path = "/internal/rounds/x/dispatch"
    assert rig.client.post(path, headers=SECRET).status_code == 404  # reached the route: unknown round
    bare = rig.client.post(path)
    assert (bare.status_code, bare.json()) == (401, {"detail": "missing or wrong secret"})
    assert rig.client.post(path, headers={"X-Internal-Secret": "wrong"}).status_code == 401
    rig.sign_in()
    with_session = rig.client.post(path)
    assert (with_session.status_code, with_session.json()) == (401, {"detail": "missing or wrong secret"})
    assert rig.client.post(f"/internal/calls/{rig.call_id}/dial").status_code == 401
    assert rig.store.get("calls", rig.call_id)["status"] == "queued"


def test_every_route_under_internal_demands_the_secret(rig):
    """The middleware lets /internal/ through, so each route there must guard itself."""
    rig.sign_in()
    checked = 0
    for route in rig.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/internal/"):
            continue
        for method in getattr(route, "methods", None) or ["GET"]:
            response = rig.client.request(method, re.sub(r"\{[^}]+\}", "x", path))
            assert response.status_code == 401, (method, path)
            checked += 1
    assert checked >= 2
    assert rig.client.get("/internal/no/such/route").status_code == 404
    assert rig.client.get("/internal/").status_code == 404


def test_admin_accepts_a_session_or_the_secret_and_nothing_else(rig):
    def replay(severity, headers=None):
        body = {"district_id": "d1", "hazard": "heat", "severity": severity}
        return rig.client.post("/admin/alerts/replay", json=body, headers=headers or {}).status_code

    def stored():
        return [alert["severity"] for alert in rig.store.query("alerts")]

    assert stored() == ["red"]  # the alert that opened the round; a replay at the same time replaces it
    assert replay("yellow") == 401
    assert replay("yellow", {"X-Internal-Secret": "wrong"}) == 401
    assert replay("yellow", {"Cookie": f"{COOKIE_NAME}=forged"}) == 401
    assert stored() == ["red"]
    assert replay("yellow", SECRET) == 200
    assert stored() == ["yellow"]
    header = rig.cookie()
    assert replay("orange", header) == 200
    assert stored() == ["orange"]
    simulate = rig.client.post(f"/admin/calls/{rig.call_id}/simulate", json={"status": "failed"}, headers=header)
    assert simulate.status_code == 200


def test_a_signed_in_account_is_the_team_on_the_round_list_and_the_demo_rebuild(store, tz, now):
    rig = Rig(store, tz, now, demo_district_id="d1")
    for headers in ({}, {"Cookie": f"{COOKIE_NAME}=forged"}):
        assert rig.client.get("/api/rounds", headers=headers).status_code == 401
        assert rig.client.post("/admin/demo/rebuild", headers=headers).status_code == 401
    assert store.get("demo", "current")["round_id"] == rig.round_id
    header = rig.cookie()  # a session and no secret
    listed = rig.client.get("/api/rounds", headers=header)
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()["rounds"]] == [rig.round_id]
    rebuilt = rig.client.post("/admin/demo/rebuild", headers=header)
    assert rebuilt.status_code == 200
    assert rebuilt.json()["round_id"] == store.get("demo", "current")["round_id"]
    stranger = Rig(store, tz, now, allowed_emails="someone-else@example.com", demo_district_id="d1")
    assert stranger.client.get("/api/rounds", headers=header).status_code == 401  # no longer listed


def connection_from(peer, forwarded=None):
    from starlette.requests import HTTPConnection

    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return HTTPConnection({"type": "http", "headers": headers, "client": (peer, 4321) if peer else None})


def test_only_the_last_forwarded_address_counts_and_only_on_cloud_run():
    settings = Settings(_env_file=None, internal_secret="s3cret")
    on_cloud_run = auth_module.Auth(settings, env={"K_SERVICE": "chhaya"})
    elsewhere = auth_module.Auth(settings, env={})
    forged = "1.1.1.1, 2.2.2.2, 203.0.113.7"
    assert on_cloud_run.client_address(connection_from("10.0.0.1", forged)) == "203.0.113.7"
    assert elsewhere.client_address(connection_from("10.0.0.1", forged)) == "10.0.0.1"
    assert on_cloud_run.client_address(connection_from("10.0.0.1", "")) == "10.0.0.1"
    assert on_cloud_run.client_address(connection_from("10.0.0.1")) == "10.0.0.1"
    assert elsewhere.client_address(connection_from(None)) == "unknown"


def test_a_browser_stream_without_a_session_or_secret_is_refused_at_the_handshake(rig, monkeypatch):
    def no_query(*args, **kwargs):
        raise AssertionError("the route was reached")

    monkeypatch.setattr(rig.store, "query", no_query)
    ticket = "t" * 32
    assert refused(rig.client, "/browser/stream") == 1008
    assert refused(rig.client, f"/browser/stream?ticket={ticket}") == 1008
    assert refused(rig.client, f"/browser/stream?ticket={ticket}", headers={"X-Internal-Secret": "wrong"}) == 1008
    assert refused(rig.client, f"/browser/stream?ticket={ticket}", headers={"Cookie": f"{COOKIE_NAME}=forged"}) == 1008
    assert refused(rig.client, "/twilio/stream/") == 1008
    assert refused(rig.client, "/twilio/streamX") == 1008
    assert refused(rig.client, "/nothing") == 1008


def test_the_twilio_stream_is_accepted_and_left_to_the_bridge(rig):
    with rig.client.websocket_connect("/twilio/stream") as ws:  # accepted: no session needed
        ws.send_text(json.dumps({"event": "start", "start": {"customParameters": {}}}))
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_text()
    assert error.value.code == 1008  # the bridge's own answer to a start without a token


def test_a_whole_browser_call_with_a_session_and_no_secret(store, tz, now):
    rig = Rig(store, tz, now, session=FakeLiveSession(healthy_call_turns()))
    header = rig.cookie()
    assert rig.client.post(f"/admin/calls/{rig.call_id}/browser-ticket").status_code == 401
    response = rig.client.post(f"/admin/calls/{rig.call_id}/browser-ticket", headers=header)
    assert response.status_code == 200
    ticket = response.json()["ticket"]
    assert refused(rig.client, f"/browser/stream?ticket={ticket}") == 1008  # the ticket alone is not enough
    with rig.client.websocket_connect(f"/browser/stream?ticket={ticket}", headers=header) as ws:
        ws.send_bytes(b"\x00\x00" * 320)
        assert ws.receive_bytes() == b"goodbye-audio"
        assert ws.receive_json() == {"type": "ended", "status": "completed"}
    assert store.get("calls", rig.call_id)["status"] == "completed"
    assert store.get("calls", rig.call_id)["channel"] == "browser"


# ---- where the redirect points (brief test 23)

def location_for(app, path, query=b""):
    status, headers, _ = asgi(app, "GET", path, query=query, headers=[(b"accept", b"text/html")])
    assert status == 302
    return headers["location"]


def test_the_requested_page_is_carried_to_the_login_page(rig):
    assert location_for(rig.app, "/dev/call", b"x=1") == "/login?next=%2Fdev%2Fcall%3Fx%3D1"
    assert location_for(rig.app, "/dev/call") == "/login?next=%2Fdev%2Fcall"
    assert location_for(rig.app, "/a&b=c", b"d=e&next=/x") == "/login?next=%2Fa%26b%3Dc%3Fd%3De%26next%3D%2Fx"


@pytest.mark.parametrize(
    "path, query",
    [("//evil.example", b""), ("https://evil.example", b""), ("/\\evil", b""), ("/dev\ncall", b""),
     ("/dev\r\nSet-Cookie: x=y", b""), ("/" + "a" * 300, b""), ("/dev/call", b"x=" + b"a" * 300),
     ("/dev/call", b"u=https://evil.example"), ("/dev call", b""), ("", b"")],
)
def test_an_unsafe_target_is_dropped_from_the_redirect(rig, path, query):
    assert location_for(rig.app, path, query) == "/login"


def test_the_redirect_never_depends_on_the_host_header(rig):
    for host in (b"evil.example", b"evil.example:443", None):
        status, headers, _ = asgi(rig.app, "GET", "/dev/call", headers=[(b"accept", b"text/html")], host=host)
        assert (status, headers["location"]) == (302, "/login?next=%2Fdev%2Fcall")


def test_the_login_page_and_the_server_agree_on_a_safe_target():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    page = (Path(auth_module.__file__).parent / "static" / "login.html").read_text(encoding="utf-8")
    function = re.search(r"// safe-next:start\n(.*?)// safe-next:end", page, re.S).group(1)
    cases = ["/dev/call", "/dev/call?x=1", "/", "/" + "a" * 199, "", "dev/call", "//evil.example", "///evil.example",
             "/\\evil", "\\/evil", "https://evil.example", "/dev\\call", "/a\nb", "/a\rb", "/a\tb", "/a\x00b",
             "/a\x7fb", "/" + "a" * 300, "/" + "a" * 200, "/x?u=https://evil.example", "javascript:alert(1)",
             " /dev/call", "/dev/call ", None, 5]
    script = function + "\nconst cases = JSON.parse(process.argv[1]);\nconsole.log(JSON.stringify(cases.map(safeNext)));"
    result = subprocess.run([node, "-e", script, json.dumps(cases)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [safe_next_path(case) for case in cases]


# ---- sign-in disabled (brief test 24)

def test_with_sign_in_off_everything_is_as_before(open_rig):
    client = open_rig.client
    assert client.get("/api/rounds/current").status_code == 200
    assert client.get("/dev/call", headers=HTML).status_code == 200
    assert client.get("/healthz").json() == client.get("/health").json() == {"ok": True}
    body = {"district_id": "d1", "hazard": "heat", "severity": "yellow"}
    assert client.post("/admin/alerts/replay", json=body).status_code == 401
    assert client.post("/admin/alerts/replay", json=body, headers={"X-Internal-Secret": "s3cret"}).status_code == 200


def test_with_sign_in_off_there_is_nothing_to_sign_in_to(open_rig):
    client = open_rig.client
    assert client.get("/auth/me").status_code == 401
    assert client.get("/login").status_code == 200
    assert client.get("/auth/config").json() == {"client_id": ""}
    response = client.post("/auth/google", json={"credential": "tok-a"})
    assert response.status_code == 404
    assert "set-cookie" not in response.headers
    assert open_rig.verifier.calls == []
    assert client.post("/auth/logout").json() == {"ok": True}


def test_with_sign_in_off_a_session_cookie_opens_nothing(store, tz, now):
    signed_in = Rig(store, tz, now, internal_secret="s3cret-but-long-enough")
    header = signed_in.cookie()
    off = Rig(store, tz, now, internal_secret="s3cret-but-long-enough", allowed_emails="", google_client_id="")
    assert off.client.get("/auth/me", headers=header).status_code == 401
    body = {"district_id": "d1", "hazard": "heat", "severity": "yellow"}
    assert off.client.post("/admin/alerts/replay", json=body, headers=header).status_code == 401


# ---- pages (brief tests 26 and 27)

def test_the_login_page_loads_google_and_holds_no_client_id_or_secret(rig):
    page = rig.client.get("/login")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "https://accounts.google.com/gsi/client" in page.text
    assert "/auth/config" in page.text and "/auth/google" in page.text
    assert 'credentials: "same-origin"' in page.text
    assert "This Google account is not allowed to use this app." in page.text
    assert "Sign-in failed. Please try again." in page.text
    for literal in (CLIENT_ID, "apps.googleusercontent.com", STRONG, "dev-secret", "@example.com"):
        assert literal not in page.text
    assert "innerHTML" not in page.text and "login_uri" not in page.text


def test_every_response_carries_the_security_headers(rig, open_rig):
    for client in (rig.client, open_rig.client):
        for response in (client.get("/health"), client.get("/login"), client.get("/api/rounds/current"),
                         client.get("/nowhere"), client.post("/auth/logout")):
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["x-frame-options"] == "DENY"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert "strict-transport-security" not in response.headers  # plain http is possible here


def test_on_cloud_run_browsers_are_told_to_use_https_only(store, tz, now, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "chhaya")
    response = Rig(store, tz, now).client.get("/health")
    assert response.headers["strict-transport-security"] == "max-age=31536000"


def script_hashes(text: str) -> list[str]:
    bodies = re.findall(r"<script>(.*?)</script>", text.replace("\r\n", "\n"), re.S)
    return ["'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'" for body in bodies]


def directives(response) -> dict:
    parts = [part.strip().split(" ") for part in response.headers["content-security-policy"].split(";")]
    return {name: values for name, *values in parts}


def test_the_login_page_may_run_its_own_script_and_googles_and_nothing_else(rig):
    page = rig.client.get("/login")
    policy = directives(page)
    hashes = script_hashes(page.text)
    assert len(hashes) == 1
    assert policy["script-src"] == [*hashes, "https://accounts.google.com/gsi/client"]
    assert policy["default-src"] == ["'none'"] and policy["frame-ancestors"] == ["'none'"]
    assert policy["base-uri"] == ["'none'"] and policy["form-action"] == ["'none'"]
    assert policy["connect-src"] == ["'self'", "https://accounts.google.com/gsi/"]
    assert policy["frame-src"] == ["https://accounts.google.com/gsi/"]
    # nothing in the page needs what the policy forbids
    assert not re.search(r"\son[a-z]+=", page.text) and "javascript:" not in page.text


def test_the_call_page_may_run_its_own_script_and_its_microphone_code(rig):
    rig.sign_in()
    page = rig.client.get("/dev/call")
    policy = directives(page)
    hashes = script_hashes(page.text)
    assert len(hashes) == 1
    assert policy["script-src"] == [*hashes, "blob:"]
    assert policy["default-src"] == ["'none'"] and policy["connect-src"] == ["'self'"]
    assert not re.search(r"\son[a-z]+=", page.text)


def test_the_call_page_no_longer_needs_the_secret(rig):
    rig.sign_in()
    page = rig.client.get("/dev/call").text
    assert "Internal secret (leave empty if you are signed in)" in page
    assert 'if (secret) headers["X-Internal-Secret"] = secret;' in page
    assert page.count("X-Internal-Secret") == 1  # set in that one place only
    assert 'credentials: "same-origin"' in page
    assert "/auth/me" in page and "/auth/logout" in page
    assert "Signed in as " in page and "Sign out" in page
    assert "innerHTML" not in page
    assert "Chhaya call test" in page and "audioWorklet" in page  # the rest is still there


# ---- logging (brief test 28)

def sign_in_records(caplog):
    return [r for r in caplog.records if r.name == "app.auth"]


def test_an_accepted_sign_in_logs_one_line_without_the_address(rig, caplog):
    with caplog.at_level(logging.DEBUG):
        assert rig.sign_in("tok-b-upper").status_code == 200
    records = sign_in_records(caplog)
    assert [r.levelno for r in records] == [logging.INFO]
    assert_nothing_private(caplog, "tok-b-upper")


@pytest.mark.parametrize("token", REJECTED + ["tok-stranger"])
def test_a_rejected_sign_in_logs_one_warning_without_the_address_or_token(rig, caplog, token):
    with caplog.at_level(logging.DEBUG):
        assert rig.sign_in(token).status_code in (401, 403)
    records = sign_in_records(caplog)
    assert [r.levelno for r in records] == [logging.WARNING]
    assert_nothing_private(caplog, token)


def test_requests_refused_by_the_middleware_log_no_cookie_or_secret(rig, caplog):
    with caplog.at_level(logging.DEBUG):
        rig.client.get("/api/rounds/current", headers={"Cookie": f"{COOKIE_NAME}=forged-cookie-value"})
        rig.client.get("/api/rounds/current", headers={"X-Internal-Secret": "wrong-secret-value", "Authorization": "Bearer abc123xyz"})
        header = rig.cookie()
        rig.client.get("/auth/me", headers=header)
    text = everything_logged(caplog)
    for private in ("forged-cookie-value", "wrong-secret-value", "abc123xyz", header["Cookie"].split("=", 1)[1], STRONG):
        assert private not in text


def everything_logged(caplog) -> str:
    parts = []
    for record in caplog.records:
        parts.append(record.getMessage())
        parts.append(repr(record.args))
        if record.exc_info:
            parts.append(repr(record.exc_info[1]))
        parts.append(record.exc_text or "")
    return "\n".join(parts) + caplog.text


def assert_nothing_private(caplog, token):
    text = everything_logged(caplog).lower()
    assert token not in text
    assert "@" not in text and "example.com" not in text
