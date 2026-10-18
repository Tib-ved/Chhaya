"""Someone in distress who cannot speak presses a key on the phone (or the browser call's help
button): the call becomes an emergency, once, and is ended if the agent does not end it."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.call.session as session_module
import app.call.twilio_bridge as twilio_module
from app.api import create_app
from app.call.session import HELP_NOTE, HELP_REASON, conduct_call
from app.config import Settings
from tests.fakes import FakeLiveSession, audio, healthy_call_turns, make_deps, said, session_opener, tool, turn_complete
from tests.helpers import queue_one_call
from tests.test_twilio_bridge import Rig, media_message, read_until, start_message, tone

SECRET = {"X-Internal-Secret": "s3cret"}


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(session_module, "HELP_END_SECONDS", 0.3)
    monkeypatch.setattr(twilio_module, "GOODBYE_WAIT_SECONDS", 0.05)


def greeting_then_silence():
    return [[said("agent", "Namaste"), audio(b"hello"), turn_complete()]]


class AnswersTheNote(FakeLiveSession):
    """After the help note the agent says the emergency line and ends the call itself."""

    def __init__(self) -> None:
        super().__init__(greeting_then_silence())
        self.noted = asyncio.Event()

    async def send_client_content(self, **kwargs) -> None:
        await super().send_client_content(**kwargs)
        if kwargs["turns"]["parts"][0]["text"] == HELP_NOTE:
            self.noted.set()

    async def receive(self):
        if self.turns:
            async for message in super().receive():
                yield message
            return
        await self.noted.wait()
        self.noted.clear()
        for message in (audio(b"call 108"), tool("e1", "end_call", outcome="emergency"), turn_complete()):
            await asyncio.sleep(0)
            yield message


def run(deps, call_id, presses: int = 1, press_after: float = 0.05):
    async def scenario():
        audio_in = asyncio.Queue()
        pressed = asyncio.Event()

        async def nothing(*args):
            pass

        async def press():
            await asyncio.sleep(press_after)
            for _ in range(presses):
                pressed.set()
                await asyncio.sleep(0.01)

        presser = asyncio.create_task(press())
        try:
            return await conduct_call(deps, call_id, audio_in, nothing, nothing, pressed)
        finally:
            presser.cancel()

    return asyncio.run(scenario())


def help_events(call):
    return [event for event in call["timeline"] if event["kind"] == "help_pressed" or event.get("by") == "help_pressed"]


# ---- the call itself


def test_a_press_flags_the_emergency_and_tells_the_agent(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    live = AnswersTheNote()
    deps = make_deps(store, now, tz, session_opener(live), queue)

    status = run(deps, call_id, presses=3)

    call = store.get("calls", call_id)
    assert status == "completed"
    assert call["emergency"]["flagged"] is True
    assert call["emergency"]["reason"] == HELP_REASON  # once, however many presses
    assert call["end_outcome"] == "emergency"  # ended by the agent, after the note
    assert store.get("cases", f"{round_id}_r1")["tier"] == "red"
    assert live.sent_text.count(HELP_NOTE) == 1
    assert ("system", HELP_NOTE) in [(entry["role"], entry["text"]) for entry in call["transcript"]]
    assert [event["kind"] for event in help_events(call)] == ["help_pressed", "tool"]  # no fallback ending


def test_the_call_is_ended_for_an_agent_that_does_not_end_it(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    live = FakeLiveSession(greeting_then_silence())  # says nothing after the note
    settings = Settings(_env_file=None, internal_secret="s3cret", max_call_seconds=10)
    deps = make_deps(store, now, tz, session_opener(live), queue, settings=settings)

    async def timed():
        return await asyncio.wait_for(asyncio.to_thread(run, deps, call_id), timeout=5)

    status = asyncio.run(timed())

    call = store.get("calls", call_id)
    assert status == "completed"  # well before the ten-second limit
    assert call["end_outcome"] == "emergency"
    assert call["emergency"]["reason"] == HELP_REASON
    assert store.get("cases", f"{round_id}_r1")["tier"] == "red"
    assert live.sent_text.count(HELP_NOTE) == 1
    ended = [event for event in help_events(call) if event.get("name") == "end_call"]
    assert len(ended) == 1


def test_without_a_press_nothing_changes(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    live = FakeLiveSession(healthy_call_turns())
    deps = make_deps(store, now, tz, session_opener(live), queue)

    async def scenario():
        async def nothing(*args):
            pass

        return await conduct_call(deps, call_id, asyncio.Queue(), nothing, nothing, asyncio.Event())

    assert asyncio.run(scenario()) == "completed"
    call = store.get("calls", call_id)
    assert not call.get("emergency")
    assert HELP_NOTE not in live.sent_text
    assert store.get("cases", f"{round_id}_r1")["tier"] == "green"


# ---- the phone


def dtmf(digit="1"):
    return {"event": "dtmf", "streamSid": "MZ1", "dtmf": {"track": "inbound_track", "digit": digit}}


def test_a_key_pressed_before_the_stream_starts_is_ignored(store, tz, now):
    turns = healthy_call_turns()
    turns[-1][0] = audio(tone(60))
    live = FakeLiveSession(turns)
    rig = Rig(store, tz, now, session=live)
    rig.dial()
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(dtmf())  # the trial account's "press any key" comes before the call
        ws.send_json(start_message(rig))
        ws.send_json(media_message())
        read_until(ws, "mark")
        ws.send_json({"event": "mark", "mark": {"name": "goodbye"}})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert rig.call()["status"] == "completed"
    assert not rig.call().get("emergency")
    assert HELP_NOTE not in live.sent_text
    assert store.get("cases", f"{rig.round_id}_r1")["tier"] == "green"


def test_a_key_pressed_during_the_call_makes_it_red_once(store, tz, now):
    live = FakeLiveSession(greeting_then_silence())
    rig = Rig(store, tz, now, session=live)
    rig.dial()
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json(start_message(rig))
        ws.send_json(media_message())
        for digit in "159":
            ws.send_json(dtmf(digit))
        with pytest.raises(WebSocketDisconnect):  # ended for the agent, which never ended it
            for _ in range(500):
                ws.receive_json()
    call = rig.call()
    assert call["status"] == "completed"
    assert call["end_outcome"] == "emergency"
    assert call["emergency"]["reason"] == HELP_REASON
    assert store.get("cases", f"{rig.round_id}_r1")["tier"] == "red"
    assert live.sent_text.count(HELP_NOTE) == 1
    assert [event["kind"] for event in help_events(call)].count("help_pressed") == 1


# ---- the browser


def test_the_browser_help_button_makes_the_call_red(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    live = AnswersTheNote()
    settings = Settings(_env_file=None, internal_secret="s3cret", max_call_seconds=5)
    client = TestClient(create_app(store, queue, settings, clock=lambda: now, open_session=session_opener(live)))
    ticket = client.post(f"/admin/calls/{call_id}/browser-ticket", headers=SECRET).json()["ticket"]
    with client.websocket_connect(f"/browser/stream?ticket={ticket}") as ws:
        ws.send_bytes(b"\x00\x00" * 160)
        ws.send_text("not json")  # ignored
        ws.send_text(json.dumps({"type": "help"}))
        ws.send_text(json.dumps({"type": "help"}))
        while True:
            message = ws.receive()
            if message.get("text") and json.loads(message["text"]).get("type") == "ended":
                break
    call = store.get("calls", call_id)
    assert call["status"] == "completed"
    assert call["end_outcome"] == "emergency"
    assert call["emergency"]["reason"] == HELP_REASON
    assert store.get("cases", f"{round_id}_r1")["tier"] == "red"
    assert live.sent_text.count(HELP_NOTE) == 1
