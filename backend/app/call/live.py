"""Runs one Gemini Live session for a call. The only module that imports google-genai."""
import asyncio
import logging
import time
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from typing import Awaitable, Callable

from google.genai import types

from app.call.audio import GEMINI_INPUT_MIME
from app.call.echo import is_echo
from app.call.guidance import TOOL_DECLARATIONS
from app.call.tools import EMERGENCY_LINE_FIRST

logger = logging.getLogger(__name__)

# Once the agent has called end_call, the call ends after this many seconds with no model audio...
END_GRACE_SECONDS = 5.0
# ...and in any case this long after end_call was first seen, however much audio keeps arriving.
END_HARD_CAP_SECONDS = 30.0
# The agent sometimes says goodbye and never calls end_call (seen on real calls, 12 October).
# Once every question is answered, a silence first has the agent asked for its goodbye, and a
# second one ends the call as completed; how long a silence is the closing_silence_seconds
# given to run_call. This is how often that is looked at.
CLOSING_CHECK_SECONDS = 1.0
# How often the quiet-line task looks again while something holds its prompt back.
QUIET_CHECK_SECONDS = 0.25
MODEL_AUDIO_BYTES_PER_SECOND = 24000 * 2  # 16-bit mono at 24 kHz
# How loud (root mean square of 16-bit samples) the person's audio must be to count as someone
# speaking, for the silence checks only. Their words come back as text late, often only when they
# stop, so the text alone let a prompt go out over a long answer (15 October). Speech on a phone
# line is in the thousands; the hiss of a quiet line is well under a hundred.
SPEECH_LEVEL = 500


def _loudness(pcm: bytes) -> float:
    """The root mean square of 16-bit little-endian samples."""
    samples = memoryview(pcm[: len(pcm) - len(pcm) % 2]).cast("h")
    return (sum(sample * sample for sample in samples) / len(samples)) ** 0.5 if len(samples) else 0.0
# For how long what the agent said is compared with what is heard, to tell its own voice coming
# back down the line from a reply. The greeting is compared for the whole call: on a real call
# it came back forty seconds after it was spoken.
ECHO_WINDOW_SECONDS = 60.0
QUESTION_MARKS = ("?", "\u061f")
# Longest we wait, on the way out, for a tool call that is still writing in its worker thread.
TOOL_DRAIN_SECONDS = 10.0

# Sent to the agent when nobody has spoken for a while in the middle of a call, and the line
# that marks it in the transcript.
QUIET_PROMPT = (
    "The line has been quiet. Ask briefly whether they can hear you and are all right, and tell "
    "them how to press a button for help if they cannot speak, as your instructions say. Then ask "
    "your last question again in a few words. If you had not finished speaking, finish."
)
QUIET_NOTE = "[prompted after a silence]"
# Sent to the agent when every question is answered and the line has gone quiet without a
# goodbye, before the call is ended for it; and the line that marks it in the transcript.
CLOSING_PROMPT = (
    "You have every answer you need. Say the closing line and goodbye now, then call end_call "
    'with outcome "completed".'
)
CLOSING_NOTE = "[prompted to close the call]"
# Sent instead of ending the call when the closing silence would end it but an emergency's 108
# line has not been heard yet (end_call is refused until it has); and its transcript line.
EMERGENCY_CLOSING_PROMPT = (
    "An emergency was flagged and the person has not yet heard what to do. Tell them now to call "
    "108 or to ask someone nearby to call, and that a health worker is being alerted. Then say "
    'goodbye and call end_call with outcome "emergency".'
)
EMERGENCY_CLOSING_NOTE = "[prompted to give the emergency line]"
# Added to the result of a tool the agent called after it had already spoken in that turn.
# Seen on a real call (13 October): the result made it start a new turn that asked the same
# question again in other words, over the person's answer.
ALREADY_SPOKEN = (
    "You have already spoken in this turn. Say nothing more now and do not repeat your question. "
    "Wait for the person's reply."
)

# The timeline of a call holds fixed strings only, never what the model or the person said:
# a tool or a question is named only if the script has it, and a refusal by one of these codes.
TOOL_NAMES = frozenset(tool["name"] for tool in TOOL_DECLARATIONS)
QUESTION_IDS = frozenset(
    next(tool for tool in TOOL_DECLARATIONS if tool["name"] == "record_answer")["parameters"]["properties"][
        "question_id"
    ]["enum"]
)
REFUSALS = {
    "no answer heard": "no_reply",
    "follow-ups not asked": "follow_ups",
    "unknown question_id": "bad_question",
    "value is not allowed for this question": "bad_value",
    "type must be one of the allowed values": "bad_value",
    "outcome is not allowed": "bad_value",
    "call not found": "call_not_found",
    "say the emergency line first": "emergency_line_first",
    "could not record the emergency": "store",
    "internal error": "internal_error",
}

SendAudio = Callable[[bytes], Awaitable[None]]
ClearAudio = Callable[[], Awaitable[None]]


# The language code the voice model is given for a resident's language: those on the Vertex list.
# Marwari has no code of its own and is spoken to as Hindi; Gujarati is not on the list.
LANGUAGE_CODES = {"hi": "hi-IN", "mwr": "hi-IN", "en": "en-IN", "mr": "mr-IN"}
# Tools whose result needs no words of its own: the agent goes on with the conversation. Their
# accepted results are sent with the scheduling of settings.live_answer_scheduling.
ANSWER_TOOLS = frozenset({"record_answer", "set_respondent"})
# What a tool that returned nothing answers instead: an empty result made the model call tools
# again and again before speaking (Vertex migration notes for gemini-3.8-live).
EMPTY_RESULT = {"status": "ok", "message": "Done. Carry on with the conversation."}


def live_config(settings, instruction: str, language: str | None = None, resume_handle: str | None = None) -> dict:
    # A setting left at "default", 0 or empty is not sent at all, so the service's own default
    # applies: that is the baseline an experiment is compared with.
    detection: dict = {"silence_duration_ms": settings.live_silence_ms}
    if settings.live_start_sensitivity != "default":
        detection["start_of_speech_sensitivity"] = f"START_SENSITIVITY_{settings.live_start_sensitivity.upper()}"
    if settings.live_end_sensitivity != "default":
        detection["end_of_speech_sensitivity"] = f"END_SENSITIVITY_{settings.live_end_sensitivity.upper()}"
    if settings.live_prefix_padding_ms:
        detection["prefix_padding_ms"] = settings.live_prefix_padding_ms
    realtime_input: dict = {"automatic_activity_detection": detection}
    if not settings.live_interruptions:
        realtime_input["activity_handling"] = "NO_INTERRUPTION"
    config = {
        "response_modalities": ["AUDIO"],
        "system_instruction": instruction,
        "input_audio_transcription": {},
        "output_audio_transcription": {},
        "realtime_input_config": realtime_input,
        "tools": [{"function_declarations": TOOL_DECLARATIONS}],
    }
    if settings.live_voice:
        config["speech_config"] = {"voice_config": {"prebuilt_voice_config": {"voice_name": settings.live_voice}}}
    code = LANGUAGE_CODES.get(language or "") if settings.live_language_codes else None
    if code:
        config.setdefault("speech_config", {})["language_code"] = code
        config["input_audio_transcription"] = {"language_codes": [code]}
    if settings.live_resume:
        # Asks the service for resumption handles; with a handle, carries on that conversation.
        config["session_resumption"] = {"handle": resume_handle} if resume_handle else {}
    return config


def live_settings_snapshot(settings) -> dict:
    """The model and every setting that is tuned between test calls, as plain values.

    Logged when a session opens and stored on the call, so that two calls made with different
    settings can be told apart afterwards. Settings only: nothing about the person called."""
    return {
        "live_model": settings.live_model,
        "live_voice": settings.live_voice,
        "live_speaks_as": settings.live_speaks_as,
        "live_silence_ms": settings.live_silence_ms,
        "live_start_sensitivity": settings.live_start_sensitivity,
        "live_end_sensitivity": settings.live_end_sensitivity,
        "live_prefix_padding_ms": settings.live_prefix_padding_ms,
        "live_interruptions": settings.live_interruptions,
        "live_quiet_seconds": settings.live_quiet_seconds,
        "live_quiet_limit": settings.live_quiet_limit,
        "live_greeting_guard_seconds": settings.live_greeting_guard_seconds,
        "live_closing_silence_seconds": settings.live_closing_silence_seconds,
        "live_mute_repeats": settings.live_mute_repeats,
        "live_language_codes": settings.live_language_codes,
        "live_answer_scheduling": settings.live_answer_scheduling,
        "live_pre_open_seconds": settings.live_pre_open_seconds,
        "live_resume": settings.live_resume,
        "end_grace_seconds": END_GRACE_SECONDS,  # a constant, kept here because it shapes how a call ends
    }


def _tool_event(name, args: dict, result) -> dict:
    """What the timeline keeps of one tool call. Takes the note meant for the timeline out of
    `result`, so that the agent is not told of it."""
    known = isinstance(name, str) and name in TOOL_NAMES
    fields: dict = {"name": name if known else "unknown"}
    question = args.get("question_id")
    if name == "record_answer" and isinstance(question, str) and question in QUESTION_IDS:
        fields["question"] = question
    if not isinstance(result, dict):
        return fields
    error = result.get("error")
    if error is not None:
        code = REFUSALS.get(error, "error") if isinstance(error, str) else "error"
        fields["refused"] = code if known else "unknown_tool"
    accepted_after = result.pop("accepted_after_refusals", None)
    if isinstance(accepted_after, int):
        fields["accepted_after_refusals"] = accepted_after
    return fields


def _function_response(call_id, name, result, answer_scheduling: str = "default") -> types.FunctionResponse:
    """The answer to one tool call. Never empty. An accepted record_answer or set_respondent is
    sent with `answer_scheduling` ("silent" or "when_idle"; "default" sends none). A refusal, and
    any other tool, keeps the default scheduling, so that the agent acts on it."""
    if not isinstance(result, dict) or not result:
        result = dict(EMPTY_RESULT)
    scheduling = None
    if answer_scheduling != "default" and name in ANSWER_TOOLS and "error" not in result:
        scheduling = types.FunctionResponseScheduling(answer_scheduling.upper())
    return types.FunctionResponse(id=call_id, name=name, response=result, scheduling=scheduling)


_clients: dict[tuple[str, str], object] = {}


def live_client(settings):
    """One Vertex client per project and location, shared by every call. A new client per call
    paid its set-up again each time: measured on 15 October, opening a session took about 3.5 s
    with a new client and 2.2 s with a shared one."""
    from google import genai

    key = (settings.gcp_project, settings.live_location)
    client = _clients.get(key)
    if client is None:
        client = _clients[key] = genai.Client(vertexai=True, project=settings.gcp_project, location=settings.live_location)
    return client


async def warm_up(settings) -> None:
    """Open and close one session at start-up, so that the first real call does not also pay for
    loading the SDK, fetching credentials and the first connection (measured on 15 October: the
    first call after a start took 9.5 s to the agent's first words, later ones 3.5 to 4.7 s).
    Never raises: if it fails, the first call simply pays as before."""
    started = time.perf_counter()
    try:
        async with open_live_session(settings, "This session only checks the connection. Say nothing."):
            pass
    except Exception as error:
        logger.warning("could not warm up the voice model (%s)", type(error).__name__)
        return
    logger.info("voice model warmed up in %.1f s", time.perf_counter() - started)


def _connect(settings, instruction: str, language: str | None = None, resume_handle: str | None = None):
    connection = live_client(settings).aio.live.connect(
        model=settings.live_model, config=live_config(settings, instruction, language, resume_handle)
    )
    logger.info("live session settings: %s", live_settings_snapshot(settings))
    return connection


async def _close_quietly(connection) -> None:
    # A resumed connection is closed with the call; a failure to close it must not touch the call.
    try:
        await connection.__aexit__(None, None, None)
    except Exception as error:
        logger.info("a resumed voice connection did not close cleanly (%s)", type(error).__name__)


def _dropped(error: Exception) -> bool:
    """Whether an error is the connection closing (a WebSocket close code), not a refusal."""
    from google.genai import errors

    if isinstance(error, errors.APIError):
        return isinstance(error.code, int) and 1000 <= error.code < 2000
    return type(error).__name__.startswith("ConnectionClosed")


class ResumableSession:
    """The session a call talks to. If its connection drops in the middle of a call, it is
    opened again once with the latest resumption handle the service sent, and the call goes on
    with it: what was being sent is sent again, and what the model says next is received.
    Also logs the service's notice that it is about to close the connection (go_away)."""

    def __init__(self, session, reopen=None, resumes: int = 1) -> None:
        self._session = session
        self._reopen = reopen  # async (handle) -> a new session; None: never resumed
        self._resumes_left = resumes
        self._lock = asyncio.Lock()
        self.handle: str | None = None
        self.resumed = 0
        self.answer_scheduling = "default"  # read by run_call

    async def send_realtime_input(self, **kwargs) -> None:
        await self._send("send_realtime_input", kwargs)

    async def send_client_content(self, **kwargs) -> None:
        await self._send("send_client_content", kwargs)

    async def send_tool_response(self, **kwargs) -> None:
        await self._send("send_tool_response", kwargs)

    async def _send(self, method: str, kwargs: dict) -> None:
        session = self._session
        try:
            await getattr(session, method)(**kwargs)
        except Exception as error:
            if not await self._resume(session, error):
                raise
            await getattr(self._session, method)(**kwargs)

    async def receive(self):
        while True:
            session = self._session
            try:
                async with aclosing(session.receive()) as messages:
                    async for message in messages:
                        self._watch(message)
                        yield message
                return
            except Exception as error:
                if not await self._resume(session, error):
                    raise
                # Carry on in the same turn with the new connection.

    def _watch(self, message) -> None:
        update = getattr(message, "session_resumption_update", None)
        if update is not None and getattr(update, "resumable", False) and getattr(update, "new_handle", None):
            self.handle = update.new_handle
        going = getattr(message, "go_away", None)
        if going is not None:
            logger.warning(
                "the voice service will close this connection in %s (resumable: %s)",
                getattr(going, "time_left", None), self.handle is not None and self._resumes_left > 0,
            )

    async def _resume(self, dropped_session, error: Exception) -> bool:
        if not _dropped(error):
            return False
        async with self._lock:
            if self._session is not dropped_session:
                return True  # another task has already resumed it
            if self._reopen is None or self._resumes_left <= 0 or not self.handle:
                logger.warning(
                    "the voice connection dropped (%s) and cannot be resumed (%s); the call ends as dropped",
                    type(error).__name__, "no handle" if not self.handle else "already resumed",
                )
                return False
            self._resumes_left -= 1
            started = time.perf_counter()
            logger.warning("the voice connection dropped (%s); resuming the session", type(error).__name__)
            try:
                self._session = await self._reopen(self.handle)
            except Exception as reopen_error:
                logger.warning(
                    "could not resume the voice session (%s); the call ends as dropped", type(reopen_error).__name__
                )
                return False
            self.resumed += 1
            logger.warning("voice session resumed in %.1f s", time.perf_counter() - started)
            return True


class _PreOpened:
    """A session opened for a call that has not been answered yet. Its own task holds the
    connection open, so that it is closed by the task that opened it."""

    def __init__(self, instruction: str, language: str | None) -> None:
        self.instruction = instruction
        self.language = language
        self.ready: asyncio.Future = asyncio.get_running_loop().create_future()  # the session, or None
        self.claimed = asyncio.Event()
        self.released = asyncio.Event()
        self.opened_at = 0.0
        self.task: asyncio.Task | None = None


# Pre-opened sessions not yet taken by a call, by the instruction they were opened with: the call
# that is answered opens a session with the same instruction and takes this one instead.
_pre_opened: dict[str, list[_PreOpened]] = {}


def _forget(held: _PreOpened) -> None:
    waiting = _pre_opened.get(held.instruction, [])
    if held in waiting:
        waiting.remove(held)
    if not waiting:
        _pre_opened.pop(held.instruction, None)


async def _hold(held: _PreOpened, settings) -> None:
    try:
        async with AsyncExitStack() as stack:
            session = await asyncio.wait_for(
                stack.enter_async_context(_connect(settings, held.instruction, held.language)),
                timeout=settings.live_connect_seconds,
            )
            held.opened_at = time.perf_counter()
            held.ready.set_result(session)
            try:
                await asyncio.wait_for(held.claimed.wait(), timeout=settings.live_pre_open_seconds)
            except asyncio.TimeoutError:
                _forget(held)
                logger.info("a pre-opened voice session was not used within %.0f s; closed", settings.live_pre_open_seconds)
                return
            await held.released.wait()
    except Exception as error:
        logger.warning("could not pre-open a voice session (%s)", type(error).__name__)
    finally:
        _forget(held)
        if not held.ready.done():
            held.ready.set_result(None)


def pre_open_session(settings, instruction: str, language: str | None = None) -> None:
    """Start opening the session of a call that is being dialled, so that it is ready when the
    phone is answered; open_live_session with the same instruction then takes it. Closed if no
    call takes it within settings.live_pre_open_seconds of opening. Runs on the event loop.
    Never raises: a call that finds no pre-opened session opens its own, as before."""
    if settings.live_pre_open_seconds <= 0:
        return
    try:
        held = _PreOpened(instruction, language)
        _pre_opened.setdefault(instruction, []).append(held)
        held.task = asyncio.get_running_loop().create_task(_hold(held, settings))
    except Exception as error:
        logger.warning("could not start pre-opening a voice session (%s)", type(error).__name__)


async def _take_pre_opened(settings, instruction: str):
    """The pre-opened session for this instruction, what releases it, and the language it was
    opened with; or (None, None, None)."""
    waiting = _pre_opened.get(instruction)
    if not waiting:
        return None, None, None
    held = waiting[0]
    _forget(held)
    held.claimed.set()

    async def release() -> None:
        held.released.set()
        if held.task is not None:
            await asyncio.wait([held.task], timeout=settings.live_connect_seconds)

    try:
        session = await asyncio.wait_for(asyncio.shield(held.ready), timeout=settings.live_connect_seconds)
    except asyncio.TimeoutError:
        held.released.set()
        return None, None, None
    except BaseException:  # cancelled while it was still opening: it must not be held for ever
        held.released.set()
        raise
    if session is None:
        return None, None, None
    logger.info("using a voice session pre-opened %.1f s ago", time.perf_counter() - held.opened_at)
    return session, release, held.language


@asynccontextmanager
async def open_live_session(settings, instruction: str, language: str | None = None):
    asked_at = time.perf_counter()
    async with AsyncExitStack() as stack:
        session, release, pre_opened_language = await _take_pre_opened(settings, instruction)
        if session is not None:
            stack.push_async_callback(release)
            language = language or pre_opened_language  # a resumed connection keeps it
        else:
            # A session that will not open must fail the attempt (it is retried later), not hold
            # the line silent until the caller gives up.
            session = await asyncio.wait_for(
                stack.enter_async_context(_connect(settings, instruction, language)),
                timeout=settings.live_connect_seconds,
            )

        async def reopen(handle: str):
            connection = _connect(settings, instruction, language, resume_handle=handle)
            resumed = await asyncio.wait_for(connection.__aenter__(), timeout=settings.live_connect_seconds)
            stack.push_async_callback(_close_quietly, connection)
            return resumed

        logger.info(
            "voice session ready in %.2f s (%s)", time.perf_counter() - asked_at,
            "pre-opened" if release is not None else "opened now",
        )
        resumable = ResumableSession(session, reopen if settings.live_resume else None)
        resumable.answer_scheduling = settings.live_answer_scheduling
        resumable.opened = "pre-opened" if release is not None else "opened now"  # for the pickup line
        yield resumable


open_live_session.pre_open = pre_open_session  # what a phone call being dialled uses


async def run_call(
    session,
    tools,
    audio_in: asyncio.Queue,
    send_audio: SendAudio,
    clear_audio: ClearAudio,
    opening: str,
    max_seconds: float,
    *,
    quiet_seconds: float = 0,
    quiet_limit: int = 2,
    greeting_guard_seconds: float = 0,
    closing_silence_seconds: float = 10.0,
    mute_repeats: bool = False,
    notes: asyncio.Queue | None = None,
    answer_scheduling: str | None = None,
    answered_at: float | None = None,
) -> str:
    """Relay audio both ways and answer tool calls until the call ends. Returns why it ended.

    Structure: two relay tasks (caller to model, model to caller) plus a grace timer that
    starts when the agent has called end_call (an idle timer with a hard cap). The first to finish decides the reason.
    Tool calls run in worker threads that cannot be cancelled, so each is shielded and
    recorded in `started`; before returning, on every path, they are awaited (bounded).
    The reason is finally corrected to "ended_by_agent" whenever tools.ended is set.

    Two things are off unless asked for. With greeting_guard_seconds, the person's audio is
    replaced by silence until the first greeting has been heard (or that long has passed), so
    a "hello" said over it does not cut it off. With quiet_seconds, an agent is prompted when
    nobody has spoken for that long in the middle of a call, at most quiet_limit times in a row
    without the person being heard; that is a fifth task, which is not among those waited on,
    so it never decides how the call ends, and which sends nothing while a tool call is running,
    before the greeting is over, or once the questions are answered.

    With mute_repeats, the agent does not ask twice in a row: once a turn of its own has ended
    in a question, nothing more of what it says is passed on (or kept in the transcript) until
    the person is heard, the agent has been prompted or refused, or an emergency is flagged.

    What the person is heard to say is compared with what the agent has said: its own words
    coming back down the line are not a reply, and are not kept.

    What happens on the line is noted with tools.add_event as it happens: when, and what kind
    of thing, never a word that was said.

    answer_scheduling is the scheduling sent with the accepted results of record_answer and
    set_respondent ("default", "silent" or "when_idle"); None takes it from the session, which
    open_live_session gives the value of settings.live_answer_scheduling.

    answered_at is when the call was answered, on the event loop's clock, before its voice
    session was asked for: the pickup is logged from then. Without it, from the start here.
    """
    if answer_scheduling is None:
        answer_scheduling = getattr(session, "answer_scheduling", "default")
    started: list[asyncio.Future] = []
    agent_ended = asyncio.Event()  # set when tools.ended is first seen
    clock = asyncio.get_running_loop().time
    call_began = clock()
    ended_at = [0.0]
    last_audio_at = [0.0]
    # When the agent's words so far will have been heard, and when the person was last heard.
    # The model sends speech faster than it is spoken, so the time a chunk arrived is not enough.
    speaking_until = [0.0]
    last_heard_at = [0.0]
    sounded_at = [0.0]  # when the person's audio was last as loud as speech (SPEECH_LEVEL)
    sounded_level = [0.0]  # how loud it was then
    last_tool_at = [0.0]  # when a tool call of the agent's last finished
    # The greeting guard: the person's audio is withheld until the model's first turn is complete
    # and its words have been heard, and never past guard_until (set when the relays start).
    guard_until = [0.0]
    guard_lifted = [greeting_guard_seconds <= 0]
    first_turn_done = [False]
    # Set when something has happened after which the agent must be heard whatever it last
    # said: a prompt was sent, or a tool call was refused and told it what to do.
    may_speak_again = [False]
    tasks: list[asyncio.Task] = []

    def note(kind: str, **fields) -> None:
        # The timeline is for reading afterwards: failing to write it must never touch the call.
        try:
            tools.add_event(kind, **fields)
        except Exception as error:
            logger.warning("timeline event not noted: %s", type(error).__name__)

    def guard_up() -> bool:
        if guard_lifted[0]:
            return False
        timed_out = clock() >= guard_until[0]
        # A complete turn is not a heard one: the model sends speech faster than it is spoken.
        if not timed_out and not (first_turn_done[0] and clock() >= speaking_until[0]):
            return True
        guard_lifted[0] = True
        note("guard_lifted", by="time" if timed_out else "heard")
        return False

    def note_ended() -> None:
        if tools.ended and not agent_ended.is_set():
            ended_at[0] = clock()
            agent_ended.set()

    async def call_tool(name: str, args: dict, handler: Callable[..., dict] | None = None) -> dict:
        if handler is None:
            future = asyncio.ensure_future(asyncio.to_thread(tools.handle, name, args))
        else:
            future = asyncio.ensure_future(asyncio.to_thread(handler, **args))
        started.append(future)
        # retrieve any exception even if nobody is awaiting any more
        future.add_done_callback(lambda f: f.cancelled() or f.exception())
        return await asyncio.shield(future)

    async def caller_to_model() -> str:
        # Callers must supply a continuous audio stream, silence included: the service detects
        # the end of a turn from trailing silence, so a stream that just stops gets no reply.
        while True:
            chunk = await audio_in.get()
            if chunk is None:
                return "caller_hung_up"
            if guard_up():
                # Silence of the same length, not nothing: the stream must stay continuous.
                chunk = bytes(len(chunk))
            elif (level := _loudness(chunk)) >= SPEECH_LEVEL:
                sounded_at[0], sounded_level[0] = clock(), level
            await session.send_realtime_input(
                audio=types.Blob(data=chunk, mime_type=GEMINI_INPUT_MIME)
            )

    async def model_to_caller() -> str:
        turn_audio = False  # model audio forwarded in the current turn
        turn_interrupted = False
        turn_bytes = 0
        turn_tools = 0
        turn_open = False  # the agent has said something in the current turn
        # A turn that repeats an unanswered question is not passed on. What it has said so far
        # is held, in case the person's words arrive late and show it to be an answer to them.
        turn_muted = False
        held: list[tuple[str, bytes | str]] = []
        muted_bytes = 0
        # The last turn the person heard: its words, and what has happened since it began.
        last_words = ""
        last_interrupted = False
        heard_since = False
        emergency = False
        in_reply = False  # the person is the last one whose words arrived
        # The reply in progress, piece by piece: it may turn out to be the agent's own echo.
        reply_text = ""
        reply_kept = False  # its pieces are in the transcript
        reply_echo = False
        before_reply = (0.0, False)  # last_heard_at and heard_since, to put back if it is an echo
        spoken: list[list] = []  # [when, words] of each turn the person heard; the first is the greeting

        def seconds(audio_bytes: int) -> float:
            return round(audio_bytes / MODEL_AUDIO_BYTES_PER_SECOND, 1)

        def begin_heard_turn() -> None:
            nonlocal last_words, last_interrupted, heard_since, in_reply
            last_words, last_interrupted, heard_since, in_reply = "", False, False, False
            may_speak_again[0] = False
            if len(spoken) > 20:
                del spoken[1:len(spoken) - 19]  # the greeting and the last turns are enough
            spoken.append([clock(), ""])
            tools.agent_turn_began()

        def open_turn() -> None:
            # Decided once, at the first thing the agent says in a turn. If the words of the
            # turn before have not arrived, it is not known to be a question: pass it on.
            nonlocal turn_open, turn_muted
            turn_open = True
            turn_muted = (
                mute_repeats
                and last_words.strip().endswith(QUESTION_MARKS)
                and not last_interrupted
                and not heard_since
                and not may_speak_again[0]
                and not emergency
            )
            if not turn_muted:
                begin_heard_turn()

        async def forward(data: bytes) -> None:
            nonlocal turn_audio, turn_bytes
            if not last_audio_at[0]:
                logger.info("first model audio %.2f s after the session was ready", clock() - call_began)
                # From the answer to the first words, the session's opening included: a browser
                # call opens it now, a phone call has it pre-opened while the phone rang.
                pickup = clock() - (call_began if answered_at is None else answered_at)
                opened = getattr(session, "opened", None)
                how = f" ({opened})" if opened in ("pre-opened", "opened now") else ""
                logger.info("voice pickup %.2f s%s", pickup, how)
            if not turn_audio and sounded_at[0] > last_audio_at[0]:
                # The first words of a turn that follows the person's speech: how long they
                # waited for it, from the last moment their audio was as loud as speech.
                gap = clock() - sounded_at[0]
                used_tool = last_tool_at[0] > sounded_at[0]
                note("reply", gap_s=round(gap, 2), tool=used_tool)
                logger.info("voice reply %.2f s after the person stopped (tool: %s)", gap, used_tool)
            await send_audio(data)
            tools.agent_spoke()  # heard, if not yet as text: an emergency's 108 line counts as said
            last_audio_at[0] = clock()
            speaking_until[0] = max(speaking_until[0], clock()) + len(data) / MODEL_AUDIO_BYTES_PER_SECOND
            turn_audio = True
            turn_bytes += len(data)

        def say(text: str) -> None:
            nonlocal last_words, in_reply
            in_reply = False
            last_words += text
            spoken[-1][1] += text
            tools.add_transcript("agent", text)

        def said_lately() -> list[str]:
            oldest = clock() - ECHO_WINDOW_SECONDS
            return [words for number, (at, words) in enumerate(spoken) if number == 0 or at >= oldest]

        while True:  # receive() returns at every turn_complete; keep listening for the whole call
            received_any = False
            async with aclosing(session.receive()) as messages:
                async for message in messages:
                    received_any = True
                    if message.data:
                        if not turn_open:
                            open_turn()
                        if turn_muted:
                            held.append(("audio", message.data))
                            muted_bytes += len(message.data)
                        else:
                            await forward(message.data)
                    content = message.server_content
                    if content:
                        if getattr(content, "interrupted", False):
                            turn_interrupted = True
                            if turn_muted:
                                held.clear()  # cut off by the person's answer: never to be heard
                            else:
                                last_interrupted = True
                            await clear_audio()
                            speaking_until[0] = clock()
                            note("interrupted", audio_seconds_so_far=seconds(turn_bytes + muted_bytes))
                        heard = getattr(content, "input_transcription", None)
                        if heard and heard.text:
                            if not in_reply:
                                note("heard")  # the start of a reply: that it came, not what it was
                                reply_text, reply_kept, reply_echo = "", False, False
                                before_reply = (last_heard_at[0], heard_since)
                            in_reply = True
                            reply_text += heard.text
                            if is_echo(reply_text, said_lately()):
                                if not reply_echo:
                                    # The agent's own voice, coming back down the line: not a
                                    # reply to anything, and not kept.
                                    reply_echo = True
                                    if reply_kept:
                                        tools.drop_reply()
                                    reply_kept = False
                                    last_heard_at[0], heard_since = before_reply
                                    note("heard", echo=True)
                            else:
                                # If it was taken for an echo and has gone on in words of its
                                # own, it is a reply after all: all of it is kept.
                                if turn_muted and not turn_interrupted:
                                    # Their words arrived after the agent began to answer them:
                                    # this turn is no repeat. Pass it on from its start.
                                    turn_muted, muted_bytes = False, 0
                                    begin_heard_turn()
                                    for kind, piece in held:
                                        if kind == "audio":
                                            await forward(piece)
                                        else:
                                            say(piece)
                                    held.clear()
                                tools.add_transcript("resident", reply_text if reply_echo else heard.text)
                                reply_echo, reply_kept = False, True
                                last_heard_at[0] = clock()
                                heard_since, in_reply = True, True
                        spoken_now = getattr(content, "output_transcription", None)
                        if spoken_now and spoken_now.text:
                            if not turn_open:
                                open_turn()
                            if turn_muted:
                                held.append(("text", spoken_now.text))
                            else:
                                say(spoken_now.text)
                    if message.tool_call:
                        responses = []
                        for call in message.tool_call.function_calls:
                            args = dict(call.args or {})
                            after_speech = turn_audio  # the agent spoke first, then called the tool
                            result = await call_tool(call.name, args)
                            turn_tools += 1
                            last_tool_at[0] = clock()
                            event = _tool_event(call.name, args, result)
                            if call.name == "flag_emergency":
                                emergency = True  # from here on everything it says must be heard
                            if "refused" in event:
                                may_speak_again[0] = True  # the refusal told it what to do
                            if after_speech:
                                event["after_speech"] = True
                                # Not on a refusal, whose own instruction must stand; and never
                                # on flag_emergency, after which the 108 line has to be spoken.
                                if (
                                    isinstance(result, dict)
                                    and "error" not in result
                                    and call.name != "flag_emergency"
                                ):
                                    result = {**result, "now": ALREADY_SPOKEN}
                            note("tool", **event)
                            responses.append(_function_response(call.id, call.name, result, answer_scheduling))
                        note_ended()  # before the response is awaited: a hung response must not delay it
                        try:
                            await session.send_tool_response(function_responses=responses)
                        except Exception as error:
                            # a dead session shows itself at the next receive()
                            logger.warning("tool response rejected: %s", type(error).__name__)
                    note_ended()
                    if content and getattr(content, "turn_complete", False):
                        first_turn_done[0] = True  # the guard comes down once this has been heard
                        fields = {
                            "audio_seconds": seconds(turn_bytes + muted_bytes),
                            "interrupted": turn_interrupted,
                            "tools": turn_tools,
                        }
                        if turn_muted:
                            fields["muted"] = True  # said by the agent, not passed on to the person
                        elif turn_open:
                            tools.agent_turn_ended()
                        note("agent_turn", **fields)
                        # the goodbye counts only if this turn was not cut off and carried speech
                        if tools.ended and turn_audio and not turn_interrupted:
                            return "ended_by_agent"
                        turn_audio = False
                        turn_interrupted = False
                        turn_bytes = 0
                        turn_tools = 0
                        turn_open, turn_muted, muted_bytes = False, False, 0
                        held.clear()
            if not received_any:
                logger.warning("live session closed with no messages")
                return "error"

    async def grace_timer() -> str:
        # Idle timer with a hard cap: audio forwarded after the agent ended restarts the idle wait.
        await agent_ended.wait()
        while True:
            deadline = min(
                max(last_audio_at[0], ended_at[0]) + END_GRACE_SECONDS,
                ended_at[0] + END_HARD_CAP_SECONDS,
            )
            remaining = deadline - clock()
            if remaining <= 0:
                return "ended_by_agent"
            await asyncio.sleep(remaining)

    async def closing_silence() -> str:
        # Ends a finished conversation the agent has not ended. Never before the questions are
        # answered: until then a silence may be the person thinking, or fetching the resident.
        # In two stages, each after closing_silence_seconds with nobody speaking: first the
        # agent is asked for its goodbye (on a real call it said nothing after the last answer,
        # and the call was cut with no closing line); only then is the call ended for it.
        # If an emergency's 108 line is still owed when the call would be ended, end_call is
        # refused: the agent is then asked for the line, and after one more silence the call is
        # ended whatever happened, as the help button's fallback does, so it always has an outcome.
        asked_at: float | None = None
        emergency_asked = False

        async def ask(text: str, transcript_note: str, **fields) -> None:
            nonlocal asked_at
            sent = True
            try:
                # Bounded: a send that hangs must not keep the call from being ended.
                await asyncio.wait_for(
                    session.send_client_content(turns={"role": "user", "parts": [{"text": text}]}, turn_complete=True),
                    timeout=closing_silence_seconds,
                )
            except Exception as error:
                sent = False
                logger.warning("prompt to close the call rejected: %s", type(error).__name__)
            asked_at = clock()
            may_speak_again[0] = may_speak_again[0] or sent
            note("prompt", closing=True, **fields, sent=sent)
            if sent:
                try:
                    tools.add_transcript("system", transcript_note)
                except Exception as error:  # this task decides how a call ends: a note must not
                    logger.warning("closing prompt not noted: %s", type(error).__name__)

        while True:
            await asyncio.sleep(CLOSING_CHECK_SECONDS)
            if tools.ended or not tools.questions_done():
                continue
            if any(not future.done() for future in started):
                continue  # the agent is acting: neither stage may land in the middle of that
            quiet_since = max(speaking_until[0], last_heard_at[0], sounded_at[0], asked_at or 0.0)
            if clock() - quiet_since < closing_silence_seconds:
                continue
            if asked_at is None:
                await ask(CLOSING_PROMPT, CLOSING_NOTE)
                continue
            logger.warning("the agent did not end a finished call; ended after a silence")
            outcome = "emergency" if emergency_asked else "completed"
            result = await call_tool("end_call", {"outcome": outcome})
            owed = isinstance(result, dict) and result.get("error") == EMERGENCY_LINE_FIRST["error"]
            if owed and not emergency_asked:
                emergency_asked = True
                await ask(EMERGENCY_CLOSING_PROMPT, EMERGENCY_CLOSING_NOTE, emergency=True)
                continue
            if owed:  # asked, and still not heard: nobody else will say it, and the call must end
                await call_tool("end_call", {"outcome": outcome}, handler=tools.end_for_the_agent)
                if tools.ended is None:
                    logger.warning("the call ended after a silence has no outcome recorded")
                note("tool", name="end_call", by="closing_silence", emergency_line="not_heard")
            elif emergency_asked:
                note("tool", name="end_call", by="closing_silence", emergency_line="heard")
            else:
                note("tool", name="end_call", by="closing_silence")
            note_ended()
            return "ended_by_agent"

    async def prompt_when_quiet() -> str:
        # Seen on real calls (13 October): the agent asked a question and the line then stayed
        # silent for over half a minute, until the caller spoke. When nobody has been speaking
        # for quiet_seconds, the agent is prompted. Runs until cancelled; never returns a reason.
        # The prompt must never land while the agent is acting: "ask your last question again"
        # in the middle of flag_emergency or end_call would undo what the agent is doing.
        prompted_at = clock()  # so that the wait never starts before the call does
        in_a_row = 0  # prompts since the person was last heard
        heard_at = last_heard_at[0]
        noise_noted = False  # the prompt was held back by sound with no words: said once a quiet spell
        while True:
            if last_heard_at[0] != heard_at:
                heard_at, in_a_row, noise_noted = last_heard_at[0], 0, False
            running = [future for future in started if not future.done()]
            if running:
                await asyncio.wait(running)  # the futures are shielded: this only waits for them
                last_tool_at[0] = clock()
                continue
            # The wait starts when the agent's words have been heard, not when they were
            # generated, and starts again when the person is heard or the agent has acted.
            quiet_since = max(speaking_until[0], last_heard_at[0], sounded_at[0], last_tool_at[0], prompted_at)
            remaining = quiet_since + quiet_seconds - clock()
            if remaining > 0:
                wordless = max(speaking_until[0], last_heard_at[0], last_tool_at[0], prompted_at)
                if not noise_noted and clock() - wordless >= 3 * quiet_seconds:
                    # No words for a long while, but sound: speech whose text is late, or noise
                    # loud enough to pass for speech. Noted so a test call can tell which.
                    noise_noted = True
                    logger.warning(
                        "no words for %.0f s but the line is not quiet (level %.0f, threshold %d)",
                        clock() - wordless, sounded_level[0], SPEECH_LEVEL,
                    )
                    note("held_by_sound", level=round(sounded_level[0]))
                await asyncio.sleep(remaining)
                continue  # look again: someone may have spoken meanwhile
            # Not before the greeting is over; not after the questions, when a quiet line is a
            # finished call and that is for closing_silence; not once the limit is reached,
            # until the person speaks.
            held_back = (
                not first_turn_done[0]
                or guard_up()
                or tools.ended
                or tools.questions_done()
                or in_a_row >= quiet_limit
            )
            if held_back:
                await asyncio.sleep(QUIET_CHECK_SECONDS)
                continue
            in_a_row += 1
            quiet_for = round(clock() - quiet_since, 1)
            sent = True
            try:
                await session.send_client_content(
                    turns={"role": "user", "parts": [{"text": QUIET_PROMPT}]}, turn_complete=True
                )
            except Exception as error:
                sent = False
                logger.warning("prompt after a silence rejected: %s", type(error).__name__)
            prompted_at, noise_noted = clock(), False
            may_speak_again[0] = may_speak_again[0] or sent
            note("prompt", quiet_seconds=quiet_for, sent=sent)
            if sent:
                tools.add_transcript("system", QUIET_NOTE)

    async def pass_notes() -> None:
        # Notes from outside the conversation (session.py: the help button), sent as they come.
        # The text is a fixed string of ours, so it may go in the transcript. Never returns.
        while True:
            text = await notes.get()
            sent = True
            try:
                await session.send_client_content(turns={"role": "user", "parts": [{"text": text}]}, turn_complete=True)
            except Exception as error:
                sent = False
                logger.warning("note to the agent rejected: %s", type(error).__name__)
            may_speak_again[0] = may_speak_again[0] or sent
            note("prompt", note=True, sent=sent)
            if sent:
                tools.add_transcript("system", text)

    reason = "error"
    logged: set[asyncio.Task] = set()  # tasks whose failure is already in the log
    try:
        await session.send_client_content(
            turns={"role": "user", "parts": [{"text": opening}]}, turn_complete=True
        )
        guard_until[0] = clock() + greeting_guard_seconds if greeting_guard_seconds > 0 else 0.0
        relays = [
            asyncio.create_task(coro) for coro in (caller_to_model(), model_to_caller(), grace_timer(), closing_silence())
        ]
        tasks.extend(relays)
        if quiet_seconds > 0:
            # Cancelled and cleaned up with the others, but not waited on: whatever happens to
            # it, it cannot decide how the call ends.
            tasks.append(asyncio.create_task(prompt_when_quiet()))
        if notes is not None:  # like the quiet prompt: cleaned up with the others, never waited on
            tasks.append(asyncio.create_task(pass_notes()))
        done, _ = await asyncio.wait(relays, timeout=max_seconds, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            reason = "timeout"
        else:
            reasons = []
            for task in done:
                if task.exception() is not None:
                    logged.add(task)
                    logger.warning("call relay failed: %s", type(task.exception()).__name__)
                    reasons.append("error")
                else:
                    reasons.append(task.result())
            for preferred in ("ended_by_agent", "caller_hung_up", "error"):
                if preferred in reasons:
                    reason = preferred
                    break
    except Exception as error:
        logger.warning("call setup failed: %s", type(error).__name__)
        reason = "error"
    finally:
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for task, result in zip(tasks, results):
            if isinstance(result, Exception) and task not in logged:
                logger.warning("call relay failed during clean-up: %s", type(result).__name__)
        running = [future for future in started if not future.done()]
        if running:
            _, stuck = await asyncio.wait(running, timeout=TOOL_DRAIN_SECONDS)
            if stuck:
                logger.warning("%d tool call(s) still running at the end of the call", len(stuck))
    # evaluated after the wait above, so a tool call that was in flight is counted
    return "ended_by_agent" if tools.ended else reason
