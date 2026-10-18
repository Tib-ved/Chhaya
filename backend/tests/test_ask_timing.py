from types import SimpleNamespace

import pytest

from app.ask import timing
from app.ask.agent import build_agents


@pytest.fixture
def clock(monkeypatch, tmp_path):
    path = tmp_path / "timing.txt"
    monkeypatch.setenv("ASK_TIMING_FILE", str(path))
    now = [100.0]
    monkeypatch.setattr(timing, "clock", lambda: now[0])
    timing._starts.clear()
    return SimpleNamespace(now=now, path=path)


def test_model_and_tool_steps_are_written_to_the_file(clock):
    ctx = SimpleNamespace(invocation_id="inv-1", agent_name="chhaya")
    assert timing.before_model(ctx, None) is None
    clock.now[0] += 1.5
    assert timing.after_model(ctx, None) is None
    tool = SimpleNamespace(name="get_overview")
    tool_ctx = SimpleNamespace(invocation_id="inv-1", function_call_id="fc-1")
    assert timing.before_tool(tool, {}, tool_ctx) is None
    clock.now[0] += 0.25
    assert timing.after_tool(tool, {}, tool_ctx, {}) is None
    assert clock.path.read_text().splitlines() == [
        "ask timing step=model name=chhaya seconds=1.500 invocation=inv-1",
        "ask timing step=tool name=get_overview seconds=0.250 invocation=inv-1",
    ]


def test_a_step_without_a_start_writes_nothing(clock):
    timing.after_model(SimpleNamespace(invocation_id="inv-1", agent_name="chhaya"), None)
    assert not clock.path.exists()


def test_the_root_has_the_gate_and_the_timer_before_the_model():
    async def gate(ctx, req):
        return None

    root = build_agents("gemini-test", gate=gate)
    assert root.before_model_callback == [gate, timing.before_model]
    assert len(root.canonical_before_model_callbacks) == 2
    assert [t.agent.before_model_callback for t in root.tools if hasattr(t, "agent")] == [timing.before_model] * 2


def test_the_gate_times_its_classifier(clock):
    import asyncio
    from app.ask.guard import make_topic_gate
    from google.genai import types

    async def classify(text):
        clock.now[0] += 0.4
        return "on_topic"

    request = SimpleNamespace(contents=[types.Content(role="user", parts=[types.Part(text="hi")])])
    ctx = SimpleNamespace(invocation_id="inv-9", agent_name="chhaya")
    assert asyncio.run(make_topic_gate(classify)(ctx, request)) is None
    assert clock.path.read_text().splitlines() == ["ask timing step=gate name=topic_gate seconds=0.400 invocation=inv-9"]


def test_only_the_final_streamed_chunk_ends_a_model_call(clock):
    ctx = SimpleNamespace(invocation_id="inv-2", agent_name="chhaya")
    timing.before_model(ctx, None)
    clock.now[0] += 0.5
    timing.after_model(ctx, SimpleNamespace(partial=True))
    assert not clock.path.exists()
    clock.now[0] += 1.0
    timing.after_model(ctx, SimpleNamespace(partial=False))
    assert clock.path.read_text().splitlines() == ["ask timing step=model name=chhaya seconds=1.500 invocation=inv-2"]


def test_a_start_with_no_end_is_forgotten_after_five_minutes(clock):
    timing.before_model(SimpleNamespace(invocation_id="old", agent_name="chhaya"), None)
    clock.now[0] += timing.STALE_SECONDS + 1
    timing.before_model(SimpleNamespace(invocation_id="new", agent_name="chhaya"), None)
    assert list(timing._starts) == [("new", "chhaya")]
