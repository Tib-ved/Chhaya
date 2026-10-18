import asyncio
from datetime import datetime, timezone

from google.adk.events import Event, EventActions
from google.adk.sessions.base_session_service import GetSessionConfig
from google.genai import types

from app.ask.sessions import StoreSessionService
from app.db import MemoryStore

APP = "ask_chhaya"


def run(coroutine):
    return asyncio.run(coroutine)


def service():
    return StoreSessionService(MemoryStore(), lambda: datetime(2026, 10, 14, tzinfo=timezone.utc))


def text_event(text, author="chhaya", partial=False, delta=None):
    return Event(author=author, invocation_id="i1", partial=partial,
                 content=types.Content(role="model" if author != "user" else "user", parts=[types.Part(text=text)]),
                 actions=EventActions(state_delta=delta or {}))


def test_round_trip_with_state():
    svc = service()
    session = run(svc.create_session(app_name=APP, user_id="v:a", state={"focus": {"district_id": "churu"}}))
    run(svc.append_event(session, text_event("hello", author="user")))
    run(svc.append_event(session, text_event("Churu has 3 red cases.", delta={"last_area": "churu", "temp:x": 1})))
    loaded = run(svc.get_session(app_name=APP, user_id="v:a", session_id=session.id))
    assert [e.content.parts[0].text for e in loaded.events] == ["hello", "Churu has 3 red cases."]
    assert loaded.state["last_area"] == "churu" and loaded.state["focus"] == {"district_id": "churu"}
    assert "temp:x" not in loaded.state


def test_partial_events_are_not_stored():
    svc = service()
    session = run(svc.create_session(app_name=APP, user_id="v:a"))
    run(svc.append_event(session, text_event("Chu", partial=True)))
    assert svc.all_events(session.id) == []


def test_recent_events_only():
    svc = service()
    session = run(svc.create_session(app_name=APP, user_id="v:a"))
    for i in range(35):
        run(svc.append_event(session, text_event(f"m{i}")))
    loaded = run(svc.get_session(app_name=APP, user_id="v:a", session_id=session.id))
    assert len(loaded.events) == 30 and loaded.events[-1].content.parts[0].text == "m34"
    two = run(svc.get_session(app_name=APP, user_id="v:a", session_id=session.id, config=GetSessionConfig(num_recent_events=2)))
    assert len(two.events) == 2


def test_other_owners_see_nothing():
    svc = service()
    session = run(svc.create_session(app_name=APP, user_id="v:a"))
    assert run(svc.get_session(app_name=APP, user_id="v:b", session_id=session.id)) is None
    assert svc.owned(session.id, "v:b") is None


def test_list_hides_empty_sessions_and_lock():
    svc = service()
    empty = run(svc.create_session(app_name=APP, user_id="v:a"))
    used = run(svc.create_session(app_name=APP, user_id="v:a"))
    svc.count_message(used.id, {"ward_id": "churu-ward-1"})
    assert [d["id"] for d in svc.list_for("v:a", APP)] == [used.id]
    assert svc.owned(used.id, "v:a")["state"]["focus"] == {"ward_id": "churu-ward-1"}
    assert svc.try_lock(empty.id) and not svc.try_lock(empty.id)
    svc.unlock(empty.id)
    assert svc.try_lock(empty.id)
