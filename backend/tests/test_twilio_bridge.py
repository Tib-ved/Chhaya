import asyncio
import base64
import logging
import math
import struct
import threading
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.call.twilio_bridge as bridge
from app.api import create_app
from app.call.twilio_bridge import build_twiml, map_twilio_status, status_callback_url
from app.config import Settings
from tests.fakes import FakeLiveSession, audio, healthy_call_turns, said, session_opener, tool, turn_complete
from tests.helpers import queue_one_call

SECRET = {"X-Internal-Secret": "s3cret"}
PHONE = "+910000000001"  # the test resident's synthetic number


def tone(milliseconds: int) -> bytes:
    count = 24000 * milliseconds // 1000
    return b"".join(struct.pack("<h", int(9000 * math.sin(n / 9))) for n in range(count))


class Rig:
    def __init__(self, store, tz, now, session=None, allow=PHONE, host="voice.example.test",
                 place_call="record", valid_signature=True, validator=True, text_model=None):
        self.store = store
        self.round_id, self.call_id, self.queue = queue_one_call(store, tz, now)
        self.placed = []

        def record_call(to, twiml, status_callback):
            self.placed.append({"to": to, "twiml": twiml, "status_callback": status_callback})
            return "CA123"

        def failing_call(to, twiml, status_callback):
            raise RuntimeError("provider down")

        chosen = {"record": record_call, "fail": failing_call, None: None}[place_call]
        self.signatures = []

        def validate(url, params, signature):
            self.signatures.append((url, params, signature))
            return valid_signature

        settings = Settings(
            _env_file=None, internal_secret="s3cret", max_call_seconds=2,
            public_host=host, dial_allowlist=allow,
        )
        self.clock_value = now
        app = create_app(
            store, self.queue, settings, clock=lambda: self.clock_value,
            open_session=session_opener(session or FakeLiveSession([])),
            place_call=chosen, validate_twilio=validate if validator else None,
            text_model=text_model,
        )
        self.client = TestClient(app)

    def dial(self):
        return self.client.post(f"/internal/calls/{self.call_id}/dial", headers=SECRET)

    def call(self):
        return self.store.get("calls", self.call_id)


@pytest.fixture(autouse=True)
def short_goodbye(monkeypatch):
    monkeypatch.setattr(bridge, "GOODBYE_WAIT_SECONDS", 0.05)


# ---- pure helpers

@pytest.mark.parametrize(
    "twilio, current, expected",
    [
        ("busy", "dialing", "busy"),
        ("no-answer", "dialing", "no_answer"),
        ("failed", "dialing", "failed"),
        ("canceled", "dialing", "failed"),
        ("completed", "in_progress", "dropped"),
        ("completed", "completed", "dropped"),
        ("completed", "dialing", "failed"),
        ("completed", "queued", "failed"),
        ("busy", "in_progress", "dropped"),
        ("no-answer", "in_progress", "dropped"),
        ("failed", "in_progress", "dropped"),
        ("canceled", "in_progress", "dropped"),
        ("busy", "completed", "dropped"),
        ("failed", "dropped", "dropped"),
        ("completed", "dropped", "dropped"),
    ],
)
def test_map_twilio_status(twilio, current, expected):
    assert map_twilio_status(twilio, current) == expected


@pytest.mark.parametrize("value", ["ringing", "in-progress", "queued", "", "no_answer", "weird"])
def test_map_twilio_status_refuses_what_it_cannot_translate(value):
    with pytest.raises(ValueError):
        map_twilio_status(value, "dialing")


def test_twiml_and_callback_url_escape_their_inputs():
    twiml = build_twiml("voice.example.test", 'a"b<c', "tok&en")
    assert '<Stream url="wss://voice.example.test/twilio/stream">' in twiml
    assert 'value="a&quot;b&lt;c"' in twiml
    assert 'value="tok&amp;en"' in twiml
    assert status_callback_url("voice.example.test", "round 1/x") == (
        "https://voice.example.test/twilio/status?call_id=round%201%2Fx"
    )


# ---- dial

def test_dial_places_one_call_and_records_it(store, tz, now):
    rig = Rig(store, tz, now)
    assert rig.dial().json() == {"dialed": True}
    assert len(rig.placed) == 1
    placed = rig.placed[0]
    assert placed["to"] == PHONE
    assert placed["status_callback"] == status_callback_url("voice.example.test", rig.call_id)
    call = rig.call()
    assert call["status"] == "dialing"
    assert call["twilio_sid"] == "CA123"
    assert call["channel"] == "phone"
    assert len(call["stream_token"]) >= 24
    assert f'value="{call["stream_token"]}"' in placed["twiml"]
    assert rig.call_id in placed["twiml"]


def test_dial_refuses_a_call_whose_round_is_complete(store, tz, now):
    rig = Rig(store, tz, now)
    store.update("rounds", rig.round_id, {"status": "complete", "completed_at": now})
    before = rig.call()
    response = rig.dial()
    assert response.status_code == 200
    assert response.json() == {"dialed": False, "reason": "round_complete"}
    assert rig.placed == [] and rig.call() == before
    store.update("rounds", rig.round_id, {"status": "calling"})
    assert rig.dial().json() == {"dialed": True}  # a round being called is dialled as before
    assert len(rig.placed) == 1


def test_the_first_sweep_closes_an_old_round_and_its_queued_task_rings_nobody(store, tz, now):
    from datetime import timedelta

    rig = Rig(store, tz, now)
    store.update("rounds", rig.round_id, {"opened_at": now - timedelta(days=3), "started_at": now - timedelta(days=3)})
    store.update("calls", rig.call_id, {"not_before": None})  # as stored before calls had a due time
    rig.queue.items.clear()
    body = rig.client.post("/internal/sweep", headers=SECRET).json()
    assert (body["calls"]["expired"], body["calls"]["requeued"]) == (1, 0)
    assert rig.queue.items == []
    assert store.get("rounds", rig.round_id)["status"] == "complete"
    assert rig.dial().json() == {"dialed": False, "reason": "round_complete"}  # a task queued long ago
    assert rig.placed == [] and rig.call()["status"] == "queued"


def test_dial_is_idempotent(store, tz, now):
    rig = Rig(store, tz, now)
    rig.dial()
    second = rig.dial().json()
    assert second["dialed"] is False
    assert len(rig.placed) == 1


def test_dial_refuses_numbers_off_the_allowlist(store, tz, now):
    rig = Rig(store, tz, now, allow="+910000000099")
    assert rig.dial().json() == {"dialed": False, "reason": "not_on_allowlist"}
    assert rig.placed == []
    assert rig.call()["status"] == "queued"  # left for the control room to simulate


def test_dial_with_an_empty_allowlist_dials_nobody(store, tz, now):
    rig = Rig(store, tz, now, allow="")
    assert rig.dial().json()["reason"] == "not_on_allowlist"
    assert rig.placed == []


def test_dial_refuses_outside_calling_hours(store, tz, now):
    rig = Rig(store, tz, now)
    rig.clock_value = datetime(2026, 5, 20, 16, 0, tzinfo=timezone.utc)  # 21:30 in Asia/Kolkata
    assert rig.dial().json() == {"dialed": False, "reason": "outside_calling_hours"}
    assert rig.placed == []
    assert rig.call()["status"] == "queued"


def test_dial_provider_error_counts_as_a_failed_attempt(store, tz, now):
    rig = Rig(store, tz, now, place_call="fail")
    assert rig.dial().json() == {"dialed": False, "reason": "provider_error"}
    assert rig.call()["status"] == "failed"
    assert store.get("calls", f"{rig.round_id}_r1_2")["status"] == "queued"


def test_dial_without_telephony_configured_is_503(store, tz, now):
    assert Rig(store, tz, now, place_call=None).dial().status_code == 503
    assert Rig(store, tz, now, host="").dial().status_code == 503


def test_dial_needs_the_secret_and_a_known_call(store, tz, now):
    rig = Rig(store, tz, now)
    assert rig.client.post(f"/internal/calls/{rig.call_id}/dial").status_code == 401
    assert rig.client.post("/internal/calls/nope/dial", headers=SECRET).status_code == 404


# ---- status callback

def post_status(rig, status, signature="sig"):
    return rig.client.post(
        f"/twilio/status?call_id={rig.call_id}",
        data={"CallStatus": status, "CallSid": "CA123"},
        headers={"X-Twilio-Signature": signature},
    )


def test_status_no_answer_schedules_a_retry(store, tz, now):
    rig = Rig(store, tz, now)
    rig.dial()
    assert post_status(rig, "no-answer").status_code == 200
    assert rig.call()["status"] == "no_answer"
    assert store.get("calls", f"{rig.round_id}_r1_2")["status"] == "queued"
    url, params, signature = rig.signatures[0]
    assert url == status_callback_url("voice.example.test", rig.call_id)
    assert params == {"CallStatus": "no-answer", "CallSid": "CA123"}
    assert signature == "sig"


def test_status_with_a_bad_signature_is_403_and_changes_nothing(store, tz, now):
    rig = Rig(store, tz, now, valid_signature=False)
    rig.dial()
    assert post_status(rig, "no-answer").status_code == 403
    assert rig.call()["status"] == "dialing"


def test_status_it_cannot_translate_is_400_and_changes_nothing(store, tz, now):
    rig = Rig(store, tz, now)
    rig.dial()
    assert post_status(rig, "ringing").status_code == 400
    assert rig.call()["status"] == "dialing"


def test_status_completed_after_the_bridge_finished_changes_nothing(store, tz, now):
    rig = Rig(store, tz, now)
    rig.dial()
    store.update("calls", rig.call_id, {"status": "completed"})
    assert post_status(rig, "completed").status_code == 200
    assert rig.call()["status"] == "completed"


def test_status_completed_when_the_stream_never_started_is_a_failed_attempt(store, tz, now):
    rig = Rig(store, tz, now)
    rig.dial()
    post_status(rig, "completed")
    assert rig.call()["status"] == "failed"


def test_status_for_an_unknown_call_is_404(store, tz, now):
    rig = Rig(store, tz, now)
    response = rig.client.post(
        "/twilio/status?call_id=nope", data={"CallStatus": "busy"}, headers={"X-Twilio-Signature": "s"}
    )
    assert response.status_code == 404


# ---- media stream

def start_message(rig, token=None, call_id=None):
    return {
        "event": "start",
        "start": {
            "streamSid": "MZ1",
            "customParameters": {
                "call_id": call_id or rig.call_id,
                "token": token if token is not None else rig.call()["stream_token"],
            },
        },
    }


def media_message():
    return {"event": "media", "media": {"payload": base64.b64encode(b"\xff" * 160).decode()}}


def read_until(ws, event):
    seen = []
    while True:
        message = ws.receive_json()
        seen.append(message)
        if message["event"] == event:
            return seen


def test_a_whole_phone_call(store, tz, now):
    turns = healthy_call_turns()
    turns[-1][0] = audio(tone(60))  # enough model audio for whole phone frames
    rig = Rig(store, tz, now, session=FakeLiveSession(turns))
    rig.dial()
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(start_message(rig))
        ws.send_json(media_message())
        seen = read_until(ws, "mark")
        ws.send_json({"event": "mark", "mark": {"name": "goodbye"}})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1000  # a normal hang-up
    media = [m for m in seen if m["event"] == "media"]
    assert len(media) >= 2
    assert all(m["streamSid"] == "MZ1" for m in seen)
    assert all(len(base64.b64decode(m["media"]["payload"])) == 160 for m in media)
    assert rig.call()["status"] == "completed"
    assert store.get("cases", f"{rig.round_id}_r1")["tier"] == "green"


def test_after_a_phone_call_the_case_gets_its_note_once(store, tz, now):
    told = []

    def text_model(instruction, prompt):
        told.append(prompt)
        return "Says she feels dizzy. Check how she is now."

    turns = healthy_call_turns()
    turns[2] = [said("resident", "chakkar aa raha hai"), tool("c1", "record_answer", question_id="feeling", value="mild_symptoms", quote="chakkar"), turn_complete()]
    turns[-1][0] = audio(tone(60))
    rig = Rig(store, tz, now, session=FakeLiveSession(turns), text_model=text_model)
    rig.dial()
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(start_message(rig))
        ws.send_json(media_message())
        read_until(ws, "mark")
        assert told == []  # the note waits until the phone has been hung up
        ws.send_json({"event": "mark", "mark": {"name": "goodbye"}})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
        # The server hangs up first and writes the note afterwards, so the socket gives no sign
        # of it. Wait for the note in here: leaving this block makes the test client cancel the
        # server's handler, and a step not yet in its worker thread would never run.
        case_id = f"{rig.round_id}_r1"
        deadline = time.monotonic() + 10
        while not store.get("cases", case_id)["note"] and time.monotonic() < deadline:
            time.sleep(0.02)
    assert rig.call()["status"] == "completed"
    case = store.get("cases", case_id)
    assert (case["tier"], case["note"], case["note_tries"]) == ("amber", "Says she feels dizzy. Check how she is now.", 1)
    assert len(told) == 1 and "chakkar" in told[0]


def test_stream_with_a_wrong_token_is_closed_and_the_call_untouched(store, tz, now):
    rig = Rig(store, tz, now)
    rig.dial()
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json(start_message(rig, token="wrong"))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert rig.call()["status"] == "dialing"


def test_stream_for_a_call_that_was_never_dialled_is_closed(store, tz, now):
    rig = Rig(store, tz, now)  # still queued: no token has been issued
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json(start_message(rig, token=""))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert rig.call()["status"] == "queued"


def test_a_token_cannot_be_used_for_a_second_stream(store, tz, now):
    rig = Rig(store, tz, now, session=FakeLiveSession(healthy_call_turns()))
    rig.dial()
    token = rig.call()["stream_token"]
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json(start_message(rig, token=token))
        read_until(ws, "mark")
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json(start_message(rig, token=token))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_hanging_up_the_phone_drops_the_call(store, tz, now, monkeypatch):
    monkeypatch.setattr(bridge, "GOODBYE_WAIT_SECONDS", 30)  # must not be waited for
    turns = [
        # an answer is taken once the two follow-ups everyone is asked have had their replies
        [said("resident", "chakkar aa raha hai"), said("agent", "Sirdard?"), said("resident", "haan"), turn_complete()],
        [said("agent", "Ulti?"), said("resident", "nahi"), tool("c1", "record_answer", question_id="feeling", value="mild_symptoms"), turn_complete()],
    ]
    rig = Rig(store, tz, now, session=FakeLiveSession(turns))
    rig.dial()
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json(start_message(rig))
        ws.send_json(media_message())
        for _ in range(250):  # the resident hangs up after their answer has been taken
            if rig.call().get("answers"):
                break
            time.sleep(0.02)
        ws.send_json({"event": "stop"})
        started = time.monotonic()
        with pytest.raises(WebSocketDisconnect) as closed:  # no mark is sent to a phone that has gone
            for _ in range(50):
                assert ws.receive_json()["event"] != "mark"
        assert closed.value.code == 1000
    assert time.monotonic() - started < 10
    assert rig.call()["status"] == "dropped"
    assert store.get("cases", f"{rig.round_id}_r1")["tier"] == "amber"


def test_interruption_sends_clear_to_twilio(store, tz, now):
    from tests.fakes import interrupted

    turns = [
        [audio(tone(40)), interrupted(), turn_complete()],
        [audio(tone(40)), tool("e", "end_call", outcome="completed"), turn_complete()],
    ]
    rig = Rig(store, tz, now, session=FakeLiveSession(turns))
    rig.dial()
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json(start_message(rig))
        seen = read_until(ws, "mark")
    assert {"event": "clear", "streamSid": "MZ1"} in seen


# ---- the real client (local SDK only, no network)

def test_make_twilio_client_builds_and_rejects_a_made_up_signature():
    settings = Settings(
        _env_file=None, twilio_account_sid="ACdummy", twilio_auth_token="dummy-token", twilio_from="+910000000002"
    )
    place_call, validate = bridge.make_twilio_client(settings)
    assert callable(place_call)
    assert validate("https://voice.example.test/twilio/status?call_id=x", {"CallStatus": "busy"}, "made-up") is False


# ---- dial: more refusals

def test_dial_refuses_when_the_resident_is_missing(store, tz, now):
    rig = Rig(store, tz, now)
    store.delete("residents", "r1")
    assert rig.dial().json() == {"dialed": False, "reason": "resident_missing"}
    assert rig.placed == []
    assert rig.call()["status"] == "queued"


def test_dial_when_the_store_fails_places_no_call(store, tz, now, monkeypatch, caplog):
    rig = Rig(store, tz, now)

    def broken(*args, **kwargs):
        raise RuntimeError("store down")

    monkeypatch.setattr(store, "transition", broken)
    with caplog.at_level(logging.ERROR):
        response = rig.dial()
        assert response.status_code == 503
        assert response.json() == {"dialed": False, "reason": "store_error"}
    assert rig.placed == []
    assert rig.call()["status"] == "queued"
    assert any(rig.call_id in r.getMessage() for r in caplog.records)


def test_a_dial_never_logs_the_phone_number_or_the_token(store, tz, now, caplog):
    rig = Rig(store, tz, now, place_call="fail")
    with caplog.at_level(logging.DEBUG):
        rig.dial()
    token = rig.call()["stream_token"]
    assert caplog.records
    for record in caplog.records:
        text = record.getMessage() + (record.exc_text or "")
        assert PHONE not in text
        assert token not in text


def test_nothing_public_shows_the_stream_token_or_the_provider_id(store, tz, now):
    rig = Rig(store, tz, now)
    rig.dial()
    token = rig.call()["stream_token"]
    body = {"status": "dropped", "answers": {"feeling": "mild_symptoms"}}
    assert rig.client.post(f"/admin/calls/{rig.call_id}/simulate", headers=SECRET, json=body).status_code == 200
    assert rig.client.get("/api/wards/w1/cases").json()["cases"]
    for url in ("/api/rounds/current", "/api/wards/w1/cases", f"/api/rounds/{rig.round_id}/report"):
        response = rig.client.get(url)
        assert response.status_code == 200
        for forbidden in (token, "stream_token", "twilio_sid", PHONE):
            assert forbidden not in response.text
    assert token not in rig.dial().text


# ---- the stream: hostile and broken connections

def refused_after(ws):
    with pytest.raises(WebSocketDisconnect):
        for _ in range(5):
            ws.receive_json()


def test_a_client_that_sends_nothing_is_closed(store, tz, now, monkeypatch):
    monkeypatch.setattr(bridge, "START_WAIT_SECONDS", 0.1)
    rig = Rig(store, tz, now)
    rig.dial()
    with rig.client.websocket_connect("/twilio/stream") as ws:
        refused_after(ws)
    assert rig.call()["status"] == "dialing"


def test_a_client_that_sends_junk_is_closed_and_the_store_untouched(store, tz, now, monkeypatch):
    rig = Rig(store, tz, now)
    rig.dial()
    token = rig.call()["stream_token"]

    touched = []

    def no_store(*args, **kwargs):
        touched.append(1)
        return None

    with monkeypatch.context() as patch:
        patch.setattr(store, "get", no_store)
        patch.setattr(store, "transition", no_store)
        with rig.client.websocket_connect("/twilio/stream") as ws:
            ws.send_json({"event": "connected"})
            ws.send_text("this is not json")
            ws.send_json({"event": "media", "media": {"payload": "AAAA"}})
            ws.send_json({"event": "start", "start": {"customParameters": {"call_id": rig.call_id}}})  # no token
            refused_after(ws)
    assert touched == []
    assert rig.call()["status"] == "dialing"
    assert rig.call()["stream_token"] == token


def test_more_than_twenty_messages_before_start_closes_the_socket(store, tz, now):
    rig = Rig(store, tz, now, session=FakeLiveSession(healthy_call_turns()))
    rig.dial()
    with rig.client.websocket_connect("/twilio/stream") as ws:
        for _ in range(25):
            ws.send_json({"event": "media", "media": {"payload": "AAAA"}})
        ws.send_json(start_message(rig))  # too late
        refused_after(ws)
    assert rig.call()["status"] == "dialing"


# ---- the stream: a call that connects but never starts

def test_a_call_the_session_refuses_is_failed_and_retried(store, tz, now):
    rig = Rig(store, tz, now)
    rig.dial()
    store.delete("rounds", rig.round_id)  # conduct_call will refuse
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json(start_message(rig))
        refused_after(ws)
    assert rig.call()["status"] == "failed"
    assert store.get("calls", f"{rig.round_id}_r1_2")["status"] == "queued"


def test_a_call_that_errors_before_starting_is_failed_and_logged(store, tz, now, monkeypatch, caplog):
    rig = Rig(store, tz, now)
    rig.dial()

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(bridge, "conduct_call", boom)
    with caplog.at_level(logging.ERROR):
        with rig.client.websocket_connect("/twilio/stream") as ws:
            ws.send_json(start_message(rig))
            refused_after(ws)
    assert rig.call()["status"] == "failed"
    assert store.get("calls", f"{rig.round_id}_r1_2")["status"] == "queued"
    assert any(r.exc_info and rig.call_id in r.getMessage() for r in caplog.records)


def test_an_error_after_the_call_started_leaves_the_stored_outcome_alone(store, tz, now, monkeypatch):
    rig = Rig(store, tz, now)
    rig.dial()

    async def started_then_boom(deps, call_id, *args, **kwargs):
        store.update("calls", call_id, {"status": "in_progress"})
        raise RuntimeError("boom")

    monkeypatch.setattr(bridge, "conduct_call", started_then_boom)
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json(start_message(rig))
        refused_after(ws)
    assert rig.call()["status"] == "in_progress"  # conduct_call owns recording that outcome
    assert store.get("calls", f"{rig.round_id}_r1_2") is None


def test_the_audio_queue_is_bounded_and_the_hang_up_gets_through(store, tz, now):
    release = threading.Event()

    class Stalled(FakeLiveSession):
        async def send_realtime_input(self, **kwargs):
            first = not self.sent_audio
            await super().send_realtime_input(**kwargs)
            if first:
                await asyncio.to_thread(release.wait, 10)

    session = Stalled([])
    rig = Rig(store, tz, now, session=session)
    rig.dial()
    store.update("calls", rig.call_id, {"respondent": "self"})  # someone spoke, so a hang-up is "dropped"
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json(start_message(rig))
        for _ in range(400):
            ws.send_json(media_message())
        ws.send_json({"event": "stop"})
        threading.Timer(0.5, release.set).start()
        for _ in range(250):
            if rig.call()["status"] == "dropped":
                break
            time.sleep(0.02)
    assert rig.call()["status"] == "dropped"
    assert len(session.sent_audio) <= 251


# ---- fix round 1: the real client

@pytest.mark.parametrize("missing", ["twilio_account_sid", "twilio_auth_token", "twilio_from"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_make_twilio_client_refuses_missing_credentials(missing, blank):
    values = dict(twilio_account_sid="ACdummy", twilio_auth_token="dummy-token", twilio_from="+910000000002")
    values[missing] = blank
    with pytest.raises(ValueError):
        bridge.make_twilio_client(Settings(_env_file=None, **values))


def test_the_real_dialler_asks_twilio_for_a_time_limit(monkeypatch):
    import twilio.rest

    created = []

    class RecordingClient:
        def __init__(self, sid, token):
            self.calls = self

        def create(self, **kwargs):
            created.append(kwargs)
            return type("Call", (), {"sid": "CAx"})()

    monkeypatch.setattr(twilio.rest, "Client", RecordingClient)
    settings = Settings(
        _env_file=None, twilio_account_sid="ACdummy", twilio_auth_token="dummy-token",
        twilio_from="+910000000002", max_call_seconds=300,
    )
    place_call, _ = bridge.make_twilio_client(settings)
    assert place_call("+910000000001", "<Response/>", "https://x/cb") == "CAx"
    assert created[0]["time_limit"] == 330


# ---- final review: every refused dial leaves a trace

def _resident_missing(rig, monkeypatch):
    rig.store.delete("residents", "r1")


def _not_eligible(rig, monkeypatch):
    rig.store.update("residents", "r1", {"consent": {"given": False}})


def _outside_calling_hours(rig, monkeypatch):
    rig.clock_value = datetime(2026, 5, 20, 16, 0, tzinfo=timezone.utc)  # 21:30 in Asia/Kolkata


def _already_started(rig, monkeypatch):
    wrap_transition(rig.store, monkeypatch, apply=False, outcome="false")


def _store_error(rig, monkeypatch):
    wrap_transition(rig.store, monkeypatch, apply=False, outcome="raise")


def _call_is_completed(rig, monkeypatch):
    rig.store.update("calls", rig.call_id, {"status": "completed"})


@pytest.mark.parametrize(
    "arrange, allow, reason, level",
    [
        (_resident_missing, PHONE, "resident_missing", logging.WARNING),
        (_not_eligible, PHONE, "not_eligible", logging.WARNING),
        (None, "+910000000099", "not_on_allowlist", logging.WARNING),
        (_store_error, PHONE, "store_error", logging.WARNING),
        (_outside_calling_hours, PHONE, "outside_calling_hours", logging.INFO),
        (_already_started, PHONE, "already_started", logging.INFO),
        (_call_is_completed, PHONE, "call is completed", logging.INFO),
    ],
)
def test_every_refused_dial_logs_the_call_id_and_the_reason(store, tz, now, monkeypatch, caplog, arrange, allow, reason, level):
    rig = Rig(store, tz, now, allow=allow)
    if arrange:
        arrange(rig, monkeypatch)
    with caplog.at_level(logging.DEBUG):
        body = rig.dial().json()
    assert body == {"dialed": False, "reason": reason}
    assert rig.placed == []
    traces = [
        r for r in caplog.records
        if r.name == "app.call.twilio_bridge" and r.levelno == level
        and rig.call_id in r.getMessage() and reason in r.getMessage()
    ]
    assert len(traces) == 1
    for record in caplog.records:
        assert PHONE not in record.getMessage() + (record.exc_text or "")


def test_a_provider_error_logs_the_call_id_and_the_reason_without_the_number(store, tz, now, caplog):
    rig = Rig(store, tz, now, place_call="fail")
    with caplog.at_level(logging.DEBUG):
        assert rig.dial().json() == {"dialed": False, "reason": "provider_error"}
    assert any(
        rig.call_id in r.getMessage() and "provider_error" in r.getMessage() and r.levelno >= logging.WARNING
        for r in caplog.records
    )
    for record in caplog.records:
        assert PHONE not in record.getMessage() + (record.exc_text or "")


# ---- fix round 1: a claim is never left half-done

def wrap_transition(store, monkeypatch, apply, outcome):
    """Calls the real transition when `apply`, then returns False / raises per `outcome`."""
    real = store.transition

    def transition(collection, doc_id, field, expected, fields):
        done = real(collection, doc_id, field, expected, fields) if apply else None
        if outcome == "raise":
            raise RuntimeError("store hiccup")
        if outcome == "false":
            return False
        return done

    monkeypatch.setattr(store, "transition", transition)


@pytest.mark.parametrize("outcome", ["false", "raise"])
def test_a_claim_that_was_applied_but_reported_lost_still_places_the_call_once(store, tz, now, monkeypatch, outcome):
    rig = Rig(store, tz, now)
    wrap_transition(store, monkeypatch, apply=True, outcome=outcome)
    assert rig.dial().json() == {"dialed": True}
    assert len(rig.placed) == 1
    assert rig.call()["status"] == "dialing"


def test_a_claim_that_failed_and_is_not_there_answers_503_and_places_nothing(store, tz, now, monkeypatch):
    rig = Rig(store, tz, now)
    wrap_transition(store, monkeypatch, apply=False, outcome="raise")
    response = rig.dial()
    assert (response.status_code, response.json()) == (503, {"dialed": False, "reason": "store_error"})
    assert rig.placed == [] and rig.call()["status"] == "queued"


def test_a_claim_lost_to_someone_else_is_already_started(store, tz, now, monkeypatch):
    rig = Rig(store, tz, now)
    wrap_transition(store, monkeypatch, apply=False, outcome="false")
    response = rig.dial()
    assert (response.status_code, response.json()) == (200, {"dialed": False, "reason": "already_started"})
    assert rig.placed == []


def test_a_claim_that_cannot_be_confirmed_answers_503(store, tz, now, monkeypatch):
    rig = Rig(store, tz, now)
    real_get, real_transition = store.get, store.transition
    state = {"claiming": False}

    def get(collection, doc_id):
        if state["claiming"]:
            raise RuntimeError("store down")
        return real_get(collection, doc_id)

    def transition(*args):
        state["claiming"] = True
        real_transition(*args)
        raise RuntimeError("store hiccup")

    monkeypatch.setattr(store, "get", get)
    monkeypatch.setattr(store, "transition", transition)
    response = rig.dial()
    assert (response.status_code, response.json()) == (503, {"dialed": False, "reason": "store_error"})
    assert rig.placed == []


def test_provider_error_then_a_failing_store_is_still_provider_error(store, tz, now, monkeypatch, caplog):
    rig = Rig(store, tz, now, place_call="fail")

    def broken(*args, **kwargs):
        raise RuntimeError("store down")

    monkeypatch.setattr(bridge, "handle_call_outcome", broken)
    with caplog.at_level(logging.ERROR):
        response = rig.dial()
    assert (response.status_code, response.json()) == (200, {"dialed": False, "reason": "provider_error"})
    assert sum(rig.call_id in r.getMessage() for r in caplog.records) >= 2


def test_a_provider_id_that_cannot_be_saved_still_counts_as_dialled(store, tz, now, monkeypatch, caplog):
    rig = Rig(store, tz, now)
    real = store.update

    def update(collection, doc_id, fields):
        if "twilio_sid" in fields:
            raise RuntimeError("store down")
        return real(collection, doc_id, fields)

    monkeypatch.setattr(store, "update", update)
    with caplog.at_level(logging.ERROR):
        assert rig.dial().json() == {"dialed": True}
    assert len(rig.placed) == 1
    assert any(rig.call_id in r.getMessage() for r in caplog.records)


def test_consent_withdrawn_after_planning_places_no_call(store, tz, now):
    rig = Rig(store, tz, now)
    store.update("residents", "r1", {"consent.given": False})
    assert rig.dial().json() == {"dialed": False, "reason": "not_eligible"}
    assert rig.placed == [] and rig.call()["status"] == "queued"


# ---- fix round 1: the stream

def test_a_second_stream_with_the_same_token_is_refused_while_the_first_runs(store, tz, now, monkeypatch):
    rig = Rig(store, tz, now)
    rig.dial()
    token = rig.call()["stream_token"]
    entered = threading.Event()
    release = threading.Event()
    calls = []

    async def blocked(deps, call_id, *args, **kwargs):
        calls.append(call_id)
        entered.set()
        await asyncio.to_thread(release.wait, 3)
        return "completed"

    monkeypatch.setattr(bridge, "conduct_call", blocked)
    with rig.client.websocket_connect("/twilio/stream") as ws1:
        ws1.send_json(start_message(rig))
        assert entered.wait(5)  # the token is consumed; the call is still `dialing`
        assert rig.call()["status"] == "dialing"
        with rig.client.websocket_connect("/twilio/stream") as ws2:
            ws2.send_json(start_message(rig, token=token))
            with pytest.raises(WebSocketDisconnect) as closed:
                ws2.receive_json()
            assert closed.value.code == 1008
        release.set()
    assert calls == [rig.call_id]


def test_a_stored_empty_token_never_authenticates(store, tz, now):
    rig = Rig(store, tz, now)
    rig.dial()
    store.update("calls", rig.call_id, {"stream_token": ""})
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json(start_message(rig, token="x"))
        refused_after(ws)
    assert rig.call()["status"] == "dialing"


def test_an_oversized_message_is_dropped_and_counted(store, tz, now, monkeypatch):
    monkeypatch.setattr(bridge, "START_WAIT_SECONDS", 0.3)
    rig = Rig(store, tz, now)
    rig.dial()
    huge = start_message(rig)
    huge["start"]["customParameters"]["pad"] = "x" * 70000

    touched = []

    def no_store(*args, **kwargs):
        touched.append(1)
        return None

    with monkeypatch.context() as patch:
        patch.setattr(store, "get", no_store)
        with rig.client.websocket_connect("/twilio/stream") as ws:
            ws.send_json(huge)
            refused_after(ws)
    assert touched == []
    assert rig.call()["status"] == "dialing"


def test_a_call_id_with_odd_characters_is_closed_before_any_store_access(store, tz, now, monkeypatch):
    rig = Rig(store, tz, now)
    rig.dial()

    touched = []

    def no_store(*args, **kwargs):
        touched.append(1)
        return None

    monkeypatch.setattr(store, "get", no_store)
    for odd in ("a/b", "a b", "x" * 201, "a.b"):
        with rig.client.websocket_connect("/twilio/stream") as ws:
            ws.send_json(start_message(rig, call_id=odd, token="tok"))
            refused_after(ws)
    assert touched == []


def test_twenty_junk_messages_are_tolerated(store, tz, now):
    rig = Rig(store, tz, now, session=FakeLiveSession(healthy_call_turns()))
    rig.dial()
    with rig.client.websocket_connect("/twilio/stream") as ws:
        for _ in range(20):
            ws.send_text("junk")
        ws.send_json(start_message(rig))
        read_until(ws, "mark")
    assert rig.call()["status"] == "completed"


def test_the_twenty_first_junk_message_closes(store, tz, now):
    rig = Rig(store, tz, now)
    rig.dial()
    with rig.client.websocket_connect("/twilio/stream") as ws:
        for _ in range(21):
            ws.send_text("junk")
        ws.send_json(start_message(rig))
        refused_after(ws)
    assert rig.call()["status"] == "dialing"


def test_a_refusal_closes_with_1008(store, tz, now):
    rig = Rig(store, tz, now)
    rig.dial()
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json(start_message(rig, token="wrong"))
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 1008


# ---- fix round 1: the status callback

def test_status_without_a_validator_is_403(store, tz, now):
    rig = Rig(store, tz, now, validator=False)
    rig.dial()
    assert post_status(rig, "no-answer").status_code == 403
    assert rig.call()["status"] == "dialing"


def test_status_with_an_oversized_or_non_utf8_body_is_403_before_parsing(store, tz, now):
    rig = Rig(store, tz, now)
    rig.dial()
    form = {"X-Twilio-Signature": "s", "Content-Type": "application/x-www-form-urlencoded"}
    big = rig.client.post(
        f"/twilio/status?call_id={rig.call_id}", content=b"CallStatus=busy&pad=" + b"x" * 20000, headers=form
    )
    bad = rig.client.post(f"/twilio/status?call_id={rig.call_id}", content=b"CallStatus=\xff\xfe", headers=form)
    assert (big.status_code, bad.status_code) == (403, 403)
    assert rig.signatures == []
    assert rig.call()["status"] == "dialing"


# ---- fix round 1: cancellation while a never-started call is cleaned up

def test_cleanup_fails_a_never_started_call_even_if_cancelled_while_stopping_the_reader(store, tz, now):
    from tests.fakes import make_deps

    round_id, call_id, queue = queue_one_call(store, tz, now)
    store.update("calls", call_id, {"status": "dialing", "channel": "phone"})
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession([])), queue)
    stopping = asyncio.Event()
    let_go = asyncio.Event()

    async def slow_reader():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopping.set()
            await let_go.wait()
            raise

    async def scenario():
        reader = asyncio.create_task(slow_reader())
        await asyncio.sleep(0)
        try:
            handler = asyncio.create_task(bridge._clean_up(deps, reader, call_id, False))
            await asyncio.wait_for(stopping.wait(), 5)
            handler.cancel()
            let_go.set()
            with pytest.raises(asyncio.CancelledError):
                await handler
        finally:
            let_go.set()
        return reader

    reader = asyncio.run(scenario())
    assert reader.done()
    assert store.get("calls", call_id)["status"] == "failed"
    assert store.get("calls", f"{round_id}_r1_2")["status"] == "queued"


# ---- a connected call that reached nobody

def test_a_hang_up_with_nothing_recorded_is_an_unanswered_attempt(store, tz, now, monkeypatch):
    monkeypatch.setattr(bridge, "GOODBYE_WAIT_SECONDS", 30)
    rig = Rig(store, tz, now, session=FakeLiveSession([[audio(tone(40)), turn_complete()]]))
    rig.dial()
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json(start_message(rig))
        ws.send_json(media_message())
        ws.send_json({"event": "stop"})
        with pytest.raises(WebSocketDisconnect):
            for _ in range(50):
                ws.receive_json()
    assert rig.call()["status"] == "no_answer"
    assert store.get("calls", f"{rig.round_id}_r1_2")["status"] == "queued"
    assert store.query("cases") == []
    assert post_status(rig, "completed").status_code == 200  # Twilio's later callback
    assert rig.call()["status"] == "no_answer"
    assert len(store.query("calls")) == 2
    assert store.query("cases") == []


def test_a_no_person_call_hangs_up_at_once_without_a_mark(store, tz, now, monkeypatch):
    monkeypatch.setattr(bridge, "GOODBYE_WAIT_SECONDS", 30)
    turns = [[audio(tone(60)), tool("e", "end_call", outcome="no_person"), turn_complete()]]
    rig = Rig(store, tz, now, session=FakeLiveSession(turns))
    rig.dial()
    started = time.monotonic()
    seen = []
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json(start_message(rig))
        with pytest.raises(WebSocketDisconnect) as closed:
            for _ in range(100):
                seen.append(ws.receive_json())
        assert closed.value.code == 1000
    assert time.monotonic() - started < 10
    assert all(m["event"] != "mark" for m in seen)
    assert rig.call()["status"] == "no_answer"


def test_no_person_after_a_person_spoke_still_plays_out_the_goodbye(store, tz, now, monkeypatch):
    monkeypatch.setattr(bridge, "GOODBYE_WAIT_SECONDS", 30)
    turns = [[
        tool("r", "set_respondent", type="self"),
        audio(tone(60)),
        tool("e", "end_call", outcome="no_person"),
        turn_complete(),
    ]]
    rig = Rig(store, tz, now, session=FakeLiveSession(turns))
    rig.dial()
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json(start_message(rig))
        seen = read_until(ws, "mark")  # the goodbye is flushed and a mark is sent
        assert any(m["event"] == "media" for m in seen)
        ws.send_json({"event": "mark", "mark": {"name": "goodbye"}})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1000
    assert rig.call()["respondent"] == "self"
