"""The script that records the library of AI-resident conversations, run with fake models."""
import json

from app.config import Settings
from app.triage import REQUIRED_QUESTIONS
from scripts import record_ai_calls as recorder
from scripts.record_ai_calls import NAME_TOKEN, NAMES, load_personas, main, mismatches, plan, tokenise

NAME = NAMES["f"]
FIRST = NAME.split(" ")[0]
HEALTHY = {"feeling": "ok", "fluids_food": "enough", "company": "with_someone",
           "shelter": "adequate", "exposure": "staying_in", "help": "none"}


def settings():
    return Settings(_env_file=None, internal_secret="s3cret", text_model="fake-model")


def tool(name, **args):
    return {"name": name, "args": args}


class FakeAgent:
    """Greets the resident by name, asks how they feel and its two follow-ups, then records each
    answer as the given value while asking the next question, and ends the call."""

    def __init__(self, values):
        self.values = values

    def __call__(self, instruction, conversation):
        if conversation and conversation[-1]["role"] == "tool":
            return {"text": "", "tool_calls": []}
        heard = [entry["text"] for entry in conversation if entry["role"] == "resident"]
        if not heard:
            return {"text": f"Namaste, kya main {NAME} ji se baat kar rahi hoon?", "tool_calls": []}
        if len(heard) == 1:
            return {"text": f"{FIRST} ji, aap kaisi hain?", "tool_calls": [tool("set_respondent", type="self")]}
        if len(heard) in (2, 3):  # the follow-ups of "feeling" that everyone is asked
            return {"text": "Chakkar ya sirdard?" if len(heard) == 2 else "Ulti ya kamzori?", "tool_calls": []}
        asked = len(heard) - 4
        calls = [tool("record_answer", question_id=REQUIRED_QUESTIONS[asked],
                      value=self.values[REQUIRED_QUESTIONS[asked]], quote=heard[-1])]
        if asked + 1 < len(REQUIRED_QUESTIONS):
            return {"text": f"Agla sawaal, {FIRST} ji.", "tool_calls": calls}
        return {"text": "Dhanyavaad, namaste.", "tool_calls": calls + [tool("end_call", outcome="completed")]}


class FakeResident:
    def __init__(self):
        self.instructions = []

    def __call__(self, instruction, conversation):
        self.instructions.append(instruction)
        replies = sum(1 for entry in conversation if entry["role"] == "resident")
        if replies == 0:
            return f"Haan, main {FIRST} hoon."
        return f"{FIRST} theek hai ji, sab theek."


def run(tmp_path, *arguments, values=HEALTHY, out_name="ai_calls.json"):
    out = tmp_path / out_name
    lines: list[str] = []
    code = main([*arguments, "--out", str(out)], agent=FakeAgent(values), resident=FakeResident(),
                settings=settings(), log=lines.append)
    return code, json.loads(out.read_text(encoding="utf-8")), "\n".join(lines)


def test_the_library_has_the_documented_shape(tmp_path):
    code, library, log = run(tmp_path, "--only", "healthy_with_family")

    assert code == 0
    assert set(library) == {"recorded_at", "commit", "model", "conversations"}
    assert library["model"] == "fake-model"
    assert library["commit"]
    recorded = [c for c in library["conversations"] if c["persona_id"]]
    assert [c["id"] for c in recorded] == ["healthy_with_family_hi", "healthy_with_family_mwr", "healthy_with_family_en"]
    for conversation in recorded:
        assert set(conversation) == {"id", "persona_id", "language", "profile", "status", "respondent",
                                     "emergency", "answers", "quotes", "tier", "transcript",
                                     "tools", "recorded_at", "commit", "model"}
        assert set(conversation["profile"]) == {"lives_alone", "outdoor_worker", "conditions", "age_band"}
        assert conversation["status"] == "completed"
        assert conversation["respondent"] == "self"
        assert conversation["emergency"] is None
        assert conversation["answers"] == HEALTHY
        assert conversation["tier"] == "green"
        assert all(set(line) == {"role", "text"} for line in conversation["transcript"])
        assert conversation["transcript"][0]["role"] == "agent"
    assert "healthy_with_family_hi" in log


class EmergencyAgent(FakeAgent):
    """Flags an emergency in the same turn as its second line."""

    def __call__(self, instruction, conversation):
        reply = super().__call__(instruction, conversation)
        heard = [e for e in conversation if e["role"] == "resident"]
        if len(heard) == 1 and not (conversation and conversation[-1]["role"] == "tool"):
            reply = {**reply, "tool_calls": [*reply["tool_calls"], tool("flag_emergency", reason="chest pain")]}
        return reply


def test_a_conversation_keeps_where_each_tool_was_called_and_its_own_date(tmp_path):
    persona = load_personas()[0]
    now = recorder.recording_time(recorder.ZoneInfo(settings().local_tz))
    entry = recorder.record_one(persona, "hi", EmergencyAgent(HEALTHY), FakeResident(), now,
                                recorder.ZoneInfo(settings().local_tz), settings())

    # The greeting, the reply, then the line said together with the tool calls: line 3.
    assert {"name": "flag_emergency", "line": 3} in entry["tools"]
    assert {"name": "set_respondent", "line": 3} in entry["tools"]
    assert entry["recorded_at"].endswith("+00:00")
    assert entry["commit"]
    assert entry["model"] == "fake-model"


def test_batches_write_the_file_after_each_one(tmp_path, monkeypatch):
    writes = []
    real = recorder.write_atomic
    monkeypatch.setattr(recorder, "write_atomic", lambda path, data: (writes.append(len(data["conversations"])), real(path, data)))
    ids = [p["id"] for p in load_personas()[:3]]
    out = tmp_path / "ai_calls.json"
    code = main(["--batch-size", "2", "--pause-seconds", "0", "--only", ",".join(ids), "--limit", "3", "--out", str(out)],
                agent=FakeAgent(HEALTHY), resident=FakeResident(), settings=settings(), log=lambda line: None)

    library = json.loads(out.read_text(encoding="utf-8"))
    wanted = {cid for p, lang in plan(load_personas(), only=",".join(ids), limit=3) for cid in [recorder.conversation_id(p["id"], lang)]}
    recorded = {c["id"] for c in library["conversations"] if c["persona_id"]}
    assert code == 0
    assert writes == [4, 5]  # two conversations and the two model-free entries, then three and the two
    assert recorded == wanted
    assert len(recorded) == 3
    assert all(c["recorded_at"] for c in library["conversations"] if c["persona_id"])


class Refusing(FakeAgent):
    """Refuses, as the model service does on policy grounds, at the greeting of conversation `at`."""

    def __init__(self, values, at):
        super().__init__(values)
        self.at = at
        self.greetings = 0

    def __call__(self, instruction, conversation):
        if not conversation:
            self.greetings += 1
            if self.greetings == self.at:
                raise Exception("403 PERMISSION_DENIED. Request rejected in accordance with our terms of use")
        return super().__call__(instruction, conversation)


def test_a_policy_refusal_stops_the_run_and_keeps_what_was_recorded(tmp_path):
    agent = Refusing(HEALTHY, at=3)
    out = tmp_path / "ai_calls.json"
    lines: list[str] = []
    code = main(["--only", "healthy_with_family,alone_dizzy", "--limit", "5", "--pause-seconds", "0", "--out", str(out)],
                agent=agent, resident=FakeResident(), settings=settings(), log=lines.append)

    library = json.loads(out.read_text(encoding="utf-8"))
    recorded = [c for c in library["conversations"] if c["persona_id"]]
    assert code == 3
    assert [c["id"] for c in recorded] == ["healthy_with_family_hi", "healthy_with_family_mwr"]
    assert all(c["recorded_at"] for c in recorded)
    assert agent.greetings == 3  # five were planned; the fourth was never started
    assert "STOPPED: Vertex refused a request on policy grounds after 2 conversations" in "\n".join(lines)


def test_a_policy_refusal_in_the_first_batch_writes_once_and_does_not_wait(tmp_path, monkeypatch):
    waits = []
    monkeypatch.setattr(recorder.clock, "sleep", waits.append)
    writes = []
    real = recorder.write_atomic
    monkeypatch.setattr(recorder, "write_atomic", lambda path, data: (writes.append(len(data["conversations"])), real(path, data)))
    agent = Refusing(HEALTHY, at=2)
    out = tmp_path / "ai_calls.json"
    code = main(["--only", "healthy_with_family,alone_dizzy", "--limit", "5", "--batch-size", "2",
                 "--pause-seconds", "60", "--out", str(out)],
                agent=agent, resident=FakeResident(), settings=settings(), log=lambda line: None)

    library = json.loads(out.read_text(encoding="utf-8"))
    assert code == 3
    assert len(writes) == 1
    assert waits == []
    assert agent.greetings == 2
    assert [c["id"] for c in library["conversations"] if c["persona_id"]] == ["healthy_with_family_hi"]


def test_is_policy_refusal_reads_the_message():
    assert recorder.is_policy_refusal(Exception("Request rejected in accordance with our terms"))
    assert recorder.is_policy_refusal(Exception("403 PERMISSION_DENIED"))
    assert not recorder.is_policy_refusal(RuntimeError("model unavailable"))


def test_the_resident_name_is_the_token_everywhere(tmp_path):
    _, library, _ = run(tmp_path, "--only", "healthy_with_family", "--limit", "1")

    text = json.dumps(library, ensure_ascii=False)
    for part in NAME.split(" "):
        assert part not in text
    conversation = library["conversations"][0]
    agent_lines = [line["text"] for line in conversation["transcript"] if line["role"] == "agent"]
    resident_lines = [line["text"] for line in conversation["transcript"] if line["role"] == "resident"]
    assert agent_lines[0] == f"Namaste, kya main {NAME_TOKEN} ji se baat kar rahi hoon?"
    assert resident_lines[0] == f"Haan, main {NAME_TOKEN} hoon."
    assert conversation["quotes"]["feeling"] == f"{NAME_TOKEN} theek hai ji, sab theek."


def test_tokenise_covers_the_name_in_devanagari_and_in_part():
    from seed.names_hi import name_hi

    assert tokenise(f"{NAME} ji, {FIRST} ji", NAME) == f"{NAME_TOKEN} ji, {NAME_TOKEN} ji"
    assert tokenise(f"नमस्ते {name_hi(NAME)} जी", NAME) == f"नमस्ते {NAME_TOKEN} जी"
    assert tokenise("Kamlawati ji", NAME) == "Kamlawati ji"  # only whole words
    assert tokenise(None, NAME) is None


def test_a_tier_other_than_expected_is_listed_and_kept(tmp_path):
    code, library, log = run(tmp_path, "--only", "healthy_with_family", "--limit", "1",
                             values={**HEALTHY, "feeling": "mild_symptoms"})

    assert code == 0
    conversation = library["conversations"][0]
    assert conversation["tier"] == "amber"
    assert "1 conversation(s) with a tier other than expected" in log
    assert "healthy_with_family_hi: amber (expected green)" in log
    assert mismatches(library["conversations"], load_personas()) == [conversation]


def test_the_model_free_entries_are_present(tmp_path):
    _, library, _ = run(tmp_path, "--only", "healthy_with_family", "--limit", "1")

    entries = {c["id"]: c for c in library["conversations"] if c["persona_id"] is None}
    assert set(entries) == {"nobody_answered", "busy"}
    assert entries["nobody_answered"]["status"] == "no_answer"
    assert entries["nobody_answered"]["failures"] == [["no_answer", 0], ["no_answer", 20], ["busy", 80]]
    assert entries["busy"]["status"] == "busy"
    assert all(e["transcript"] == [] and e["answers"] == {} for e in entries.values())


def test_merge_keeps_what_was_not_recorded_again_and_a_failure_does_not_stop_the_rest(tmp_path, monkeypatch):
    _, first, _ = run(tmp_path, "--only", "healthy_with_family")
    real = recorder.record_one

    def failing(persona, language, *rest):
        if language == "mwr":
            raise RuntimeError("model unavailable")
        return real(persona, language, *rest)

    monkeypatch.setattr(recorder, "record_one", failing)
    code, merged, log = run(tmp_path, "--only", "healthy_with_family", "--merge")

    assert code == 1
    assert "healthy_with_family_mwr: RuntimeError: model unavailable" in log
    ids = [c["id"] for c in merged["conversations"]]
    assert sorted(ids) == sorted(c["id"] for c in first["conversations"])
    assert ids.count("nobody_answered") == 1


def test_without_merge_a_failed_conversation_is_left_out(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder, "record_one", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    code, library, log = run(tmp_path, "--only", "healthy_with_family", "--limit", "1")

    assert code == 1
    assert [c["id"] for c in library["conversations"]] == ["nobody_answered", "busy"]


def test_an_unknown_persona_writes_nothing(tmp_path):
    out = tmp_path / "ai_calls.json"
    assert main(["--only", "nobody", "--out", str(out)], agent=FakeAgent(HEALTHY), resident=FakeResident(),
                settings=settings(), log=lambda line: None) == 2
    assert not out.exists()


def test_the_personas_cover_the_situations_and_reach_forty_to_sixty_conversations():
    personas = load_personas()
    chosen = plan(personas)
    assert 40 <= len(chosen) <= 60
    assert len({p["id"] for p in personas}) == len(personas)
    assert {language for _, language in chosen} == {"hi", "mwr", "en"}
    for persona in personas:
        assert set(persona["profile"]) == {"lives_alone", "outdoor_worker", "conditions", "age_band"}
        assert persona["profile"]["age_band"] in recorder.AGES
        assert persona["respondent"] in ("self", "family")
        assert persona["expected_tier"] in {"green", "amber", "red"}
        assert persona["facts"]
        assert set(persona["language"]) <= {"hi", "mwr", "en"}
    tiers = {p["expected_tier"] for p in personas}
    assert tiers == {"green", "amber", "red"}
    assert any(p["respondent"] == "family" for p in personas)
    assert any(p.get("hangs_up_after") for p in personas)
    assert any(p["profile"]["outdoor_worker"] for p in personas)


def test_plan_limits_and_filters():
    personas = load_personas()
    assert len(plan(personas, limit=5)) == 5
    assert {p["id"] for p, _ in plan(personas, only="alone_dizzy")} == {"alone_dizzy"}
    assert plan(personas, only="missing") == []


class RateLimited(FakeAgent):
    """Answers 429 at the greeting of the conversations numbered in `at`."""

    def __init__(self, values, at):
        super().__init__(values)
        self.at = set(at)
        self.greetings = 0

    def __call__(self, instruction, conversation):
        if not conversation:
            self.greetings += 1
            if self.greetings in self.at:
                raise Exception("429 RESOURCE_EXHAUSTED. Quota exceeded")
        return super().__call__(instruction, conversation)


def test_a_model_error_is_never_recorded_as_a_call(tmp_path):
    agent = RateLimited(HEALTHY, at=[2])
    out = tmp_path / "ai_calls.json"
    lines: list[str] = []
    code = main(["--only", "healthy_with_family", "--out", str(out)],
                agent=agent, resident=FakeResident(), settings=settings(), log=lines.append)

    library = json.loads(out.read_text(encoding="utf-8"))
    ids = [c["id"] for c in library["conversations"] if c["persona_id"]]
    assert code == 1
    assert "healthy_with_family_mwr" not in ids
    assert ids == ["healthy_with_family_hi", "healthy_with_family_en"]
    assert "healthy_with_family_mwr: " in "\n".join(lines)
    assert "429 RESOURCE_EXHAUSTED" in "\n".join(lines)


def test_three_rate_limited_conversations_in_a_row_stop_the_run(tmp_path):
    agent = RateLimited(HEALTHY, at=[2, 3, 4])
    out = tmp_path / "ai_calls.json"
    lines: list[str] = []
    code = main(["--only", "healthy_with_family,alone_dizzy", "--limit", "6", "--out", str(out)],
                agent=agent, resident=FakeResident(), settings=settings(), log=lines.append)

    library = json.loads(out.read_text(encoding="utf-8"))
    assert code == 5
    assert agent.greetings == 4  # six were planned; the fifth was never started
    assert [c["id"] for c in library["conversations"] if c["persona_id"]] == ["healthy_with_family_hi"]
    assert "STOPPED: Vertex rate limit after 1 conversations" in "\n".join(lines)


def test_rate_limits_that_do_not_run_three_in_a_row_do_not_stop_the_run(tmp_path):
    agent = RateLimited(HEALTHY, at=[2, 3, 5])
    out = tmp_path / "ai_calls.json"
    code = main(["--only", "healthy_with_family,alone_dizzy", "--limit", "6", "--out", str(out)],
                agent=agent, resident=FakeResident(), settings=settings(), log=lambda line: None)

    assert code == 1
    assert agent.greetings == 6


def test_claude_stands_in_on_both_sides_and_several_run_at_once_in_plan_order(tmp_path, monkeypatch):
    from evals.voice import claude_models

    made = {}
    monkeypatch.setattr(claude_models, "make_claude_agent_model",
                        lambda model: made.setdefault("agent", model) and FakeAgent(HEALTHY))
    monkeypatch.setattr(claude_models, "make_claude_resident_model",
                        lambda model: made.setdefault("resident", model) and FakeResident())
    out = tmp_path / "ai_calls.json"
    no_vertex = Settings(_env_file=None, internal_secret="s3cret", text_model="", gcp_project="")
    code = main(["--only", "healthy_with_family,alone_dizzy", "--claude", "--workers", "3",
                 "--claude-resident-model", "claude-test-resident", "--out", str(out)],
                settings=no_vertex, log=lambda line: None)

    library = json.loads(out.read_text(encoding="utf-8"))
    recorded = [c for c in library["conversations"] if c["persona_id"]]
    assert code == 0
    assert made == {"agent": "claude-sonnet-5-5", "resident": "claude-test-resident"}
    assert library["model"] == "claude-sonnet-5-5"
    assert library["stand_in"] is True
    assert library["resident_model"] == "claude-test-resident"
    expected = [f"{p}_{lang}" for p in ("healthy_with_family", "alone_dizzy")
                for lang in next(x for x in load_personas() if x["id"] == p)["language"]]
    assert [c["id"] for c in recorded] == expected
    assert all(c["model"] == "claude-sonnet-5-5" and c["resident_model"] == "claude-test-resident" for c in recorded)


def test_a_policy_refusal_with_several_workers_starts_nothing_new(tmp_path, monkeypatch):
    import threading
    import time as wall

    real = recorder.record_one
    started = []
    refused = threading.Event()

    def recording(persona, language, *rest):
        started.append(f"{persona['id']}_{language}")
        if language == "mwr":
            refused.set()
            raise Exception("403 PERMISSION_DENIED. Request rejected in accordance with our terms of use")
        refused.wait(5)
        wall.sleep(0.2)  # the refusal is read before these finish
        return real(persona, language, *rest)

    monkeypatch.setattr(recorder, "record_one", recording)
    out = tmp_path / "ai_calls.json"
    code = main(["--only", "healthy_with_family,alone_dizzy", "--limit", "6", "--workers", "3", "--out", str(out)],
                agent=FakeAgent(HEALTHY), resident=FakeResident(), settings=settings(), log=lambda line: None)

    library = json.loads(out.read_text(encoding="utf-8"))
    assert code == 3
    assert sorted(started) == sorted(["healthy_with_family_hi", "healthy_with_family_mwr", "healthy_with_family_en"])
    assert [c["id"] for c in library["conversations"] if c["persona_id"]] == ["healthy_with_family_hi",
                                                                               "healthy_with_family_en"]
