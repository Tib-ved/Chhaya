"""Phone calls through Twilio: placing the call, the audio stream, and the status callback."""
import asyncio
import hmac
import json
import logging
import re
import secrets
from dataclasses import dataclass
from html import escape
from urllib.parse import parse_qsl, quote

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse

from app.call.audio import TwilioAudio
from app.call.bridge_common import (
    new_audio_queue, push_frame, push_hangup, run_after_call, stop_reader,
)
from app.call.deps import CallDeps
from app.call.pre_open import pre_open_call
from app.call.session import CallRefused, _settle, conduct_call
from app.dispatch import clamp_to_calling_hours, eligible, handle_call_outcome, person_was_reached
from app.models import REACHED_STATUSES

logger = logging.getLogger(__name__)
GOODBYE_WAIT_SECONDS = 6.0  # how long to let the last words play before hanging up
START_WAIT_SECONDS = 10.0  # the stream is unauthenticated until its start message arrives
MAX_MESSAGES_BEFORE_START = 20
MAX_MESSAGE_CHARS = 65536  # Twilio's own messages are far smaller
MAX_CALLBACK_BYTES = 16384
CALL_ID_SHAPE = re.compile(r"[A-Za-z0-9_\-]{1,200}")
PROVIDER_GRACE_SECONDS = 30  # Twilio's own limit sits this far above ours
TWILIO_FAILURES = {"busy": "busy", "no-answer": "no_answer", "failed": "failed", "canceled": "failed"}


def map_twilio_status(twilio_status: str, current_status: str) -> str:
    """Translate Twilio's final CallStatus into ours. Raises ValueError if it cannot."""
    if twilio_status not in TWILIO_FAILURES and twilio_status != "completed":
        raise ValueError(f"cannot translate Twilio status {twilio_status!r}")
    if current_status == "in_progress" or current_status in REACHED_STATUSES:
        # A conversation had begun: whatever Twilio says, it was cut short, not a failed dial.
        # (A no-op if the bridge already stored the real outcome.)
        return "dropped"
    if twilio_status == "completed":
        return "failed"  # answered or rang, but our audio stream never started
    return TWILIO_FAILURES[twilio_status]


def status_callback_url(host: str, call_id: str) -> str:
    return f"https://{host}/twilio/status?call_id={quote(call_id, safe='')}"


def build_twiml(host: str, call_id: str, token: str) -> str:
    return (
        "<Response><Connect>"
        f'<Stream url="wss://{host}/twilio/stream">'
        f'<Parameter name="call_id" value="{escape(call_id, quote=True)}"/>'
        f'<Parameter name="token" value="{escape(token, quote=True)}"/>'
        "</Stream></Connect></Response>"
    )


def make_twilio_client(settings):
    """The real dialler and signature check. Imported lazily so tests never need Twilio."""
    missing = [
        name for name in ("twilio_account_sid", "twilio_auth_token", "twilio_from")
        if not (getattr(settings, name) or "").strip()
    ]
    if missing:
        # A validator built with an empty auth token accepts signatures anyone can compute.
        raise ValueError(f"Twilio is not configured: {', '.join(missing)} is empty")
    from twilio.request_validator import RequestValidator
    from twilio.rest import Client

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    validator = RequestValidator(settings.twilio_auth_token)

    def place_call(to: str, twiml: str, status_callback: str, time_limit: int | None = None) -> str:
        call = client.calls.create(
            to=to,
            from_=settings.twilio_from,
            twiml=twiml,
            status_callback=status_callback,
            status_callback_event=["completed"],
            status_callback_method="POST",
            timeout=30,
            time_limit=time_limit or settings.max_call_seconds + PROVIDER_GRACE_SECONDS,
        )
        return call.sid

    return place_call, validator.validate


def build_telephony(settings):
    """Real Twilio dialler and signature check, or (None, None) when Twilio is not configured."""
    names = ("twilio_account_sid", "twilio_auth_token", "twilio_from")
    if not all((getattr(settings, name) or "").strip() for name in names):
        return None, None
    try:
        return make_twilio_client(settings)
    except ValueError:
        return None, None


def warn_if_phone_calls_impossible(settings) -> None:
    """With Twilio configured, phone calls still need a public host and an allowlist."""
    if build_telephony(settings) == (None, None):
        return
    missing = []
    if not (settings.public_host or "").strip():
        missing.append("PUBLIC_HOST")
    if not (settings.dial_allowlist or "").strip():
        missing.append("DIAL_ALLOWLIST")
    if missing:
        logger.warning("phone calls cannot be placed until %s is set", " and ".join(missing))


def telephony_ready(deps: CallDeps) -> bool:
    """Whether a phone call can be placed at all: a dialler and a host for it to call back.
    Without both the dial endpoint answers 503, so nothing is worth queueing for it."""
    return deps.place_call is not None and bool(deps.settings.public_host)


@dataclass
class StreamStart:
    call_id: str
    token: str
    stream_id: str | None


def _parse(raw: str | None) -> dict | None:
    try:
        message = json.loads(raw or "")
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


async def _read_start(ws: WebSocket) -> StreamStart | None:
    """Wait for Twilio's `start` message. Anyone can open this socket, so before it arrives
    only `connected` is acknowledged, everything else is ignored, and a flood is refused.
    Returns None for a start without call_id and token, a disconnect, or too many messages."""
    for _ in range(MAX_MESSAGES_BEFORE_START + 1):
        received = await ws.receive()
        if received["type"] != "websocket.receive":
            return None
        text = received.get("text")
        if text is None or len(text) > MAX_MESSAGE_CHARS:
            continue  # counted, never parsed
        message = _parse(text)
        if message is None or message.get("event") != "start":
            continue  # includes `connected`; nothing else is processed
        details = message.get("start")
        details = details if isinstance(details, dict) else {}
        parameters = details.get("customParameters")
        parameters = parameters if isinstance(parameters, dict) else {}
        call_id, token = parameters.get("call_id"), parameters.get("token")
        if not (isinstance(call_id, str) and isinstance(token, str) and token):
            return None
        if not CALL_ID_SHAPE.fullmatch(call_id):
            return None
        stream_id = details.get("streamSid") or message.get("streamSid")
        return StreamStart(call_id, token, stream_id if isinstance(stream_id, str) else None)
    return None


async def _wait_for_start(ws: WebSocket) -> StreamStart | None:
    try:
        return await asyncio.wait_for(_read_start(ws), timeout=START_WAIT_SECONDS)
    except Exception:  # timeout, disconnect or a broken socket: the same answer
        return None


async def _close_quietly(ws: WebSocket, code: int = 1008) -> None:
    try:
        await ws.close(code=code)
    except Exception:
        pass  # already closed


def _finish(deps: CallDeps, call_id: str, status: str) -> None:
    handle_call_outcome(
        deps.store, deps.queue, call_id, status, deps.clock(), deps.tz,
        deps.settings.call_start_hour, deps.settings.call_end_hour,
    )


def _fail_if_never_started(deps: CallDeps, call_id: str) -> bool:
    """A phone call that connected but never began is a failed attempt (which schedules a
    retry); there is no queued task that would pick a re-queued call up again."""
    call = deps.store.get("calls", call_id)
    if call is None or call["status"] != "dialing":
        return False  # started (conduct_call recorded the outcome) or already finished
    _finish(deps, call_id, "failed")
    return True


async def _settle_never_started(deps: CallDeps, call_id: str) -> asyncio.CancelledError | None:
    """Run _fail_if_never_started in a shielded worker thread; wait even if cancelled."""
    future = asyncio.ensure_future(asyncio.to_thread(_fail_if_never_started, deps, call_id))
    cancelled = await _settle(future)
    error = future.exception()
    if error is not None:
        logger.error("could not fail phone call %s that never started", call_id, exc_info=error)
    elif future.result():
        logger.warning("phone call %s never started; recorded as failed", call_id)
    return cancelled


async def _clean_up(deps: CallDeps, reader: asyncio.Task | None, call_id: str, finished: bool) -> None:
    """Stop the reader, then fail the call if it never started. Both always happen, whatever
    happens to this task; a cancellation received meanwhile is re-raised once both are done."""
    cancelled = None
    try:
        cancelled = await stop_reader(reader)
    finally:
        late = None if finished else await _settle_never_started(deps, call_id)
    cancelled = cancelled or late
    if cancelled is not None:
        raise cancelled


async def _wait_for_any(timeout: float, *events: asyncio.Event) -> None:
    """Wait until one of the events is set, or the timeout passes."""
    waiters = [asyncio.ensure_future(event.wait()) for event in events]
    try:
        await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)


def _refusal(call_id: str, reason: str, level: int = logging.WARNING) -> dict:
    """The answer for a call that was not dialled. Logged, so that every refusal leaves a trace:
    the call id and the reason, never the number."""
    logger.log(level, "call %s was not dialled: %s", call_id, reason)
    return {"dialed": False, "reason": reason}


def place_visitor_phone_call(deps: CallDeps, call_id: str) -> bool:
    """Dial a visitor's phone call now: no queue, no retry, no calling hours (the visitor asked
    for it this moment). Returns False, with the call ended as failed, if the provider refused."""
    from app.models import VISITOR_USAGE
    from app.visitor_phone import PROVIDER_GRACE_SECONDS as GRACE

    store, settings = deps.store, deps.settings
    call = store.get("calls", call_id)
    resident = store.get("residents", call["resident_id"])
    token = secrets.token_urlsafe(24)

    def give_up() -> None:
        """Free the reservation and end the call as failed, which also erases the number."""
        try:
            store.update(VISITOR_USAGE, call_id, {"cost_usd": 0.0})
        except KeyError:
            pass
        try:
            _finish(deps, call_id, "failed")
        except Exception as error:
            # Left for the sweep, which ends it (and erases the number) after 15 minutes.
            logger.error("could not record the failed dial of visitor call %s (%s)", call_id, type(error).__name__)

    if not store.transition(
        "calls", call_id, "status", "queued",
        {"status": "dialing", "stream_token": token, "channel": "phone", "dialing_at": deps.clock()},
    ):
        logger.error("could not claim visitor call %s for dialling", call_id)
        give_up()
        return False
    pre_open_call(deps, call_id)  # the voice session opens while the phone rings; never raises
    try:
        provider_id = deps.place_call(
            to=resident["phone"],
            twiml=build_twiml(settings.public_host, call_id, token),
            status_callback=status_callback_url(settings.public_host, call_id),
            time_limit=settings.visitor_phone_max_seconds + GRACE,
        )
    except Exception as error:
        logger.error("could not place visitor call %s: provider_error (%s)", call_id, type(error).__name__)
        give_up()
        return False
    try:
        store.update("calls", call_id, {"twilio_sid": provider_id})
    except Exception as error:
        # The call exists and is ringing; the status callback still works.
        logger.error("could not save the provider id of call %s (%s)", call_id, type(error).__name__)
    return True


def register_twilio_routes(app: FastAPI, deps: CallDeps, secured: list) -> None:
    store, settings = deps.store, deps.settings
    hours = (settings.call_start_hour, settings.call_end_hour)
    allowlist = {number.strip() for number in settings.dial_allowlist.split(",") if number.strip()}

    def finish(call_id: str, status: str) -> None:
        _finish(deps, call_id, status)

    async def signed_form(request: Request, url: str) -> dict:
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_CALLBACK_BYTES:
                raise HTTPException(status_code=403, detail="bad request")
        try:
            text = bytes(body).decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=403, detail="bad request")
        params = dict(parse_qsl(text, keep_blank_values=True))
        signature = request.headers.get("X-Twilio-Signature", "")
        if deps.validate_twilio is None or not deps.validate_twilio(url, params, signature):
            raise HTTPException(status_code=403, detail="bad signature")
        return params

    def _settle_visitor_cost(call_id: str, duration: str) -> None:
        from app.models import VISITOR_USAGE
        from app.visitor_phone import settled_usd

        entry = store.get(VISITOR_USAGE, call_id)
        if entry is None or not entry.get("country"):
            return
        seconds = int(duration) if duration.isdigit() else 0
        store.update(VISITOR_USAGE, call_id, {"cost_usd": settled_usd(settings, entry["country"], seconds)})

    @app.post("/internal/calls/{call_id}/dial", dependencies=secured)
    def dial(call_id: str):
        call = store.get("calls", call_id)
        if call is None:
            raise HTTPException(status_code=404, detail=f"not found: calls/{call_id}")
        if not telephony_ready(deps):
            raise HTTPException(status_code=503, detail="telephony is not configured")
        if call["status"] != "queued":
            return _refusal(call_id, f"call is {call['status']}", logging.INFO)
        if (store.get("rounds", call["round_id"]) or {}).get("status") == "complete":
            # The round was closed (it finished, or its working life ran out) with this task
            # still queued. Left as it is.
            return _refusal(call_id, "round_complete", logging.INFO)
        resident = store.get("residents", call["resident_id"])
        if resident is None:
            return _refusal(call_id, "resident_missing")
        if not eligible(resident):  # consent may have been withdrawn since the round was planned
            return _refusal(call_id, "not_eligible")
        if resident.get("phone") not in allowlist:
            return _refusal(call_id, "not_on_allowlist")
        now = deps.clock()
        if clamp_to_calling_hours(now, deps.tz, *hours) != now:
            # Left queued: the next plan sweep re-queues such calls.
            return _refusal(call_id, "outside_calling_hours", logging.INFO)

        # The token generated here is the proof that a claim is ours.
        token = secrets.token_urlsafe(24)
        store_failed = False
        try:
            claimed = store.transition(
                "calls", call_id, "status", "queued",
                {"status": "dialing", "stream_token": token, "channel": "phone", "dialing_at": now},
            )
        except Exception:
            logger.exception("could not claim call %s for dialling", call_id)
            claimed, store_failed = False, True
        if not claimed:
            # An earlier attempt of the same write may have succeeded: look for our token.
            try:
                stored = (store.get("calls", call_id) or {}).get("stream_token") or ""
            except Exception:
                logger.exception("could not confirm the claim on call %s", call_id)
                return JSONResponse(status_code=503, content=_refusal(call_id, "store_error"))
            if hmac.compare_digest(stored.encode(), token.encode()):
                claimed = True
            elif store_failed:
                return JSONResponse(status_code=503, content=_refusal(call_id, "store_error"))
            else:
                return _refusal(call_id, "already_started", logging.INFO)
        pre_open_call(deps, call_id)  # the voice session opens while the phone rings; never raises
        try:
            provider_id = deps.place_call(
                to=resident["phone"],
                twiml=build_twiml(settings.public_host, call_id, token),
                status_callback=status_callback_url(settings.public_host, call_id),
            )
        except Exception as error:
            # Only the type: a provider message can contain the number or credentials.
            logger.error("could not place call %s: provider_error (%s)", call_id, type(error).__name__)
            try:
                finish(call_id, "failed")
            except Exception as store_error:
                logger.error(
                    "could not record the failed dial of call %s (%s)", call_id, type(store_error).__name__
                )
            return {"dialed": False, "reason": "provider_error"}
        try:
            store.update("calls", call_id, {"twilio_sid": provider_id})
        except Exception as error:
            # The call exists; the token and the status callback still work.
            logger.error("could not save the provider id of call %s (%s)", call_id, type(error).__name__)
        return {"dialed": True}

    @app.post("/twilio/status")
    async def twilio_status(request: Request, call_id: str) -> dict:
        params = await signed_form(request, status_callback_url(settings.public_host, call_id))
        call = await asyncio.to_thread(store.get, "calls", call_id)
        if call is None:
            raise HTTPException(status_code=404, detail=f"not found: calls/{call_id}")
        twilio_status = params.get("CallStatus", "")
        try:
            status = map_twilio_status(twilio_status, call["status"])
        except ValueError as error:
            logger.warning("twilio status for %s rejected: %s", call_id, error)
            raise HTTPException(status_code=400, detail=str(error))
        if call.get("visitor_id") and call.get("channel") == "phone":
            await asyncio.to_thread(_settle_visitor_cost, call_id, params.get("CallDuration", "0"))
        await asyncio.to_thread(finish, call_id, status)
        return {"ok": True}

    def authenticate(start: StreamStart) -> bool:
        """Check the token and consume it, so that it opens exactly one stream."""
        call = store.get("calls", start.call_id)
        expected = (call or {}).get("stream_token") or ""
        if (
            call is None
            or not expected  # an empty stored token never matches anything
            or call["status"] != "dialing"
            or not hmac.compare_digest(expected.encode(), start.token.encode())
        ):
            return False
        return store.transition(
            "calls", start.call_id, "stream_token", expected, {"stream_token": None}
        )

    @app.websocket("/twilio/stream")
    async def twilio_stream(ws: WebSocket) -> None:
        await ws.accept()
        start = await _wait_for_start(ws)  # nothing touches the store before a valid start
        if start is None:
            await _close_quietly(ws)
            return
        try:
            allowed = await asyncio.to_thread(authenticate, start)
        except Exception:
            logger.exception("could not check the stream token of call %s", start.call_id)
            allowed = False
        if not allowed:
            await _close_quietly(ws)
            return

        call_id, stream_id = start.call_id, start.stream_id
        audio = TwilioAudio()
        audio_in = new_audio_queue()
        goodbye_played = asyncio.Event()
        reader_gone = asyncio.Event()
        help_pressed = asyncio.Event()  # a keypad press: see session.HELP_NOTE

        async def read_phone() -> None:
            try:
                while True:
                    received = await ws.receive()
                    if received["type"] != "websocket.receive":
                        break
                    text = received.get("text")
                    message = _parse(text) if text is not None and len(text) <= MAX_MESSAGE_CHARS else None
                    if message is None:
                        continue
                    event = message.get("event")
                    if event == "media":
                        media = message.get("media")
                        payload = media.get("payload") if isinstance(media, dict) else None
                        # Every frame goes on, silence included: the service detects the end of
                        # a turn from a continuous stream.
                        pcm = audio.to_gemini(payload) if isinstance(payload, str) else b""
                        if pcm:
                            push_frame(audio_in, pcm)
                    elif event == "mark":
                        goodbye_played.set()
                    elif event == "dtmf":
                        # Any key, once the stream has started: someone who cannot speak asks for
                        # help. A press before the start (the trial account's prompt) never gets here.
                        help_pressed.set()
                    elif event == "stop":
                        break
            except Exception:
                pass  # a broken socket is a hang-up
            finally:
                reader_gone.set()
                push_hangup(audio_in)  # tells the call the caller has gone; always fits

        async def send_frames(frames: list[str]) -> None:
            for payload in frames:
                await ws.send_json({"event": "media", "streamSid": stream_id, "media": {"payload": payload}})

        async def send_audio(pcm: bytes) -> None:
            await send_frames(audio.to_twilio(pcm))

        async def clear_audio() -> None:
            audio.reset_output()
            await ws.send_json({"event": "clear", "streamSid": stream_id})

        reader = None
        finished = False  # conduct_call returned, so the call has a stored final status
        closed = False
        try:
            reader = asyncio.create_task(read_phone())
            try:
                await conduct_call(deps, call_id, audio_in, send_audio, clear_audio, help_pressed)
                finished = True
            except CallRefused as error:
                logger.warning("phone call %s refused: %s", call_id, error)
            except Exception:
                logger.exception("phone call %s failed", call_id)
            nobody_there = False
            if finished:
                try:
                    ended = await asyncio.to_thread(deps.store.get, "calls", call_id)
                    ended = ended or {}
                    nobody_there = ended.get("end_outcome") == "no_person" and not person_was_reached(ended)
                except Exception:
                    logger.exception("could not read how phone call %s ended", call_id)
            if finished and not nobody_there and not reader_gone.is_set():
                try:
                    await send_frames(audio.flush())
                    # run_call returns when the model has finished generating, not when the
                    # phone has finished playing: wait for Twilio to echo the mark, or for
                    # the caller to go, or for the time to run out.
                    await ws.send_json({"event": "mark", "streamSid": stream_id, "mark": {"name": "goodbye"}})
                    await _wait_for_any(GOODBYE_WAIT_SECONDS, goodbye_played, reader_gone)
                except Exception:
                    logger.info("phone call %s: the line closed before the goodbye finished", call_id)
            closed = True
            # Ending the stream ends the <Connect>, which hangs up the phone.
            await _close_quietly(ws, 1000 if finished else 1008)
            if finished:
                # After the hang-up, so that nobody waits on the line for it. If the service is
                # throttled before it finishes, the sweep writes the note instead.
                await run_after_call(deps, call_id)
        finally:
            try:
                if not closed:
                    await _close_quietly(ws, 1001)  # cancelled: do not leave the line to a default
            finally:
                await _clean_up(deps, reader, call_id, finished)
