"""Lets a browser microphone stand in for the phone line. Same agent, same call logic."""
import asyncio
import json
import logging
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse

from app.call.bridge_common import (
    new_audio_queue, push_frame, push_hangup, run_after_call, stop_reader,
)
from app.call.deps import CallDeps
from app.call.session import CallRefused, _settle, conduct_call
from app.security_headers import page_policy

logger = logging.getLogger(__name__)
TICKET_SECONDS = 60
TICKET_SHAPE = re.compile(r"[A-Za-z0-9_-]{20,64}")
MAX_FRAME_BYTES = 32768
MAX_HELP_CHARS = 64  # the help button's message is {"type": "help"}
CALL_PAGE = Path(__file__).parent.parent / "static" / "call.html"
# The microphone code is loaded from a blob: address the page makes itself.
CALL_PAGE_POLICY = page_policy(CALL_PAGE, script_src="blob:")


def new_ticket(now: datetime) -> dict:
    """The two call fields that let one browser open one call within TICKET_SECONDS."""
    return {
        "browser_ticket": secrets.token_urlsafe(24),
        "browser_ticket_expires": now + timedelta(seconds=TICKET_SECONDS),
    }


def _release(deps: CallDeps, call_id: str) -> bool:
    """Give back a claim that never became a call, so the call can be tried again. A resident's
    call goes back to the phone; a visitor's call is only ever taken in the browser, and must
    not look like a visitor phone call (whose number is erased and whose stuck calls are ended)."""
    call = deps.store.get("calls", call_id) or {}
    channel = "browser" if call.get("visitor_id") else "phone"
    return deps.store.transition(
        "calls", call_id, "status", "dialing", {"status": "queued", "channel": channel}
    )


async def _release_claim(deps: CallDeps, call_id: str) -> asyncio.CancelledError | None:
    """Release in a shielded worker thread and wait for it, even if this task is cancelled.

    Returns the cancellation received meanwhile, for the caller to re-raise."""
    future = asyncio.ensure_future(asyncio.to_thread(_release, deps, call_id))
    cancelled = await _settle(future)
    error = future.exception()
    if error is not None:
        logger.error("could not release the claim on browser call %s", call_id, exc_info=error)
    elif future.result():
        logger.warning("browser call %s did not start; released its claim", call_id)
    return cancelled


async def _clean_up(deps: CallDeps, reader: asyncio.Task | None, call_id: str) -> None:
    """Stop the reader and release the claim. Both always happen, whatever happens to this task;
    a cancellation received meanwhile is re-raised once both are done."""
    cancelled = None
    try:
        cancelled = await stop_reader(reader)
    finally:
        late = await _release_claim(deps, call_id)  # a no-op once the call has started
    cancelled = cancelled or late
    if cancelled is not None:
        raise cancelled


def register_browser_routes(app: FastAPI, deps: CallDeps, operator: list) -> None:
    # Tickets live on the call document, so any instance of the service can redeem one.

    @app.post("/admin/calls/{call_id}/browser-ticket", dependencies=operator)
    def browser_ticket(call_id: str) -> dict:
        call = deps.store.get("calls", call_id)
        if call is None:
            raise HTTPException(status_code=404, detail=f"not found: calls/{call_id}")
        if call["status"] != "queued":
            raise HTTPException(status_code=409, detail=f"call is {call['status']}, not queued")
        fields = new_ticket(deps.clock())
        deps.store.update("calls", call_id, fields)
        return {"ticket": fields["browser_ticket"], "expires_in": TICKET_SECONDS}

    def redeem(ticket: str) -> str | None:
        """Find the call this ticket belongs to and consume the ticket. Returns the call id."""
        for call in deps.store.query("calls", browser_ticket=ticket):
            expires = call.get("browser_ticket_expires")
            if expires is None or expires < deps.clock():
                continue
            consumed = deps.store.transition(
                "calls", call["id"], "browser_ticket", ticket,
                {"browser_ticket": None, "browser_ticket_expires": None},
            )
            if consumed:
                return call["id"]
        return None

    def claim(call_id: str) -> bool:
        """Take the call for this socket. Only one claimant can move it out of `queued`."""
        return deps.store.transition(
            "calls", call_id, "status", "queued",
            {"status": "dialing", "channel": "browser", "dialing_at": deps.clock()},
        )

    def is_help(text: str | None) -> bool:
        """Whether a text message is the help button: {"type": "help"}, and nothing else."""
        if not text or len(text) > MAX_HELP_CHARS:
            return False
        try:
            message = json.loads(text)
        except ValueError:
            return False
        return isinstance(message, dict) and message.get("type") == "help"

    def accept_frame(audio_in: asyncio.Queue, frame: bytes) -> None:
        if len(frame) > MAX_FRAME_BYTES or len(frame) % 2:
            return
        push_frame(audio_in, frame)

    @app.websocket("/browser/stream")
    async def browser_stream(ws: WebSocket, ticket: str = "") -> None:
        call_id = None
        if TICKET_SHAPE.fullmatch(ticket):
            call_id = await asyncio.to_thread(redeem, ticket)
            if call_id is not None and not await asyncio.to_thread(claim, call_id):
                call_id = None
        if call_id is None:
            await ws.close(code=1008)  # before accept: the handshake is refused
            return
        # The call is claimed (dialing): whatever happens from here, the finally releases it.
        reader = None
        try:
            await ws.accept()
            audio_in: asyncio.Queue = new_audio_queue()
            help_pressed = asyncio.Event()  # the call page's help button: see session.HELP_NOTE

            async def read_microphone() -> None:
                try:
                    while True:
                        message = await ws.receive()
                        if message["type"] == "websocket.disconnect":
                            break
                        if message.get("bytes"):
                            accept_frame(audio_in, message["bytes"])
                        elif is_help(message.get("text")):
                            help_pressed.set()
                except Exception:
                    pass
                finally:
                    push_hangup(audio_in)  # tells the call the caller has gone; always fits

            async def send_audio(pcm: bytes) -> None:
                await ws.send_bytes(pcm)

            async def clear_audio() -> None:
                await ws.send_json({"type": "clear"})

            reader = asyncio.create_task(read_microphone())
            try:
                status = await conduct_call(deps, call_id, audio_in, send_audio, clear_audio, help_pressed)
            except CallRefused as error:
                logger.warning("browser call %s refused: %s", call_id, error)
                status = "refused"
            except Exception:
                logger.exception("browser call %s failed", call_id)
                status = "error"
            try:
                await ws.send_json({"type": "ended", "status": status})
            except Exception:
                pass  # the socket is already dead; the outcome is stored
            if status in ("completed", "dropped"):
                # While the socket is still open the service is given processor time: the note
                # is written now, after the caller has been told the call is over.
                await run_after_call(deps, call_id)
            try:
                await ws.close()
            except Exception:
                pass
        finally:
            await _clean_up(deps, reader, call_id)

    @app.get("/dev/call")
    def dev_call_page() -> FileResponse:
        return FileResponse(CALL_PAGE, media_type="text/html", headers=CALL_PAGE_POLICY)
