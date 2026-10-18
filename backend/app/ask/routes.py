"""/api/ask: Ask Chhaya's sessions and its streamed answers."""
import asyncio
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from google.genai import types
from pydantic import BaseModel, Field

from app.ask import context
from app.ask.guard import Rejected, clean_text
from app.ask.limits import Limits
from app.ask.sessions import StoreSessionService
from app.ask.stream import Turn, sse, to_sse
from app.ask.visuals import validate_visual
from app.operations_auth import check_origin
from app.visitor import clean_visitor_id

log = logging.getLogger(__name__)

APP_NAME = "ask_chhaya"
TURN_SECONDS = 60
REFUSALS = {"rate": (429, "Too many questions in a minute; wait a moment."),
            "daily": (429, "That is today's allowance of questions; it renews tomorrow."),
            "resting": (429, "Ask Chhaya is resting for today.")}
Titler = Callable[[str], Awaitable[str]]


class Focus(BaseModel):
    state: str = Field("", max_length=40)
    district_id: str = Field("", max_length=80)
    ward_id: str = Field("", max_length=80)


class NewSession(BaseModel):
    focus: Focus = Field(default_factory=Focus)


class NewMessage(BaseModel):
    text: str = Field(max_length=4000)  # cleaned and held to 2,000 below, with a clear reason
    focus: Focus | None = None


def fallback_title(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= 60 else text[:57].rstrip() + "…"


def history(events) -> list[dict]:
    """Stored events back into the messages the drawer shows."""
    messages: list[dict] = []
    for event in events:
        parts = (event.content.parts or []) if event.content else []
        if event.author == "user":
            text = "".join(p.text or "" for p in parts)
            if text:
                messages.append({"role": "user", "text": text, "visuals": []})
            continue
        if event.author != "chhaya":
            continue
        if not messages or messages[-1]["role"] != "assistant":
            messages.append({"role": "assistant", "text": "", "visuals": []})
        current = messages[-1]
        for part in parts:
            if part.function_call and part.function_call.name in ("show_chart", "show_table"):
                spec = validate_visual(part.function_call.name, dict(part.function_call.args or {}))
                if spec is not None:
                    kind = "chart" if part.function_call.name == "show_chart" else "table"
                    current["visuals"].append({"kind": kind, "id": part.function_call.id or kind, "spec": spec})
            elif part.text and not part.thought:
                current["text"] += part.text
    return messages


def register_ask(app: FastAPI, store, settings, auth, clock: Callable[[], datetime],
                 runner: Any = None, titler: Titler | None = None) -> None:
    """runner: an ADK Runner (or a stand-in with the same run_async); None builds one when a model is set."""
    context.set_store(store)
    context.set_clock(clock)
    sessions = StoreSessionService(store, clock)
    limits = Limits(store, clock, settings.ask_daily_cap)
    if runner is None and (settings.ask_model or settings.text_model).strip() and settings.gcp_project.strip():
        runner, titler = build_runner(settings, sessions), titler or make_titler(settings)
    tasks: set[asyncio.Task] = set()  # titles being written, held so they are not collected half-way
    # The dataset this app serves. Conversations are kept in one collection for both apps, so each
    # carries its own in its session state, and only this app's are listed or answered here.
    mode = context.dataset_of(store)

    def owner_of(request: Request, secret: str, visitor: str) -> tuple[str, bool]:
        email = auth.email_from_cookies(request.cookies)
        if auth.secret_matches(secret):
            return (email or "team"), True
        if email:
            return email, True
        visitor_id = clean_visitor_id(visitor)
        if visitor_id:
            return f"v:{visitor_id}", False
        raise HTTPException(401, "who is asking? (sign in, or send X-Visitor)")

    def ready() -> None:
        if runner is None:
            raise HTTPException(503, "Ask Chhaya isn't connected yet.")

    @app.post("/api/ask/sessions", status_code=201, tags=["ask"], summary="Start a conversation")
    async def create(body: NewSession, request: Request, x_internal_secret: str = Header(default=""),
                     x_visitor: str = Header(default="")) -> dict:
        check_origin(request)
        owner, _ = owner_of(request, x_internal_secret, x_visitor)
        ready()
        session = await sessions.create_session(app_name=APP_NAME, user_id=owner, state={"focus": body.focus.model_dump(), "mode": mode})
        return {"session_id": session.id, "created_at": clock().isoformat()}

    @app.get("/api/ask/sessions", tags=["ask"], summary="My conversations")
    async def mine(request: Request, x_internal_secret: str = Header(default=""), x_visitor: str = Header(default="")) -> list[dict]:
        owner, _ = owner_of(request, x_internal_secret, x_visitor)
        docs = [d for d in await asyncio.to_thread(sessions.list_for, owner, APP_NAME)
                if context.mode_of_state(d.get("state")) == mode]
        return [{"session_id": d["id"], "title": d.get("title") or "New conversation", "updated_at": d["updated_at"]} for d in docs]

    @app.get("/api/ask/sessions/{session_id}", tags=["ask"], summary="One conversation")
    async def one(session_id: str, request: Request, x_internal_secret: str = Header(default=""),
                  x_visitor: str = Header(default="")) -> dict:
        owner, _ = owner_of(request, x_internal_secret, x_visitor)
        doc = await asyncio.to_thread(sessions.owned, session_id, owner)
        # The other mode's conversation is not listed here, so it is not readable here either.
        if not doc or context.mode_of_state(doc.get("state")) != mode:
            raise HTTPException(404, "no such conversation")
        events = await asyncio.to_thread(sessions.all_events, session_id)
        return {"session_id": session_id, "title": doc.get("title") or "New conversation",
                "focus": (doc.get("state") or {}).get("focus") or {}, "messages": history(events)}

    @app.post("/api/ask/sessions/{session_id}/messages", tags=["ask"], summary="Ask, and stream the answer")
    async def message(session_id: str, body: NewMessage, request: Request, x_internal_secret: str = Header(default=""),
                      x_visitor: str = Header(default="")):
        check_origin(request)
        owner, team = owner_of(request, x_internal_secret, x_visitor)
        ready()
        try:
            text = clean_text(body.text)
        except Rejected as error:
            raise HTTPException(400, f"message {error}")
        doc = await asyncio.to_thread(sessions.owned, session_id, owner)
        if not doc:
            raise HTTPException(404, "no such conversation")
        belongs = context.mode_of_state(doc.get("state"))
        if belongs != mode:
            # With a reason, so the drawer can tell it from "still answering" and start a new conversation.
            return JSONResponse({"detail": f"This conversation belongs to the {belongs} data; start a new one.",
                                 "reason": "other_mode"}, status_code=409)
        refusal = await asyncio.to_thread(limits.check_and_count, owner, team)
        if refusal:
            status, words = REFUSALS[refusal]
            raise HTTPException(status, words)
        if not limits.open_stream(owner, team):
            raise HTTPException(429, "One answer at a time, please.")
        if not await asyncio.to_thread(sessions.try_lock, session_id):
            limits.close_stream(owner)
            raise HTTPException(409, "Still answering the last question.")
        focus = body.focus.model_dump() if body.focus else None
        count = await asyncio.to_thread(sessions.count_message, session_id, focus)
        title_task = None
        if count == 1 and titler is not None:
            title_task = asyncio.create_task(_title(titler, text))
        elif count == 1:
            await asyncio.to_thread(sessions.set_title, session_id, fallback_title(text))

        async def answer():
            turn = Turn()
            # This app's store and clock for the tools, whichever app (live, demo) last set the fallback.
            with context.use(store, clock, belongs):
                try:
                    from google.adk.agents.run_config import RunConfig, StreamingMode

                    events = runner.run_async(
                        user_id=owner, session_id=session_id,
                        new_message=types.Content(role="user", parts=[types.Part(text=text)]),
                        state_delta={"focus": focus} if focus is not None else None,
                        run_config=RunConfig(streaming_mode=StreamingMode.SSE, max_llm_calls=15),
                    )
                    async with asyncio.timeout(TURN_SECONDS):
                        async for chunk in to_sse(events, turn):
                            yield chunk
                    yield sse("sources", {"case_ids": turn.case_ids, "call_ids": turn.call_ids})
                    if title_task is not None and title_task.done():
                        title = title_task.result()
                        await asyncio.to_thread(sessions.set_title, session_id, title)
                        yield sse("title", {"title": title})
                    yield sse("done", {"data_as_of": context.now().isoformat(), "kind": "refused" if turn.refused else "answer"})
                except TimeoutError:
                    yield sse("error", {"code": "timeout", "message": "That took too long; try a narrower question."})
                except Exception:
                    log.exception("ask turn failed")
                    yield sse("error", {"code": "model", "message": "Something went wrong answering that; please try again."})
                finally:
                    # Synchronous on purpose: on a disconnect this runs while the generator is being closed.
                    limits.close_stream(owner)
                    sessions.unlock(session_id)
                    if title_task is not None and not title_task.done():
                        task = asyncio.create_task(_save_title(title_task, sessions, session_id))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)

        return StreamingResponse(answer(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def _title(make: Titler, text: str) -> str:
        try:
            title = " ".join((await asyncio.wait_for(make(text), 10)).split()).strip(" .\"'")
            return title[:80] if title else fallback_title(text)
        except Exception:
            return fallback_title(text)


async def _save_title(task: asyncio.Task, sessions: StoreSessionService, session_id: str) -> None:
    await asyncio.to_thread(sessions.set_title, session_id, await task)


def build_runner(settings, sessions: StoreSessionService):
    from google.adk.agents.context_cache_config import ContextCacheConfig
    from google.adk.apps import App
    from google.adk.runners import Runner

    from app.ask.agent import build_root

    # The instruction and tool definitions are the same on every turn, so they are cached: Gemini keeps them
    # as a server-side cache, from a conversation's second turn, once the prefix reaches its minimum size.
    caching = ContextCacheConfig(ttl_seconds=settings.ask_cache_seconds, cache_intervals=10)
    app = App(name=APP_NAME, root_agent=build_root(settings), context_cache_config=caching)
    return Runner(app=app, session_service=sessions)


TITLE_INSTRUCTION = ("Write a title of at most six words for a conversation that starts with the officer's question "
                     "below. Plain words, no quotes, no full stop. Reply with the title only.")


def make_titler(settings) -> Titler | None:
    if not settings.ask_gate_model.strip() or not settings.gcp_project.strip():
        return None
    clients: list = []

    async def title(text: str) -> str:
        from google import genai

        if not clients:
            clients.append(genai.Client(vertexai=True, project=settings.gcp_project, location=settings.text_location))
        config: dict = {"system_instruction": TITLE_INSTRUCTION, "temperature": 0.2, "max_output_tokens": 32}
        if settings.ask_gate_thinking.strip():
            config["thinking_config"] = {"thinking_level": settings.ask_gate_thinking.strip()}
        response = await clients[0].aio.models.generate_content(model=settings.ask_gate_model, contents=text[:500], config=config)
        return response.text or ""
    return title
