"""Ask Chhaya's conversations, kept in the app's store (Firestore in production) as ADK sessions.

ask_sessions/{id}: the session, its state, title and owner. ask_events/{id}_{seq}: one ADK event each.
Only one answer streams per session at a time (`busy`), so the sequence numbers never race.
"""
import asyncio
import secrets
import time
from datetime import datetime
from typing import Any, Callable, Optional

from google.adk.events import Event
from google.adk.sessions import BaseSessionService, Session
from google.adk.sessions.base_session_service import GetSessionConfig, ListSessionsResponse

from app.db import Store

SESSIONS = "ask_sessions"
EVENTS = "ask_events"
RECENT_EVENTS = 30


class StoreSessionService(BaseSessionService):
    def __init__(self, store: Store, clock: Callable[[], datetime]) -> None:
        self._store, self._clock = store, clock

    # ---- what ADK calls ----

    async def create_session(self, *, app_name: str, user_id: str, state: Optional[dict[str, Any]] = None,
                             session_id: Optional[str] = None) -> Session:
        session_id = session_id or f"s_{secrets.token_urlsafe(12)}"
        now = self._clock()
        doc = {"id": session_id, "app_name": app_name, "user_id": user_id, "title": "", "state": dict(state or {}),
               "created_at": now, "updated_at": now, "seq": 0, "message_count": 0, "busy": False}
        if not await asyncio.to_thread(self._store.create, SESSIONS, session_id, doc):
            raise ValueError("session exists")
        return Session(id=session_id, app_name=app_name, user_id=user_id, state=dict(state or {}), events=[],
                       last_update_time=now.timestamp())

    async def get_session(self, *, app_name: str, user_id: str, session_id: str,
                          config: Optional[GetSessionConfig] = None) -> Optional[Session]:
        doc = await asyncio.to_thread(self.owned, session_id, user_id)
        if not doc or doc.get("app_name") != app_name:
            return None
        recent = RECENT_EVENTS
        if config and config.num_recent_events is not None:
            recent = config.num_recent_events
        events = await asyncio.to_thread(self.all_events, session_id, recent)
        if config and config.after_timestamp is not None:
            events = [e for e in events if e.timestamp >= config.after_timestamp]
        return Session(id=session_id, app_name=app_name, user_id=user_id, state=dict(doc.get("state") or {}),
                       events=events, last_update_time=_stamp(doc["updated_at"]))

    async def list_sessions(self, *, app_name: str, user_id: Optional[str] = None) -> ListSessionsResponse:
        query = {"app_name": app_name} if user_id is None else {"app_name": app_name, "user_id": user_id}
        docs = await asyncio.to_thread(self._store.query, SESSIONS, **query)
        return ListSessionsResponse(sessions=[
            Session(id=d["id"], app_name=d["app_name"], user_id=d["user_id"], state=dict(d.get("state") or {}),
                    events=[], last_update_time=_stamp(d["updated_at"])) for d in docs])

    async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
        doc = await asyncio.to_thread(self.owned, session_id, user_id)
        if doc:
            await asyncio.to_thread(self._store.delete, SESSIONS, session_id)

    async def append_event(self, session: Session, event: Event) -> Event:
        event = await super().append_event(session, event)
        if event.partial:
            return event
        await asyncio.to_thread(self._write_event, session, event)
        return event

    # ---- what the routes use ----

    def _write_event(self, session: Session, event: Event) -> None:
        doc = self._store.get(SESSIONS, session.id) or {}
        seq = int(doc.get("seq", 0)) + 1
        self._store.set(EVENTS, f"{session.id}_{seq:06d}", {"session_id": session.id, "seq": seq,
                                                           "event_json": event.model_dump_json(exclude_none=True),
                                                           "at": self._clock()})
        state = {k: v for k, v in session.state.items() if not k.startswith("temp:")}
        self._store.update(SESSIONS, session.id, {"seq": seq, "state": state, "updated_at": self._clock()})

    def owned(self, session_id: str, user_id: str) -> dict | None:
        doc = self._store.get(SESSIONS, session_id)
        return doc if doc and doc.get("user_id") == user_id else None

    def all_events(self, session_id: str, recent: int | None = None) -> list[Event]:
        docs = sorted(self._store.query(EVENTS, session_id=session_id), key=lambda d: d["seq"])
        if recent is not None:
            docs = docs[-recent:] if recent else []
        return [Event.model_validate_json(d["event_json"]) for d in docs]

    def list_for(self, user_id: str, app_name: str) -> list[dict]:
        docs = [d for d in self._store.query(SESSIONS, app_name=app_name, user_id=user_id) if d.get("message_count")]
        return sorted(docs, key=lambda d: str(d["updated_at"]), reverse=True)

    def try_lock(self, session_id: str) -> bool:
        return self._store.transition(SESSIONS, session_id, "busy", False, {"busy": True})

    def unlock(self, session_id: str) -> None:
        self._store.update(SESSIONS, session_id, {"busy": False})

    def count_message(self, session_id: str, focus: dict | None) -> int:
        doc = self._store.get(SESSIONS, session_id) or {}
        count = int(doc.get("message_count", 0)) + 1
        fields: dict = {"message_count": count, "updated_at": self._clock()}
        if focus is not None:
            fields["state.focus"] = focus
        self._store.update(SESSIONS, session_id, fields)
        return count

    def set_title(self, session_id: str, title: str) -> None:
        self._store.update(SESSIONS, session_id, {"title": title})


def _stamp(value) -> float:
    return value.timestamp() if isinstance(value, datetime) else time.time()
