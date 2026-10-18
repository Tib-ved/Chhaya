"""Every API answers from the dataset its mode names (spec L8 to L14), pinned route by route.

The two datasets hold different records under the same ids (the same round, case and call ids, as
the app would make them), each carrying its dataset's marker in what the screens show: a case note,
a call's transcript, an alert's note, the weather's condition. A read with mode=demo must show only
DEMO-MARKER, one without only LIVE-MARKER; the routes fixed to the demo dataset show DEMO-MARKER
either way. Writes are checked in the base store, by collection.

The route list at the bottom is compared with the app's own: a route added without a line here fails."""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from google.adk.events import Event
from google.genai import types

from app.config import Settings
from app.datasets import DatasetStore, collection_for
from app.db import MemoryStore
from app.dispatch import RecordingQueue
from app.router import build_apps, pick_dataset
from seed.seed import build_all, load_seed

NOW = datetime(2026, 10, 15, 6, 30, tzinfo=timezone.utc)  # 12:00 in India
ROUND = "churu_heat_20261015"
RESIDENT, WARD, WORKER = "r01", "churu-ward-2", "churu-worker-2"
CASE = f"{ROUND}_{RESIDENT}"
CALL = f"{ROUND}_{RESIDENT}_1"
VISITOR = str(uuid.UUID("3f2b8c1e-7a4d-4e2a-9b1c-5d6e7f8a9b0c"))
VISITOR_RESIDENT = "visitor-resident-1"
VISITOR_CALL = "visitor-call-1"
TEAM = {"X-Internal-Secret": "s3cret"}
SEEN_BY = {"team": TEAM, "visitor": {"X-Visitor": VISITOR}}
MARK = {"live": "LIVE-MARKER", "demo": "DEMO-MARKER"}
# What else tells the datasets apart where a screen shows no text of its own: the round's peak and
# the pointer's build time. Each appears only in its own dataset's answers.
PEAK = {"live": 43.21, "demo": 47.89}
BUILT = {"live": NOW - timedelta(minutes=43), "demo": NOW - timedelta(minutes=17)}
SIGNS = {
    dataset: (MARK[dataset], f"{PEAK[dataset]}", BUILT[dataset].isoformat().replace("+00:00", "Z"))
    for dataset in MARK
}
WRITE_RESIDENT = "r04"  # a second open case in both datasets, for the writes
WRITE_CASE = f"{ROUND}_{WRITE_RESIDENT}"
DONE_RESIDENT = "r07"  # a visit done in the demo dataset only
THREAD = "a" * 32
PROPOSAL = "b" * 32
ASK = {"live": "s_live_conversation", "demo": "s_demo_conversation"}


class StubRunner:
    async def run_async(self, **kwargs):
        yield Event(author="chhaya", content=types.Content(role="model", parts=[types.Part(text="Hot.")]))


def _seed_dataset(store: DatasetStore, dataset: str) -> None:
    """One dataset's day: an alert, a round, two open cases and a call, a visitor's call, the weather,
    the pointer, and the team's operations thread and proposal."""
    mark = MARK[dataset]
    alert_id = "declared_churu_heat_20261015"
    store.set("alerts", alert_id, {
        "id": alert_id, "source": "declared", "hazard": "heat", "severity": "red", "district_id": "churu",
        "valid_from": NOW - timedelta(hours=6), "valid_to": NOW + timedelta(hours=42),
        "raw": {"note": f"declared {mark}", "peak_c": 46.5}, "received_at": NOW - timedelta(hours=6)})
    opened = NOW - timedelta(hours=4)
    store.set("rounds", ROUND, {
        "id": ROUND, "alert_id": alert_id, "district_id": "churu", "hazard": "heat", "status": "calling",
        "opened_at": opened, "started_at": opened, "peak_temp_c": PEAK[dataset], "peak_hour_local": 15,
        "summary": {"text": f"summary {mark}", "at": NOW}})
    started = opened + timedelta(minutes=10)
    transcript = [{"role": "agent", "text": f"Namaste {mark}", "at": started},
                  {"role": "resident", "text": f"Seene mein dard {mark}", "at": started + timedelta(seconds=15)}]
    store.set("calls", CALL, {
        "id": CALL, "round_id": ROUND, "resident_id": RESIDENT, "attempt": 1, "channel": "phone",
        "status": "completed", "created_at": started, "started_at": started, "ended_at": started + timedelta(minutes=3),
        "duration_s": 180, "respondent": "self", "language_used": "hi",
        "answers": {"dizzy": {"value": "yes", "quote": mark, "at": started}},
        "emergency": {"flagged": True, "reason": f"chest pain {mark}", "at": started}, "transcript": transcript})
    store.set("cases", CASE, {
        "id": CASE, "round_id": ROUND, "resident_id": RESIDENT, "ward_id": WARD, "worker_id": WORKER,
        "tier": "red", "reasons": ["chest pain or breathlessness"], "note": f"note {mark}", "status": "open",
        "visited_at": None, "outcome": None, "updated_at": started + timedelta(minutes=4),
        "opened_at": started + timedelta(minutes=4)})
    store.set("calls", VISITOR_CALL, {
        "id": VISITOR_CALL, "round_id": ROUND, "resident_id": VISITOR_RESIDENT, "visitor_id": VISITOR, "attempt": 1,
        "channel": "browser", "status": "completed", "created_at": started, "started_at": started,
        "ended_at": started + timedelta(minutes=2), "respondent": "self", "language_used": "en", "answers": {},
        "emergency": None, "transcript": [{"role": "agent", "text": f"Hello {mark}", "at": started}]})
    hours = [{"hour": h, "temp_c": 40.0 + h / 10, "humidity_pct": 20, "rain_pct": 0, "condition_type": "CLEAR",
              "condition_text": f"Sunny {mark}", "is_day": True} for h in range(6, 20)]
    for kind, area_id in (("district", "churu"), ("ward", WARD)):
        snap = {"id": f"{area_id}_20261015_0600", "area_kind": kind, "area_id": area_id, "district_id": "churu",
                "state": "Rajasthan", "fetched_at": NOW - timedelta(minutes=30), "local_day": "2026-10-15",
                "hours": hours, "peak": {"c": 47.0, "hour": 15}, "source": "google_weather"}
        if kind == "district":
            snap["days"] = [{"date": "2026-10-15", "max_c": 47.0, "min_c": 31.0, "condition_text": f"Sunny {mark}"}]
        store.set("weather", snap["id"], snap)
    store.set("demo", "current", {"id": "current", "round_id": ROUND, "rounds": {"churu": ROUND}, "built_at": BUILT[dataset]})
    store.set("cases", WRITE_CASE, {
        "id": WRITE_CASE, "round_id": ROUND, "resident_id": WRITE_RESIDENT, "ward_id": WARD, "worker_id": WORKER,
        "tier": "amber", "reasons": ["dizziness"], "note": f"second {mark}", "status": "open", "visited_at": None,
        "outcome": None, "updated_at": started, "opened_at": started})
    if dataset == "demo":  # the workers' screen shows only counts: these differ
        store.set("cases", f"{ROUND}_{DONE_RESIDENT}", {
            "id": f"{ROUND}_{DONE_RESIDENT}", "round_id": ROUND, "resident_id": DONE_RESIDENT, "ward_id": WARD,
            "worker_id": WORKER, "tier": "amber", "reasons": ["dizziness"], "note": f"done {mark}", "status": "visited",
            "visited_at": started, "outcome": "advised_ok", "updated_at": started, "opened_at": started})
    store.set("ops_threads", THREAD, {
        "id": THREAD, "owner": "internal", "title": f"thread {mark}", "scope": {}, "created_at": NOW, "updated_at": NOW,
        "busy": False})
    store.set("ops_proposals", PROPOSAL, {
        "id": PROPOSAL, "owner": "internal", "space": "team", "case_id": CASE, "action": "notify_crew",
        "worker_id": WORKER, "status": "submitted", "provider_sid": "SM0123", "demo": False, "target_phone": "+15005550006",
        "content": f"brief {mark}", "thread_id": THREAD, "created_at": NOW, "expires_at": NOW + timedelta(hours=1)})


def make_base() -> MemoryStore:
    base = MemoryStore()
    load_seed(base, build_all([]))  # the register: shared by both datasets
    resident = dict(base.get("residents", RESIDENT), id=VISITOR_RESIDENT, visitor_id=VISITOR)
    base.set("residents", VISITOR_RESIDENT, resident)  # a visitor's stand-in resident, as a test call makes
    for dataset in MARK:
        _seed_dataset(DatasetStore(base, dataset), dataset)
        # Ask Chhaya's conversations share one collection; each carries its mode in its state.
        base.set("ask_sessions", ASK[dataset], {
            "id": ASK[dataset], "app_name": "ask_chhaya", "user_id": "team", "title": f"chat {MARK[dataset]}",
            "state": {"focus": {}, "mode": dataset}, "created_at": NOW, "updated_at": NOW, "seq": 0,
            "message_count": 1, "busy": False})
    return base


@pytest.fixture
def rig():
    base = make_base()
    settings = Settings(_env_file=None, internal_secret="s3cret", demo_district_id="churu", text_model="", ask_model="")
    client = TestClient(build_apps(base, RecordingQueue(), settings, clock=lambda: NOW, ask_runner=StubRunner()))
    client.base = base
    with client:
        yield client


# Every route, and the dataset that serves it: "mode" (demo with mode=demo, else live), or fixed.
ROUTES = {
    ("GET", "/login"): "mode", ("GET", "/auth/config"): "mode", ("POST", "/auth/google"): "mode",
    ("POST", "/auth/logout"): "mode", ("GET", "/auth/me"): "mode",
    ("POST", "/admin/calls/{call_id}/browser-ticket"): "demo", ("WS", "/browser/stream"): "demo",
    ("GET", "/dev/call"): "demo", ("POST", "/internal/calls/{call_id}/dial"): "demo",
    ("POST", "/twilio/status"): "demo", ("WS", "/twilio/stream"): "demo",
    ("GET", "/healthz"): "mode", ("GET", "/health"): "mode",
    # A replayed alert opens a round only the demo dataset can dispatch: never one in live data.
    ("POST", "/admin/alerts/replay"): "demo",
    ("POST", "/internal/rounds/{round_id}/dispatch"): "demo",
    ("POST", "/internal/sweep"): "mode",  # one scheduler job per dataset
    ("POST", "/internal/ingest"): "mode",  # gone (410) in both
    ("POST", "/internal/weather/fetch"): "live",
    ("POST", "/admin/calls/{call_id}/simulate"): "demo",
    ("GET", "/api/demo"): "demo", ("GET", "/api/demo/recorded-call"): "demo",
    ("GET", "/api/demo/recorded-call/audio"): "demo",
    ("GET", "/api/rounds/current"): "mode", ("GET", "/api/rounds"): "mode",
    ("GET", "/api/rounds/{round_id}/overview"): "mode", ("POST", "/admin/rounds/{round_id}/summary"): "mode",
    ("GET", "/api/rounds/{round_id}/report"): "mode", ("GET", "/api/wards"): "mode",
    ("GET", "/api/wards/{ward_id}/cases"): "mode",
    ("POST", "/api/cases/{case_id}/visit"): "mode", ("POST", "/api/cases/{case_id}/escalate"): "mode",
    ("POST", "/api/cases/{case_id}/acknowledge"): "mode", ("POST", "/api/cases/{case_id}/reassign"): "mode",
    ("POST", "/api/cases/{case_id}/raise"): "mode",
    ("GET", "/api/region"): "mode", ("GET", "/api/districts/{district_id}"): "mode",
    ("GET", "/api/wards/{ward_id}/detail"): "mode", ("GET", "/api/residents/{resident_id}"): "mode",
    ("GET", "/api/weather"): "mode", ("GET", "/api/workers"): "mode",
    ("GET", "/api/workers/{worker_id}/residents"): "mode", ("GET", "/api/eval/latest"): "mode",
    ("POST", "/internal/demo/rebuild"): "demo", ("POST", "/admin/demo/rebuild"): "demo",
    ("POST", "/api/visitor/calls"): "demo", ("POST", "/api/visitor/phone-calls"): "demo",
    ("GET", "/api/visitor/calls/latest"): "demo", ("GET", "/api/visitor/calls/{call_id}"): "demo",
    ("GET", "/api/ops/session"): "mode", ("GET", "/api/ops/geography"): "mode", ("GET", "/api/ops/overview"): "mode",
    ("GET", "/api/ops/cases/{case_id}"): "mode", ("POST", "/api/ops/proposals"): "mode",
    ("GET", "/api/ops/proposals"): "mode", ("POST", "/api/ops/proposals/{proposal_id}/approve"): "mode",
    ("POST", "/api/ops/proposals/{proposal_id}/reject"): "mode", ("POST", "/api/ops/tasks/{task_id_value}"): "mode",
    ("GET", "/api/ops/threads"): "mode", ("POST", "/api/ops/threads"): "mode",
    ("GET", "/api/ops/threads/{thread_id}"): "mode", ("POST", "/api/ops/threads/{thread_id}/messages"): "mode",
    # A provider's receipt belongs to the proposal's dataset: its callback URL carries the mode.
    ("POST", "/twilio/operations/status"): "mode",
    ("POST", "/api/ask/sessions"): "mode", ("GET", "/api/ask/sessions"): "mode",
    ("GET", "/api/ask/sessions/{session_id}"): "mode", ("POST", "/api/ask/sessions/{session_id}/messages"): "mode",
}
DOCS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
SERVED = {"mode": {False: "live", True: "demo"}, "demo": {False: "demo", True: "demo"}, "live": {False: "live", True: "live"}}


def _app_routes(app) -> set[tuple[str, str]]:
    found = set()
    for route in app.routes:
        if route.path in DOCS:
            continue
        methods = getattr(route, "methods", None) or {"WS"}
        found |= {(method, route.path) for method in methods if method != "HEAD"}
    return found


def test_every_route_is_listed_with_its_dataset(rig):
    for app in rig.app.apps.values():
        assert _app_routes(app) == set(ROUTES)


@pytest.mark.parametrize("route", sorted(ROUTES), ids=lambda r: f"{r[0]} {r[1]}")
def test_the_router_sends_each_route_to_its_dataset(route):
    path = route[1].replace("{", "").replace("}", "")
    for demo in (False, True):
        scope = {"path": path, "query_string": b"mode=demo" if demo else b""}
        assert pick_dataset(scope) == SERVED[ROUTES[route]][demo]


def _template_of(url: str) -> tuple[str, str]:
    path = url.split("?")[0]
    for method, template in ROUTES:
        pattern = "".join("[^/]+" if part.startswith("{") else re.escape(part) for part in re.split(r"(\{[^}]+\})", template))
        if method == "GET" and re.fullmatch(pattern, path):
            return method, template
    raise AssertionError(f"no route for {url}")


def _shown(url: str, body: str) -> set[str]:
    """The datasets whose records an answer shows."""
    if url == "/api/workers":  # counts only: the demo dataset has one visit done
        worker = next(w for w in json.loads(body)["workers"] if w["id"] == WORKER)
        return {{(2, 0): "live", (2, 1): "demo"}[(worker["open"], worker["done"])]}
    return {dataset for dataset, signs in SIGNS.items() if any(sign in body for sign in signs)}


def _with_mode(url: str, demo: bool) -> str:
    return url if not demo else url + ("&" if "?" in url else "?") + "mode=demo"


# Every read (GET), with who asks: "team" (the secret) and/or "visitor" (X-Visitor). "data" reads
# show one dataset's records; "register" reads show neither and must be the same in both modes.
READS = [
    ("/api/demo", ("team", "visitor"), "data"),
    ("/api/rounds/current", ("team", "visitor"), "data"),
    ("/api/rounds", ("team",), "data"),
    (f"/api/rounds/{ROUND}/overview", ("team",), "data"),
    (f"/api/rounds/{ROUND}/report", ("team", "visitor"), "data"),
    (f"/api/wards/{WARD}/cases", ("team", "visitor"), "data"),
    ("/api/region", ("team", "visitor"), "data"),
    ("/api/districts/churu", ("team", "visitor"), "data"),
    (f"/api/wards/{WARD}/detail", ("team", "visitor"), "data"),
    (f"/api/residents/{RESIDENT}", ("team", "visitor"), "data"),
    ("/api/weather", ("team", "visitor"), "data"),
    (f"/api/weather?ward_id={WARD}", ("team", "visitor"), "data"),
    ("/api/weather?district_id=churu", ("team", "visitor"), "data"),
    ("/api/weather?state=Rajasthan", ("team", "visitor"), "data"),
    ("/api/workers", ("team", "visitor"), "data"),
    (f"/api/workers/{WORKER}/residents", ("team", "visitor"), "data"),
    ("/api/visitor/calls/latest", ("visitor",), "data"),
    (f"/api/visitor/calls/{VISITOR_CALL}", ("visitor",), "data"),
    ("/api/ops/overview", ("team",), "data"),
    (f"/api/ops/cases/{CASE}", ("team",), "data"),
    ("/api/ops/proposals", ("team",), "data"),
    ("/api/ops/threads", ("team",), "data"),
    (f"/api/ops/threads/{THREAD}", ("team",), "data"),
    ("/api/ask/sessions", ("team",), "data"),
    (f"/api/ask/sessions/{ASK['live']}", ("team",), "live-only"),
    (f"/api/ask/sessions/{ASK['demo']}", ("team",), "demo-only"),
    ("/api/wards", ("team", "visitor"), "register"),
    ("/api/ops/session", ("team",), "register"),
    ("/api/ops/geography", ("team",), "register"),
    ("/api/eval/latest", ("team", "visitor"), "register"),
    ("/api/demo/recorded-call", ("visitor",), "register"),
    ("/auth/config", ("visitor",), "register"),
    ("/healthz", ("visitor",), "register"),
    ("/health", ("visitor",), "register"),
]
NOT_READ = {  # the GET routes READS leaves out, and why
    ("GET", "/login"): "the sign-in page, a file",
    ("GET", "/auth/me"): "who is signed in; 401 with sign-in off, never dataset data",
    ("GET", "/dev/call"): "the call page, a file",
    ("GET", "/api/demo/recorded-call/audio"): "an audio file, absent in tests",
}


def test_every_get_route_is_read():
    gets = {route for route in ROUTES if route[0] == "GET"}
    assert {_template_of(url) for url, _, _ in READS} | set(NOT_READ) == gets


@pytest.mark.parametrize("url,who,kind", READS, ids=[r[0] for r in READS])
def test_every_read_answers_from_its_dataset(rig, url, who, kind):
    rule = ROUTES[_template_of(url)]
    for person in who:
        answers = {}
        for demo in (False, True):
            response = rig.get(_with_mode(url, demo), headers=SEEN_BY[person])
            served = SERVED[rule][demo]
            if kind.endswith("-only"):  # one conversation: readable only in the dataset it belongs to
                owner = kind.removesuffix("-only")
                assert response.status_code == (200 if served == owner else 404), (person, demo, response.text[:200])
                if served == owner:
                    assert _shown(url, response.text) == {owner}
                continue
            assert response.status_code == 200, (person, demo, response.text[:200])
            answers[demo] = response.text
            expected = set() if kind == "register" else {served}
            assert _shown(url.split("?")[0], response.text) == expected, (person, demo, response.text[:300])
        if kind == "register":
            assert answers[False] == answers[True], person


# ---------- writes: each lands in the collection of the dataset that served it ----------

def _in(base, dataset: str, collection: str, doc_id: str) -> dict | None:
    return base.get(collection_for(dataset, collection), doc_id)


def _other(dataset: str) -> str:
    return "live" if dataset == "demo" else "demo"


@pytest.mark.parametrize("demo", [False, True], ids=["plain", "mode=demo"])
def test_case_actions_change_the_case_of_their_dataset(rig, demo):
    served = "demo" if demo else "live"
    escalated = rig.post(_with_mode(f"/api/cases/{WRITE_CASE}/escalate", demo), json={"reason": "transport"}, headers=TEAM)
    assert escalated.status_code == 200, escalated.text
    visited = rig.post(_with_mode(f"/api/cases/{WRITE_CASE}/visit", demo), json={"outcome": "advised_ok"}, headers=TEAM)
    assert visited.status_code == 200, visited.text
    assert visited.json()["note"] == f"second {MARK[served]}"
    case = _in(rig.base, served, "cases", WRITE_CASE)
    assert case["status"] == "visited" and case.get("escalation")
    untouched = _in(rig.base, _other(served), "cases", WRITE_CASE)
    assert untouched["status"] == "open" and not untouched.get("escalation")


@pytest.mark.parametrize("demo", [False, True], ids=["plain", "mode=demo"])
def test_a_visitor_call_is_always_made_in_the_demo_dataset(rig, demo):
    made = rig.post(_with_mode("/api/visitor/calls", demo), json={"card_id": "alone_dizzy", "language": "en"},
                    headers={"X-Visitor": str(uuid.uuid4())})
    assert made.status_code == 200, made.text
    call_id = made.json()["call_id"]
    assert _in(rig.base, "demo", "calls", call_id) is not None
    assert _in(rig.base, "live", "calls", call_id) is None


@pytest.mark.parametrize("demo", [False, True], ids=["plain", "mode=demo"])
def test_an_ask_conversation_keeps_the_mode_it_was_started_in(rig, demo):
    made = rig.post(_with_mode("/api/ask/sessions", demo), json={}, headers=TEAM)
    assert made.status_code == 201, made.text
    stored = rig.base.get("ask_sessions", made.json()["session_id"])  # shared: one collection for both
    assert stored["state"]["mode"] == ("demo" if demo else "live")


@pytest.mark.parametrize("demo", [False, True], ids=["plain", "mode=demo"])
def test_operations_writes_land_in_their_dataset(rig, demo):
    served = "demo" if demo else "live"
    thread = rig.post(_with_mode("/api/ops/threads", demo), json={}, headers=TEAM)
    assert thread.status_code == 200, thread.text
    assert _in(rig.base, served, "ops_threads", thread.json()["id"]) is not None
    assert _in(rig.base, _other(served), "ops_threads", thread.json()["id"]) is None
    proposed = rig.post(_with_mode("/api/ops/proposals", demo), json={"action": "assign_crew", "case_id": WRITE_CASE}, headers=TEAM)
    assert proposed.status_code == 200, proposed.text
    assert _in(rig.base, served, "ops_proposals", proposed.json()["id"]) is not None
    assert _in(rig.base, _other(served), "ops_proposals", proposed.json()["id"]) is None


@pytest.mark.parametrize("demo", [False, True], ids=["plain", "mode=demo"])
def test_the_demo_rebuild_and_a_replayed_alert_write_only_demo_data(rig, demo):
    rebuilt = rig.post(_with_mode("/admin/demo/rebuild", demo), headers=TEAM)
    assert rebuilt.status_code == 200, rebuilt.text
    assert _in(rig.base, "demo", "demo", "current")["built_at"] == NOW
    assert _in(rig.base, "live", "demo", "current")["built_at"] == BUILT["live"]
    replayed = rig.post(_with_mode("/admin/alerts/replay", demo), json={"district_id": "sikar", "hazard": "heat", "severity": "red"}, headers=TEAM)
    assert replayed.status_code == 200, replayed.text
    assert _in(rig.base, "demo", "alerts", replayed.json()["alert_id"]) is not None
    assert _in(rig.base, "live", "alerts", replayed.json()["alert_id"]) is None


def test_the_demo_rebuild_uses_the_apps_own_settings():
    """The thresholds the rebuild opens alerts with are the app's, not the process environment's."""
    base = MemoryStore()
    load_seed(base, build_all([]))
    settings = Settings(_env_file=None, internal_secret="s3cret", demo_district_id="churu", heat_orange_c=90, heat_red_c=95)
    client = TestClient(build_apps(base, RecordingQueue(), settings, clock=lambda: NOW))
    with client:
        assert client.post("/admin/demo/rebuild", headers=TEAM).status_code == 200
    # Nothing reaches 90 C: every district's alert is declared, none comes from the heat scenario.
    alerts = DatasetStore(base, "demo").query("alerts")
    assert alerts and {a["source"] for a in alerts} == {"declared"}


# ---------- provider receipts for operations notifications ----------

TWILIO = {"twilio_auth_token": "tok3n", "twilio_account_sid": "AC123", "twilio_from": "+15005550006", "public_host": "chhaya.example"}


@pytest.fixture
def twilio_rig():
    base = make_base()
    settings = Settings(_env_file=None, internal_secret="s3cret", demo_district_id="churu", **TWILIO)
    client = TestClient(build_apps(base, RecordingQueue(), settings, clock=lambda: NOW))
    client.base = base
    with client:
        yield client


def _receipt(client, query: str):
    from twilio.request_validator import RequestValidator

    fields = {"MessageSid": "SM0123", "MessageStatus": "delivered", "To": "+15005550006"}
    url = f"https://{TWILIO['public_host']}/twilio/operations/status?{query}"
    signature = RequestValidator(TWILIO["twilio_auth_token"]).compute_signature(url, fields)
    return client.post(f"/twilio/operations/status?{query}", data=fields, headers={"X-Twilio-Signature": signature})


@pytest.mark.parametrize("dataset", ["live", "demo"])
def test_a_provider_receipt_updates_the_proposal_of_its_dataset(twilio_rig, dataset):
    query = f"proposal_id={PROPOSAL}" + ("&mode=demo" if dataset == "demo" else "")
    answer = _receipt(twilio_rig, query)
    assert answer.status_code == 200, answer.text
    assert _in(twilio_rig.base, dataset, "ops_proposals", PROPOSAL)["status"] == "delivered"
    assert _in(twilio_rig.base, _other(dataset), "ops_proposals", PROPOSAL)["status"] == "submitted"


@pytest.mark.parametrize("dataset", ["live", "demo"])
def test_a_notification_asks_for_its_receipt_in_its_own_dataset(twilio_rig, dataset, monkeypatch):
    import twilio.rest

    sent = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.messages = self

        def create(self, **kwargs):
            sent.update(kwargs)
            return type("Message", (), {"sid": "SM9"})()

    monkeypatch.setattr(twilio.rest, "Client", FakeClient)
    service = twilio_rig.app.apps[dataset].state.operations
    service.send_notification({"id": PROPOSAL, "action": "notify_crew", "target_phone": "+15005550006", "content": "x"})
    from urllib.parse import parse_qs, urlsplit

    asked = parse_qs(urlsplit(sent["status_callback"]).query)
    assert asked["proposal_id"] == [PROPOSAL]
    assert pick_dataset({"path": "/twilio/operations/status", "query_string": urlsplit(sent["status_callback"]).query.encode()}) == dataset
