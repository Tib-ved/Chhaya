"""Claude standing in for the text model in the evaluation, with fake replies: no model is called."""
from types import SimpleNamespace

from app import ai_resident
from app.ai_resident import HANG_UP, OPENING_LINE, is_policy_refusal
from app.call.guidance import TOOL_DECLARATIONS
from evals.voice import claude_models


def text(value):
    return SimpleNamespace(type="text", text=value)


def tool_use(id, name, **args):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=args)


def message(*blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


class FakeClient:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.messages = self

    def create(self, **request):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def fake(monkeypatch, *replies):
    client = FakeClient(*replies)
    monkeypatch.setattr(claude_models, "_lazy_client", lambda: (lambda: client))
    return client


def test_the_tools_are_the_live_calls_tools_as_anthropic_schemas():
    assert [t["name"] for t in claude_models.TOOLS] == [d["name"] for d in TOOL_DECLARATIONS]
    assert claude_models.TOOLS[0]["input_schema"] == TOOL_DECLARATIONS[0]["parameters"]
    assert all(set(t) == {"name", "description", "input_schema"} for t in claude_models.TOOLS)


def test_an_agent_reply_is_read_as_text_tool_calls_and_its_own_blocks():
    reply = claude_models.read_agent_reply(message(text("Namaste "), tool_use("tu_1", "set_respondent", type="self"),
                                                   text("ji.")))
    assert reply["text"] == "Namaste ji."
    assert reply["tool_calls"] == [{"name": "set_respondent", "args": {"type": "self"}}]
    assert reply["raw"][1] == {"type": "tool_use", "id": "tu_1", "name": "set_respondent", "input": {"type": "self"}}


def test_the_agents_conversation_maps_to_alternating_turns_with_results_matched_by_id():
    first = claude_models.read_agent_reply(message(
        text("Aap kaisi hain?"), tool_use("tu_a", "set_respondent", type="self"),
        tool_use("tu_b", "record_answer", question_id="feeling", value="ok")))
    conversation = [
        {"role": "resident", "text": "Haan ji."},
        {"role": "agent", "text": first["text"], "tool_calls": first["tool_calls"], "raw": first["raw"]},
        {"role": "tool", "name": "set_respondent", "args": {"type": "self"}, "result": {"ok": True}},
        {"role": "tool", "name": "record_answer", "args": {}, "result": {"ok": True, "now": "wait"}},
        {"role": "resident", "text": "Theek hoon."},
    ]
    messages = claude_models.agent_messages(conversation)

    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    results = [b for b in messages[2]["content"] if b["type"] == "tool_result"]
    assert [b["tool_use_id"] for b in results] == ["tu_a", "tu_b"]
    assert '"now": "wait"' in results[1]["content"]
    assert messages[2]["content"][-1] == {"type": "text", "text": "Theek hoon."}  # results come first


def test_a_first_turn_opens_with_the_caller_and_an_agent_kept_without_raw_gets_ids():
    conversation = [{"role": "agent", "text": "", "tool_calls": [{"name": "end_call", "args": {"outcome": "completed"}}]},
                    {"role": "tool", "name": "end_call", "args": {}, "result": {"ok": True}}]
    messages = claude_models.agent_messages(conversation)
    assert messages[0] == {"role": "user", "content": [{"type": "text", "text": OPENING_LINE}]}
    use = messages[1]["content"][0]
    assert messages[2]["content"][0]["tool_use_id"] == use["id"]
    assert claude_models.agent_messages([])[-1]["role"] == "user"


def test_the_agent_model_sends_the_instruction_and_tools_and_answers_as_ai_resident_expects(monkeypatch):
    client = fake(monkeypatch, message(text("Namaste."), tool_use("tu_1", "set_respondent", type="family")))
    agent = claude_models.make_claude_agent_model("claude-test-agent")
    reply = agent("the call instruction", [])

    request = client.requests[0]
    assert request["model"] == "claude-test-agent"
    assert request["system"] == "the call instruction"
    assert request["tools"] == claude_models.TOOLS
    assert "temperature" not in request
    assert reply["text"] == "Namaste."
    assert reply["tool_calls"] == [{"name": "set_respondent", "args": {"type": "family"}}]
    assert reply["raw"] is not None


def test_the_resident_plays_the_assistant_and_keeps_the_hang_up_token(monkeypatch):
    client = fake(monkeypatch, message(text(f"Achha, namaste. {HANG_UP}")))
    resident = claude_models.make_claude_resident_model("claude-test-resident")
    line = resident("the persona", [{"role": "agent", "text": "Namaste?"}, {"role": "resident", "text": "Haan."},
                                    {"role": "agent", "text": "Paani piya?"}])

    request = client.requests[0]
    assert request["model"] == "claude-test-resident"
    assert request["system"] == "the persona"
    assert [m["role"] for m in request["messages"]] == ["user", "assistant", "user"]
    assert line == f"Achha, namaste. {HANG_UP}"


def test_a_rate_limit_is_waited_out_and_a_refusal_stops_the_run(monkeypatch):
    waits = []
    monkeypatch.setattr(ai_resident, "sleep", waits.append)
    fake(monkeypatch, Exception("Error code: 429 - rate_limit_error"), message(text("Haan ji.")))
    assert claude_models.make_claude_resident_model()("p", []) == "Haan ji."
    assert waits == [20]

    fake(monkeypatch, message(stop_reason="refusal"))
    try:
        claude_models.make_claude_agent_model()("i", [])
    except Exception as error:
        assert is_policy_refusal(error)
    else:
        raise AssertionError("a refusal must raise")


def test_other_errors_are_let_through_at_once(monkeypatch):
    waits = []
    monkeypatch.setattr(ai_resident, "sleep", waits.append)
    fake(monkeypatch, ValueError("400 bad request"))
    try:
        claude_models.make_claude_agent_model()("i", [])
    except ValueError:
        pass
    assert waits == []


def test_requests_on_claude_haiku_carry_no_thinking_setting(monkeypatch):
    client = fake(monkeypatch, message(text("Namaste.")), message(text("Haan ji.")))
    claude_models.make_claude_agent_model("claude-haiku-5-5")("i", [])
    claude_models.make_claude_resident_model("claude-haiku-5-5")("p", [{"role": "agent", "text": "Namaste?"}])
    assert [r["model"] for r in client.requests] == ["claude-haiku-5-5", "claude-haiku-5-5"]
    assert all("thinking" not in r for r in client.requests)
