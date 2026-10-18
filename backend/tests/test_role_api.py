import json

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.call.fake_live import open_fake_session
from app.config import Settings
from app.db import MemoryStore
from app.dispatch import RecordingQueue
from seed.regions import DISTRICTS
from seed.seed import ward_ids
from tests.helpers import seeded_region

ALICE = {"X-Visitor": "3f2b8c1e-7a4d-4e2a-9b1c-5d6e7f8a9b0c"}
BOB = {"X-Visitor": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"}
TEAM = {"X-Internal-Secret": "s3cret"}
WARD_2 = ward_ids("churu")[0]  # the ward of the alone_dizzy card
WORKER_2, _, WORKER_58 = (w.replace("-ward-", "-worker-") for w in ward_ids("churu"))
BIKANER_WORKER = ward_ids("bikaner")[0].replace("-ward-", "-worker-")
BIKANER_RESIDENT = "bikaner-1-01"  # the first resident of Bikaner's first ward


@pytest.fixture
def rig(store, now, tz):
    round_id = seeded_region(store, now, tz)
    settings = Settings(
        _env_file=None, internal_secret="s3cret", max_call_seconds=20,
        fake_live=True, fake_live_step_seconds=0, transcript_save_seconds=0,
    )
    client = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now, open_session=open_fake_session))
    client.round_id, client.store = round_id, store
    return client


def take_test_call(client, who, card="alone_dizzy") -> dict:
    """Take a whole fake call as a visitor. Returns its feed."""
    made = client.post("/api/visitor/calls", json={"card_id": card, "language": "hi"}, headers=who).json()
    with client.websocket_connect(f"/browser/stream?ticket={made['ticket']}") as ws:
        ws.send_bytes(b"\x00\x00" * 320)
        while True:
            message = ws.receive()
            assert message["type"] != "websocket.close"
            if message.get("text") and json.loads(message["text"]).get("type") == "ended":
                break
    return client.get(f"/api/visitor/calls/{made['call_id']}", headers=who).json()


def test_the_drill_down_from_region_to_one_resident(rig):
    region = rig.get("/api/region").json()["districts"]
    ids = [d["id"] for d in region]
    assert ids[0] == "churu" and sorted(ids) == sorted(d[0] for d in DISTRICTS)
    district = rig.get("/api/districts/churu").json()
    ward = rig.get(f"/api/wards/{district['wards'][0]['id']}/detail").json()
    resident = rig.get(f"/api/residents/{ward['residents'][0]['id']}").json()
    assert resident["resident"]["district_name"] == "Churu" and resident["rules"]
    assert rig.get(f"/api/residents/{BIKANER_RESIDENT}").json()["call"] is None
    for path in ("/api/districts/nowhere", "/api/wards/nowhere/detail", "/api/residents/nobody", "/api/workers/nobody/residents", "/api/districts/a b"):
        assert rig.get(path).status_code == 404, path


def test_workers(rig):
    workers = rig.get("/api/workers").json()
    assert workers["district_id"] == "churu" and len(workers["workers"]) == 3
    mine = rig.get(f"/api/workers/{WORKER_2}/residents").json()
    assert mine["worker"]["name"] == "Sunita Kanwar" and mine["residents"]


def test_a_visitors_test_call_is_theirs_alone(rig):
    feed = take_test_call(rig, ALICE)
    resident_id = feed["resident_id"]
    hers = rig.get(f"/api/residents/{resident_id}", headers=ALICE)
    assert hers.status_code == 200 and hers.json()["resident"]["yours"] is True
    assert hers.json()["case"]["can_act"] is True and hers.json()["transcript"]
    assert rig.get(f"/api/residents/{resident_id}", headers=BOB).status_code == 404
    assert rig.get(f"/api/residents/{resident_id}").status_code == 404
    assert rig.get(f"/api/residents/{resident_id}", headers=TEAM).status_code == 404
    for path in ("/api/region", "/api/districts/churu", f"/api/wards/{WARD_2}/detail", "/api/workers", f"/api/workers/{WORKER_2}/residents"):
        text = rig.get(path, headers=BOB).text
        assert resident_id not in text and text == rig.get(path).text, path
        assert resident_id in rig.get(path, headers=ALICE).text or path in ("/api/region", "/api/workers"), path
    assert rig.get("/api/districts/churu", headers=ALICE).json()["round"]["called"] == 31


def test_the_whole_loop_on_a_visitors_own_case(rig):
    feed = take_test_call(rig, ALICE)
    case_id, resident_id = feed["case"]["id"], feed["resident_id"]

    def post(action, body, who=ALICE):
        return rig.post(f"/api/cases/{case_id}/{action}", json=body, headers=who)

    assert post("escalate", {"reason": "supplies", "note": " out  of ORS "}, BOB).status_code == 403
    flagged = post("escalate", {"reason": "supplies", "note": " out  of ORS " + "x" * 400})
    assert flagged.status_code == 200 and "visitor_id" not in flagged.json()
    assert len(flagged.json()["escalation"]["note"]) == 200
    again = post("escalate", {"reason": "supplies"})
    assert again.status_code == 409 and again.json()["reason"] == "already_flagged"
    assert post("escalate", {"reason": "nonsense"}).status_code == 422

    attention = rig.get("/api/districts/churu", headers=ALICE).json()["attention"]
    assert next(r for r in attention if r["yours"])["escalated"] is True
    assert post("acknowledge", {"reply": "on my way"}).json()["escalation"]["reply"] == "on my way"
    assert post("acknowledge", {}).json()["reason"] == "no_flag"

    moved = post("reassign", {"worker_id": WORKER_58})
    assert moved.json()["worker_id"] == WORKER_58 and moved.json()["reassigned_from"] == WORKER_2
    assert post("reassign", {"worker_id": BIKANER_WORKER}).status_code == 422
    assert resident_id in rig.get(f"/api/workers/{WORKER_58}/residents", headers=ALICE).text
    assert resident_id not in rig.get(f"/api/workers/{WORKER_2}/residents", headers=ALICE).text

    assert post("raise", {"observation": "nonsense"}).status_code == 422
    red = post("raise", {"observation": "fainted"})
    assert red.json()["tier"] == "red" and red.json()["reasons"][0] == "R5"
    assert post("raise", {"observation": "fainted"}).json()["reason"] == "not_amber"
    page = rig.get(f"/api/residents/{resident_id}", headers=ALICE).json()
    assert page["rules"][0]["id"] == "R5" and page["case"]["tier"] == "red"

    visit = post("visit", {"outcome": "hospital", "note": "  taken  by 108 "})
    assert visit.status_code == 200 and visit.json()["visit_note"] == "taken by 108"
    assert post("escalate", {"reason": "other"}).json()["reason"] == "not_open"

    report = rig.get(f"/api/rounds/{rig.round_id}/report", headers=ALICE).json()["response"]
    seeded = rig.get(f"/api/rounds/{rig.round_id}/report").json()["response"]
    assert (report["escalated"], report["acknowledged"], report["raised"], report["reassigned"]) == (
        seeded["escalated"] + 1, seeded["acknowledged"] + 1, 1, 1,
    )
    assert (seeded["escalated"], seeded["acknowledged"], seeded["raised"], seeded["reassigned"]) == (1, 0, 0, 0)


def test_a_seeded_case_is_for_the_team_only(rig):
    case_id = f"{rig.round_id}_r08"
    for action, body in (("escalate", {"reason": "supplies"}), ("acknowledge", {}), ("reassign", {"worker_id": WORKER_2}), ("raise", {"observation": "fainted"})):
        assert rig.post(f"/api/cases/{case_id}/{action}", json=body, headers=ALICE).status_code == 403, action
        assert rig.post(f"/api/cases/{case_id}/{action}", json=body).status_code == 403, action
        assert rig.post(f"/api/cases/no-such-case/{action}", json=body, headers=TEAM).status_code == 404, action
    assert rig.post(f"/api/cases/{case_id}/raise", json={"observation": "fainted"}, headers=TEAM).json()["tier"] == "red"
    assert rig.post(f"/api/cases/{rig.round_id}_r18/acknowledge", json={"reply": "seen"}, headers=TEAM).status_code == 200


def keys_of(value):
    """Every key at every depth, through dicts and lists."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from keys_of(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from keys_of(inner)


def test_no_phone_number_leaves_by_any_new_route(rig):
    feed = take_test_call(rig, ALICE)
    paths = ["/api/region", "/api/districts/churu", "/api/districts/bikaner", f"/api/wards/{WARD_2}/detail", "/api/residents/r08",
             f"/api/residents/{feed['resident_id']}", "/api/workers", f"/api/workers/{WORKER_2}/residents"]
    for path in paths:
        response = rig.get(path, headers=ALICE)
        assert response.status_code == 200, path
        assert "+91" not in response.text, path
        assert ALICE["X-Visitor"] not in response.text, path
        # a transcript may say "phone", so only the keys are searched
        for key in keys_of(response.json()):
            assert not any(word in key for word in ("phone", "visitor_id", "ticket", "token")), (path, key)


def test_a_worker_id_that_cannot_be_stored_is_an_unknown_worker(rig, monkeypatch):
    case_id = take_test_call(rig, ALICE)["case"]["id"]
    get = rig.store.get

    def strict_get(collection, doc_id):  # Firestore raises on an id with a slash; the memory store does not
        if "/" in doc_id:
            raise ValueError(doc_id)
        return get(collection, doc_id)

    monkeypatch.setattr(rig.store, "get", strict_get)
    refused = rig.post(f"/api/cases/{case_id}/reassign", json={"worker_id": "a/b"}, headers=ALICE)
    assert refused.status_code == 422
    assert refused.json() == {"detail": "no such worker in this district", "reason": "unknown_worker"}


def test_a_body_that_is_too_long_is_refused(rig):
    case_id = take_test_call(rig, ALICE)["case"]["id"]
    refused = rig.post(f"/api/cases/{case_id}/escalate", json={"reason": "supplies", "note": "x" * 5000}, headers=ALICE)
    assert refused.status_code == 422
    assert rig.post(f"/api/cases/{case_id}/acknowledge", json={"reply": "x" * 1001}, headers=ALICE).status_code == 422
    assert rig.post(f"/api/cases/{case_id}/reassign", json={"worker_id": "w" * 65}, headers=ALICE).status_code == 422


def test_with_the_caches_on_a_visitor_still_sees_only_their_own_call(store, now, tz):
    round_id = seeded_region(store, now, tz)
    settings = Settings(
        _env_file=None, internal_secret="s3cret", max_call_seconds=20, view_cache_seconds=600,
        fake_live=True, fake_live_step_seconds=0, transcript_save_seconds=0,
    )
    client = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now, open_session=open_fake_session))
    assert client.get("/api/districts/churu", headers=ALICE).json()["round"]["called"] == 30  # warms the caches
    resident_id = take_test_call(client, ALICE)["resident_id"]
    called = lambda who: client.get("/api/districts/churu", headers=who).json()["round"]["called"]  # noqa: E731
    assert called(ALICE) == 31
    assert called(BOB) == 30 and called({}) == 30
    assert called(ALICE) == 31
    assert resident_id not in client.get("/api/districts/churu", headers=BOB).text


def test_the_settings_a_call_ran_with_are_stored_but_never_served(rig):
    feed = take_test_call(rig, ALICE)
    call_id = next(call["id"] for call in rig.store.query("calls") if call.get("visitor_id"))
    assert rig.store.get("calls", call_id)["live_settings"]["live_start_sensitivity"] == "low"
    resident = rig.get(f"/api/residents/{feed['resident_id']}", headers=ALICE)
    assert resident.status_code == 200 and resident.json()["transcript"]
    served = rig.get(f"/api/visitor/calls/{call_id}", headers=ALICE)
    assert served.status_code == 200 and served.json() == feed
    for text in (served.text, resident.text):
        assert "live_settings" not in text and "live_voice" not in text and "live_start_sensitivity" not in text
        assert "timeline" not in text and "agent_turn" not in text and "audio_seconds" not in text
    assert "timeline" not in served.json() and "timeline" not in resident.json()
    stored = rig.store.get("calls", call_id)["timeline"]
    assert {event["kind"] for event in stored} >= {"agent_turn", "heard", "tool"}


class AskedStore(MemoryStore):
    """Notes which collections are queried by which fields."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple] = []

    def query(self, collection, **equals):
        self.asked.append((collection, tuple(sorted(equals))))
        return super().query(collection, **equals)


def test_with_the_register_cache_on_no_round_load_reads_the_districts_residents(now, tz):
    """Visitors' test residents are kept for good in Churu, so reading every resident of the
    district on each load would cost more each day. The team shares the register cache too."""
    store = AskedStore()
    seeded_region(store, now, tz)
    settings = Settings(
        _env_file=None, internal_secret="s3cret", max_call_seconds=20, register_cache_seconds=86400,
        fake_live=True, fake_live_step_seconds=0, transcript_save_seconds=0,
    )
    client = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now, open_session=open_fake_session))
    resident_id = take_test_call(client, ALICE)["resident_id"]
    first = {who: client.get("/api/districts/churu", headers=headers).json() for who, headers in (("team", TEAM), ("bob", BOB))}
    store.asked = []
    for who, headers in (("team", TEAM), ("bob", BOB), ("alice", ALICE)):
        shown = client.get("/api/districts/churu", headers=headers)
        assert shown.status_code == 200
        if who in first:
            assert shown.json() == first[who]
            assert resident_id not in shown.text
        else:
            assert resident_id in shown.text  # her own, read by her id
    assert ("residents", ("district_id",)) not in store.asked
    assert ("residents", ("visitor_id",)) in store.asked  # Alice's own records, asked for by her id
