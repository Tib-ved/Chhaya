import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from google.adk.events import Event
from google.genai import types

from app.api import create_app
from app.ask.guard import REFUSAL
from app.ask.limits import PER_MINUTE
from app.ask.routes import history
from app.config import Settings
from app.dispatch import RecordingQueue
from evals.ask_chhaya.fixture import FIXTURE_NOW, build_store

ALICE = {"X-Visitor": "3f2b8c1e-7a4d-4e2a-9b1c-5d6e7f8a9b0c"}
BOB = {"X-Visitor": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"}
TEAM = {"X-Internal-Secret": "s3cret"}
CHART = {"type": "bar", "title": "Red cases", "series": [{"name": "Red", "points": [{"label": "Ward 1", "value": 1}]}]}


def text(t, partial=False):
    return Event(author="chhaya", partial=partial, content=types.Content(role="model", parts=[types.Part(text=t)]))


def call(name, args, partial=False, cid="c1"):
    return Event(author="chhaya", partial=partial, content=types.Content(
        role="model", parts=[types.Part(function_call=types.FunctionCall(id=cid, name=name, args=args))]))


def response(name, payload):
    return Event(author="chhaya", content=types.Content(
        role="user", parts=[types.Part(function_response=types.FunctionResponse(name=name, response=payload))]))


ANSWER = [
    call("list_cases", {"area": "churu"}, partial=True), call("list_cases", {"area": "churu"}),
    response("list_cases", {"items": [{"case_id": "churu_heat_20261014_churu-ward-1-r-1"}]}),
    call("show_chart", CHART, partial=True, cid="v1"), call("show_chart", CHART, cid="v1"),
    response("show_chart", {"ok": True}),
    call("show_chart", {"type": "pie", "title": "bad", "series": []}, cid="v2"),
    text("Churu has ", partial=True), text("3 red cases.", partial=True), text("Churu has 3 red cases."),
]


class StubRunner:
    def __init__(self, events=ANSWER, fail=False, delay=0.0):
        self.events, self.fail, self.delay, self.seen = events, fail, delay, []

    async def run_async(self, **kwargs):
        self.seen.append(kwargs)
        for event in self.events:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield event
        if self.fail:
            raise RuntimeError("boom")


def make(runner=None, titler=None, **settings):
    store = build_store()
    s = Settings(_env_file=None, internal_secret="s3cret", **settings)
    client = TestClient(create_app(store, RecordingQueue(), s, clock=lambda: FIXTURE_NOW,
                                   ask_runner=runner or StubRunner(), ask_titler=titler))
    client.store = store
    return client


def frames(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def new_session(client, who=ALICE, focus=None):
    made = client.post("/api/ask/sessions", json={"focus": focus or {"district_id": "churu"}}, headers=who)
    assert made.status_code == 201
    return made.json()["session_id"]


def ask(client, sid, words="How many red cases?", who=ALICE):
    return client.post(f"/api/ask/sessions/{sid}/messages", json={"text": words}, headers=who)


def test_stream_order_and_content():
    client = make()
    sid = new_session(client)
    got = frames(ask(client, sid).text)
    kinds = [k for k, _ in got]
    assert kinds == ["status", "chart", "delta", "sources", "done"]  # a response's text goes out whole
    assert got[0][1]["label"].startswith("Reading cases in churu")
    assert got[1][1]["spec"]["type"] == "bar"  # the bad pie is never sent
    assert "".join(d["text"] for k, d in got if k == "delta") == "Churu has 3 red cases."
    assert got[-2][1]["case_ids"] == ["churu_heat_20261014_churu-ward-1-r-1"]
    assert got[-1][1]["kind"] == "answer"


def test_words_before_a_tool_call_are_never_shown():
    # The model thinking aloud ("Ward 58 likely churu-ward-58.") before it calls a tool is not the answer.
    preamble = "Ward 58 likely churu-ward-58."
    client = make(StubRunner([
        text(preamble, partial=True),
        Event(author="chhaya", content=types.Content(role="model", parts=[
            types.Part(text=preamble),
            types.Part(function_call=types.FunctionCall(id="c1", name="list_cases", args={"area": "churu"})),
        ])),
        response("list_cases", {"items": []}),
        text("Three red cases.", partial=True), text("Three red cases."),
    ]))
    got = frames(ask(client, new_session(client)).text)
    shown = "".join(d["text"] for k, d in got if k == "delta")
    assert shown == "Three red cases."
    assert [k for k, _ in got][:1] == ["status"]


def test_final_text_without_partials_is_sent_once():
    client = make(StubRunner([text("Hello.")]))
    got = frames(ask(client, new_session(client)).text)
    assert [d["text"] for k, d in got if k == "delta"] == ["Hello."]


def test_refusal_is_marked():
    client = make(StubRunner([text(REFUSAL)]))
    got = frames(ask(client, new_session(client)).text)
    assert got[-1] == ("done", {**got[-1][1], "kind": "refused"})


def test_error_keeps_partial_text_and_releases_the_session():
    runner = StubRunner([text("Churu has", partial=True)], fail=True)
    client = make(runner)
    sid = new_session(client)
    got = frames(ask(client, sid).text)
    assert [k for k, _ in got] == ["delta", "error"] and got[-1][1]["code"] == "model"
    assert client.store.get("ask_sessions", sid)["busy"] is False
    assert ask(client, sid).status_code == 200


def test_focus_and_message_reach_the_runner():
    runner = StubRunner([text("ok")])
    client = make(runner)
    sid = new_session(client)
    client.post(f"/api/ask/sessions/{sid}/messages", json={"text": "here?", "focus": {"ward_id": "churu-ward-2"}}, headers=ALICE)
    seen = runner.seen[0]
    assert seen["session_id"] == sid and seen["user_id"].startswith("v:")
    assert seen["new_message"].parts[0].text == "here?"
    assert seen["state_delta"]["focus"]["ward_id"] == "churu-ward-2"


def test_owners_and_callers():
    client = make()
    sid = new_session(client)
    assert client.get(f"/api/ask/sessions/{sid}", headers=BOB).status_code == 404
    assert ask(client, sid, who=BOB).status_code == 404
    assert client.post("/api/ask/sessions", json={}).status_code == 401
    assert client.post("/api/ask/sessions", json={}, headers=TEAM).status_code == 201


def test_list_hides_empty_and_titles_fall_back():
    client = make()
    new_session(client)
    sid = new_session(client)
    ask(client, sid, "Which wards in Churu have the most red cases this round?")
    listed = client.get("/api/ask/sessions", headers=ALICE).json()
    assert [s["session_id"] for s in listed] == [sid]
    assert listed[0]["title"].startswith("Which wards in Churu")


def test_smart_title_is_sent_and_saved():
    async def titler(words):
        return "Red cases across Churu wards"
    runner = StubRunner(ANSWER, delay=0.01)
    client = make(runner, titler=titler)
    sid = new_session(client)
    got = frames(ask(client, sid).text)
    assert ("title", {"title": "Red cases across Churu wards"}) in got
    assert client.get("/api/ask/sessions", headers=ALICE).json()[0]["title"] == "Red cases across Churu wards"


def test_bad_messages():
    client = make()
    sid = new_session(client)
    assert ask(client, sid, "a" * 2001).status_code == 400
    assert ask(client, sid, " \x00 ").status_code == 400
    assert ask(client, "s_nope").status_code == 404


def test_busy_session_is_409():
    client = make()
    sid = new_session(client)
    client.store.update("ask_sessions", sid, {"busy": True})
    assert ask(client, sid).status_code == 409
    assert client.store.get("ask_usage", f"{FIXTURE_NOW.date()}_v:{ALICE['X-Visitor']}")  # counted, then refused


def test_rate_limit():
    client = make(StubRunner([text("ok")]))
    sid = new_session(client)
    codes = [ask(client, sid).status_code for _ in range(PER_MINUTE + 1)]
    assert codes[:PER_MINUTE] == [200] * PER_MINUTE and codes[-1] == 429


def test_daily_cap_rests():
    client = make(StubRunner([text("ok")]), ask_daily_cap=1)
    sid = new_session(client)
    assert ask(client, sid).status_code == 200
    resting = ask(client, sid, who=ALICE)
    assert resting.status_code == 429 and "resting" in resting.json()["detail"]


def test_no_model_is_503():
    store = build_store()
    client = TestClient(create_app(store, RecordingQueue(), Settings(_env_file=None), clock=lambda: FIXTURE_NOW))
    assert client.post("/api/ask/sessions", json={}, headers=ALICE).status_code == 503


def test_a_slow_answer_does_not_block_other_requests():
    client = make(StubRunner([text("slow", partial=True)] * 20, delay=0.05))
    sid = new_session(client)
    done = {}

    def slow():
        done["stream"] = ask(client, sid).status_code

    thread = threading.Thread(target=slow)
    thread.start()
    time.sleep(0.2)
    started = time.monotonic()
    assert client.get("/api/ask/sessions", headers=BOB).status_code == 200
    assert time.monotonic() - started < 0.5
    thread.join()
    assert done["stream"] == 200


def test_history_rebuilds_messages():
    user = Event(author="user", content=types.Content(role="user", parts=[types.Part(text="Red cases?")]))
    rebuilt = history([user, call("show_chart", CHART), response("show_chart", {"ok": True}), text("Three.")])
    assert rebuilt[0] == {"role": "user", "text": "Red cases?", "visuals": []}
    assert rebuilt[1]["text"] == "Three." and rebuilt[1]["visuals"][0]["kind"] == "chart"
