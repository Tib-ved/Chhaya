"""Start-up rules for sign-in, and the session cookie on its own (no routes)."""
import asyncio
import time

import pytest
from itsdangerous import TimestampSigner

from app.auth import COOKIE_NAME, Auth, safe_next_path
from app.config import Settings, allowed_emails, check_settings, sign_in_enabled
from app.db import MemoryStore

STRONG = "a-long-random-value-not-in-any-repository"
SESSION = "a-second-random-value-that-signs-the-cookie"
CLIENT_ID = "client-123.apps.googleusercontent.com"
ON = dict(allowed_emails="a@example.com, B@Example.com", google_client_id=CLIENT_ID, internal_secret=STRONG,
          session_secret=SESSION)


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**ON, **overrides})


# ---- what turns sign-in on

@pytest.mark.parametrize("value", ["", "   ", ",", " , ,"])
def test_an_empty_allowlist_leaves_sign_in_off(value):
    assert not sign_in_enabled(Settings(_env_file=None, allowed_emails=value))
    assert allowed_emails(Settings(_env_file=None, allowed_emails=value)) == frozenset()


def test_the_allowlist_is_trimmed_and_lower_cased():
    assert allowed_emails(settings()) == {"a@example.com", "b@example.com"}
    assert sign_in_enabled(settings())


def test_defaults():
    plain = Settings(_env_file=None)
    assert (plain.google_client_id, plain.allowed_emails) == ("", "")
    assert (plain.session_hours, plain.allow_open_access) == (12, False)


# ---- check_settings

def test_a_normal_local_configuration_without_sign_in_still_starts():
    check_settings(Settings(_env_file=None, seed_on_start=True), env={})


def test_sign_in_fully_configured_starts_locally_and_on_cloud_run():
    check_settings(settings(), env={})
    check_settings(settings(), env={"K_SERVICE": "svc"})


@pytest.mark.parametrize("client_id", ["", "   "])
def test_an_allowlist_without_a_client_id_stops_start_up(client_id):
    with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_ID") as error:
        check_settings(settings(google_client_id=client_id), env={})
    assert "example.com" not in str(error.value)


@pytest.mark.parametrize("secret", ["", "dev-secret"])
def test_sign_in_with_a_weak_secret_stops_start_up(secret):
    # the secret opens everything a session opens, and the default is published
    with pytest.raises(RuntimeError, match="INTERNAL_SECRET") as error:
        check_settings(settings(internal_secret=secret), env={})
    assert "ALLOWED_EMAILS" in str(error.value)
    assert "example.com" not in str(error.value) and CLIENT_ID not in str(error.value)


@pytest.mark.parametrize("secret", ["", "   ", "short", "x" * 31])
def test_sign_in_without_a_long_session_secret_stops_start_up(secret):
    with pytest.raises(RuntimeError, match="SESSION_SECRET") as error:
        check_settings(settings(session_secret=secret), env={})
    assert "at least 32" in str(error.value) and "example.com" not in str(error.value)


def test_the_session_secret_may_not_be_the_internal_secret():
    # people type the internal secret into pages and commands; it must not forge a session
    with pytest.raises(RuntimeError, match="SESSION_SECRET must differ") as error:
        check_settings(settings(session_secret=STRONG), env={})
    assert STRONG not in str(error.value)


def test_no_session_secret_is_needed_with_sign_in_off():
    check_settings(Settings(_env_file=None, internal_secret=STRONG), env={})


@pytest.mark.parametrize("store", ["memory", "firestore"])
def test_cloud_run_without_an_allowlist_stops_start_up(store):
    plain = Settings(_env_file=None, store=store, internal_secret=STRONG)
    with pytest.raises(RuntimeError, match="ALLOWED_EMAILS") as error:
        check_settings(plain, env={"K_SERVICE": "svc"})
    assert "ALLOW_OPEN_ACCESS=true" in str(error.value)
    assert STRONG not in str(error.value)


def test_cloud_run_without_an_allowlist_starts_when_open_access_is_chosen():
    plain = Settings(_env_file=None, internal_secret=STRONG, allow_open_access=True)
    check_settings(plain, env={"K_SERVICE": "svc"})


def test_open_access_does_not_excuse_a_weak_secret_on_cloud_run():
    plain = Settings(_env_file=None, allow_open_access=True)
    with pytest.raises(RuntimeError, match="INTERNAL_SECRET"):
        check_settings(plain, env={"K_SERVICE": "svc"})


@pytest.mark.parametrize("hours", [0, -1])
def test_sign_in_with_no_session_length_stops_start_up(hours):
    with pytest.raises(RuntimeError, match="SESSION_HOURS"):
        check_settings(settings(session_hours=hours), env={})


# ---- Auth refuses to be built in a state that check_settings would refuse

@pytest.mark.parametrize("bad", [{"internal_secret": ""}, {"internal_secret": "dev-secret"},
                                 {"session_secret": ""}, {"session_secret": STRONG},
                                 {"google_client_id": " "}, {"session_hours": 0}])
def test_auth_cannot_be_built_with_sign_in_on_and_a_forgeable_or_useless_setup(bad):
    with pytest.raises(RuntimeError) as error:
        Auth(settings(**bad))
    assert "example.com" not in str(error.value)


def test_auth_with_sign_in_off_accepts_no_session_at_all():
    off = Auth(Settings(_env_file=None, internal_secret=STRONG))
    on = Auth(settings())
    assert not off.enabled
    assert off.email_from_cookies({COOKIE_NAME: on.sign("a@example.com")}) is None


# ---- the session value

def test_a_signed_session_round_trips_and_is_lower_cased_on_the_list():
    auth = Auth(settings())
    assert auth.email_from_cookies({COOKIE_NAME: auth.sign("a@example.com")}) == "a@example.com"
    assert auth.email_from_cookies({}) is None
    assert auth.email_from_cookies({COOKIE_NAME: ""}) is None


def test_a_tampered_or_foreign_session_is_refused():
    auth = Auth(settings())
    value = auth.sign("a@example.com")
    middle = len(value) - 20  # inside the signature; the last character has spare bits
    flipped = value[:middle] + ("A" if value[middle] != "A" else "B") + value[middle + 1:]
    assert auth.email_from_cookies({COOKIE_NAME: flipped}) is None
    other = Auth(settings(session_secret=SESSION + "-other"))
    assert other.email_from_cookies({COOKIE_NAME: value}) is None
    # the internal secret plays no part: changing it leaves a session valid, knowing it forges none
    assert Auth(settings(internal_secret=STRONG + "-other")).email_from_cookies({COOKIE_NAME: value}) == "a@example.com"
    assert auth.email_from_cookies({COOKIE_NAME: "x" * 5000}) is None


def test_a_session_for_an_address_no_longer_listed_is_refused():
    value = Auth(settings()).sign("b@example.com")
    shorter = Auth(settings(allowed_emails="a@example.com"))
    assert shorter.email_from_cookies({COOKIE_NAME: value}) is None


def test_a_correctly_signed_payload_of_the_wrong_shape_is_refused():
    auth = Auth(settings())
    for payload in ("a@example.com", ["a@example.com"], {"email": ["a@example.com"], "sid": "s"},
                    {"mail": "a@example.com", "sid": "s"}, {"email": "A@EXAMPLE.COM", "sid": "s"},
                    {"email": "a@example.com"}, {"email": "a@example.com", "sid": ""},
                    {"email": "a@example.com", "sid": 5}):
        assert auth.email_from_cookies({COOKIE_NAME: auth._serializer.dumps(payload)}) is None, payload


def test_a_session_expires_after_session_hours_and_a_future_one_is_refused(monkeypatch):
    auth = Auth(settings(session_hours=2))
    start = 1_800_000_000
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: start)
    value = auth.sign("a@example.com")
    for later, expected in ((0, "a@example.com"), (7200, "a@example.com"), (7201, None), (-60, None)):
        monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self, t=start + later: t)
        assert auth.email_from_cookies({COOKIE_NAME: value}) == expected, later


# ---- signing out

class FailingStore:
    def query(self, collection, **equals):
        raise OSError("store is down")

    def set(self, collection, doc_id, data):
        raise OSError("store is down")


def test_signing_out_ends_that_session_and_no_other():
    auth = Auth(settings())
    first, second = auth.sign("a@example.com"), auth.sign("a@example.com")
    auth.sign_out({COOKIE_NAME: first})
    assert auth.email_from_cookies({COOKIE_NAME: first}) is None
    assert auth.email_from_cookies({COOKIE_NAME: second}) == "a@example.com"


def test_a_sign_out_survives_a_restart_and_reaches_another_instance():
    store = MemoryStore()
    auth = Auth(settings(), store=store)
    other = Auth(settings(), store=store)  # running before the sign-out
    value = auth.sign("a@example.com")
    assert other.email_from_cookies({COOKIE_NAME: value}) == "a@example.com"
    auth.sign_out({COOKIE_NAME: value})
    assert Auth(settings(), store=store).email_from_cookies({COOKIE_NAME: value}) is None  # a restart
    other._refresh_due = 0.0  # the refresh interval has passed
    asyncio.run(other.refresh_signed_out())
    assert other.email_from_cookies({COOKIE_NAME: value}) is None


def test_a_forged_or_missing_cookie_signs_nothing_out():
    store = MemoryStore()
    auth = Auth(settings(), store=store)
    foreign = Auth(settings(session_secret=SESSION + "-other")).sign("a@example.com")
    for cookies in ({}, {COOKIE_NAME: "x.y.z"}, {COOKIE_NAME: foreign}):
        auth.sign_out(cookies)
    assert store.query("signed_out_sessions") == []


def test_expired_sign_outs_are_cleared_from_the_store(monkeypatch):
    store = MemoryStore()
    auth = Auth(settings(session_hours=1), store=store)
    auth.sign_out({COOKIE_NAME: auth.sign("a@example.com")})
    assert len(store.query("signed_out_sessions")) == 1
    later = time.time() + 3601
    monkeypatch.setattr(time, "time", lambda: later)
    restarted = Auth(settings(session_hours=1), store=store)
    assert store.query("signed_out_sessions") == [] and restarted._signed_out == {}


def test_a_store_failure_neither_stops_start_up_nor_loses_the_sign_out_here(caplog):
    auth = Auth(settings(), store=FailingStore())
    value = auth.sign("a@example.com")
    assert auth.email_from_cookies({COOKIE_NAME: value}) == "a@example.com"
    auth.sign_out({COOKIE_NAME: value})
    assert auth.email_from_cookies({COOKIE_NAME: value}) is None
    assert "store is down" not in caplog.text and "OSError" in caplog.text


# ---- the internal secret

def test_the_secret_matches_only_itself_and_an_empty_secret_matches_nothing():
    auth = Auth(settings())
    assert auth.secret_matches(STRONG)
    for wrong in ("", STRONG + "x", STRONG[:-1], "dev-secret", "é"):
        assert not auth.secret_matches(wrong)
    empty = Auth(Settings(_env_file=None, internal_secret=""))
    assert not empty.secret_matches("")


# ---- where to go after signing in

@pytest.mark.parametrize("path", ["/dev/call", "/dev/call?x=1", "/", "/api/wards/w1/cases?round_id=r%201", "/" + "a" * 199])
def test_local_paths_are_kept(path):
    assert safe_next_path(path) == path


@pytest.mark.parametrize(
    "path",
    ["", "dev/call", "//evil.example", "///evil.example", "/\\evil", "\\/evil", "\\\\evil", "https://evil.example",
     "//evil.example/dev/call", "/dev\\call", "/a\nb", "/a\rb", "/a\tb", "/a\x00b", "/a\x7fb", "/" + "a" * 300,
     "/" + "a" * 200, "/x?u=https://evil.example", "javascript:alert(1)", " /dev/call", "/dev/call ", None, 5],
)
def test_anything_else_is_dropped(path):
    assert safe_next_path(path) is None
