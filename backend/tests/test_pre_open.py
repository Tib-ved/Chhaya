"""A phone call's voice session is opened while the phone rings, and handed to the call."""
import asyncio
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.call.live as live_module
import app.call.twilio_bridge as bridge
from app.api import create_app
from app.call.live import open_live_session, pre_open_session
from app.call.pre_open import pre_open_call
from app.config import Settings
from tests.fakes import FakeLiveSession, audio, healthy_call_turns, session_opener
from tests.helpers import queue_one_call
from tests.test_twilio_bridge import SECRET, media_message, read_until, start_message, tone


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(live_module, "_clients", {})
    monkeypatch.setattr(live_module, "_pre_opened", {})
    monkeypatch.setattr(bridge, "GOODBYE_WAIT_SECONDS", 0.05)


@pytest.fixture
def connections(monkeypatch):
    """The fake voice service: each connection is a dict with its config, its session and
    whether it is closed. connections.delay holds the next connections back; connections.fail
    makes the next one fail."""
    made = []
    control = SimpleNamespace(made=made, delay=0.0, fail=0)

    @asynccontextmanager
    async def connect(**kwargs):
        record = {"config": kwargs["config"], "closed": False, "session": object()}
        made.append(record)
        await asyncio.sleep(control.delay)
        if control.fail:
            control.fail -= 1
            raise ConnectionError("refused")
        try:
            yield record["session"]
        finally:
            record["closed"] = True

    class FakeClient:
        def __init__(self, **kwargs):
            self.aio = SimpleNamespace(live=SimpleNamespace(connect=connect))

    monkeypatch.setattr("google.genai.Client", FakeClient)
    return control


def settings(**overrides):
    return Settings(_env_file=None, gcp_project="p", **overrides)


async def answer(chosen, instruction, language=None):
    """What a call answered now gets: the session it is handed, inside the call."""
    async with open_live_session(chosen, instruction, language) as session:
        return session._session


def test_a_pre_opened_session_is_handed_to_the_call_that_is_answered(connections, caplog):
    chosen = settings()

    async def scenario():
        pre_open_session(chosen, "INSTRUCTION", "hi")
        await asyncio.sleep(0.01)  # the phone rings
        with caplog.at_level(logging.INFO, logger="app.call.live"):
            handed = await answer(chosen, "INSTRUCTION")  # session.py does not pass the language
        await asyncio.sleep(0)
        return handed

    handed = asyncio.run(scenario())
    assert len(connections.made) == 1  # no second connection when the phone was answered
    only = connections.made[0]
    assert handed is only["session"]
    assert only["config"]["speech_config"]["language_code"] == "hi-IN"
    assert only["closed"]  # closed with the call
    assert live_module._pre_opened == {}
    assert "voice session ready in" in caplog.text and "(pre-opened)" in caplog.text


def test_a_call_answered_while_its_session_is_still_opening_waits_for_it(connections):
    connections.delay = 0.1
    chosen = settings()

    async def scenario():
        pre_open_session(chosen, "INSTRUCTION", "hi")
        await asyncio.sleep(0)
        return await answer(chosen, "INSTRUCTION")

    assert asyncio.run(scenario()) is connections.made[0]["session"]
    assert len(connections.made) == 1


def test_a_pre_opened_session_nobody_takes_is_closed(connections):
    chosen = settings(live_pre_open_seconds=0.05)

    async def scenario():
        pre_open_session(chosen, "INSTRUCTION", "hi")
        await asyncio.sleep(0.2)  # not answered
        assert connections.made[0]["closed"]
        assert live_module._pre_opened == {}
        return await answer(chosen, "INSTRUCTION", "hi")  # a late answer opens its own

    handed = asyncio.run(scenario())
    assert len(connections.made) == 2 and handed is connections.made[1]["session"]


def test_a_call_with_another_instruction_opens_its_own(connections):
    chosen = settings(live_pre_open_seconds=0.05)

    async def scenario():
        pre_open_session(chosen, "ANOTHER CALL", "hi")
        await asyncio.sleep(0.01)
        handed = await answer(chosen, "INSTRUCTION", "en")
        await asyncio.sleep(0.2)
        return handed

    handed = asyncio.run(scenario())
    assert handed is connections.made[1]["session"]
    assert connections.made[1]["config"]["speech_config"]["language_code"] == "en-IN"
    assert all(made["closed"] for made in connections.made)


def test_a_pre_open_that_fails_leaves_the_call_to_open_its_own(connections, caplog):
    connections.fail = 1
    chosen = settings()

    async def scenario():
        pre_open_session(chosen, "INSTRUCTION", "hi")
        await asyncio.sleep(0)
        with caplog.at_level(logging.WARNING, logger="app.call.live"):
            return await answer(chosen, "INSTRUCTION", "hi")

    assert asyncio.run(scenario()) is connections.made[1]["session"]
    assert "could not pre-open a voice session (ConnectionError)" in caplog.text


def test_nothing_is_pre_opened_when_it_is_switched_off(connections):
    async def scenario():
        pre_open_session(settings(live_pre_open_seconds=0), "INSTRUCTION", "hi")
        await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert connections.made == [] and live_module._pre_opened == {}


def test_a_call_cancelled_while_its_session_is_opening_releases_it(connections):
    connections.delay = 0.05
    chosen = settings()

    async def scenario():
        pre_open_session(chosen, "INSTRUCTION", "hi")
        await asyncio.sleep(0)
        waiting = asyncio.create_task(answer(chosen, "INSTRUCTION"))
        await asyncio.sleep(0.01)
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        await asyncio.sleep(0.1)

    asyncio.run(scenario())
    assert connections.made[0]["closed"]  # not held open for ever


# --- dialling a phone call pre-opens its session, with the instruction the call opens with


def dial_rig(store, tz, now, session, **settings_overrides):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    seen, pre_opened, placed = [], [], []

    def place_call(to, twiml, status_callback):
        placed.append(to)
        return "CA123"

    chosen = Settings(
        _env_file=None, internal_secret="s3cret", max_call_seconds=2,
        public_host="voice.example.test", dial_allowlist="+910000000001", **settings_overrides,
    )
    app = create_app(
        store, queue, chosen, clock=lambda: now,
        open_session=session_opener(session, seen=seen, pre_opened=pre_opened),
        place_call=place_call, validate_twilio=lambda *args: True,
    )
    rig = SimpleNamespace(store=store, call_id=call_id, client=TestClient(app))
    rig.call = lambda: store.get("calls", call_id)
    return rig, seen, pre_opened, placed


def test_dialling_pre_opens_the_session_the_answered_call_then_opens(store, tz, now):
    turns = healthy_call_turns()
    turns[-1][0] = audio(tone(60))
    rig, seen, pre_opened, placed = dial_rig(store, tz, now, FakeLiveSession(turns))
    assert rig.client.post(f"/internal/calls/{rig.call_id}/dial", headers=SECRET).json() == {"dialed": True}
    assert placed and len(pre_opened) == 1
    instruction, language = pre_opened[0]
    assert language == store.get("residents", store.get("calls", rig.call_id)["resident_id"])["language"]
    with rig.client.websocket_connect("/twilio/stream") as ws:
        ws.send_json({"event": "connected"})
        ws.send_json(start_message(rig))
        ws.send_json(media_message())
        read_until(ws, "mark")
        ws.send_json({"event": "mark", "mark": {"name": "goodbye"}})
    # The same instruction, so the answered call takes the pre-opened session.
    assert seen == [instruction]
    assert store.get("calls", rig.call_id)["status"] == "completed"


def test_dialling_pre_opens_nothing_when_it_is_switched_off(store, tz, now):
    rig, _, pre_opened, placed = dial_rig(store, tz, now, FakeLiveSession([]), live_pre_open_seconds=0)
    rig.client.post(f"/internal/calls/{rig.call_id}/dial", headers=SECRET)
    assert placed and pre_opened == []


def test_a_failing_pre_open_never_stops_the_dial(store, tz, now, caplog):
    def broken(settings, instruction, language=None):
        raise RuntimeError("no network")

    rig, _, _, placed = dial_rig(store, tz, now, FakeLiveSession([]))
    deps = SimpleNamespace(open_session=SimpleNamespace(pre_open=broken), settings=Settings(_env_file=None))
    with caplog.at_level(logging.WARNING, logger="app.call.pre_open"):
        pre_open_call(deps, "no-such-call")  # never raises
    assert "could not pre-open the voice session of call no-such-call" in caplog.text
    assert rig.client.post(f"/internal/calls/{rig.call_id}/dial", headers=SECRET).json() == {"dialed": True}
