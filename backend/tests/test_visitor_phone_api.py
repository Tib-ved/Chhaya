import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.call.fake_live import open_fake_session
from app.config import Settings
from app.dispatch import RecordingQueue
from app.models import VISITOR_USAGE
from app.sweep import sweep
from tests.helpers import seeded_demo

ALICE = {"X-Visitor": "3f2b8c1e-7a4d-4e2a-9b1c-5d6e7f8a9b0c"}
BOB = {"X-Visitor": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"}
PHONE = "+919812345678"
FORM = {"Content-Type": "application/x-www-form-urlencoded", "X-Twilio-Signature": "sig"}


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class Rig:
    def __init__(self, store, now, tz, place="record", signature_ok=True, **overrides):
        self.store, self.clock, self.placed = store, Clock(now), []
        seeded_demo(store, now, tz)

        def record(to, twiml, status_callback, time_limit=None):
            self.placed.append({"to": to, "twiml": twiml, "status_callback": status_callback, "time_limit": time_limit})
            return "CA123"

        def fail(to, twiml, status_callback, time_limit=None):
            raise RuntimeError("provider down")

        self.settings = Settings(
            _env_file=None, internal_secret="s3cret", public_host="voice.example.test",
            fake_live=True, fake_live_step_seconds=0, transcript_save_seconds=0,
            visitor_phone_rates_usd={"91": 0.10, "61": 0.10}, visitor_phone_countries="91,61",
            **overrides,
        )
        app = create_app(
            store, RecordingQueue(), self.settings, clock=self.clock, open_session=open_fake_session,
            place_call={"record": record, "fail": fail}[place],
            validate_twilio=lambda url, params, signature: signature_ok,
        )
        self.client = TestClient(app)

    def start(self, phone=PHONE, who=ALICE):
        return self.client.post(
            "/api/visitor/phone-calls", json={"card_id": "alone_dizzy", "language": "hi", "phone": phone}, headers=who
        )

    def status(self, call_id, twilio_status, duration):
        return self.client.post(
            f"/twilio/status?call_id={call_id}",
            content=f"CallStatus={twilio_status}&CallDuration={duration}", headers=FORM,
        )

    def call(self, call_id):
        return self.store.get("calls", call_id)

    def resident(self, call_id):
        return self.store.get("residents", self.call(call_id)["resident_id"])


@pytest.fixture
def rig(store, now, tz):
    return Rig(store, now, tz)


def test_a_phone_call_is_dialled_at_once_straight_to_the_agent(rig):
    made = rig.start()
    assert made.status_code == 200 and set(made.json()) == {"call_id"}
    call_id = made.json()["call_id"]
    [placed] = rig.placed
    assert placed["to"] == PHONE
    assert "<Gather" not in placed["twiml"] and "wss://voice.example.test/twilio/stream" in placed["twiml"]
    assert placed["time_limit"] == 180 + 30
    call = rig.call(call_id)
    assert call["status"] == "dialing" and call["channel"] == "phone" and call["twilio_sid"] == "CA123"
    assert "9812345678" not in made.text
    assert "9812345678" not in rig.client.get(f"/api/visitor/calls/{call_id}", headers=ALICE).text


@pytest.mark.parametrize("phone, reason", [
    ("98123 45678", "phone_invalid"),
    ("+91", "phone_invalid"),
    ("+8613812345678", "country_not_allowed"),
])
def test_numbers_that_cannot_be_rung_are_refused_before_anything_is_written(rig, phone, reason):
    refused = rig.start(phone)
    assert refused.status_code == 422 and refused.json()["reason"] == reason
    assert rig.placed == [] and rig.store.query(VISITOR_USAGE) == []


def test_a_visitor_id_is_required(rig):
    response = rig.client.post("/api/visitor/phone-calls", json={"card_id": "alone_dizzy", "language": "hi", "phone": PHONE})
    assert response.status_code == 400


def test_a_number_is_rung_twice_a_day_at_most(rig):
    first = rig.start(who=ALICE).json()["call_id"]
    rig.status(first, "completed", 30)
    second = rig.start(who=BOB).json()["call_id"]
    rig.status(second, "completed", 30)
    refused = rig.start(who=ALICE)
    assert refused.status_code == 429 and refused.json()["reason"] == "phone_number_daily"


def test_the_budget_stops_phone_calls(store, now, tz):
    rig = Rig(store, now, tz, visitor_phone_daily_budget_usd=0.5)  # one reservation is 0.40
    assert rig.start(PHONE).status_code == 200
    refused = rig.start("+61412345678", who=BOB)
    assert refused.status_code == 429 and refused.json()["reason"] == "phone_budget"
    assert "browser" in refused.json()["detail"]


def test_answered_but_never_streamed_is_failed_and_settles_the_cost(rig):
    call_id = rig.start().json()["call_id"]
    rig.status(call_id, "completed", 14)  # answered, but the agent's stream never started
    call = rig.call(call_id)
    assert call["status"] == "failed" and call.get("end_outcome") is None
    assert rig.resident(call_id)["phone"] == ""
    assert rig.store.get(VISITOR_USAGE, call_id)["cost_usd"] == pytest.approx(0.10)


def test_a_bad_signature_is_refused(store, now, tz):
    rig = Rig(store, now, tz, signature_ok=False)
    call_id = rig.start().json()["call_id"]
    assert rig.status(call_id, "completed", 10).status_code == 403


def test_the_provider_refusing_ends_the_call_and_frees_the_reservation(store, now, tz):
    rig = Rig(store, now, tz, place="fail")
    refused = rig.start()
    assert refused.status_code == 502 and refused.json()["reason"] == "provider_error"
    [entry] = rig.store.query(VISITOR_USAGE)
    assert entry["cost_usd"] == 0
    call = rig.store.get("calls", entry["id"])
    assert call["status"] == "failed"
    assert rig.store.get("residents", call["resident_id"])["phone"] == ""


def test_no_answer_erases_the_number(rig):
    call_id = rig.start().json()["call_id"]
    rig.status(call_id, "no-answer", 0)
    assert rig.call(call_id)["status"] == "no_answer"
    assert rig.resident(call_id)["phone"] == ""
    assert rig.store.get(VISITOR_USAGE, call_id)["cost_usd"] == 0


def test_sweep_ends_a_stuck_phone_call_and_erases_the_number(rig, tz):
    from datetime import timedelta
    call_id = rig.start().json()["call_id"]
    rig.clock.now += timedelta(minutes=16)
    sweep(rig.store, RecordingQueue(), rig.settings, rig.clock.now, tz)
    assert rig.call(call_id)["status"] == "failed"
    assert rig.resident(call_id)["phone"] == ""


def test_the_feed_carries_the_case_note(rig):
    call_id = rig.start().json()["call_id"]
    call = rig.call(call_id)
    rig.store.set("cases", f"{call['round_id']}_{call['resident_id']}", {
        "id": f"{call['round_id']}_{call['resident_id']}", "tier": "amber", "reasons": ["A1"],
        "ward_id": rig.resident(call_id)["ward_id"], "visitor_id": ALICE["X-Visitor"], "status": "open",
        "note": "Feels dizzy; a neighbour will look in.",
    })
    feed = rig.client.get(f"/api/visitor/calls/{call_id}", headers=ALICE).json()
    assert feed["case"]["note"] == "Feels dizzy; a neighbour will look in."


def test_a_long_number_is_refused_as_invalid_and_never_echoed(rig):
    long = "+91" + "9" * 37
    assert len(long) == 40
    refused = rig.start(long)
    assert refused.status_code == 422 and refused.json()["reason"] == "phone_invalid"
    assert "9999999" not in refused.text
    assert rig.placed == [] and rig.store.query(VISITOR_USAGE) == []


def browser_call(rig):
    made = rig.client.post("/api/visitor/calls", json={"card_id": "alone_dizzy", "language": "hi"}, headers=ALICE)
    assert made.status_code == 200
    return made.json()["call_id"]


def test_a_released_browser_call_goes_back_to_the_browser(rig):
    from types import SimpleNamespace
    from app.call.browser_bridge import _release
    call_id = browser_call(rig)
    rig.store.update("calls", call_id, {"status": "dialing", "channel": "browser"})
    assert _release(SimpleNamespace(store=rig.store), call_id)
    assert (rig.call(call_id)["status"], rig.call(call_id)["channel"]) == ("queued", "browser")


def test_the_phone_pass_leaves_a_browser_call_alone_even_marked_phone(rig, tz):
    from datetime import timedelta
    call_id = browser_call(rig)
    rig.store.update("calls", call_id, {"status": "queued", "channel": "phone"})  # as an old release left it
    rig.clock.now += timedelta(minutes=16)
    sweep(rig.store, RecordingQueue(), rig.settings, rig.clock.now, tz)
    assert rig.call(call_id)["status"] == "queued"
    assert not rig.store.get(VISITOR_USAGE, call_id).get("failed")


def test_a_claim_that_fails_frees_the_reservation_and_erases_the_number(rig, monkeypatch):
    real = rig.store.transition

    def refuse_claim(collection, doc_id, field, expected, updates):
        if collection == "calls" and "stream_token" in updates:  # the claim only
            return False
        return real(collection, doc_id, field, expected, updates)

    monkeypatch.setattr(rig.store, "transition", refuse_claim)
    refused = rig.start()
    assert refused.status_code == 502 and refused.json()["reason"] == "provider_error"
    assert rig.placed == []
    [entry] = rig.store.query(VISITOR_USAGE)
    assert entry["cost_usd"] == 0
    call = rig.store.get("calls", entry["id"])
    assert call["status"] == "failed"
    assert rig.store.get("residents", call["resident_id"])["phone"] == ""


def test_saving_the_provider_id_failing_still_answers_200(rig, monkeypatch):
    real = rig.store.update

    def broken(collection, doc_id, fields):
        if "twilio_sid" in fields:
            raise RuntimeError("store down")
        return real(collection, doc_id, fields)

    monkeypatch.setattr(rig.store, "update", broken)
    made = rig.start()
    assert made.status_code == 200 and len(rig.placed) == 1


def test_a_malformed_body_is_refused_without_echoing_the_number(rig):
    response = rig.client.post(
        "/api/visitor/phone-calls", json={"card_id": "alone_dizzy", "language": "xx", "phone": PHONE}, headers=ALICE
    )
    assert response.status_code == 422
    assert response.json()["reason"] == "bad_request"
    assert PHONE not in response.text and PHONE.lstrip("+") not in response.text
