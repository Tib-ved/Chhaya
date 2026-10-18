import json
import re
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.api as api_module
from app.api import create_app
from app.call.fake_live import open_fake_session
from app.config import Settings
from app.demo_cards import CARDS
from app.dispatch import RecordingQueue
from seed.seed import ward_ids
from tests.helpers import seeded_demo

ALICE = {"X-Visitor": "3f2b8c1e-7a4d-4e2a-9b1c-5d6e7f8a9b0c"}
BOB = {"X-Visitor": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"}
SECRET = {"X-Internal-Secret": "s3cret"}
WARD_2 = ward_ids("churu")[0]


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def rig(store, now, tz):
    clock = Clock(now)
    round_id = seeded_demo(store, now, tz)
    settings = Settings(
        _env_file=None, internal_secret="s3cret", max_call_seconds=20,
        fake_live=True, fake_live_step_seconds=0, transcript_save_seconds=0,
    )
    app = create_app(store, RecordingQueue(), settings, clock=clock, open_session=open_fake_session)
    client = TestClient(app)
    client.round_id, client.clock, client.store = round_id, clock, store
    return client


def start(client, who=ALICE, card="alone_dizzy", language="hi"):
    return client.post("/api/visitor/calls", json={"card_id": card, "language": language}, headers=who)


def take_call(client, ticket) -> str:
    """Connect the browser side and wait for the call to end. Returns the final status."""
    with client.websocket_connect(f"/browser/stream?ticket={ticket}") as ws:
        ws.send_bytes(b"\x00\x00" * 320)
        while True:
            message = ws.receive()
            assert message["type"] != "websocket.close", "the call closed without saying how it ended"
            if message.get("text"):
                body = json.loads(message["text"])
                if body.get("type") == "ended":
                    return body["status"]


def test_the_whole_path_of_one_visitor(rig):
    before = rig.get("/api/rounds/current", headers=ALICE).json()["counts"]
    assert rig.get("/api/visitor/calls/latest", headers=ALICE).json() == {"call": None}

    made = start(rig).json()
    assert set(made) == {"call_id", "ticket", "expires_in"}
    waiting = rig.get(f"/api/visitor/calls/{made['call_id']}", headers=ALICE).json()
    assert waiting["status"] == "queued"
    assert all(question["value"] is None for question in waiting["questions"])

    assert take_call(rig, made["ticket"]) == "completed"

    feed = rig.get(f"/api/visitor/calls/{made['call_id']}", headers=ALICE).json()
    assert feed["status"] == "completed"
    assert feed["end_outcome"] == "completed"
    assert feed["respondent"] == "self"
    assert [q["value"] for q in feed["questions"]] == [
        "mild_symptoms", "low", "alone", "adequate", "staying_in", "has", "none",
    ]
    assert feed["questions"][0]["value_label"] == "Mild symptoms"
    assert [rule["id"] for rule in feed["rules_so_far"]] == ["A1", "A2", "A4"]
    assert feed["case"]["tier"] == "amber"
    assert feed["transcript"][0]["role"] == "agent"
    assert rig.get("/api/visitor/calls/latest", headers=ALICE).json()["call"]["call_id"] == made["call_id"]

    cases = rig.get(f"/api/wards/{feed['ward_id']}/cases", headers=ALICE).json()["cases"]
    mine = [c for c in cases if c["yours"]]
    assert len(mine) == 1 and mine[0]["can_mark"] is True
    assert mine[0]["resident"]["name"] == "Kamla Devi"
    assert cases.index(mine[0]) > 0  # at its true rank: the ward's red case is above it

    after = rig.get("/api/rounds/current", headers=ALICE).json()["counts"]
    assert (after["targeted"], after["reached"], after["amber"]) == (
        before["targeted"] + 1, before["reached"] + 1, before["amber"] + 1,
    )

    visit = rig.post(f"/api/cases/{mine[0]['id']}/visit", json={"outcome": "supplies_given"}, headers=ALICE)
    assert visit.status_code == 200
    assert rig.post(f"/api/cases/{mine[0]['id']}/visit", json={"outcome": "referred"}, headers=ALICE).status_code == 409

    report = rig.get(f"/api/rounds/{rig.round_id}/report", headers=ALICE).json()
    assert report["includes_yours"] is True
    assert report["response"]["visited"] == 6
    assert report["coverage"]["not_callable"] == 89  # the rest of Churu: 119 on the register, 30 with test numbers


def test_nobody_else_sees_any_of_it(rig):
    untouched = {
        path: rig.get(path, headers=BOB).json()
        for path in ("/api/rounds/current", f"/api/wards/{WARD_2}/cases", f"/api/rounds/{rig.round_id}/report")
    }
    made = start(rig).json()
    take_call(rig, made["ticket"])
    for path, before in untouched.items():
        assert rig.get(path, headers=BOB).json() == before, path
        assert rig.get(path).json() == before, path  # and nobody without an id
    assert rig.get("/api/visitor/calls/latest", headers=BOB).json() == {"call": None}


def test_another_visitor_cannot_read_or_mark(rig):
    made = start(rig).json()
    take_call(rig, made["ticket"])
    case_id = rig.get(f"/api/visitor/calls/{made['call_id']}", headers=ALICE).json()["case"]["id"]
    before = rig.store.get("cases", case_id)

    for headers in (BOB, {}, {"X-Visitor": "x"}):
        assert rig.get(f"/api/visitor/calls/{made['call_id']}", headers=headers).status_code == 404
        refused = rig.post(f"/api/cases/{case_id}/visit", json={"outcome": "referred"}, headers=headers)
        assert refused.status_code == 403
    assert rig.get("/api/visitor/calls/no-such-call", headers=ALICE).status_code == 404
    assert rig.store.get("cases", case_id) == before
    assert rig.post(f"/api/cases/{case_id}/visit", json={"outcome": "referred"}, headers=SECRET).status_code == 200


def test_creating_a_call_needs_a_visitor_id(rig):
    for headers in ({}, {"X-Visitor": ""}, {"X-Visitor": "short"}, {"X-Visitor": "x" * 200}):
        response = rig.post("/api/visitor/calls", json={"card_id": "alone_dizzy", "language": "hi"}, headers=headers)
        assert response.status_code == 400
    assert rig.store.query("demo_usage") == []


def test_unknown_card_and_unknown_language(rig):
    assert start(rig, card="astronaut").status_code == 404
    assert start(rig, language="klingon").status_code == 422


def test_a_missing_record_other_than_the_card_is_not_called_an_unknown_card(rig, monkeypatch):
    def broken(*args, **kwargs):
        raise KeyError("wards/ward-x")

    monkeypatch.setattr(api_module, "create_visitor_call", broken)
    with pytest.raises(KeyError):
        start(rig)


def test_a_visitor_cannot_follow_a_call_of_the_seeded_round(rig, store):
    seeded = store.query("calls", round_id=rig.round_id)
    assert len(seeded) >= 30
    for call in seeded[:5]:
        for headers in (ALICE, {}):
            assert rig.get(f"/api/visitor/calls/{call['id']}", headers=headers).status_code == 404


ODD_IDS = ["__x__", "__name__", "a.b", "a b", "a%2Fb", "..", "x" * 201, "caf\u00e9"]


@pytest.mark.parametrize("odd", ODD_IDS)
def test_an_id_the_store_could_not_hold_is_simply_not_found(rig, store, monkeypatch, odd):
    made = start(rig).json()
    real_get = store.get

    def careful_get(collection, doc_id):
        assert collection not in ("calls", "cases", "wards") or api_module.storable_id(doc_id), doc_id
        return real_get(collection, doc_id)

    monkeypatch.setattr(store, "get", careful_get)
    assert rig.get(f"/api/visitor/calls/{odd}", headers=ALICE).status_code == 404
    assert rig.get(f"/api/wards/{odd}/cases", headers=ALICE).status_code == 404
    for headers in (ALICE, SECRET):
        visit = rig.post(f"/api/cases/{odd}/visit", json={"outcome": "referred"}, headers=headers)
        assert visit.status_code == 404
    assert rig.post(f"/admin/calls/{odd}/simulate", json={"status": "failed"}, headers=SECRET).status_code == 404
    assert rig.get(f"/api/visitor/calls/{made['call_id']}", headers=ALICE).status_code == 200  # a real id still works


def test_ids_this_service_makes_pass_the_check_and_unknown_ones_are_plainly_not_found(rig, store):
    made = start(rig).json()
    for collection in ("calls", "cases", "wards", "residents"):
        assert all(api_module.storable_id(doc["id"]) for doc in store.query(collection)), collection
    assert api_module.storable_id("a_b-C9") and api_module.storable_id("_x_") and api_module.storable_id("x" * 200)
    assert not api_module.storable_id("") and not api_module.storable_id("____")
    assert rig.get("/api/visitor/calls/_x_", headers=ALICE).status_code == 404  # well formed, unknown
    assert rig.post("/api/cases/_x_/visit", json={"outcome": "referred"}, headers=SECRET).status_code == 404
    assert rig.get("/api/wards/_x_/cases").json()["cases"] == []  # an unknown ward has no cases, as before
    assert made["call_id"]


def test_the_fourth_call_in_an_hour_is_refused_with_a_time_to_retry(rig, now):
    for minutes in (0, 10, 20):
        rig.clock.now = now + timedelta(minutes=minutes)
        assert start(rig).status_code == 200
    rig.clock.now = now + timedelta(minutes=30)
    refused = start(rig)
    assert refused.status_code == 429
    assert refused.json()["reason"] == "visitor_hourly"
    assert refused.json()["retry_after_s"] == 1800
    assert refused.headers["Retry-After"] == "1800"
    assert start(rig, who=BOB).status_code == 200


def test_busy_lines_answer_503_with_no_retry_time(rig, store, now):
    for n in range(3):
        made = start(rig, who={"X-Visitor": f"visitor-{n:012d}-aaaa"}).json()
        store.update("calls", made["call_id"], {"status": "in_progress", "started_at": now})
    refused = start(rig, who=BOB)
    assert refused.status_code == 503
    assert refused.json()["reason"] == "lines_busy"
    assert refused.json()["retry_after_s"] is None
    assert "Retry-After" not in refused.headers


def test_a_ticket_opens_one_call_and_never_appears_again(rig):
    made = start(rig).json()
    take_call(rig, made["ticket"])
    with pytest.raises(WebSocketDisconnect):
        with rig.websocket_connect(f"/browser/stream?ticket={made['ticket']}"):
            pass
    for path in (
        f"/api/visitor/calls/{made['call_id']}", "/api/visitor/calls/latest", "/api/rounds/current",
        f"/api/wards/{WARD_2}/cases", f"/api/rounds/{rig.round_id}/report",
    ):
        text = rig.get(path, headers=ALICE).text
        for forbidden in (made["ticket"], "browser_ticket", "stream_token", "twilio_sid"):
            assert forbidden not in text, path
        assert not re.search(r"\+\d{8,15}", text), path


def test_hanging_up_mid_call_keeps_what_was_recorded(store, now, tz):
    round_id = seeded_demo(store, now, tz)
    settings = Settings(
        _env_file=None, internal_secret="s3cret", max_call_seconds=20,
        fake_live=True, fake_live_step_seconds=0.05, transcript_save_seconds=0,
    )
    client = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now, open_session=open_fake_session))
    made = start(client).json()
    with client.websocket_connect(f"/browser/stream?ticket={made['ticket']}") as ws:
        ws.send_bytes(b"\x00\x00" * 320)
        for _ in range(400):
            answers = store.get("calls", made["call_id"]).get("answers") or {}
            if "feeling" in answers:
                break
            ws.receive()  # audio from the scripted agent; keeps the test in step with the call
    for _ in range(250):  # the server notices the closed socket a moment later
        if store.get("calls", made["call_id"])["status"] == "dropped":
            break
        time.sleep(0.02)
    feed = client.get(f"/api/visitor/calls/{made['call_id']}", headers=ALICE).json()
    assert feed["status"] == "dropped"
    assert feed["questions"][0]["value"] == "mild_symptoms"
    assert feed["case"]["tier"] == "amber"
    assert "A12" in feed["case"]["reasons"]  # "Call ended before all the questions were asked"
    assert store.query("calls", round_id=round_id, resident_id=store.get("calls", made["call_id"])["resident_id"]) == [
        store.get("calls", made["call_id"])
    ]  # no retry


def test_after_opening_the_days_rounds_the_old_call_is_kept_and_a_new_one_works(rig, now):
    made = start(rig).json()
    take_call(rig, made["ticket"])
    rig.clock.now = now + timedelta(minutes=5)
    assert rig.post("/internal/demo/rebuild", headers=SECRET).status_code == 200
    old = rig.get(f"/api/visitor/calls/{made['call_id']}", headers=ALICE)
    assert old.status_code == 200 and old.json()["status"] == "completed"  # nothing is deleted
    assert rig.get("/api/visitor/calls/latest", headers=ALICE).json() == {"call": None}  # none yet in today's round
    current = rig.get("/api/rounds/current", headers=ALICE).json()
    assert current["round"]["id"] == "churu_heat_20260520" and current["counts"]["targeted"] == 0
    again = start(rig).json()
    assert again["call_id"].startswith("churu_heat_20260520_v-")
    assert take_call(rig, again["ticket"]) == "completed"
    assert rig.get("/api/visitor/calls/latest", headers=ALICE).json()["call"]["call_id"] == again["call_id"]
    assert rig.get("/api/rounds/current", headers=ALICE).json()["counts"]["targeted"] == 1


def test_with_the_cache_on_visitors_share_one_read_and_still_see_their_own_call_at_once(store, now, tz):
    round_id = seeded_demo(store, now, tz)
    settings = Settings(
        _env_file=None, internal_secret="s3cret", max_call_seconds=20, view_cache_seconds=600,
        fake_live=True, fake_live_step_seconds=0, transcript_save_seconds=0,
    )
    client = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now, open_session=open_fake_session))
    paths = ("/api/rounds/current", f"/api/wards/{WARD_2}/cases", f"/api/rounds/{round_id}/report")
    anonymous = {path: client.get(path).json() for path in paths}

    made = start(client).json()
    assert take_call(client, made["ticket"]) == "completed"
    current = client.get("/api/rounds/current", headers=ALICE).json()
    assert [row["yours"] for row in current["calls"]].count(True) == 1
    assert current["counts"]["targeted"] == anonymous["/api/rounds/current"]["counts"]["targeted"] + 1
    assert client.get(f"/api/rounds/{round_id}/report", headers=ALICE).json()["includes_yours"] is True
    for path in paths:
        assert client.get(path, headers=BOB).json() == anonymous[path], path
        assert client.get(path).json() == anonymous[path], path

    # a change to what everyone sees: visitors get the kept copy, the team reads the store
    listed = client.get(f"/api/wards/{WARD_2}/cases").json()["cases"]
    seeded = next(c for c in listed if c["status"] == "open" and c["tier"] != "green")
    marked = client.post(f"/api/cases/{seeded['id']}/visit", json={"outcome": "referred"}, headers=SECRET)
    assert marked.status_code == 200
    for_visitors = {c["id"]: c["status"] for c in client.get(f"/api/wards/{WARD_2}/cases").json()["cases"]}
    for_team = {c["id"]: c["status"] for c in client.get(f"/api/wards/{WARD_2}/cases", headers=SECRET).json()["cases"]}
    assert (for_visitors[seeded["id"]], for_team[seeded["id"]]) == ("open", "visited")


def test_with_no_demo_round_a_call_cannot_start(store, now):
    settings = Settings(_env_file=None, internal_secret="s3cret")
    client = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now))
    assert start(client).status_code == 409


def test_failed_starts_are_refused_in_their_own_words(store, now, tz):
    from app.dispatch import handle_call_outcome

    seeded_demo(store, now, tz)
    settings = Settings(_env_file=None, internal_secret="s3cret", address_failed_starts_per_hour=1)
    client = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now))
    body = {"card_id": next(iter(CARDS)), "language": "hi"}
    made = client.post("/api/visitor/calls", json=body, headers={"X-Visitor": "visitor-aaaaaaaaaaaa"})
    assert made.status_code == 200
    handle_call_outcome(store, RecordingQueue(), made.json()["call_id"], "failed", now, tz)
    refused = client.post("/api/visitor/calls", json=body, headers={"X-Visitor": "visitor-bbbbbbbbbbbb"})
    assert refused.status_code == 429
    assert refused.json()["reason"] == "address_failed_starts"
    assert refused.json()["detail"] == "Calls from this network are not starting at the moment. Try again later."
    assert refused.headers["Retry-After"] == "3600"
