import os
from collections.abc import Mapping
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    store: Literal["memory", "firestore"] = "memory"  # a typo fails at start-up
    gcp_project: str = ""
    internal_secret: str = "dev-secret"
    local_tz: str = "Asia/Kolkata"
    call_start_hour: int = 8
    call_end_hour: int = 20
    seed_on_start: bool = False
    team_phones: str = ""  # comma-separated E.164 numbers
    live_model: str = "gemini-3.8-live"
    live_location: str = "us-central1"
    live_silence_ms: int = 700  # elderly speakers pause; a short value cuts them off
    # What the team tunes between test calls. Each call stores the values it ran with.
    # The prebuilt voice; empty leaves the model's default. "Kore" is a woman's voice, heard on a
    # real call with this model on 13 October. A name the model refuses stops every call from
    # opening: try a new name with scripts/live_smoke.py before it is deployed.
    live_voice: str = "Kore"
    live_speaks_as: Literal["woman", "man"] = "woman"  # the gender the agent uses for itself; must match the voice
    # How readily a sound counts as the start of speech. "low" lets a cough or an echo pass; "default" sends nothing.
    live_start_sensitivity: Literal["default", "low", "high"] = "low"
    live_end_sensitivity: Literal["default", "low", "high"] = "default"  # how readily a pause counts as the end of speech
    # How much speech is needed before it counts as started; 0 sends nothing. Like the three
    # numbers below, never negative: the service would refuse it and no call would open.
    live_prefix_padding_ms: int = Field(200, ge=0)
    live_interruptions: bool = True  # false: the person's speech does not cut the agent off
    # When nobody has been speaking for this long in the middle of a call, the agent is prompted to
    # ask whether it can be heard and to ask its last question again. 0 = never. Counted from when
    # the agent's words have been heard, or the person was last heard, whichever is later.
    live_quiet_seconds: float = Field(5.0, ge=0)
    # The most such prompts in a row without the person being heard in between; after that the
    # line is left alone until they speak.
    live_quiet_limit: int = Field(2, ge=0)
    # Once every question is answered, how long a silence has the agent asked for its goodbye,
    # and how long a second one ends a call the agent has still not ended.
    live_closing_silence_seconds: float = Field(5.0, ge=1)
    # Once the agent has asked a question, nothing more it says is passed on until the person is
    # heard: a tool's result made it ask the same thing again, over the answer. false passes all on.
    live_mute_repeats: bool = True
    # The longest the person's audio is withheld while the first greeting is spoken, so that a
    # "hello" said over it does not restart it. 0 = never withheld.
    live_greeting_guard_seconds: float = Field(6.0, ge=0)
    # Tell the voice model the resident's language: speech_config.language_code and the language
    # of the transcript of what the person says (hi and mwr as hi-IN, en as en-IN, mr as mr-IN;
    # gu is not on the Vertex list and is sent nothing). false sends neither, if the model refuses them.
    live_language_codes: bool = True
    # The scheduling sent with an accepted record_answer or set_respondent result; a refusal, and
    # every other tool, always has the default. "silent" adds the result to the model's context
    # without making it speak; "default" sends no scheduling. Measured 15 Oct on gemini-3.8-live:
    # with "silent" the agent fell quiet after set_respondent until the quiet-line prompt 5 s
    # later (2 of 2 runs); with "default" and with "when_idle" it spoke 0.9 s after the result.
    live_answer_scheduling: Literal["default", "silent", "when_idle"] = "default"
    # A phone call's voice session is opened when the call is dialled, so that it is ready when
    # the phone is answered (measured 15 Oct: opening one takes about 3.5 s). If the call is not
    # answered within this many seconds of the session opening, it is closed. 0 = never pre-opened.
    # Twilio rings for at most 30 s; a session lives about 10 minutes.
    live_pre_open_seconds: float = Field(60.0, ge=0)
    # If the voice session's connection drops in the middle of a call, open it again once with
    # the last resumption handle the service gave, and carry on. false: the call ends as dropped.
    live_resume: bool = True
    text_model: str = ""  # e.g. gemini-3.8-flash; empty means no case notes and no report summary
    text_location: str = "global"  # the text model is not offered in the regional locations
    text_thinking: str = "low"  # how hard the text model thinks; empty leaves the model's default (too slow for our limit)
    # Ask Chhaya (app/ask). Its agents use ask_model, or text_model when empty.
    ask_model: str = ""
    ask_thinking: str = "low"
    # The topic gate before each question: a small fast model. Measured 14 Oct: about 0.9 s, the first call 2.8 s.
    ask_gate_model: str = "gemini-3.5-flash-lite"
    ask_gate_thinking: str = "minimal"
    ask_gate_seconds: float = 3.0
    ask_daily_cap: int = 2000  # messages a day for everyone together; past it Ask Chhaya rests until tomorrow
    ask_cache_seconds: int = 1800  # how long the cached instruction and tools of Ask Chhaya live; a conversation within it reuses them
    max_call_seconds: int = 300
    live_connect_seconds: float = 15  # how long opening the voice model's session may take
    live_warm_up: bool = True  # open and close one voice session at start-up, so the first call is not a cold start
    worker_threads: int = 32  # store calls run in worker threads; the default pool is far too small
    transcript_save_seconds: float = 1.5  # how often a running call's transcript is stored; 0 = only at the end
    fake_live: bool = False  # replace the voice agent with a script; for building screens, never deployed
    fake_live_step_seconds: float = 1.2  # the pause before each scripted line
    public_host: str = ""  # host name Twilio can reach, without scheme, e.g. abc.ngrok-free.app
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from: str = ""
    dial_allowlist: str = ""  # comma-separated E.164 numbers; nothing else is ever dialled
    tasks_queue: str = ""  # Cloud Tasks queue name; empty means calls are not queued anywhere
    tasks_location: str = "asia-south1"
    tasks_target: str = ""  # this service's https address, which the queue calls back
    tasks_service_account: str = ""  # whose identity the queue calls with, when Cloud Run is locked
    weather_api_key: str = ""  # Google Maps Platform key with the Weather API enabled
    # Where the key is read from when WEATHER_API_KEY is empty: this secret in GCP_PROJECT (app.secret_manager).
    weather_api_key_secret: str = "weather-api-key"
    # Forecast thresholds that open a round (main spec D13). Degrees Celsius.
    heat_orange_c: float = 45
    heat_red_c: float = 47
    cold_orange_c: float = 4
    cold_red_c: float = 2
    ingest_auto_dispatch: bool = False  # also start calling when the forecast opens a round
    google_client_id: str = ""  # OAuth client id of a "Web application" client; not a secret
    allowed_emails: str = ""  # comma-separated; one or more addresses turns sign-in on
    session_hours: int = 12
    session_secret: str = ""  # signs the session cookie; needed when sign-in is on
    allow_open_access: bool = False  # on Cloud Run: run without sign-in on purpose
    demo_district_id: str = "churu"  # the district of the pinned demo round
    web_dir: str = ""  # where the built web app is; empty means app/static/web
    operations_admin_emails: str = ""
    operations_crew_accounts: dict[str, str] = {}
    # How long what every visitor sees alike is kept between reads of the store. 0 = always read.
    view_cache_seconds: float = 0
    register_cache_seconds: float = 0  # districts' registers change only on a reseed; set high in production

    # What an open call may cost. A call whose agent never started is not counted.
    visitor_calls_per_hour: int = 3
    address_calls_per_hour: int = 6
    address_failed_starts_per_hour: int = 6  # calls from one address whose agent never started
    visitor_calls_at_once: int = 3
    visitor_calls_per_day: int = 150  # calls whose agent was started
    visitor_tickets_per_day: int = 1500  # every call asked for, started or not

    # A visitor's test call on their own phone (spec 2026-10-14-visitor-test-calls).
    visitor_phone_countries: str = "91,81,82,852,886,65,60,62,63,66,84,61,64"  # JAPAC calling codes
    # Per-minute price of a call to a mobile, US$. Deliberately high estimates, so the budget runs
    # out early rather than late; set VISITOR_PHONE_RATES_USD (JSON) to override.
    visitor_phone_rates_usd: dict[str, float] = {
        "91": 0.10, "81": 0.20, "82": 0.10, "852": 0.10, "886": 0.25, "65": 0.10, "60": 0.15,
        "62": 0.35, "63": 0.30, "66": 0.15, "84": 0.20, "61": 0.10, "64": 0.15,
    }
    visitor_phone_daily_budget_usd: float = 8.0  # estimated spend per Indian day, reserved before dialling
    visitor_phone_max_seconds: int = 180
    visitor_phone_calls_per_number: int = 2  # per Indian day
    address_phone_calls_per_day: int = 4

    @field_validator("live_voice")
    @classmethod
    def _trimmed_voice(cls, value: str) -> str:
        # A stray space would be sent as part of the name and refused; a blank one is "no voice".
        return value.strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()


def allowed_emails(settings: Settings) -> frozenset[str]:
    """The addresses that may sign in: trimmed, lower-cased, empty entries dropped."""
    entries = (entry.strip().lower() for entry in settings.allowed_emails.split(","))
    return frozenset(entry for entry in entries if entry)


def sign_in_enabled(settings: Settings) -> bool:
    return bool(allowed_emails(settings))


def weak_secret(settings: Settings) -> bool:
    return settings.internal_secret in ("", "dev-secret")


MIN_SESSION_SECRET_CHARS = 32


def sign_in_problem(settings: Settings) -> str | None:
    """Why sign-in cannot be used with these settings, or None. Names settings, never values."""
    if not sign_in_enabled(settings):
        return None
    if weak_secret(settings):
        # The secret opens every path that a session opens, and its default is published.
        return (
            "INTERNAL_SECRET must be set to a strong, non-default value "
            "when any of these is set: ALLOWED_EMAILS"
        )
    session_secret = settings.session_secret.strip()
    if len(session_secret) < MIN_SESSION_SECRET_CHARS:
        return (
            f"SESSION_SECRET must be a random value of at least {MIN_SESSION_SECRET_CHARS} "
            "characters when ALLOWED_EMAILS is set"
        )
    if session_secret == settings.internal_secret.strip():
        # People type the internal secret into pages and commands; it must not forge sessions.
        return "SESSION_SECRET must differ from INTERNAL_SECRET"
    if not settings.google_client_id.strip():
        return "GOOGLE_CLIENT_ID must be set when ALLOWED_EMAILS is set"
    if settings.session_hours < 1:
        return "SESSION_HOURS must be at least 1 when ALLOWED_EMAILS is set"
    return None


def check_settings(settings: Settings, env: Mapping[str, str] | None = None) -> None:
    """Refuse to start a deployment, or a server others can reach, with a missing or default secret.

    Also refuse sign-in that is half configured, and refuse to run on Cloud Run with no
    sign-in unless that was chosen with ALLOW_OPEN_ACCESS.

    A deployment is any store other than memory, or anything running on Cloud Run
    (which sets K_SERVICE), whatever the store. A server is reachable when PUBLIC_HOST or any
    Twilio setting is set: what Twilio can reach, anyone can, and the default secret is published.
    Error messages name settings and never show their values.
    """
    env = os.environ if env is None else env
    weak = weak_secret(settings)
    on_cloud_run = bool(env.get("K_SERVICE"))
    deployed = settings.store != "memory" or on_cloud_run
    if deployed and weak:
        raise RuntimeError(
            "INTERNAL_SECRET must be set to a strong, non-default value "
            "when STORE is not 'memory' or when running on Cloud Run"
        )
    twilio = {
        "TWILIO_ACCOUNT_SID": settings.twilio_account_sid,
        "TWILIO_AUTH_TOKEN": settings.twilio_auth_token,
        "TWILIO_FROM": settings.twilio_from,
    }
    set_names = [name for name, value in twilio.items() if value.strip()]
    if set_names and len(set_names) < len(twilio):
        missing = [name for name in twilio if name not in set_names]
        raise RuntimeError(
            "The phone line is half configured: " + ", ".join(missing) + " must also be set "
            "(or clear the other Twilio settings to use only the browser call)"
        )
    reachable = (
        ["PUBLIC_HOST"] * bool(settings.public_host.strip())
        + ["TASKS_QUEUE"] * bool(settings.tasks_queue.strip())
        + set_names
    )
    if reachable and weak:
        raise RuntimeError(
            "INTERNAL_SECRET must be set to a strong, non-default value "
            "when any of these is set: " + ", ".join(reachable)
        )
    if settings.tasks_queue.strip():
        if not settings.tasks_target.strip() or not settings.gcp_project.strip():
            raise RuntimeError("TASKS_QUEUE needs TASKS_TARGET and GCP_PROJECT to be set as well")
        if not settings.tasks_target.strip().startswith("https://"):
            raise RuntimeError("TASKS_TARGET must be an https:// address")
    if on_cloud_run and settings.fake_live:
        raise RuntimeError(
            "FAKE_LIVE must not be set on Cloud Run: it replaces the voice agent with a script"
        )
    if on_cloud_run and settings.call_start_hour <= 0 and settings.call_end_hour >= 24:
        raise RuntimeError(
            "CALL_START_HOUR and CALL_END_HOUR must not span the whole day on Cloud Run: "
            "nobody is phoned at night"
        )
    problem = sign_in_problem(settings)
    if problem:
        raise RuntimeError(problem)
    if on_cloud_run and not sign_in_enabled(settings) and not settings.allow_open_access:
        raise RuntimeError(
            "Set ALLOWED_EMAILS to require sign-in on Cloud Run, "
            "or ALLOW_OPEN_ACCESS=true to run without sign-in on purpose"
        )
