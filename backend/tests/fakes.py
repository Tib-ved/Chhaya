"""Test doubles for the Gemini Live session and the tool handler."""
import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace


def _content(**overrides):
    base = dict(interrupted=False, turn_complete=False, input_transcription=None, output_transcription=None)
    base.update(overrides)
    return SimpleNamespace(**base)


def audio(data: bytes):
    return SimpleNamespace(data=data, server_content=None, tool_call=None)


def said(role: str, text: str):
    key = "input_transcription" if role == "resident" else "output_transcription"
    return SimpleNamespace(
        data=None, server_content=_content(**{key: SimpleNamespace(text=text)}), tool_call=None
    )


def turn_complete():
    return SimpleNamespace(data=None, server_content=_content(turn_complete=True), tool_call=None)


def interrupted():
    return SimpleNamespace(data=None, server_content=_content(interrupted=True), tool_call=None)


def tool(call_id: str, name: str, **args):
    call = SimpleNamespace(id=call_id, name=name, args=args)
    return SimpleNamespace(
        data=None, server_content=None, tool_call=SimpleNamespace(function_calls=[call])
    )


class pause:
    """Placed in a turn: the fake's receive() sleeps this long, without yielding a message."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds


def tool_batch(*calls):
    """One message carrying several function calls: (call_id, name, args_dict) each."""
    built = [SimpleNamespace(id=i, name=n, args=a) for i, n, a in calls]
    return SimpleNamespace(data=None, server_content=None, tool_call=SimpleNamespace(function_calls=built))


class FakeLiveSession:
    """Each inner list of `turns` is what one receive() call yields.

    When the turns run out: then="block" waits for ever (a silent model), then="close"
    yields nothing (a closed session). An Exception in a turn is raised at that point.
    """

    def __init__(
        self, turns, then: str = "block", reject_tool_response: bool = False, reject_opening: bool = False,
        reject_prompts: bool = False, hang_prompts: bool = False,
    ) -> None:
        self.reject_tool_response = reject_tool_response
        self.reject_opening = reject_opening
        # What happens to a text turn sent after the opening: refused, or never answered.
        self.reject_prompts = reject_prompts
        self.hang_prompts = hang_prompts
        self.text_turns = 0
        self.tool_response_calls: list[list] = []
        self.turns = [list(turn) for turn in turns]
        self.then = then
        self.sent_audio: list[bytes] = []
        self.sent_text: list[str] = []
        self.tool_responses: list = []

    async def send_realtime_input(self, **kwargs) -> None:
        self.sent_audio.append(kwargs["audio"].data)

    async def send_client_content(self, **kwargs) -> None:
        if self.reject_opening:
            raise ConnectionError("session refused the opening")
        self.text_turns += 1
        if self.text_turns > 1 and self.reject_prompts:
            raise ConnectionError("session refused the prompt")
        if self.text_turns > 1 and self.hang_prompts:
            await asyncio.Event().wait()
        self.sent_text.append(kwargs["turns"]["parts"][0]["text"])

    async def send_tool_response(self, **kwargs) -> None:
        self.tool_response_calls.append(list(kwargs["function_responses"]))
        if self.reject_tool_response:
            raise ConnectionError("tool response refused")
        self.tool_responses.extend(kwargs["function_responses"])

    async def receive(self):
        if not self.turns:
            if self.then == "block":
                await asyncio.Event().wait()
            return
        for message in self.turns.pop(0):
            if isinstance(message, pause):
                await asyncio.sleep(message.seconds)
                continue
            await asyncio.sleep(0)
            if isinstance(message, Exception):
                raise message
            yield message


class FakeTools:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.finished = 0
        self.calls: list[tuple[str, dict]] = []
        self.transcript: list[tuple[str, str]] = []
        self.events: list[tuple[str, dict]] = []
        self.reply_pieces = 0  # the pieces of the reply in progress, as they arrived
        self.turns_heard: list[str] = []  # "began" and "ended", for each turn the person heard
        self.ended: str | None = None
        self.spoke = 0  # chunks of the agent's audio passed on to the person

    def add_event(self, kind: str, **fields) -> None:
        self.events.append((kind, fields))

    def handle(self, name: str, args: dict) -> dict:
        self.calls.append((name, args))
        if self.delay:
            time.sleep(self.delay)
        try:
            if name == "end_call":
                self.ended = args.get("outcome", "completed")
                return {"result": "ending"}
            return {"result": "recorded"}
        finally:
            self.finished += 1

    def add_transcript(self, role: str, text: str) -> None:
        self.transcript.append((role, text))
        self.reply_pieces = self.reply_pieces + 1 if role == "resident" else 0

    def drop_reply(self) -> None:
        """The reply in progress was the agent's own echo: its pieces leave the transcript."""
        if self.reply_pieces:
            del self.transcript[-self.reply_pieces:]
        self.reply_pieces = 0

    def agent_turn_began(self) -> None:
        self.reply_pieces = 0
        self.turns_heard.append("began")

    def agent_turn_ended(self) -> None:
        self.turns_heard.append("ended")

    def agent_spoke(self) -> None:
        self.spoke += 1

    def questions_done(self) -> bool:
        return False


def session_opener(session=None, fail: bool = False, seen: list | None = None, pre_opened: list | None = None):
    """Stands in for open_live_session. `seen` collects the instructions it was given; with
    `pre_opened`, it also has a pre_open that collects (instruction, language) of each call."""

    @asynccontextmanager
    async def open_session(settings, instruction, language=None):
        if seen is not None:
            seen.append(instruction)
        if fail:
            raise RuntimeError("could not open a session")
        yield session

    if pre_opened is not None:
        def pre_open(settings, instruction, language=None):
            asyncio.get_running_loop()  # like the real one, it runs on the event loop
            pre_opened.append((instruction, language))

        open_session.pre_open = pre_open
    return open_session


def make_deps(store, now, tz, open_session, queue=None, **overrides):
    from app.call.deps import CallDeps
    from app.config import Settings
    from app.dispatch import RecordingQueue

    settings = overrides.pop("settings", None) or Settings(
        _env_file=None, internal_secret="s3cret", max_call_seconds=2
    )
    return CallDeps(
        store=store,
        queue=queue or RecordingQueue(),
        settings=settings,
        clock=lambda: now,
        tz=tz,
        open_session=open_session,
        **overrides,
    )


HEALTHY_TOOL_CALLS = [
    ("set_respondent", {"type": "self"}),
    ("record_answer", {"question_id": "feeling", "value": "ok", "quote": "theek hoon"}),
    ("record_answer", {"question_id": "fluids_food", "value": "enough"}),
    ("record_answer", {"question_id": "company", "value": "with_someone"}),
    ("record_answer", {"question_id": "shelter", "value": "adequate"}),
    ("record_answer", {"question_id": "exposure", "value": "staying_in"}),
    ("record_answer", {"question_id": "help", "value": "none"}),
]


HEALTHY_REPLIES = " haan" * 6  # what the resident is heard to say: one reply before each answer
# "ok" is recorded only after the two follow-ups everyone is asked, each with a reply of its own.
FEELING_FOLLOW_UPS = [(" ji", " Chakkar?"), (" nahi", " Ulti?")]


def healthy_transcript(first_reply: str = "") -> list[str]:
    """What is said on the healthy call, line by line. `first_reply` is anything heard before it."""
    spoken = [text for reply, question in FEELING_FOLLOW_UPS for text in (reply, question)]
    return ["Namaste", first_reply + spoken[0], *spoken[1:], HEALTHY_REPLIES]


def healthy_call_turns():
    """A complete, uneventful call: a reply and then its answer, for every question, then goodbye."""
    turns = [[said("agent", "Namaste"), turn_complete()]]
    for number, (name, args) in enumerate(HEALTHY_TOOL_CALLS):
        reply = [said("resident", " haan")] if name == "record_answer" else []
        if args.get("question_id") == "feeling":
            for heard, asked in FEELING_FOLLOW_UPS:
                turns.append([said("resident", heard), said("agent", asked), turn_complete()])
        turns.append([*reply, tool(f"c{number}", name, **args), turn_complete()])
    turns.append([audio(b"goodbye-audio"), tool("end", "end_call", outcome="completed"), turn_complete()])
    return turns
