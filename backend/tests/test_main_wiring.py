import logging

import pytest

from app.call.twilio_bridge import build_telephony, warn_if_phone_calls_impossible
from app.config import Settings, check_settings

FULL = dict(twilio_account_sid="AC" + "0" * 32, twilio_auth_token="t" * 32, twilio_from="+10000000000")


def test_no_twilio_settings_means_no_telephony():
    assert build_telephony(Settings(_env_file=None)) == (None, None)


def test_partial_twilio_settings_mean_no_telephony():
    settings = Settings(_env_file=None, twilio_account_sid="ACxxxxxxxx", twilio_auth_token="")
    assert build_telephony(settings) == (None, None)


@pytest.mark.parametrize("missing", list(FULL))
@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_twilio_setting_means_no_telephony_and_no_error(missing, blank):
    assert build_telephony(Settings(_env_file=None, **{**FULL, missing: blank})) == (None, None)


def test_full_twilio_settings_build_a_dialler_and_a_validator():
    place_call, validate = build_telephony(Settings(_env_file=None, **FULL))
    assert callable(place_call) and callable(validate)
    # a signature made with a different token must not validate; no network is used
    assert validate("https://voice.example.test/twilio/status?call_id=x", {"CallStatus": "busy"}, "bogus") is False


# ---- start-up check for a half-configured phone line

@pytest.mark.parametrize("present", list(FULL))
def test_a_single_twilio_setting_stops_start_up_naming_the_others(present):
    settings = Settings(_env_file=None, **{present: FULL[present]})
    with pytest.raises(RuntimeError) as error:
        check_settings(settings, env={})
    message = str(error.value).lower()
    for name in FULL:
        assert (name in message) == (name != present)
    assert FULL[present] not in str(error.value)


def test_two_of_three_twilio_settings_stop_start_up():
    settings = Settings(_env_file=None, twilio_account_sid=FULL["twilio_account_sid"], twilio_from="+10000000000")
    with pytest.raises(RuntimeError, match="TWILIO_AUTH_TOKEN"):
        check_settings(settings, env={})


def test_whitespace_only_counts_as_not_set():
    settings = Settings(_env_file=None, twilio_account_sid="  ")
    check_settings(settings, env={})


def test_no_twilio_settings_and_all_twilio_settings_both_start():
    check_settings(Settings(_env_file=None), env={})
    # missing host is only a warning (the secret must be strong once Twilio is configured)
    check_settings(Settings(_env_file=None, internal_secret=STRONG, **FULL), env={})


# ---- a published default secret never guards a reachable server

STRONG = "a-long-random-value-not-in-any-repository"
REACHABLE = [
    {"public_host": "voice.example.test"},
    FULL,
    {**FULL, "public_host": "voice.example.test"},
]


@pytest.mark.parametrize("reachable", REACHABLE)
@pytest.mark.parametrize("secret", ["", "dev-secret"])
def test_a_weak_secret_stops_start_up_when_the_server_can_be_reached(reachable, secret):
    settings = Settings(_env_file=None, internal_secret=secret, **reachable)
    with pytest.raises(RuntimeError, match="INTERNAL_SECRET") as error:
        check_settings(settings, env={})
    for value in (*FULL.values(), "voice.example.test"):  # names only, never values
        assert value not in str(error.value)


@pytest.mark.parametrize("reachable", REACHABLE)
def test_a_strong_secret_starts_with_the_same_settings(reachable):
    check_settings(Settings(_env_file=None, internal_secret=STRONG, **reachable), env={})


def test_plain_local_memory_mode_still_starts_with_the_default_secret():
    settings = Settings(_env_file=None, public_host="  ", dial_allowlist="+910000000001", seed_on_start=True)
    assert settings.internal_secret == "dev-secret" and settings.store == "memory"
    check_settings(settings, env={})


# ---- warning when the phone cannot be used yet

@pytest.mark.parametrize("extra", [{}, {"public_host": "h.example.test"}, {"dial_allowlist": "+910000000001"}])
def test_full_twilio_without_host_or_allowlist_warns_once(extra, caplog):
    with caplog.at_level(logging.WARNING):
        warn_if_phone_calls_impossible(Settings(_env_file=None, **FULL, **extra))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    text = warnings[0].getMessage()
    assert "PUBLIC_HOST" in text or "DIAL_ALLOWLIST" in text
    assert FULL["twilio_auth_token"] not in text and FULL["twilio_account_sid"] not in text


def test_no_warning_when_fully_configured_or_not_using_the_phone(caplog):
    full = Settings(_env_file=None, **FULL, public_host="h.example.test", dial_allowlist="+910000000001")
    with caplog.at_level(logging.WARNING):
        warn_if_phone_calls_impossible(full)
        warn_if_phone_calls_impossible(Settings(_env_file=None))
    assert caplog.records == []


def test_calling_all_day_is_refused_on_cloud_run():
    settings = Settings(_env_file=None, internal_secret=STRONG, allow_open_access=True, call_start_hour=0, call_end_hour=24)
    check_settings(settings, env={})  # fine on a laptop, for an evening test
    with pytest.raises(RuntimeError, match="CALL_START_HOUR"):
        check_settings(settings, env={"K_SERVICE": "chhaya"})


# ---- a local start in memory mode

def test_a_memory_start_seeds_all_sixteen_districts(monkeypatch):
    import importlib
    import sys

    from app.config import get_settings

    for name, value in {"STORE": "memory", "SEED_ON_START": "true", "INTERNAL_SECRET": "local-secret"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("K_SERVICE", raising=False)
    get_settings.cache_clear()
    sys.modules.pop("app.main", None)
    try:
        main = importlib.import_module("app.main")
        ids = {district["id"] for district in main.store.query("districts")}
    finally:
        sys.modules.pop("app.main", None)
        get_settings.cache_clear()
    from seed.regions import DISTRICTS

    assert len(ids) == 16 and ids == {district_id for district_id, _, _ in DISTRICTS}
