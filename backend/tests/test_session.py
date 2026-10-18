import asyncio
import logging
from contextlib import asynccontextmanager

import pytest

import app.call.session as session_module
from app.call.session import conduct_call
from app.config import Settings
from app.db import MemoryStore
from tests.fakes import (
    HEALTHY_REPLIES, FakeLiveSession, audio, healthy_call_turns, healthy_transcript, make_deps, pause, said, session_opener,
    tool, turn_complete,
)
from tests.helpers import queue_one_call


def conduct(deps, call_id, hang_up_after=None):
    sent = []

    async def scenario():
        audio_in = asyncio.Queue()

        async def send_audio(pcm):
            sent.append(pcm)

        async def clear_audio():
            pass

        async def hang_up():
            await asyncio.sleep(hang_up_after)
            audio_in.put_nowait(None)

        hanger = asyncio.create_task(hang_up()) if hang_up_after is not None else None
        try:
            return await conduct_call(deps, call_id, audio_in, send_audio, clear_audio)
        finally:
            if hanger:
                hanger.cancel()

    return asyncio.run(scenario()), sent


def test_a_complete_healthy_call(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now, name="Sunita Sharma", age=66)
    seen = []
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(healthy_call_turns()), seen=seen), queue)

    status, sent = conduct(deps, call_id)

    assert status == "completed"
    call = store.get("calls", call_id)
    assert call["status"] == "completed"
    assert call["started_at"] == now
    assert call["respondent"] == "self"
    assert call["language_used"] == "hi"
    assert call["answers"]["feeling"]["quote"] == "theek hoon"
    assert [(entry["role"], entry["text"]) for entry in call["transcript"]] == [
        ("agent", "Namaste"), ("resident", " ji"), ("agent", " Chakkar?"), ("resident", " nahi"), ("agent", " Ulti?"),
        ("resident", HEALTHY_REPLIES),
    ]
    assert {entry["at"] for entry in call["transcript"]} == {now}
    assert sent == [b"goodbye-audio"]
    case = store.get("cases", f"{round_id}_r1")
    assert case["tier"] == "green"
    assert "Sunita Sharma" in seen[0]
    assert "Ward 1 cooling centre" in seen[0]


def test_hangup_mid_call_is_dropped_and_keeps_answers(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    turns = [[said("resident", "chakkar aa raha hai"), tool("c1", "record_answer", question_id="feeling", value="mild_symptoms"), turn_complete()]]
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(turns)), queue)

    status, _ = conduct(deps, call_id, hang_up_after=0.1)

    assert status == "dropped"
    assert store.get("calls", call_id)["answers"]["feeling"]["value"] == "mild_symptoms"
    case = store.get("cases", f"{round_id}_r1")
    assert case["tier"] == "amber"
    assert {"A1", "A12"} <= set(case["reasons"])


def test_session_open_failure_counts_as_failed_attempt(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    deps = make_deps(store, now, tz, session_opener(fail=True), queue)

    status, _ = conduct(deps, call_id)

    assert status == "failed"
    assert store.get("calls", call_id)["status"] == "failed"
    retry = store.get("calls", f"{round_id}_r1_2")
    assert retry["status"] == "queued"
    assert (f"{round_id}_r1_2") in [queued_id for queued_id, _ in queue.items]
    assert store.query("cases") == []


def test_session_dying_mid_call_is_dropped_with_a_case(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    turns = [
        # "ok" is recorded only after the two follow-ups everyone is asked
        [said("resident", "chakkar aa raha hai"), said("agent", "Sirdard?"), said("resident", "nahi")],
        [said("agent", "Ulti?"), said("resident", "nahi")],
        [tool("c1", "record_answer", question_id="feeling", value="ok"), RuntimeError("stream broke")],
    ]
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(turns)), queue)

    status, _ = conduct(deps, call_id)

    assert status == "dropped"
    assert store.get("cases", f"{round_id}_r1")["tier"] == "amber"


def test_emergency_is_red_before_the_call_ends(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    observed = {}

    class Watching(FakeLiveSession):
        async def send_tool_response(self, **kwargs):
            await super().send_tool_response(**kwargs)
            observed["case"] = store.get("cases", f"{round_id}_r1")
            observed["call_status"] = store.get("calls", call_id)["status"]

    turns = [[tool("c1", "flag_emergency", reason="collapsed"), turn_complete()]]
    deps = make_deps(store, now, tz, session_opener(Watching(turns)), queue)

    conduct(deps, call_id, hang_up_after=0.1)

    assert observed["call_status"] == "in_progress"
    assert observed["case"]["tier"] == "red"
    assert store.get("cases", f"{round_id}_r1")["tier"] == "red"


def test_time_limit_ends_the_call_as_dropped(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    store.update("calls", call_id, {"respondent": "self"})  # someone was on the line
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession([])), queue)
    deps.settings.max_call_seconds = 0
    status, _ = conduct(deps, call_id)
    assert status == "dropped"
    assert store.get("calls", call_id)["status"] == "dropped"
    assert store.get("cases", f"{round_id}_r1") is not None


def test_time_limit_with_nobody_on_the_line_is_an_unanswered_attempt(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession([])), queue)
    deps.settings.max_call_seconds = 0
    status, _ = conduct(deps, call_id)
    assert status == "no_answer"
    assert store.get("calls", call_id)["status"] == "no_answer"
    assert store.query("cases") == []


def test_cold_round_uses_cold_guidance(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    store.update("rounds", round_id, {"hazard": "cold", "peak_temp_c": 2.0})
    seen = []
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(healthy_call_turns()), seen=seen), queue)
    conduct(deps, call_id)
    assert "cold-wave warning" in seen[0]


def test_unknown_call_is_refused(store, tz, now):
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession([])))
    with pytest.raises(session_module.CallRefused):
        conduct(deps, "nope")


def snapshot(store):
    return {c: store.query(c) for c in ("calls", "cases", "residents", "rounds")}


def test_refusals_change_nothing(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    seen = []
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession([]), seen=seen), queue)

    def refuses(call):
        before = snapshot(store)
        with pytest.raises(session_module.CallRefused):
            conduct(deps, call)
        assert snapshot(store) == before
        assert seen == []

    refuses("nope")
    resident = store.get("residents", "r1")
    store.delete("residents", "r1")
    refuses(call_id)
    store.set("residents", "r1", resident)
    current = store.get("rounds", round_id)
    store.delete("rounds", round_id)
    refuses(call_id)
    store.set("rounds", round_id, current)
    store.update("calls", call_id, {"status": "completed"})
    refuses(call_id)


def test_failing_to_build_the_instruction_leaves_the_call_untouched(store, tz, now, monkeypatch):
    round_id, call_id, queue = queue_one_call(store, tz, now)

    def broken(*args, **kwargs):
        raise RuntimeError("no instruction")

    monkeypatch.setattr(session_module, "build_instruction", broken)
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession([])), queue)
    with pytest.raises(RuntimeError):
        conduct(deps, call_id)
    call = store.get("calls", call_id)
    assert call["status"] == "queued"
    assert call["started_at"] is None
    assert store.query("cases") == []
    assert store.get("calls", f"{round_id}_r1_2") is None


def test_cancelling_mid_call_still_records_the_outcome(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    reached = asyncio.Event()

    class Blocking(FakeLiveSession):
        async def send_tool_response(self, **kwargs):
            await super().send_tool_response(**kwargs)
            reached.set()

    turns = [[said("agent", "Namaste"), said("resident", "chakkar"), tool("c1", "record_answer", question_id="feeling", value="mild_symptoms"), turn_complete()]]
    deps = make_deps(store, now, tz, session_opener(Blocking(turns)), queue)
    deps.settings.max_call_seconds = 30

    async def scenario():
        async def noop(*args):
            pass

        task = asyncio.create_task(conduct_call(deps, call_id, asyncio.Queue(), noop, noop))
        await asyncio.wait_for(reached.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    call = store.get("calls", call_id)
    assert call["status"] == "dropped"
    assert call["answers"]["feeling"]["value"] == "mild_symptoms"
    assert call["transcript"][0]["text"] == "Namaste"
    assert store.get("cases", f"{round_id}_r1") is not None


def test_cancelling_while_the_session_opens_counts_as_a_failed_attempt(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    reached = asyncio.Event()

    @asynccontextmanager
    async def slow_open(settings, instruction):
        reached.set()
        await asyncio.Event().wait()
        yield None

    deps = make_deps(store, now, tz, slow_open, queue)

    async def scenario():
        async def noop(*args):
            pass

        task = asyncio.create_task(conduct_call(deps, call_id, asyncio.Queue(), noop, noop))
        await asyncio.wait_for(reached.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert store.get("calls", call_id)["status"] == "failed"
    retry = store.get("calls", f"{round_id}_r1_2")
    assert retry["status"] == "queued"
    assert retry["channel"] == "phone"
    assert f"{round_id}_r1_2" in [queued_id for queued_id, _ in queue.items]


def test_a_failing_language_write_does_not_prevent_the_outcome(store, tz, now, monkeypatch):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    real_update = store.update

    def update(collection, doc_id, fields):
        if "language_used" in fields:
            raise RuntimeError("write refused")
        return real_update(collection, doc_id, fields)

    monkeypatch.setattr(store, "update", update)
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(healthy_call_turns())), queue)
    status, _ = conduct(deps, call_id)
    assert status == "completed"
    assert store.get("calls", call_id)["status"] == "completed"
    assert store.get("cases", f"{round_id}_r1") is not None


def failing_outcome(times):
    real = session_module.handle_call_outcome
    state = {"left": times}

    def outcome(*args, **kwargs):
        if state["left"] > 0:
            state["left"] -= 1
            raise RuntimeError("store down")
        return real(*args, **kwargs)

    return outcome


def logged_failures(caplog, call_id):
    return [r for r in caplog.records if r.exc_info and call_id in r.getMessage()]


def test_an_outcome_that_fails_once_is_retried(store, tz, now, monkeypatch, caplog):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    monkeypatch.setattr(session_module, "handle_call_outcome", failing_outcome(1))
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(healthy_call_turns())), queue)
    with caplog.at_level(logging.ERROR):
        status, _ = conduct(deps, call_id)
    assert status == "completed"
    assert store.get("calls", call_id)["status"] == "completed"
    assert len(logged_failures(caplog, call_id)) == 1


def test_an_outcome_that_fails_twice_raises_and_is_logged(store, tz, now, monkeypatch, caplog):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    monkeypatch.setattr(session_module, "handle_call_outcome", failing_outcome(2))
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(healthy_call_turns())), queue)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError):
            conduct(deps, call_id)
    assert len(logged_failures(caplog, call_id)) == 2


def test_settle_keeps_a_cancellation_that_arrives_as_the_worker_finishes():
    async def scenario():
        future = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(session_module._settle(future))
        await asyncio.sleep(0)  # the task is now waiting on the future
        task.cancel()
        future.set_result("worker finished")  # both land before the task next runs
        cancelled = await task
        return cancelled, future.result()

    cancelled, effect = asyncio.run(scenario())
    assert isinstance(cancelled, asyncio.CancelledError)
    assert effect == "worker finished"


def test_settle_does_not_report_the_workers_own_cancellation():
    async def scenario():
        future = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(session_module._settle(future))
        await asyncio.sleep(0)
        future.cancel()
        return await task

    assert asyncio.run(scenario()) is None


def test_cancelling_during_preparation_ends_the_call_as_a_failed_attempt(store, tz, now, monkeypatch):
    import threading

    round_id, call_id, queue = queue_one_call(store, tz, now)
    reached, release = threading.Event(), threading.Event()
    real = session_module.build_instruction

    def slow(*args, **kwargs):
        reached.set()
        release.wait(5)
        return real(*args, **kwargs)

    monkeypatch.setattr(session_module, "build_instruction", slow)
    seen = []
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession([]), seen=seen), queue)

    async def scenario():
        async def noop(*args):
            pass

        task = asyncio.create_task(conduct_call(deps, call_id, asyncio.Queue(), noop, noop))
        await asyncio.to_thread(reached.wait, 5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    # the worker went on to mark the call started, so the cancellation is recorded as a failed
    # attempt (the session never opened) and a phone retry is queued
    assert seen == []
    assert store.get("calls", call_id)["status"] == "failed"
    assert store.get("calls", f"{round_id}_r1_2")["channel"] == "phone"


def test_an_agent_that_only_greets_and_ends_as_no_person_is_an_unanswered_attempt(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    turns = [
        [said("agent", "Namaste"), turn_complete()],
        [audio(b"bye"), tool("e", "end_call", outcome="no_person"), turn_complete()],
    ]
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(turns)), queue)

    status, _ = conduct(deps, call_id)

    assert status == "no_answer"
    call = store.get("calls", call_id)
    assert call["status"] == "no_answer"
    assert call["end_outcome"] == "no_person"
    assert store.get("calls", f"{round_id}_r1_2")["status"] == "queued"
    assert f"{round_id}_r1_2" in [queued_id for queued_id, _ in queue.items]
    assert store.query("cases") == []


def test_a_caller_who_hangs_up_before_anything_is_recorded_is_an_unanswered_attempt(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    turns = [[said("agent", "Namaste"), turn_complete()]]
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(turns)), queue)

    status, _ = conduct(deps, call_id, hang_up_after=0.1)

    assert status == "no_answer"
    assert store.get("calls", call_id)["status"] == "no_answer"
    assert store.get("calls", f"{round_id}_r1_2")["status"] == "queued"
    assert store.query("cases") == []


class TranscriptWatcher(MemoryStore):
    """Remembers every transcript written to a call, in order."""

    def __init__(self) -> None:
        super().__init__()
        self.transcripts: list[list[str]] = []

    def update(self, collection, doc_id, fields):
        if "transcript" in fields:
            self.transcripts.append([entry["text"] for entry in fields["transcript"]])
        super().update(collection, doc_id, fields)


def run_to_the_end(deps, call_id):
    async def scenario():
        async def nothing(*args):
            pass

        return await conduct_call(deps, call_id, asyncio.Queue(), nothing, nothing)

    return asyncio.run(scenario())


def slow_call_turns():
    turns = healthy_call_turns()
    turns[1] = [pause(0.4), said("resident", "theek hoon"), *turns[1]]  # a gap after the greeting
    return turns


def test_the_transcript_reaches_the_store_while_the_call_is_running(tz, now):
    store = TranscriptWatcher()
    _, call_id, queue = queue_one_call(store, tz, now)
    settings = Settings(_env_file=None, internal_secret="s3cret", max_call_seconds=5, transcript_save_seconds=0.05)
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(slow_call_turns())), queue, settings=settings)

    assert run_to_the_end(deps, call_id) == "completed"

    assert store.transcripts[0] == ["Namaste"]  # written during the gap, before the call ended
    assert len(store.transcripts) >= 2
    assert store.transcripts[-1] == healthy_transcript("theek hoon")
    assert [entry["text"] for entry in store.get("calls", call_id)["transcript"]] == healthy_transcript("theek hoon")


def test_a_saver_that_breaks_stops_quietly_and_the_call_ends_as_it_would_have(tz, now, monkeypatch, caplog):
    asked = []

    def broken(self):
        asked.append(1)
        raise RuntimeError("could not copy the transcript")

    monkeypatch.setattr(session_module.CallTools, "transcript_if_changed", broken)
    store = TranscriptWatcher()
    round_id, call_id, queue = queue_one_call(store, tz, now)
    settings = Settings(_env_file=None, internal_secret="s3cret", max_call_seconds=5, transcript_save_seconds=0.05)
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(slow_call_turns())), queue, settings=settings)

    with caplog.at_level(logging.ERROR, logger="app.call.session"):
        assert run_to_the_end(deps, call_id) == "completed"

    assert asked == [1]  # it stopped after the first failure
    assert store.get("calls", call_id)["status"] == "completed"
    assert store.get("cases", f"{round_id}_r1")["tier"] == "green"
    assert store.transcripts == [healthy_transcript("theek hoon")]  # the one written when the call ended
    logged = [record for record in caplog.records if record.name == "app.call.session"]
    assert len(logged) == 1 and "no longer being saved" in logged[0].getMessage()
    assert "Namaste" not in logged[0].getMessage() and "theek" not in logged[0].getMessage()


def test_the_saver_itself_never_raises(caplog):
    class Broken:
        calls = 0

        def transcript_if_changed(self):
            self.calls += 1
            raise ValueError("no")

    async def scenario():
        tools = Broken()
        await asyncio.wait_for(session_module._keep_transcript_saved(tools, 0.01, asyncio.Event()), timeout=2)
        return tools.calls

    with caplog.at_level(logging.ERROR, logger="app.call.session"):
        assert asyncio.run(scenario()) == 1
    assert len(caplog.records) == 1


def test_a_cancelled_saver_is_still_cancelled():
    class Quiet:
        def transcript_if_changed(self):
            return None

    async def scenario():
        saver = asyncio.ensure_future(session_module._keep_transcript_saved(Quiet(), 0.01, asyncio.Event()))
        await asyncio.sleep(0.05)
        saver.cancel()
        with pytest.raises(asyncio.CancelledError):
            await saver

    asyncio.run(scenario())


def test_with_saving_off_the_transcript_is_written_once_at_the_end(tz, now):
    store = TranscriptWatcher()
    _, call_id, queue = queue_one_call(store, tz, now)
    settings = Settings(_env_file=None, internal_secret="s3cret", max_call_seconds=5, transcript_save_seconds=0)
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(slow_call_turns())), queue, settings=settings)

    assert run_to_the_end(deps, call_id) == "completed"
    assert store.transcripts == [healthy_transcript("theek hoon")]


def test_a_failing_mid_call_save_does_not_disturb_the_call(tz, now):
    class FailsMidCall(TranscriptWatcher):
        def update(self, collection, doc_id, fields):
            if "transcript" in fields and len(self.transcripts) == 0:
                self.transcripts.append(["(failed)"])
                raise ConnectionError("store unavailable")
            super().update(collection, doc_id, fields)

    store = FailsMidCall()
    round_id, call_id, queue = queue_one_call(store, tz, now)
    settings = Settings(_env_file=None, internal_secret="s3cret", max_call_seconds=5, transcript_save_seconds=0.05)
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(slow_call_turns())), queue, settings=settings)

    assert run_to_the_end(deps, call_id) == "completed"
    assert store.get("cases", f"{round_id}_r1")["tier"] == "green"
    assert store.transcripts[-1] == healthy_transcript("theek hoon")


# --- the settings a call ran with ------------------------------------------------------


def test_a_started_call_stores_the_settings_it_ran_with(store, tz, now):
    from app.call.live import live_settings_snapshot

    round_id, call_id, queue = queue_one_call(store, tz, now)
    assert store.get("calls", call_id)["live_settings"] == {}
    settings = Settings(_env_file=None, internal_secret="s3cret", max_call_seconds=2, live_voice="Puck", live_quiet_seconds=0)
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(healthy_call_turns())), queue, settings=settings)

    conduct(deps, call_id)

    stored = store.get("calls", call_id)["live_settings"]
    assert stored == live_settings_snapshot(settings)
    assert stored["live_voice"] == "Puck" and stored["live_quiet_seconds"] == 0


def test_a_refused_call_stores_no_settings(store, tz, now, monkeypatch):
    round_id, call_id, queue = queue_one_call(store, tz, now)

    def broken(*args, **kwargs):
        raise RuntimeError("no instruction")

    monkeypatch.setattr(session_module, "build_instruction", broken)
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession([])), queue)
    with pytest.raises(RuntimeError):
        conduct(deps, call_id)
    assert store.get("calls", call_id)["live_settings"] == {}


def test_the_runner_and_the_instruction_are_given_the_tuning_settings(store, tz, now, monkeypatch):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    given = {}

    async def fake_run_call(session, tools, audio_in, send_audio, clear_audio, opening, max_seconds, **tuning):
        given.update(tuning)
        return "caller_hung_up"

    monkeypatch.setattr(session_module, "run_call", fake_run_call)
    settings = Settings(
        _env_file=None, internal_secret="s3cret", live_speaks_as="man",
        live_quiet_seconds=4.5, live_quiet_limit=1, live_greeting_guard_seconds=7.5, live_closing_silence_seconds=3,
    )
    seen = []
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession([]), seen=seen), queue, settings=settings)

    conduct(deps, call_id)

    assert isinstance(given.pop("notes"), asyncio.Queue)  # how the help button reaches the agent
    assert isinstance(given.pop("answered_at"), float)  # the pickup is timed from the answer
    assert given == {
        "quiet_seconds": 4.5, "quiet_limit": 1, "greeting_guard_seconds": 7.5, "closing_silence_seconds": 3,
        "mute_repeats": True,
    }
    assert "You speak as a man." in seen[0] and "You speak as a woman." not in seen[0]


# --- the timeline of a call, and the follow-ups everyone is asked ------------------------

TIMELINE_KINDS = {"agent_turn", "interrupted", "heard", "tool", "guard_lifted", "prompt"}


def test_a_call_stores_its_timeline_and_no_word_of_what_was_said(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    assert store.get("calls", call_id)["timeline"] == []
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(healthy_call_turns())), queue)

    assert conduct(deps, call_id)[0] == "completed"

    call = store.get("calls", call_id)
    timeline = call["timeline"]
    assert {"agent_turn", "heard", "tool"} <= {event["kind"] for event in timeline} <= TIMELINE_KINDS
    assert all(event["at"] == now for event in timeline)
    tools_called = [event for event in timeline if event["kind"] == "tool"]
    assert [event["name"] for event in tools_called] == ["set_respondent"] + ["record_answer"] * 6 + ["end_call"]
    assert not any("refused" in event for event in tools_called)
    turns = [event for event in timeline if event["kind"] == "agent_turn"]
    assert turns[-1] == {"at": now, "kind": "agent_turn", "audio_seconds": 0.0, "interrupted": False, "tools": 1}
    text = repr(timeline)
    for entry in call["transcript"]:
        assert entry["text"].strip() not in text
    assert "theek hoon" not in text  # the quote given with the first answer


def test_a_dropped_call_keeps_the_timeline_it_had(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    turns = [[audio(b"x" * 4800), said("agent", "Namaste"), turn_complete()], [said("resident", "haan")]]
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(turns)), queue)

    conduct(deps, call_id, hang_up_after=0.2)

    kinds = [event["kind"] for event in store.get("calls", call_id)["timeline"]]
    assert [kind for kind in kinds if kind != "guard_lifted"] == ["agent_turn", "heard"]


def test_the_timeline_reaches_the_store_while_the_call_is_running(tz, now):
    class Watcher(TranscriptWatcher):
        def __init__(self):
            super().__init__()
            self.timelines = []

        def update(self, collection, doc_id, fields):
            if "transcript" in fields:
                self.timelines.append([event["kind"] for event in fields.get("timeline", [])])
            super().update(collection, doc_id, fields)

    store = Watcher()
    _, call_id, queue = queue_one_call(store, tz, now)
    settings = Settings(_env_file=None, internal_secret="s3cret", max_call_seconds=5, transcript_save_seconds=0.05)
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession(slow_call_turns())), queue, settings=settings)

    assert run_to_the_end(deps, call_id) == "completed"
    assert "agent_turn" in store.timelines[0]  # written with the transcript, during the gap
    assert len(store.timelines[-1]) > len(store.timelines[0])


def test_an_agent_that_skips_the_follow_ups_is_refused_and_told_what_to_ask(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    session = FakeLiveSession(
        [
            [said("agent", "Kaisi tabiyat hai?"), turn_complete()],
            [said("resident", "theek hoon"), tool("c1", "record_answer", question_id="feeling", value="ok"), turn_complete()],
        ]
    )
    deps = make_deps(store, now, tz, session_opener(session), queue)

    conduct(deps, call_id, hang_up_after=0.3)

    call = store.get("calls", call_id)
    assert call["answers"] == {}
    refusal = session.tool_responses[0].response
    assert refusal["error"] == "follow-ups not asked"
    assert refusal["ask_next"] == ["क्या चक्कर या सिरदर्द हो रहा है?", "क्या उल्टी, ऐंठन या कमज़ोरी है?"]  # the call is in Hindi
    assert [event for event in call["timeline"] if event["kind"] == "tool"] == [
        {"at": now, "kind": "tool", "name": "record_answer", "question": "feeling", "refused": "follow_ups"}
    ]


def test_a_worrying_answer_is_recorded_at_once_on_a_real_call(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    session = FakeLiveSession(
        [[said("resident", "chakkar aa raha hai"), tool("c1", "record_answer", question_id="feeling", value="mild_symptoms"), turn_complete()]]
    )
    deps = make_deps(store, now, tz, session_opener(session), queue)
    conduct(deps, call_id, hang_up_after=0.3)
    assert store.get("calls", call_id)["answers"]["feeling"]["value"] == "mild_symptoms"


def test_an_answer_recorded_after_the_agents_own_echo_is_refused(store, tz, now):
    # Seen on real calls (13 October): shelter, company and exposure were recorded right after
    # a "reply" that was the agent's question coming back down the line.
    round_id, call_id, queue = queue_one_call(store, tz, now)
    question = "Kya aaj ghar par aapke saath koi hai?"
    session = FakeLiveSession(
        [
            [audio(b"q"), said("agent", question), turn_complete()],
            [said("resident", "kya aaj ghar par aapke saath koi hai"), tool("c1", "record_answer", question_id="company", value="with_someone"), turn_complete()],
        ]
    )
    deps = make_deps(store, now, tz, session_opener(session), queue)

    conduct(deps, call_id, hang_up_after=0.3)

    call = store.get("calls", call_id)
    assert call["answers"] == {}
    assert session.tool_responses[0].response["error"] == "no answer heard"
    assert [entry["text"] for entry in call["transcript"]] == [question]
    assert {"at": now, "kind": "heard", "echo": True} in call["timeline"]
    assert "koi hai" not in repr(call["timeline"])


def test_the_runner_is_told_not_to_pass_on_a_repeated_question(store, tz, now, monkeypatch):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    given = {}

    async def fake_run_call(*args, **tuning):
        given.update(tuning)
        return "caller_hung_up"

    monkeypatch.setattr(session_module, "run_call", fake_run_call)
    deps = make_deps(store, now, tz, session_opener(FakeLiveSession([])), queue)
    conduct(deps, call_id)
    assert given["mute_repeats"] is True
