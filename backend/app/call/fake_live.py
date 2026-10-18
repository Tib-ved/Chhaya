"""A scripted stand-in for the Gemini Live session.

For building and testing the screens with no microphone, no model and no bill. Turned on with
FAKE_LIVE=true and refused on Cloud Run. It does not listen: it plays its script on a timer,
through the same tool handlers and the same call logic as a real call.

A script is a list of steps: ("agent", words), ("resident", words) or ("tool", name, args).
The Hindi lines are drafts for a native speaker to correct. Like the real script, the agent
asks one question in a turn."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from app.demo_cards import CARDS

SILENCE = b"\x00\x00" * 2400  # a tenth of a second at 24 kHz, so the page shows the agent speaking


def _message(data=None, tool_call=None, **content):
    server_content = None
    if content:
        base = dict(interrupted=False, turn_complete=False, input_transcription=None, output_transcription=None)
        server_content = SimpleNamespace(**{**base, **content})
    return SimpleNamespace(data=data, server_content=server_content, tool_call=tool_call)


def _ask(agent_says: str, resident_says: str, question_id: str, value: str) -> list[tuple]:
    return [
        ("agent", agent_says),
        ("resident", resident_says),
        ("tool", "record_answer", {"question_id": question_id, "value": value, "quote": resident_says}),
    ]


def _feeling(
    agent_says: str, resident_says: str, value: str = "ok", then: tuple[str, str] = ("नहीं।", "नहीं, कुछ नहीं।")
) -> list[tuple]:
    """The first question and its reply, then the two follow-ups everyone is asked, each a
    turn of its own with its own reply: only then is the answer recorded (it is refused sooner,
    unless it is the most worrying one)."""
    return [
        ("agent", agent_says),
        ("resident", resident_says),
        ("agent", "क्या चक्कर या सिरदर्द हो रहा है?"),
        ("resident", then[0]),
        ("agent", "क्या उल्टी, ऐंठन या कमज़ोरी है?"),
        ("resident", then[1]),
        ("tool", "record_answer", {"question_id": "feeling", "value": value, "quote": resident_says}),
    ]


def _goodbye(outcome: str = "completed") -> list[tuple]:
    return [
        ("agent", "धन्यवाद। पानी पीते रहिए और धूप से बचिए। एक स्वास्थ्य कार्यकर्ता आपसे मिलने आ सकती हैं या फ़ोन कर सकती हैं।"),
        ("tool", "end_call", {"outcome": outcome}),
    ]


def _greeting(name: str) -> tuple:
    return (
        "agent",
        f"नमस्ते, मैं ज़िला स्वास्थ्य कार्यालय की ओर से एक स्वचालित सहायक बोल रही हूँ। "
        f"आज बहुत गर्मी है। क्या मैं {name} जी से बात कर रही हूँ?",
    )


SCRIPTS: dict[str, list[tuple]] = {
    "alone_dizzy": [
        _greeting("Kamla Devi"),
        ("resident", "हाँ, मैं कमला बोल रही हूँ।"),
        ("tool", "set_respondent", {"type": "self"}),
        *_feeling("अभी आपकी तबीयत कैसी है?", "थोड़ा चक्कर आ रहा है।", "mild_symptoms", ("हाँ, चक्कर है, सिरदर्द नहीं।", "नहीं, बस थोड़ी कमज़ोरी है।")),
        *_ask("क्या आज आपने पानी पिया है?", "आज बहुत कम पानी पिया है।", "fluids_food", "low"),
        *_ask("क्या आज घर पर कोई आपके साथ है?", "नहीं, मैं अकेली हूँ।", "company", "alone"),
        *_ask("क्या आपके पास पंखा या ठंडी जगह है?", "हाँ, पंखा चल रहा है।", "shelter", "adequate"),
        *_ask("क्या आज आपको बाहर जाना है?", "नहीं, घर पर ही रहूँगी।", "exposure", "staying_in"),
        *_ask("क्या आपकी रोज़ की दवाइयाँ आपके पास हैं?", "हाँ, दवाई है।", "medicine", "has"),
        *_ask("क्या आज आपको किसी मदद की ज़रूरत है?", "नहीं, अभी नहीं।", "help", "none"),
        *_goodbye(),
    ],
    "must_work": [
        _greeting("Ramesh Kumar"),
        ("resident", "हाँ, बोल रहा हूँ।"),
        ("tool", "set_respondent", {"type": "self"}),
        *_feeling("अभी आपकी तबीयत कैसी है?", "मैं ठीक हूँ।"),
        *_ask("क्या आज आपने पानी पिया है?", "हाँ, पी रहा हूँ।", "fluids_food", "enough"),
        *_ask("क्या आज घर पर कोई आपके साथ है?", "हाँ, घरवाले हैं।", "company", "with_someone"),
        *_ask("क्या आपके पास पंखा या ठंडी जगह है?", "हाँ, है।", "shelter", "adequate"),
        *_ask("क्या आज आपको बाहर काम पर जाना है?", "हाँ, काम पर तो जाना ही पड़ेगा।", "exposure", "must_go_out"),
        *_ask("क्या आपकी रोज़ की दवाइयाँ आपके पास हैं?", "हाँ, हैं।", "medicine", "has"),
        *_ask("क्या आज आपको किसी मदद की ज़रूरत है?", "नहीं।", "help", "none"),
        *_goodbye(),
    ],
    "family_answers": [
        _greeting("Gulab Devi"),
        ("resident", "नहीं, मैं उनकी बहू बोल रही हूँ। वे सो रही हैं।"),
        ("tool", "set_respondent", {"type": "family"}),
        *_feeling("ठीक है। अभी उनकी तबीयत कैसी है?", "ठीक से पता नहीं, सुबह से सो रही हैं।", "unclear", ("पता नहीं, उन्होंने कुछ कहा नहीं।", "नहीं, ऐसा तो कुछ नहीं दिखा।")),
        *_ask("क्या आज उन्होंने पानी पिया है?", "हाँ, पिया है।", "fluids_food", "enough"),
        *_ask("क्या आज घर पर कोई उनके साथ है?", "हाँ, हम सब हैं।", "company", "with_someone"),
        *_ask("क्या उनके कमरे में पंखा है?", "हाँ, चल रहा है।", "shelter", "adequate"),
        *_ask("क्या आज उन्हें बाहर जाना है?", "नहीं।", "exposure", "staying_in"),
        *_ask("क्या आज किसी मदद की ज़रूरत है?", "नहीं, अभी नहीं।", "help", "none"),
        *_goodbye(),
    ],
    "emergency": [
        _greeting("Mohan Singh"),
        ("resident", "हाँ... सीने में दर्द हो रहा है, कुछ समझ नहीं आ रहा।"),
        ("tool", "set_respondent", {"type": "self"}),
        ("tool", "flag_emergency", {"reason": "chest pain and confusion"}),
        ("tool", "record_answer", {"question_id": "feeling", "value": "severe_symptoms", "quote": "सीने में दर्द हो रहा है"}),
        ("agent", "यह गंभीर हो सकता है। अभी 108 पर फ़ोन कीजिए, या पास में किसी से फ़ोन करने को कहिए। एक स्वास्थ्य कार्यकर्ता को सूचना दी जा रही है।"),
        ("tool", "end_call", {"outcome": "emergency"}),
    ],
}

DEFAULT_SCRIPT: list[tuple] = [
    ("agent", "नमस्ते, मैं ज़िला स्वास्थ्य कार्यालय की ओर से एक स्वचालित सहायक बोल रही हूँ।"),
    ("resident", "हाँ, बोलिए।"),
    ("tool", "set_respondent", {"type": "self"}),
    *_feeling("अभी आपकी तबीयत कैसी है?", "मैं ठीक हूँ।"),
    *_ask("क्या आज आपने पानी पिया है?", "हाँ।", "fluids_food", "enough"),
    *_ask("क्या आज घर पर कोई आपके साथ है?", "हाँ।", "company", "with_someone"),
    *_ask("क्या आपके पास पंखा या ठंडी जगह है?", "हाँ।", "shelter", "adequate"),
    *_ask("क्या आज आपको बाहर जाना है?", "नहीं।", "exposure", "staying_in"),
    *_ask("क्या आज आपको किसी मदद की ज़रूरत है?", "नहीं।", "help", "none"),
    *_goodbye(),
]


class ScriptedSession:
    """Looks like a Live session to run_call. Each receive() plays one step as one turn."""

    def __init__(self, script: list[tuple], step_seconds: float) -> None:
        self._script = list(script)
        self._step_seconds = step_seconds
        self._position = 0

    async def send_realtime_input(self, **kwargs) -> None:
        pass  # the microphone is ignored: the script does not listen

    async def send_client_content(self, **kwargs) -> None:
        pass

    async def send_tool_response(self, **kwargs) -> None:
        pass

    async def receive(self):
        if self._position >= len(self._script):
            await asyncio.Event().wait()  # the script is over; the call ends itself
        step = self._script[self._position]
        self._position += 1
        await asyncio.sleep(self._step_seconds)
        kind = step[0]
        if kind == "agent":
            yield _message(data=SILENCE)
            yield _message(output_transcription=SimpleNamespace(text=step[1]))
        elif kind == "resident":
            yield _message(input_transcription=SimpleNamespace(text=step[1]))
        else:
            _, name, args = step
            if name == "end_call":
                yield _message(data=SILENCE)  # a goodbye counts only in a turn that carried speech
            call = SimpleNamespace(id=f"fake-{self._position}", name=name, args=dict(args))
            yield _message(tool_call=SimpleNamespace(function_calls=[call]))
        yield _message(turn_complete=True)


def script_for(instruction: str) -> list[tuple]:
    """The card whose character the agent was told it is calling, or the default script.
    The instruction is all a session opener is given, so the name in it is what we match."""
    for card_id, card in CARDS.items():
        if f"YOU ARE CALLING: {card['name']}," in instruction:
            return SCRIPTS[card_id]
    return DEFAULT_SCRIPT


@asynccontextmanager
async def open_fake_session(settings, instruction: str, language: str | None = None):
    yield ScriptedSession(script_for(instruction), settings.fake_live_step_seconds)
