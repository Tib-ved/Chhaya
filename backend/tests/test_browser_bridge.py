import asyncio
import logging
import struct
import threading
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.call.browser_bridge as bridge_module
from app.api import create_app
from app.config import Settings
from tests.fakes import (
    FakeLiveSession, audio, healthy_call_turns, said, interrupted, session_opener, tool, turn_complete,
)
from tests.helpers import queue_one_call

SECRET = {"X-Internal-Secret": "s3cret"}


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def make_client(store, tz, now, session, clock=None, max_call_seconds=2, **tuning):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    settings = Settings(_env_file=None, internal_secret="s3cret", max_call_seconds=max_call_seconds, **tuning)
    app = create_app(store, queue, settings, clock=clock or (lambda: now), open_session=session_opener(session))
    return TestClient(app), round_id, call_id


def ticket_for(client, call_id):
    response = client.post(f"/admin/calls/{call_id}/browser-ticket", headers=SECRET)
    assert response.status_code == 200
    return response.json()["ticket"]


def refused(client, url):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(url):
            pass


def wait_for_status(store, call_id, status):
    for _ in range(250):
        if store.get("calls", call_id)["status"] == status:
            return
        time.sleep(0.02)
    assert store.get("calls", call_id)["status"] == status


def test_ticket_needs_the_secret(store, tz, now):
    client, _, call_id = make_client(store, tz, now, FakeLiveSession([]))
    assert client.post(f"/admin/calls/{call_id}/browser-ticket").status_code == 401


def test_ticket_for_unknown_or_started_call(store, tz, now):
    client, _, call_id = make_client(store, tz, now, FakeLiveSession([]))
    assert client.post("/admin/calls/nope/browser-ticket", headers=SECRET).status_code == 404
    store.update("calls", call_id, {"status": "completed"})
    assert client.post(f"/admin/calls/{call_id}/browser-ticket", headers=SECRET).status_code == 409


def test_ticket_marks_the_call_as_a_browser_call(store, tz, now):
    client, _, call_id = make_client(store, tz, now, FakeLiveSession([]))
    body = client.post(f"/admin/calls/{call_id}/browser-ticket", headers=SECRET).json()
    assert body["expires_in"] == 60
    assert len(body["ticket"]) >= 24
    call = store.get("calls", call_id)
    assert call["browser_ticket"] == body["ticket"]
    assert call["browser_ticket_expires"] == now + timedelta(seconds=60)


def test_stream_rejects_a_missing_or_wrong_ticket(store, tz, now):
    client, _, _ = make_client(store, tz, now, FakeLiveSession([]))
    for url in ("/browser/stream", "/browser/stream?ticket=", "/browser/stream?ticket=wrong"):
        refused(client, url)


def test_an_empty_ticket_never_matches_a_call_without_one(store, tz, now):
    client, _, call_id = make_client(store, tz, now, FakeLiveSession([]))
    assert store.get("calls", call_id)["browser_ticket"] is None
    refused(client, "/browser/stream?ticket=")
    assert store.get("calls", call_id)["status"] == "queued"


def test_a_whole_browser_call(store, tz, now):
    client, round_id, call_id = make_client(store, tz, now, FakeLiveSession(healthy_call_turns()))
    ticket = ticket_for(client, call_id)
    with client.websocket_connect(f"/browser/stream?ticket={ticket}") as ws:
        ws.send_bytes(b"\x00\x00" * 320)
        assert ws.receive_bytes() == b"goodbye-audio"
        assert ws.receive_json() == {"type": "ended", "status": "completed"}
    assert store.get("calls", call_id)["status"] == "completed"
    assert store.get("cases", f"{round_id}_r1")["tier"] == "green"
    assert store.get("calls", call_id)["browser_ticket"] is None
    assert store.get("calls", call_id)["channel"] == "browser"


def test_a_ticket_works_once(store, tz, now):
    client, _, call_id = make_client(store, tz, now, FakeLiveSession(healthy_call_turns()))
    ticket = ticket_for(client, call_id)
    with client.websocket_connect(f"/browser/stream?ticket={ticket}") as ws:
        ws.receive_bytes()
        ws.receive_json()
    refused(client, f"/browser/stream?ticket={ticket}")


def test_an_expired_ticket_is_refused(store, tz, now):
    clock = Clock(now)
    client, _, call_id = make_client(store, tz, now, FakeLiveSession([]), clock=clock)
    ticket = ticket_for(client, call_id)
    clock.now = now + timedelta(seconds=61)
    refused(client, f"/browser/stream?ticket={ticket}")
    assert store.get("calls", call_id)["status"] == "queued"


def test_a_replaced_ticket_no_longer_works(store, tz, now):
    client, _, call_id = make_client(store, tz, now, FakeLiveSession(healthy_call_turns()))
    old = ticket_for(client, call_id)
    new = ticket_for(client, call_id)
    assert old != new
    refused(client, f"/browser/stream?ticket={old}")
    with client.websocket_connect(f"/browser/stream?ticket={new}") as ws:
        ws.receive_bytes()
        assert ws.receive_json()["type"] == "ended"


def test_the_ticket_is_not_in_any_public_view(store, tz, now):
    client, round_id, call_id = make_client(store, tz, now, FakeLiveSession([]))
    ticket = ticket_for(client, call_id)
    body = {"status": "dropped", "answers": {"feeling": "mild_symptoms"}}
    assert client.post(f"/admin/calls/{call_id}/simulate", headers=SECRET, json=body).status_code == 200
    assert store.get("calls", call_id)["browser_ticket"] == ticket
    assert client.get("/api/wards/w1/cases").json()["cases"]
    for url in ("/api/rounds/current", "/api/wards/w1/cases", f"/api/rounds/{round_id}/report"):
        response = client.get(url)
        assert response.status_code == 200
        for forbidden in (ticket, "browser_ticket", "stream_token"):
            assert forbidden not in response.text


def test_closing_the_browser_mid_call_drops_the_call(store, tz, now):
    turns = [[said("resident", "chakkar aa raha hai"), tool("c1", "record_answer", question_id="feeling", value="mild_symptoms"), turn_complete()]]
    client, round_id, call_id = make_client(store, tz, now, FakeLiveSession(turns))
    ticket = ticket_for(client, call_id)
    with client.websocket_connect(f"/browser/stream?ticket={ticket}") as ws:
        ws.send_bytes(b"\x00\x00" * 320)
        # Mid-call means the conversation is under way: wait for the answer to be taken. A
        # socket closed before the agent's session has opened is a failed attempt, not a drop.
        deadline = time.monotonic() + 10
        while not store.get("calls", call_id).get("answers") and time.monotonic() < deadline:
            time.sleep(0.02)
    # leaving the block closes the socket; the server finishes the call on its own
    wait_for_status(store, call_id, "dropped")
    assert store.get("cases", f"{round_id}_r1")["tier"] == "amber"


def test_interruption_is_sent_as_a_clear_message(store, tz, now):
    turns = [
        [audio(b"talking"), interrupted(), turn_complete()],
        [audio(b"bye"), tool("e", "end_call", outcome="completed"), turn_complete()],
    ]
    client, _, call_id = make_client(store, tz, now, FakeLiveSession(turns))
    ticket = ticket_for(client, call_id)
    with client.websocket_connect(f"/browser/stream?ticket={ticket}") as ws:
        assert ws.receive_bytes() == b"talking"
        assert ws.receive_json() == {"type": "clear"}
        assert ws.receive_bytes() == b"bye"
        assert ws.receive_json() == {"type": "ended", "status": "completed"}


def test_a_call_finished_after_the_ticket_is_refused_at_the_handshake(store, tz, now):
    client, _, call_id = make_client(store, tz, now, FakeLiveSession([]))
    ticket = ticket_for(client, call_id)
    store.update("calls", call_id, {"status": "completed"})
    refused(client, f"/browser/stream?ticket={ticket}")
    assert store.get("calls", call_id)["status"] == "completed"


def test_a_call_taken_by_something_else_is_refused_at_the_handshake(store, tz, now):
    client, _, call_id = make_client(store, tz, now, FakeLiveSession([]))
    ticket = ticket_for(client, call_id)
    store.update("calls", call_id, {"status": "dialing"})
    refused(client, f"/browser/stream?ticket={ticket}")
    assert store.get("calls", call_id)["status"] == "dialing"


def test_a_refusal_by_the_call_is_reported_and_the_claim_released(store, tz, now):
    client, _, call_id = make_client(store, tz, now, FakeLiveSession([]))
    ticket = ticket_for(client, call_id)
    store.delete("residents", "r1")
    with client.websocket_connect(f"/browser/stream?ticket={ticket}") as ws:
        assert ws.receive_json() == {"type": "ended", "status": "refused"}
    assert store.get("calls", call_id)["status"] == "queued"


def bridge_warnings(caplog, call_id):
    return [
        r for r in caplog.records
        if r.levelno == logging.WARNING and r.name == "app.call.browser_bridge" and call_id in r.getMessage()
    ]


def test_a_refused_call_is_released_and_leaves_a_warning(store, tz, now, caplog):
    client, _, call_id = make_client(store, tz, now, FakeLiveSession([]))
    ticket = ticket_for(client, call_id)
    store.delete("residents", "r1")
    with caplog.at_level(logging.WARNING):
        with client.websocket_connect(f"/browser/stream?ticket={ticket}") as ws:
            assert ws.receive_json() == {"type": "ended", "status": "refused"}
    call = store.get("calls", call_id)
    assert (call["status"], call["channel"]) == ("queued", "phone")
    # one warning that the call was refused, one that its claim was released
    messages = [r.getMessage() for r in bridge_warnings(caplog, call_id)]
    assert len(messages) == 2
    assert sum("refused" in message for message in messages) == 1
    assert sum("released" in message for message in messages) == 1


def test_a_call_that_ran_normally_leaves_no_release_warning(store, tz, now, caplog):
    client, _, call_id = make_client(store, tz, now, FakeLiveSession(healthy_call_turns()))
    ticket = ticket_for(client, call_id)
    with caplog.at_level(logging.WARNING):
        with client.websocket_connect(f"/browser/stream?ticket={ticket}") as ws:
            ws.receive_bytes()
            assert ws.receive_json()["status"] == "completed"
    assert store.get("calls", call_id)["status"] == "completed"
    assert bridge_warnings(caplog, call_id) == []


def test_accept_failing_after_the_claim_releases_the_call(store, tz, now, monkeypatch):
    from starlette.websockets import WebSocket

    client, _, call_id = make_client(store, tz, now, FakeLiveSession([]))
    ticket = ticket_for(client, call_id)

    async def broken_accept(self, *args, **kwargs):
        raise RuntimeError("accept failed")

    monkeypatch.setattr(WebSocket, "accept", broken_accept)
    with pytest.raises(Exception):
        with client.websocket_connect(f"/browser/stream?ticket={ticket}"):
            pass
    call = store.get("calls", call_id)
    assert (call["status"], call["channel"]) == ("queued", "phone")


def test_a_release_that_fails_is_logged_and_does_not_stop_the_ended_message(store, tz, now, monkeypatch, caplog):
    client, _, call_id = make_client(store, tz, now, FakeLiveSession([]))
    ticket = ticket_for(client, call_id)
    store.delete("residents", "r1")
    real = store.transition

    def transition(collection, doc_id, field, expected, fields):
        if expected == "dialing":
            raise RuntimeError("store down")
        return real(collection, doc_id, field, expected, fields)

    monkeypatch.setattr(store, "transition", transition)
    with caplog.at_level(logging.ERROR):
        with client.websocket_connect(f"/browser/stream?ticket={ticket}") as ws:
            assert ws.receive_json() == {"type": "ended", "status": "refused"}
    assert any(r.exc_info and call_id in r.getMessage() for r in caplog.records)


def test_a_failure_is_reported_as_an_error_and_logged(store, tz, now, monkeypatch, caplog):
    client, _, call_id = make_client(store, tz, now, FakeLiveSession([]))
    ticket = ticket_for(client, call_id)

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(bridge_module, "conduct_call", boom)
    with caplog.at_level(logging.ERROR):
        with client.websocket_connect(f"/browser/stream?ticket={ticket}") as ws:
            assert ws.receive_json() == {"type": "ended", "status": "error"}
    assert any(r.exc_info and call_id in r.getMessage() for r in caplog.records)


def test_malformed_tickets_are_refused_without_a_store_query(store, tz, now, monkeypatch):
    client, _, _ = make_client(store, tz, now, FakeLiveSession([]))

    def no_query(*args, **kwargs):
        raise AssertionError("the store was queried")

    monkeypatch.setattr(store, "query", no_query)
    for ticket in ("a" * 19, "a" * 65, "a%2Fb" + "c" * 20, "a.b" + "c" * 20):
        refused(client, f"/browser/stream?ticket={ticket}")


def test_the_audio_queue_is_bounded_and_the_hang_up_gets_through(store, tz, now):
    release = threading.Event()

    class Stalled(FakeLiveSession):
        async def send_realtime_input(self, **kwargs):
            first = not self.sent_audio
            await super().send_realtime_input(**kwargs)
            if first:
                await asyncio.to_thread(release.wait, 10)

    session = Stalled([])
    # No greeting guard: the frames are told apart by their content, which the guard would blank.
    client, _, call_id = make_client(store, tz, now, session, max_call_seconds=30, live_greeting_guard_seconds=0)
    ticket = ticket_for(client, call_id)
    store.update("calls", call_id, {"respondent": "self"})  # someone spoke, so a hang-up is "dropped"
    with client.websocket_connect(f"/browser/stream?ticket={ticket}") as ws:
        for number in range(400):
            ws.send_bytes(struct.pack(">H", number))
            if number % 100 == 0:
                ws.send_bytes(bytes(40000))  # too big
                ws.send_bytes(bytes(3))  # odd length
        ws.close()  # the caller hangs up while the model side is still stalled on the first frame
        threading.Timer(0.5, release.set).start()
        # the hang-up can only end the call (well before the 30 s limit) if it got past the full queue
        wait_for_status(store, call_id, "dropped")
    sent = session.sent_audio
    assert all(len(frame) == 2 for frame in sent)
    assert len(sent) <= 251
    assert sent[-1] == struct.pack(">H", 399)


def test_dev_page_is_served(store, tz, now):
    client, _, _ = make_client(store, tz, now, FakeLiveSession([]))
    page = client.get("/dev/call")
    assert page.status_code == 200
    assert "Chhaya call test" in page.text


def test_cleanup_releases_the_claim_even_if_cancelled_while_stopping_the_reader(store, tz, now):
    from tests.fakes import make_deps

    round_id, call_id, queue = queue_one_call(store, tz, now)
    store.update("calls", call_id, {"status": "dialing", "channel": "browser"})
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession([])), queue)
    stopping = asyncio.Event()
    let_go = asyncio.Event()

    async def slow_reader():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopping.set()
            await let_go.wait()  # takes its time to stop
            raise

    async def scenario():
        reader = asyncio.create_task(slow_reader())
        await asyncio.sleep(0)
        try:
            handler = asyncio.create_task(bridge_module._clean_up(deps, reader, call_id))
            await asyncio.wait_for(stopping.wait(), 5)  # clean-up is now waiting for the reader
            handler.cancel()
            let_go.set()
            with pytest.raises(asyncio.CancelledError):
                await handler
        finally:
            let_go.set()
        return reader

    reader = asyncio.run(scenario())
    assert reader.done()
    call = store.get("calls", call_id)
    assert (call["status"], call["channel"]) == ("queued", "phone")


def test_an_ended_message_reports_no_answer_when_nobody_was_reached(store, tz, now):
    turns = [
        [audio(b"hello"), turn_complete()],
        [audio(b"bye"), tool("e", "end_call", outcome="no_person"), turn_complete()],
    ]
    client, round_id, call_id = make_client(store, tz, now, FakeLiveSession(turns))
    ticket = ticket_for(client, call_id)
    with client.websocket_connect(f"/browser/stream?ticket={ticket}") as ws:
        assert ws.receive_bytes() == b"hello"
        assert ws.receive_bytes() == b"bye"
        assert ws.receive_json() == {"type": "ended", "status": "no_answer"}
    assert store.get("calls", call_id)["status"] == "no_answer"
    assert store.get("calls", f"{round_id}_r1_2")["status"] == "queued"
    assert store.query("cases") == []


def test_the_after_call_step_runs_once_the_call_has_ended(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    settings = Settings(_env_file=None, internal_secret="s3cret", max_call_seconds=2)
    told = []

    def text_model(instruction, prompt):
        told.append(prompt)
        return "Says they are managing. Check again if the warning continues."

    turns = healthy_call_turns()
    turns[1] = [said("resident", "chakkar aa raha hai"), tool("c0", "record_answer", question_id="feeling", value="mild_symptoms", quote="chakkar"), turn_complete()]
    app = create_app(
        store, queue, settings, clock=lambda: now,
        open_session=session_opener(FakeLiveSession(turns)), text_model=text_model,
    )
    client = TestClient(app)
    ticket = ticket_for(client, call_id)
    with client.websocket_connect(f"/browser/stream?ticket={ticket}") as ws:
        ws.send_bytes(b"\x00\x00" * 320)
        assert ws.receive_bytes() == b"goodbye-audio"
        assert ws.receive_json()["type"] == "ended"  # the caller is told first; the note comes after
        # Stay on, as a real caller's browser does, until the server closes the socket: it does
        # that only once the after-call step has finished. Leaving this block makes the test
        # client cancel the server's handler, and a step not yet in its worker thread never runs.
        assert ws.receive()["type"] == "websocket.close"
    assert len(told) == 1
    assert store.get("cases", f"{round_id}_r1")["note"].startswith("Says they are managing")


def test_a_failing_after_call_step_does_not_disturb_the_call(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    settings = Settings(_env_file=None, internal_secret="s3cret", max_call_seconds=2)

    asked = []

    def broken(instruction, prompt):
        asked.append(prompt)
        raise RuntimeError("model unavailable")

    turns = healthy_call_turns()
    turns[1] = [said("resident", "chakkar aa raha hai"), tool("c0", "record_answer", question_id="feeling", value="mild_symptoms"), turn_complete()]
    app = create_app(store, queue, settings, clock=lambda: now, open_session=session_opener(FakeLiveSession(turns)), text_model=broken)
    client = TestClient(app)
    ticket = ticket_for(client, call_id)
    with client.websocket_connect(f"/browser/stream?ticket={ticket}") as ws:
        ws.send_bytes(b"\x00\x00" * 320)
        ws.receive_bytes()
        assert ws.receive_json() == {"type": "ended", "status": "completed"}
        # The server closes the socket only after the step has run and failed; leaving before
        # that would cancel the handler (see the test above).
        assert ws.receive()["type"] == "websocket.close"
    assert len(asked) == 1
    case = store.get("cases", f"{round_id}_r1")
    assert (case["note"], case["tier"]) == ("", "amber")
    assert store.get("calls", call_id)["status"] == "completed"
