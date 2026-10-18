"""A text conversation between Chhaya's agent and a resident played by a model, both faked here."""
import copy

import pytest

from app.ai_resident import HANG_UP, MAX_REPLIES_PER_TURN, persona_instruction, run_conversation
from app.config import Settings
from app.dispatch import FINAL_STATUSES
from tests.helpers import queue_one_call

PERSONA = {
    "id": "healthy",
    "language": "hi",
    "situation": "An older woman at home on a hot day, feeling fine.",
    "facts": {"feeling": "fine, no dizziness, no vomiting", "water": "drinking plenty of water"},
}


def tool(name, **args):
    return {"name": name, "args": args}


class FakeAgent:
    """Scripted agent. `turns` is a list of turns, each a list of replies: the first is the reply
    at the start of the turn, the rest the replies after each set of tool results. Once a turn's
    replies run out, it says nothing more in that turn."""

    def __init__(self, turns, default=None):
        self.turns = list(turns)
        self.current: list[dict] = []
        self.default = default
        self.seen: list[tuple[str, list[dict]]] = []

    def __call__(self, instruction, conversation):
        self.seen.append((instruction, copy.deepcopy(conversation)))
        if conversation and conversation[-1]["role"] == "tool":
            reply = self.current.pop(0) if self.current else {"text": ""}
        elif self.turns:
            self.current = list(self.turns.pop(0))
            reply = self.current.pop(0)
        else:
            reply = self.default or {"text": ""}
        return {"text": reply.get("text", ""), "tool_calls": reply.get("tool_calls", [])}


class FakeResident:
    def __init__(self, lines, default="ji?"):
        self.lines = list(lines)
        self.default = default
        self.seen: list[tuple[str, list[dict]]] = []

    def __call__(self, instruction, conversation):
        self.seen.append((instruction, copy.deepcopy(conversation)))
        return self.lines.pop(0) if self.lines else self.default


def settings():
    return Settings(_env_file=None, internal_secret="s3cret")


def run(store, call_id, now, tz, agent, resident, persona=PERSONA, **kwargs):
    return run_conversation(store, call_id, persona, agent, resident, lambda: now, settings(), tz, **kwargs)


GREETING = {"text": "Namaste ji, main Chhaya se bol rahi hoon. Kya main Sunita ji se baat kar rahi hoon?"}


def test_a_healthy_conversation_is_completed_and_green(store, tz, now):
    round_id, call_id, _ = queue_one_call(store, tz, now, name="Sunita Sharma")
    agent = FakeAgent([
        [GREETING],
        [{"text": "Aap kaisa mehsoos kar rahi hain?", "tool_calls": [tool("set_respondent", type="self")]}],
        [{"text": "Chakkar aata hai?"}],
        [{"text": "Ulti jaisa lagta hai?"}],
        [{"text": "Paani aur khana theek se le rahi hain?",
          "tool_calls": [tool("record_answer", question_id="feeling", value="ok", quote="theek hoon")]}],
        [{"text": "Ghar mein koi saath hai?", "tool_calls": [tool("record_answer", question_id="fluids_food", value="enough")]}],
        [{"text": "Ghar mein thandak hai?", "tool_calls": [tool("record_answer", question_id="company", value="with_someone")]}],
        [{"text": "Dhoop mein bahar jaana padta hai?", "tool_calls": [tool("record_answer", question_id="shelter", value="adequate")]}],
        [{"text": "Kisi madad ki zaroorat hai?", "tool_calls": [tool("record_answer", question_id="exposure", value="staying_in")]}],
        [{"text": "Dhanyavaad, apna khayal rakhiye. Namaste.",
          "tool_calls": [tool("record_answer", question_id="help", value="none"), tool("end_call", outcome="completed")]}],
    ])
    resident = FakeResident([
        "Haan, main hi hoon.", "Theek hoon.", "Nahi.", "Nahi.", "Haan, paani pi rahi hoon.",
        "Bahu saath hai.", "Haan, pankha hai.", "Nahi, ghar par hi hoon.", "Nahi, kuch nahi chahiye.",
    ])

    result = run(store, call_id, now, tz, agent, resident)

    assert result["status"] == "completed"
    assert result["respondent"] == "self"
    assert result["tier"] == "green"
    assert result["emergency"] is None
    assert {q: a["value"] for q, a in result["answers"].items()} == {
        "feeling": "ok", "fluids_food": "enough", "company": "with_someone",
        "shelter": "adequate", "exposure": "staying_in", "help": "none",
    }
    call = store.get("calls", call_id)
    assert call["status"] == "completed"
    assert call["end_outcome"] == "completed"
    assert call["language_used"] == "hi"
    assert call["answers"]["feeling"]["quote"] == "theek hoon"
    assert store.get("cases", f"{round_id}_r1")["tier"] == "green"
    lines = [(entry["role"], entry["text"]) for entry in result["transcript"]]
    assert lines[0] == ("agent", GREETING["text"])
    assert lines[1] == ("resident", "Haan, main hi hoon.")
    assert lines[-1] == ("agent", "Dhanyavaad, apna khayal rakhiye. Namaste.")
    assert [role for role, _ in lines] == ["agent", "resident"] * 9 + ["agent"]
    assert call["transcript"] == result["transcript"]
    # The agent was given the real instruction of the live call.
    instruction = agent.seen[0][0]
    assert "Sunita Sharma" in instruction
    assert "Ward 1 cooling centre" in instruction
    # Tool results go back to the agent; one after it has spoken tells it to wait for the reply.
    tool_entries = [entry for entry in agent.seen[-1][1] if entry["role"] == "tool"]
    assert all(entry["result"].get("result") in ("recorded", "ending") for entry in tool_entries)
    assert "now" in tool_entries[0]["result"]
    # The resident never sees the tool calls.
    assert {entry["role"] for entry in resident.seen[-1][1]} == {"agent", "resident"}
    assert all(set(entry) == {"role", "text"} for entry in resident.seen[-1][1])


def test_severe_symptoms_and_a_flagged_emergency_make_the_case_red(store, tz, now):
    round_id, call_id, _ = queue_one_call(store, tz, now)
    agent = FakeAgent([
        [GREETING],
        [{"text": "Aap kaisa mehsoos kar rahe hain?", "tool_calls": [tool("set_respondent", type="self")]}],
        [
            {"tool_calls": [
                tool("record_answer", question_id="feeling", value="severe_symptoms", quote="seene mein dard"),
                tool("flag_emergency", reason="chest pain and breathlessness"),
            ]},
            {"text": "Abhi turant 108 par call kijiye. Health worker ko bataya ja raha hai.",
             "tool_calls": [tool("end_call", outcome="emergency")]},
        ],
    ])
    resident = FakeResident(["Haan.", "Seene mein bahut dard hai, saans nahi aa rahi."])

    result = run(store, call_id, now, tz, agent, resident)

    assert result["status"] == "completed"
    assert result["tier"] == "red"
    assert result["emergency"]["flagged"] is True
    assert "chest pain" in result["emergency"]["reason"]
    assert result["answers"]["feeling"]["value"] == "severe_symptoms"
    assert store.get("cases", f"{round_id}_r1")["tier"] == "red"
    assert result["transcript"][-1]["text"].startswith("Abhi turant 108")
    # The 108 line was asked for by the tool result.
    flag = next(e for e in agent.seen[-1][1] if e["role"] == "tool" and e["name"] == "flag_emergency")
    assert "108" in flag["result"]["say"]


def test_a_flag_and_end_call_with_nothing_said_must_speak_the_108_line_first(store, tz, now):
    # The pattern of the recordings that missed the line: the agent spoke in an earlier turn,
    # then replied with flag_emergency and end_call alone.
    _, call_id, _ = queue_one_call(store, tz, now)
    agent = FakeAgent([
        [GREETING],
        [{"text": "Aap kaisa mehsoos kar rahe hain?", "tool_calls": [tool("set_respondent", type="self")]}],
        [
            {"tool_calls": [tool("flag_emergency", reason="chest pain"), tool("end_call", outcome="emergency")]},
            {"text": "Abhi turant 108 par call kijiye.", "tool_calls": [tool("end_call", outcome="emergency")]},
        ],
    ])
    resident = FakeResident(["Haan.", "Seene mein bahut dard hai."])

    result = run(store, call_id, now, tz, agent, resident)

    ends = [e for e in result["timeline"] if e["kind"] == "tool" and e["name"] == "end_call"]
    assert ends[0].get("refused") and not ends[1].get("refused")
    refusal = next(e for e in agent.seen[-1][1] if e["role"] == "tool" and e["name"] == "end_call")
    assert refusal["result"]["error"] == "say the emergency line first"
    assert result["transcript"][-1]["text"] == "Abhi turant 108 par call kijiye."
    assert result["emergency"]["flagged"] is True


def test_a_resident_who_hangs_up_ends_the_call(store, tz, now):
    round_id, call_id, _ = queue_one_call(store, tz, now)
    agent = FakeAgent([
        [GREETING],
        [{"text": "Aap kaisa mehsoos kar rahi hain?", "tool_calls": [tool("set_respondent", type="self")]}],
        [{"text": "Should never be said."}],
    ])
    resident = FakeResident(["Haan, main hi hoon.", "Abhi baat nahi kar sakti. [HANG UP]"])

    result = run(store, call_id, now, tz, agent, resident, persona={**PERSONA, "hangs_up_after": 2})

    assert result["status"] == "dropped"  # final_status: the agent never ended it
    assert result["transcript"][-1]["role"] == "resident"
    assert result["transcript"][-1]["text"] == "Abhi baat nahi kar sakti."
    assert all("Should never" not in entry["text"] for entry in result["transcript"])
    assert len(agent.seen) == 3  # the third is the reply to the tool result, in the second turn


def test_a_tool_call_before_any_reply_is_refused(store, tz, now):
    round_id, call_id, _ = queue_one_call(store, tz, now)
    agent = FakeAgent([
        [{"text": GREETING["text"], "tool_calls": [tool("record_answer", question_id="feeling", value="ok")]}],
    ])
    resident = FakeResident([HANG_UP])

    result = run(store, call_id, now, tz, agent, resident)

    assert "feeling" not in (store.get("calls", call_id).get("answers") or {})
    refusal = next(e for e in agent.seen[-1][1] if e["role"] == "tool")
    assert refusal["result"]["error"] == "no answer heard"
    # Nobody said anything, and nothing was recorded: nobody was reached.
    assert result["status"] == "no_answer"
    assert [entry["role"] for entry in result["transcript"]] == ["agent"]


def test_each_tool_event_keeps_its_place_in_the_transcript(store, tz, now):
    _, call_id, _ = queue_one_call(store, tz, now)
    agent = FakeAgent([
        [{"text": GREETING["text"], "tool_calls": [tool("record_answer", question_id="feeling", value="ok")]}],
    ])

    result = run(store, call_id, now, tz, agent, FakeResident([HANG_UP]))

    # The clock is fixed, so the events share one time: the line is what tells them apart.
    events = [e for e in result["timeline"] if e["kind"] == "tool"]
    assert [(e["name"], e["line"]) for e in events] == [("record_answer", 1)]
    assert events[0]["refused"]


def test_max_turns_stops_a_conversation_that_never_ends(store, tz, now):
    round_id, call_id, _ = queue_one_call(store, tz, now)
    agent = FakeAgent(
        [[{"text": "Namaste?", "tool_calls": [tool("set_respondent", type="self")]}]],
        default={"text": "Kya aap sun sakti hain?"},
    )
    resident = FakeResident([], default="Kya?")

    result = run(store, call_id, now, tz, agent, resident, max_turns=3)

    assert result["status"] == "dropped"
    assert [entry["role"] for entry in result["transcript"]] == ["agent", "resident"] * 3
    assert len(agent.seen) == 4  # three turns, and the reply to the tool result in the first
    assert result["tier"] in (None, "amber")


def test_a_failing_model_still_records_the_outcome(store, tz, now):
    round_id, call_id, _ = queue_one_call(store, tz, now)
    agent = FakeAgent([[GREETING], [{"text": "Kaisi hain?", "tool_calls": [tool("set_respondent", type="self")]}]])

    def resident(instruction, conversation):
        if len(conversation) > 2:
            raise RuntimeError("service unavailable")
        return "Haan."

    result = run(store, call_id, now, tz, agent, resident)

    assert result["status"] == "dropped"
    assert store.get("calls", call_id)["status"] == "dropped"


def test_a_malformed_persona_raises_before_the_call_is_touched(store, tz, now):
    round_id, call_id, _ = queue_one_call(store, tz, now)
    before = store.get("calls", call_id)

    with pytest.raises(TypeError):
        run(store, call_id, now, tz, FakeAgent([[GREETING]]), FakeResident([]), persona={**PERSONA, "facts": 5})

    assert store.get("calls", call_id) == before
    assert before["status"] != "in_progress"


def test_a_failure_after_the_call_is_started_still_records_an_outcome(store, tz, now, monkeypatch):
    import app.ai_resident as ai_resident

    round_id, call_id, _ = queue_one_call(store, tz, now)

    def broken(resident):
        raise KeyError("name")

    monkeypatch.setattr(ai_resident, "opening_prompt", broken)

    result = run(store, call_id, now, tz, FakeAgent([[GREETING]]), FakeResident([]))

    assert result["status"] in FINAL_STATUSES
    assert store.get("calls", call_id)["status"] in FINAL_STATUSES


def test_a_turn_of_endless_tool_calls_is_stopped(store, tz, now, caplog):
    round_id, call_id, _ = queue_one_call(store, tz, now)
    looping = {"tool_calls": [tool("set_respondent", type="self")]}
    agent = FakeAgent([[looping] * 10])

    result = run(store, call_id, now, tz, agent, FakeResident([HANG_UP]))

    assert len(agent.seen) == MAX_REPLIES_PER_TURN
    assert "stopped after" in caplog.text
    assert result["status"] == "dropped"


def test_persona_instruction_plays_the_resident():
    text = persona_instruction({**PERSONA, "language": "mwr", "hangs_up_after": 3})
    assert "mwr" in text or "Marwari" in text
    assert "fine, no dizziness, no vomiting" in text
    assert "drinking plenty of water" in text
    assert "only when" in text
    assert HANG_UP in text
    assert "3" in text
    plain = persona_instruction({**PERSONA, "facts": ["lives with her son"]})
    assert "lives with her son" in plain
    assert "after 3" not in plain


class FlakyClient:
    """A model client whose generate_content raises the given errors one by one, then answers."""

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "answer"


def generate(client, monkeypatch):
    import app.ai_resident as ai_resident

    waits = []
    monkeypatch.setattr(ai_resident, "sleep", waits.append)
    return ai_resident._generate(client, Settings(_env_file=None, text_model="m", text_thinking=""), [], {}), waits


def test_a_rate_limit_waits_and_asks_again(monkeypatch):
    client = FlakyClient([Exception("429 RESOURCE_EXHAUSTED"), Exception("429 Too many")])

    result, waits = generate(client, monkeypatch)

    assert result == "answer"
    assert waits == [20, 40]


def test_a_rate_limit_that_lasts_waits_twenty_forty_eighty(monkeypatch):
    import app.ai_resident as ai_resident

    waits = []
    monkeypatch.setattr(ai_resident, "sleep", waits.append)
    client = FlakyClient([Exception("RESOURCE_EXHAUSTED")] * 4)

    with pytest.raises(Exception, match="RESOURCE_EXHAUSTED"):
        ai_resident._generate(client, Settings(_env_file=None, text_model="m", text_thinking=""), [], {})

    assert waits == [20, 40, 80]
    assert client.calls == 4


def test_a_policy_refusal_is_not_asked_again_or_waited_for(monkeypatch):
    import app.ai_resident as ai_resident

    waits = []
    monkeypatch.setattr(ai_resident, "sleep", waits.append)
    client = FlakyClient([Exception("403 PERMISSION_DENIED. Request rejected in accordance with our terms of use")])

    with pytest.raises(Exception, match="PERMISSION_DENIED"):
        ai_resident._generate(client, Settings(_env_file=None, text_model="m", text_thinking=""), [], {})

    assert client.calls == 1
    assert waits == []


def test_another_error_is_asked_once_more_at_once(monkeypatch):
    import app.ai_resident as ai_resident

    waits = []
    monkeypatch.setattr(ai_resident, "sleep", waits.append)
    settings = Settings(_env_file=None, text_model="m", text_thinking="")

    assert ai_resident._generate(FlakyClient([RuntimeError("503 unavailable")]), settings, [], {}) == "answer"
    twice = FlakyClient([RuntimeError("503 unavailable")] * 2)
    with pytest.raises(RuntimeError):
        ai_resident._generate(twice, settings, [], {})

    assert twice.calls == 2
    assert waits == []


def test_is_rate_limited_reads_the_message():
    from app.ai_resident import is_rate_limited

    assert is_rate_limited(Exception("429 RESOURCE_EXHAUSTED. Quota exceeded"))
    assert is_rate_limited(Exception("RESOURCE_EXHAUSTED"))
    assert is_rate_limited(Exception("got a 429"))
    assert not is_rate_limited(RuntimeError("503 unavailable"))
