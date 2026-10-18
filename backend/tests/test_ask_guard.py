import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from google.genai import types

from app.ask.guard import REFUSAL, Rejected, clean_text, make_topic_gate, parse_label
from app.ask.limits import PER_MINUTE, VISITOR_DAILY, Limits
from app.db import MemoryStore


def run(coroutine):
    return asyncio.run(coroutine)


def request(*contents):
    return SimpleNamespace(contents=list(contents))


def user(text):
    return types.Content(role="user", parts=[types.Part(text=text)])


def tool_result():
    return types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(name="get_case", response={}))])


def test_clean_text():
    assert clean_text("  how many\x07 red cases?\n ") == "how many red cases?"
    with pytest.raises(Rejected, match="empty"):
        clean_text(" \x00 ")
    with pytest.raises(Rejected, match="too_long"):
        clean_text("a" * 2001)
    assert clean_text("a" * 2000)


@pytest.mark.parametrize("label", ["off_topic", "injection_attempt"])
def test_gate_refuses(label):
    async def classify(_):
        return label
    response = run(make_topic_gate(classify)(None, request(user("write me a poem"))))
    assert response.content.parts[0].text == REFUSAL


def test_gate_passes_on_topic_and_failures():
    async def on_topic(_):
        return "on_topic"

    async def broken(_):
        raise RuntimeError("model down")

    async def slow(_):
        await asyncio.sleep(1)
        return "off_topic"

    for classify in (on_topic, broken):
        assert run(make_topic_gate(classify)(None, request(user("red cases in Churu?")))) is None
    assert run(make_topic_gate(slow, timeout=0.05)(None, request(user("poem")))) is None


def test_gate_only_screens_the_users_turn():
    calls = []

    async def classify(text):
        calls.append(text)
        return "off_topic"
    assert run(make_topic_gate(classify)(None, request(user("red cases?"), tool_result()))) is None
    assert calls == []


def test_gate_sees_the_conversation_for_follow_ups():
    seen = []

    async def classify(text):
        seen.append(text)
        return "on_topic"
    model = types.Content(role="model", parts=[types.Part(text="Churu has 3 red cases.")])
    run(make_topic_gate(classify)(None, request(user("red cases in Churu?"), model, user("and in Sikar?"))))
    assert "Churu has 3 red cases" in seen[0] and seen[0].endswith("and in Sikar?")


def test_parse_label():
    assert parse_label('{"label": "on_topic"}') == "on_topic"
    for junk in ("on_topic", '{"label": "maybe"}', "[]"):
        with pytest.raises(ValueError):
            parse_label(junk)


class Tick:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def limits(cap=2000):
    tick = Tick()
    return Limits(MemoryStore(), lambda: datetime(2026, 10, 14, tzinfo=timezone.utc), cap, monotonic=tick), tick


def test_rate_per_minute():
    lim, tick = limits()
    assert all(lim.check_and_count("v:a", False) is None for _ in range(PER_MINUTE))
    assert lim.check_and_count("v:a", False) == "rate"
    assert lim.check_and_count("v:b", False) is None
    tick.t = 61
    assert lim.check_and_count("v:a", False) is None


def test_visitor_daily_and_global_cap():
    lim, tick = limits()
    for i in range(VISITOR_DAILY):
        tick.t = i * 61
        assert lim.check_and_count("v:a", False) is None
    tick.t += 61
    assert lim.check_and_count("v:a", False) == "daily"
    assert lim.check_and_count("team", True) is None  # the team has no daily allowance
    capped, _ = limits(cap=1)
    assert capped.check_and_count("v:a", False) is None
    assert capped.check_and_count("v:b", False) == "resting"


def test_streams():
    lim, _ = limits()
    assert lim.open_stream("v:a", False) and not lim.open_stream("v:a", False)
    lim.close_stream("v:a")
    assert lim.open_stream("v:a", False)
    assert all(lim.open_stream("team", True) for _ in range(3)) and not lim.open_stream("team", True)
