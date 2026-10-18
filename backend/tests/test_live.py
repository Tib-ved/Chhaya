import asyncio
import logging
import re
import time

import pytest

import app.call.live as live_module
from app.call.audio import GEMINI_INPUT_MIME
from app.call.guidance import TOOL_DECLARATIONS
from app.call.live import (
    ALREADY_SPOKEN, CLOSING_NOTE, CLOSING_PROMPT, EMERGENCY_CLOSING_NOTE, EMERGENCY_CLOSING_PROMPT, QUIET_NOTE, QUIET_PROMPT, live_config, live_settings_snapshot, run_call,
)
from app.config import Settings
from tests.fakes import FakeLiveSession, FakeTools, audio, interrupted, pause, said, tool, tool_batch, turn_complete


def run(session, tools=None, caller=(), hang_up_after=None, max_seconds=2.0, later=(), **tuning):
    """Runs run_call. `caller` is audio queued before the call; hang_up_after is seconds.

    `later` is (seconds, chunk) pairs of audio that arrive during the call; `tuning` is passed
    to run_call as it is."""
    tools = tools or FakeTools()
    sent, cleared = [], []

    async def scenario():
        audio_in = asyncio.Queue()
        for chunk in caller:
            audio_in.put_nowait(chunk)

        async def send_audio(pcm):
            sent.append(pcm)

        async def clear_audio():
            cleared.append(True)

        async def hang_up():
            await asyncio.sleep(hang_up_after)
            audio_in.put_nowait(None)

        async def arrive(seconds, chunk):
            await asyncio.sleep(seconds)
            audio_in.put_nowait(chunk)

        hanger = asyncio.create_task(hang_up()) if hang_up_after is not None else None
        arrivals = [asyncio.create_task(arrive(seconds, chunk)) for seconds, chunk in later]
        try:
            return await run_call(
                session, tools, audio_in, send_audio, clear_audio, "Greet them.", max_seconds, **tuning
            )
        finally:
            for task in (hanger, *arrivals):
                if task:
                    task.cancel()

    return asyncio.run(scenario()), tools, sent, cleared


def test_opening_prompt_is_sent_first_as_text():
    session = FakeLiveSession([[audio(b"bye"), tool("c9", "end_call", outcome="completed"), turn_complete()]])
    run(session)
    assert session.sent_text == ["Greet them."]


def test_model_audio_is_forwarded():
    session = FakeLiveSession([[audio(b"one"), audio(b"two"), tool("c9", "end_call"), turn_complete()]])
    reason, _, sent, _ = run(session)
    assert reason == "ended_by_agent"
    assert sent == [b"one", b"two"]


def test_caller_audio_is_forwarded_with_the_right_format():
    session = FakeLiveSession([])
    reason, _, _, _ = run(session, caller=[b"\x00\x01" * 160], hang_up_after=0.05)
    assert reason == "caller_hung_up"
    assert session.sent_audio == [b"\x00\x01" * 160]
    assert GEMINI_INPUT_MIME == "audio/pcm;rate=16000"


def test_tool_calls_are_answered_and_the_loop_survives_turn_complete():
    session = FakeLiveSession(
        [
            [tool("c1", "record_answer", question_id="feeling", value="ok"), turn_complete()],
            [audio(b"after-tool"), turn_complete()],
        ]
    )
    reason, tools, sent, _ = run(session, hang_up_after=0.1)
    assert tools.calls == [("record_answer", {"question_id": "feeling", "value": "ok"})]
    response = session.tool_responses[0]
    assert (response.id, response.name, response.response) == ("c1", "record_answer", {"result": "recorded"})
    assert sent == [b"after-tool"]  # receive() was called again after the first turn_complete
    assert reason == "caller_hung_up"


def test_call_ends_when_the_agent_calls_end_call():
    session = FakeLiveSession([[audio(b"bye"), tool("c2", "end_call", outcome="declined"), turn_complete()]])
    reason, tools, _, _ = run(session)
    assert reason == "ended_by_agent"
    assert tools.ended == "declined"


def test_turn_complete_without_end_call_does_not_end_the_call():
    session = FakeLiveSession([[audio(b"hello"), turn_complete()]])
    reason, _, _, _ = run(session, hang_up_after=0.1)
    assert reason == "caller_hung_up"


def test_interruption_clears_playback():
    session = FakeLiveSession([[audio(b"talking"), interrupted(), turn_complete()]])
    _, _, _, cleared = run(session, hang_up_after=0.1)
    assert cleared == [True]


def test_transcripts_are_recorded_by_speaker():
    session = FakeLiveSession([[said("agent", "Namaste"), said("resident", "haan"), turn_complete()]])
    _, tools, _, _ = run(session, hang_up_after=0.1)
    assert tools.transcript == [("agent", "Namaste"), ("resident", "haan")]


def test_time_limit():
    reason, _, _, _ = run(FakeLiveSession([]), max_seconds=0.05)
    assert reason == "timeout"


def test_error_from_the_session_ends_the_call():
    session = FakeLiveSession([[audio(b"x"), RuntimeError("stream broke")]])
    reason, _, _, _ = run(session)
    assert reason == "error"


def test_a_closed_session_ends_the_call():
    reason, _, _, _ = run(FakeLiveSession([], then="close"))
    assert reason == "error"


def test_a_failing_audio_sink_ends_the_call():
    session = FakeLiveSession([[audio(b"x"), turn_complete()]])

    async def scenario():
        async def broken(pcm):
            raise ConnectionError("socket gone")

        async def clear():
            pass

        return await run_call(session, FakeTools(), asyncio.Queue(), broken, clear, "Hi", 2.0)

    assert asyncio.run(scenario()) == "error"


def test_live_config():
    settings = Settings(_env_file=None, live_silence_ms=650)
    config = live_config(settings, "INSTRUCTION")
    assert config["response_modalities"] == ["AUDIO"]
    assert config["system_instruction"] == "INSTRUCTION"
    assert config["input_audio_transcription"] == {}
    assert config["output_audio_transcription"] == {}
    assert config["tools"] == [{"function_declarations": TOOL_DECLARATIONS}]
    assert config["realtime_input_config"]["automatic_activity_detection"]["silence_duration_ms"] == 650


# --- the settings experiments are run with ---------------------------------------------

NOTHING_SENT = dict(
    live_voice="", live_start_sensitivity="default", live_end_sensitivity="default",
    live_prefix_padding_ms=0, live_interruptions=True, live_resume=False,
)


def test_the_tuning_settings_have_their_defaults():
    settings = Settings(_env_file=None)
    # A woman's voice for a script written in the feminine; the name was heard on a real call.
    assert (settings.live_voice, settings.live_speaks_as) == ("Kore", "woman")
    assert (settings.live_start_sensitivity, settings.live_end_sensitivity) == ("low", "default")
    assert settings.live_prefix_padding_ms == 200
    assert settings.live_interruptions is True
    assert (settings.live_quiet_seconds, settings.live_quiet_limit) == (5.0, 2)
    assert settings.live_closing_silence_seconds == 5.0
    assert settings.live_mute_repeats is True
    assert not hasattr(settings, "live_resume_seconds") and not hasattr(settings, "live_resume_limit")
    assert settings.live_greeting_guard_seconds == 6.0
    assert settings.live_silence_ms == 700


def test_the_tuning_settings_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("LIVE_VOICE", "Puck")
    monkeypatch.setenv("LIVE_SPEAKS_AS", "man")
    monkeypatch.setenv("LIVE_START_SENSITIVITY", "high")
    monkeypatch.setenv("LIVE_INTERRUPTIONS", "false")
    monkeypatch.setenv("LIVE_QUIET_SECONDS", "0")
    monkeypatch.setenv("LIVE_MUTE_REPEATS", "false")
    monkeypatch.setenv("LIVE_CLOSING_SILENCE_SECONDS", "7.5")
    settings = Settings(_env_file=None)
    assert (settings.live_voice, settings.live_speaks_as, settings.live_start_sensitivity) == ("Puck", "man", "high")
    assert settings.live_interruptions is False and settings.live_quiet_seconds == 0
    assert settings.live_closing_silence_seconds == 7.5
    assert settings.live_mute_repeats is False


@pytest.mark.parametrize("name", ["live_speaks_as", "live_start_sensitivity", "live_end_sensitivity"])
def test_a_misspelt_tuning_value_fails_at_start_up(name):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{name: "medium"})


@pytest.mark.parametrize(
    "name", ["live_prefix_padding_ms", "live_quiet_seconds", "live_quiet_limit", "live_greeting_guard_seconds"]
)
def test_a_negative_tuning_value_fails_at_start_up(name):
    # The service would refuse it, and then no call would open.
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{name: -1})
    assert getattr(Settings(_env_file=None, **{name: 0}), name) == 0


def test_a_finished_call_is_never_ended_after_less_than_a_second_of_silence():
    with pytest.raises(ValueError):
        Settings(_env_file=None, live_closing_silence_seconds=0.5)
    assert Settings(_env_file=None, live_closing_silence_seconds=1).live_closing_silence_seconds == 1


def test_the_agent_is_given_five_seconds_to_finish_after_it_has_ended_the_call():
    assert live_module.END_GRACE_SECONDS == 5.0
    assert live_module.END_HARD_CAP_SECONDS == 30.0
    assert not hasattr(live_module, "CLOSING_SILENCE_SECONDS")  # a setting now


def test_the_voice_name_is_trimmed_and_a_blank_one_is_no_voice():
    assert Settings(_env_file=None, live_voice="  Kore \n").live_voice == "Kore"
    blank = Settings(_env_file=None, live_voice="   ")
    assert blank.live_voice == ""
    assert "speech_config" not in live_config(blank, "INSTRUCTION")


def test_with_nothing_chosen_the_config_is_what_it_was():
    settings = Settings(_env_file=None, live_silence_ms=650, **NOTHING_SENT)
    assert live_config(settings, "INSTRUCTION") == {
        "response_modalities": ["AUDIO"],
        "system_instruction": "INSTRUCTION",
        "input_audio_transcription": {},
        "output_audio_transcription": {},
        "realtime_input_config": {"automatic_activity_detection": {"silence_duration_ms": 650}},
        "tools": [{"function_declarations": TOOL_DECLARATIONS}],
    }


def test_the_default_config_chooses_a_womans_voice_and_a_less_ready_start_of_speech():
    config = live_config(Settings(_env_file=None), "INSTRUCTION")
    assert config["speech_config"] == {"voice_config": {"prebuilt_voice_config": {"voice_name": "Kore"}}}
    assert config["session_resumption"] == {}  # asks for resumption handles
    assert "speech_config" not in live_config(Settings(_env_file=None, live_voice=""), "INSTRUCTION")
    assert config["realtime_input_config"] == {
        "automatic_activity_detection": {
            "silence_duration_ms": 700,
            "start_of_speech_sensitivity": "START_SENSITIVITY_LOW",
            "prefix_padding_ms": 200,
        }
    }


def test_every_tuning_setting_reaches_the_config():
    settings = Settings(
        _env_file=None, live_voice="Puck", live_start_sensitivity="high", live_end_sensitivity="low",
        live_prefix_padding_ms=350, live_interruptions=False,
    )
    config = live_config(settings, "INSTRUCTION")
    assert config["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"] == "Puck"
    assert config["realtime_input_config"] == {
        "automatic_activity_detection": {
            "silence_duration_ms": 700,
            "start_of_speech_sensitivity": "START_SENSITIVITY_HIGH",
            "end_of_speech_sensitivity": "END_SENSITIVITY_LOW",
            "prefix_padding_ms": 350,
        },
        "activity_handling": "NO_INTERRUPTION",
    }
    high_end = live_config(Settings(_env_file=None, live_end_sensitivity="high"), "I")
    detection = high_end["realtime_input_config"]["automatic_activity_detection"]
    assert detection["end_of_speech_sensitivity"] == "END_SENSITIVITY_HIGH"


def test_the_sdk_accepts_the_config_with_everything_set():
    from google.genai import types

    settings = Settings(_env_file=None, live_voice="Kore", live_end_sensitivity="high", live_interruptions=False)
    parsed = types.LiveConnectConfig.model_validate(live_config(settings, "INSTRUCTION"))
    assert parsed.speech_config.voice_config.prebuilt_voice_config.voice_name == "Kore"
    assert parsed.realtime_input_config.activity_handling == types.ActivityHandling.NO_INTERRUPTION
    detection = parsed.realtime_input_config.automatic_activity_detection
    assert detection.start_of_speech_sensitivity == types.StartSensitivity.START_SENSITIVITY_LOW
    assert detection.end_of_speech_sensitivity == types.EndSensitivity.END_SENSITIVITY_HIGH
    assert detection.prefix_padding_ms == 200


def test_the_snapshot_holds_the_model_and_every_tuning_setting():
    snapshot = live_settings_snapshot(Settings(_env_file=None, live_voice="Puck", live_quiet_seconds=0))
    assert snapshot == {
        "live_model": "gemini-3.8-live",
        "live_voice": "Puck",
        "live_speaks_as": "woman",
        "live_silence_ms": 700,
        "live_start_sensitivity": "low",
        "live_end_sensitivity": "default",
        "live_prefix_padding_ms": 200,
        "live_interruptions": True,
        "live_quiet_seconds": 0.0,
        "live_quiet_limit": 2,
        "live_greeting_guard_seconds": 6.0,
        "live_closing_silence_seconds": 5.0,
        "live_mute_repeats": True,
        "live_language_codes": True,
        "live_answer_scheduling": "default",
        "live_pre_open_seconds": 60.0,
        "live_resume": True,
        "end_grace_seconds": 5.0,
    }
    assert live_settings_snapshot(Settings(_env_file=None))["live_quiet_seconds"] == 5.0


# --- how a call ends -------------------------------------------------------------------


def test_interrupted_goodbye_does_not_end_the_call():
    session = FakeLiveSession(
        [
            [tool("c1", "end_call"), audio(b"bye"), interrupted(), turn_complete()],
            [audio(b"bye again"), turn_complete()],
        ]
    )
    reason, _, sent, _ = run(session)
    assert reason == "ended_by_agent"
    assert sent == [b"bye", b"bye again"]


def test_end_call_without_speech_waits_for_the_spoken_reply():
    session = FakeLiveSession([[tool("c1", "end_call"), turn_complete()], [audio(b"goodbye"), turn_complete()]])
    reason, _, sent, _ = run(session)
    assert reason == "ended_by_agent"
    assert sent == [b"goodbye"]


def test_silent_model_after_end_call_is_bounded_by_the_grace(monkeypatch):
    monkeypatch.setattr(live_module, "END_GRACE_SECONDS", 0.2)
    session = FakeLiveSession([[tool("c1", "end_call"), turn_complete()]])
    start = time.monotonic()
    reason, _, _, _ = run(session, max_seconds=5.0)
    assert reason == "ended_by_agent"
    assert time.monotonic() - start < 3.0


def test_endless_interruptions_after_end_call_are_bounded_by_the_grace(monkeypatch):
    monkeypatch.setattr(live_module, "END_GRACE_SECONDS", 0.2)
    turns = [[tool("c1", "end_call"), interrupted(), turn_complete()]]
    turns += [[interrupted(), turn_complete()] for _ in range(5)]
    start = time.monotonic()
    reason, _, _, _ = run(FakeLiveSession(turns), max_seconds=5.0)
    assert reason == "ended_by_agent"
    assert time.monotonic() - start < 3.0


def test_normal_goodbye_ends_without_waiting_for_the_grace(monkeypatch):
    monkeypatch.setattr(live_module, "END_GRACE_SECONDS", 30.0)
    session = FakeLiveSession([[audio(b"bye"), tool("c1", "end_call"), turn_complete()]])
    start = time.monotonic()
    reason, _, _, _ = run(session, max_seconds=5.0)
    assert reason == "ended_by_agent"
    assert time.monotonic() - start < 3.0


def test_hang_up_waits_for_a_running_tool_call():
    session = FakeLiveSession([[tool("c1", "record_answer", question_id="q", value="v")]])
    tools = FakeTools(delay=0.2)
    reason, _, _, _ = run(session, tools=tools, hang_up_after=0.05)
    assert tools.finished == 1
    assert reason == "caller_hung_up"


def test_time_limit_waits_for_a_running_end_call():
    session = FakeLiveSession([[tool("c1", "end_call", outcome="completed")]])
    tools = FakeTools(delay=0.2)
    reason, _, _, _ = run(session, tools=tools, max_seconds=0.05)
    assert tools.finished == 1
    assert reason == "ended_by_agent"


def test_time_limit_waits_for_any_running_tool_call():
    session = FakeLiveSession([[tool("c1", "record_answer", question_id="q", value="v")]])
    tools = FakeTools(delay=0.2)
    reason, _, _, _ = run(session, tools=tools, max_seconds=0.05)
    assert tools.finished == 1
    assert reason == "timeout"


def test_hang_up_after_the_agent_ended_reports_ended_by_agent():
    tools = FakeTools()
    tools.ended = "completed"
    reason, _, _, _ = run(FakeLiveSession([]), tools=tools, hang_up_after=0.05)
    assert reason == "ended_by_agent"


def test_a_rejected_tool_response_does_not_end_the_call():
    session = FakeLiveSession(
        [[tool("c1", "record_answer", question_id="q", value="v")], [audio(b"still here"), turn_complete()]],
        reject_tool_response=True,
    )
    reason, _, sent, _ = run(session, hang_up_after=0.1)
    assert sent == [b"still here"]
    assert reason == "caller_hung_up"


def test_a_rejected_opening_is_an_error():
    reason, _, _, _ = run(FakeLiveSession([], reject_opening=True))
    assert reason == "error"


def test_two_function_calls_are_answered_in_one_response():
    session = FakeLiveSession(
        [[tool_batch(("a", "record_answer", {"question_id": "q", "value": "1"}), ("b", "flag_emergency", {}))]]
    )
    _, tools, _, _ = run(session, hang_up_after=0.1)
    assert [name for name, _ in tools.calls] == ["record_answer", "flag_emergency"]
    assert len(session.tool_response_calls) == 1
    assert [r.id for r in session.tool_response_calls[0]] == ["a", "b"]


def test_relay_failure_does_not_log_the_exception_message(caplog):
    session = FakeLiveSession([[audio(b"x"), RuntimeError("SECRET-WORDS")]])
    with caplog.at_level(logging.DEBUG):
        reason, _, _, _ = run(session)
    assert reason == "error"
    assert "SECRET-WORDS" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_agent_still_speaking_after_end_call_is_not_cut_off(monkeypatch):
    monkeypatch.setattr(live_module, "END_GRACE_SECONDS", 0.3)
    chunks = [bytes([n]) for n in range(8)]
    messages = [tool("c1", "end_call")]
    for chunk in chunks:
        messages += [audio(chunk), pause(0.1)]  # 0.8 s of speech in all, no turn_complete
    reason, _, sent, _ = run(FakeLiveSession([messages]), max_seconds=10.0)
    assert reason == "ended_by_agent"
    assert sent == chunks


def test_hard_cap_bounds_endless_speech_after_end_call(monkeypatch):
    monkeypatch.setattr(live_module, "END_GRACE_SECONDS", 0.3)
    monkeypatch.setattr(live_module, "END_HARD_CAP_SECONDS", 0.5)
    messages = [tool("c1", "end_call")]
    for _ in range(200):  # 4 s of speech if it were let run
        messages += [audio(b"x"), pause(0.02)]
    start = time.monotonic()
    reason, _, sent, _ = run(FakeLiveSession([messages]), max_seconds=10.0)
    assert reason == "ended_by_agent"
    assert time.monotonic() - start < 2.5
    assert 0 < len(sent) < 200


def test_opening_the_session_gives_up_after_the_time_limit(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from app.call.live import open_live_session
    from app.config import Settings

    closed = []

    @asynccontextmanager
    async def never_connects(**kwargs):
        try:
            await asyncio.sleep(60)
            yield object()
        finally:
            closed.append(True)

    class FakeClient:
        def __init__(self, **kwargs):
            self.aio = SimpleNamespace(live=SimpleNamespace(connect=never_connects))

    monkeypatch.setattr("google.genai.Client", FakeClient)
    settings = Settings(_env_file=None, gcp_project="p", live_connect_seconds=0.05)

    async def scenario():
        async with open_live_session(settings, "instruction"):
            raise AssertionError("the session must not open")

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(scenario())
    assert closed == [True]


def test_a_session_that_opens_in_time_is_used_and_closed(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from app.call.live import open_live_session
    from app.config import Settings

    events = []

    @asynccontextmanager
    async def connects(**kwargs):
        events.append("opened")
        try:
            yield "the-session"
        finally:
            events.append("closed")

    class FakeClient:
        def __init__(self, **kwargs):
            self.aio = SimpleNamespace(live=SimpleNamespace(connect=connects))

    monkeypatch.setattr("google.genai.Client", FakeClient)

    async def scenario():
        async with open_live_session(Settings(_env_file=None, gcp_project="p"), "instruction") as session:
            return session

    # Handed to the call inside the wrapper that resumes a dropped connection.
    assert asyncio.run(scenario())._session == "the-session"
    assert events == ["opened", "closed"]


def test_opening_a_session_logs_its_settings_once(monkeypatch, caplog):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from app.call.live import open_live_session

    seen = []

    @asynccontextmanager
    async def connects(**kwargs):
        seen.append(kwargs["config"])
        yield "the-session"

    class FakeClient:
        def __init__(self, **kwargs):
            self.aio = SimpleNamespace(live=SimpleNamespace(connect=connects))

    monkeypatch.setattr("google.genai.Client", FakeClient)
    settings = Settings(_env_file=None, gcp_project="p", live_voice="Puck")

    async def scenario():
        async with open_live_session(settings, "SECRET INSTRUCTION"):
            pass

    with caplog.at_level(logging.INFO, logger="app.call.live"):
        asyncio.run(scenario())
    lines = [record for record in caplog.records if "live session settings" in record.getMessage()]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO
    assert str(live_settings_snapshot(settings)) in lines[0].getMessage()
    assert "SECRET INSTRUCTION" not in caplog.text
    assert seen[0]["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"] == "Puck"


# The agent sometimes says goodbye and never calls end_call. Once the questions are answered
# and nobody has spoken for a while, the call is ended for it.


class AnsweredTools(FakeTools):
    def __init__(self, done: bool = True) -> None:
        super().__init__()
        self.done = done

    def questions_done(self) -> bool:
        return self.done


@pytest.fixture
def quick_closing(monkeypatch):
    monkeypatch.setattr(live_module, "CLOSING_CHECK_SECONDS", 0.02)
    return {"closing_silence_seconds": 0.3}


def test_a_goodbye_with_no_end_call_still_ends_the_call(quick_closing):
    session = FakeLiveSession([[audio(b"\x00\x00" * 240), said("agent", "Alvida."), turn_complete()]])
    started = time.monotonic()
    reason, tools, _, _ = run(session, tools=AnsweredTools(), max_seconds=5, **quick_closing)
    assert reason == "ended_by_agent"
    assert tools.calls == [("end_call", {"outcome": "completed"})]
    assert 0.6 <= time.monotonic() - started < 3  # two waits: one before the prompt, one after


def test_silence_before_the_questions_are_answered_ends_nothing(quick_closing):
    session = FakeLiveSession([[audio(b"\x00\x00" * 240), turn_complete()]])
    reason, tools, _, _ = run(session, tools=AnsweredTools(done=False), max_seconds=1.0, **quick_closing)
    assert reason == "timeout"
    assert tools.calls == []


def test_the_call_is_not_ended_while_the_last_words_are_still_playing(quick_closing):
    one_second = b"\x00\x00" * 24000  # the model sends speech faster than it is heard
    session = FakeLiveSession([[audio(one_second), turn_complete()]])
    started = time.monotonic()
    reason, _, _, _ = run(session, tools=AnsweredTools(), max_seconds=5, **quick_closing)
    assert reason == "ended_by_agent"
    assert time.monotonic() - started >= 1.3


def test_the_person_speaking_keeps_the_call_open(quick_closing):
    session = FakeLiveSession(
        [[audio(b"\x00\x00" * 240), turn_complete()], [pause(0.2), said("resident", "ek baat aur"), pause(0.2), said("resident", "suniye")]]
    )
    started = time.monotonic()
    reason, _, _, _ = run(session, tools=AnsweredTools(), max_seconds=5, **quick_closing)
    assert reason == "ended_by_agent"
    assert time.monotonic() - started >= 0.6  # two replies 0.2 s apart, then the silence


def test_the_person_speaking_keeps_the_call_open_before_their_words_come_back_as_text(quick_closing):
    session = FakeLiveSession([[audio(b"\x00\x00" * 240), turn_complete()]])
    loud = (3000).to_bytes(2, "little", signed=True) * 320
    speaking = [(0.1 * step, loud) for step in range(1, 9)]  # 0.1 s to 0.8 s, no transcript
    started = time.monotonic()
    reason, _, _, _ = run(session, tools=AnsweredTools(), later=speaking, max_seconds=5, **quick_closing)
    assert reason == "ended_by_agent"
    assert time.monotonic() - started >= 0.8  # the silence counts from the end of their speech


def test_an_agent_that_ends_the_call_itself_is_left_to_it(quick_closing):
    session = FakeLiveSession([[audio(b"bye"), tool("c9", "end_call", outcome="declined"), turn_complete()]])
    reason, tools, _, _ = run(session, tools=AnsweredTools(), **quick_closing)
    assert reason == "ended_by_agent"
    assert tools.calls == [("end_call", {"outcome": "declined"})]


# --- the first greeting is not cut off by what is said over it ---------------------------

VOICE = b"\x01\x02" * 160


def test_without_a_guard_the_persons_audio_is_sent_from_the_first_moment():
    session = FakeLiveSession([])
    run(session, caller=[VOICE], hang_up_after=0.05)
    assert session.sent_audio == [VOICE]


def test_during_the_greeting_silence_of_the_same_length_is_sent_instead():
    session = FakeLiveSession([[audio(b"namaste"), pause(1.0)]])
    reason, _, sent, _ = run(session, caller=[VOICE, VOICE[:100]], hang_up_after=0.1, greeting_guard_seconds=5.0)
    assert session.sent_audio == [bytes(320), bytes(100)]
    assert sent == [b"namaste"]
    assert reason == "caller_hung_up"  # a hang-up is honoured while the guard is up


def test_the_guard_comes_down_when_the_first_turn_completes():
    session = FakeLiveSession([[audio(b"namaste"), pause(0.1), turn_complete()]])
    run(session, caller=[VOICE], later=[(0.5, VOICE)], hang_up_after=0.7, greeting_guard_seconds=5.0)
    assert session.sent_audio == [bytes(320), VOICE]


def test_the_guard_stays_up_until_the_greeting_has_been_heard():
    # The model sends speech faster than it is spoken: the turn is complete at once, but the
    # person is still hearing the greeting, and a "hello" said over it must not get through.
    greeting = bytes(int(0.5 * live_module.MODEL_AUDIO_BYTES_PER_SECOND))
    session = FakeLiveSession([[audio(greeting), turn_complete()]])
    run(session, later=[(0.1, VOICE), (1.0, VOICE)], hang_up_after=1.2, greeting_guard_seconds=5.0)
    assert session.sent_audio == [bytes(320), VOICE]


def test_the_guard_comes_down_after_its_time_even_if_the_greeting_goes_on():
    session = FakeLiveSession([[audio(b"namaste"), pause(1.0)]])
    run(session, caller=[VOICE], later=[(0.5, VOICE)], hang_up_after=0.7, greeting_guard_seconds=0.1)
    assert session.sent_audio == [bytes(320), VOICE]


def test_without_being_told_a_finished_call_is_given_ten_seconds_as_before(monkeypatch):
    monkeypatch.setattr(live_module, "CLOSING_CHECK_SECONDS", 0.02)
    session = FakeLiveSession([[audio(b"\x00\x00" * 240), turn_complete()]])
    reason, tools, _, _ = run(session, tools=AnsweredTools(), max_seconds=1.0)
    assert reason == "timeout" and tools.calls == []


# --- a line that has gone quiet in the middle of a call -----------------------------------
# Heard on a real call (13 October): twice the agent asked a question and the line then stayed
# silent for more than half a minute, until the caller spoke. Nobody speaking for a while, in
# the middle of a call, now prompts the agent, whatever came before the silence.

QUESTION = [audio(b"a question"), turn_complete()]


@pytest.fixture(autouse=True)
def quick_quiet_checks(monkeypatch):
    monkeypatch.setattr(live_module, "QUIET_CHECK_SECONDS", 0.02)


def prompts(session):
    return session.sent_text[1:]


def test_a_line_that_has_gone_quiet_is_prompted():
    session = FakeLiveSession([QUESTION])
    reason, tools, _, _ = run(session, hang_up_after=0.8, quiet_seconds=0.2, quiet_limit=1)
    assert session.sent_text == ["Greet them.", QUIET_PROMPT]
    assert tools.transcript == [("system", QUIET_NOTE)]
    assert reason == "caller_hung_up"
    assert QUIET_PROMPT == (
        "The line has been quiet. Ask briefly whether they can hear you and are all right, and tell "
        "them how to press a button for help if they cannot speak, as your instructions say. Then ask "
        "your last question again in a few words. If you had not finished speaking, finish."
    )
    assert QUIET_NOTE == "[prompted after a silence]"
    kind, fields = tools.events[-1]
    assert kind == "prompt" and fields["sent"] is True and 0.2 <= fields["quiet_seconds"] < 0.8


def test_by_default_a_quiet_line_is_never_prompted():
    session = FakeLiveSession([QUESTION, [pause(0.2), interrupted(), turn_complete()]])
    _, tools, _, _ = run(session, hang_up_after=0.5)
    assert session.sent_text == ["Greet them."]
    assert tools.transcript == []
    assert "prompt" not in [kind for kind, _ in tools.events]


def test_the_wait_starts_when_the_question_has_been_heard_not_when_it_was_generated():
    def turns():
        half_a_second = bytes(int(0.5 * live_module.MODEL_AUDIO_BYTES_PER_SECOND))
        return [[audio(half_a_second), turn_complete()]]

    session = FakeLiveSession(turns())
    run(session, hang_up_after=0.6, quiet_seconds=0.4, quiet_limit=1)  # due 0.9 s into the call
    assert prompts(session) == []
    session = FakeLiveSession(turns())
    run(session, hang_up_after=1.4, quiet_seconds=0.4, quiet_limit=1)
    assert prompts(session) == [QUIET_PROMPT]


def test_the_wait_starts_again_when_the_person_is_heard():
    def turns():
        return [QUESTION, [pause(0.3), said("resident", "ek minute"), turn_complete()]]

    session = FakeLiveSession(turns())
    run(session, hang_up_after=0.6, quiet_seconds=0.5, quiet_limit=1)  # due 0.8 s into the call
    assert prompts(session) == []
    session = FakeLiveSession(turns())
    _, tools, _, _ = run(session, hang_up_after=1.4, quiet_seconds=0.5, quiet_limit=1)
    assert prompts(session) == [QUIET_PROMPT]
    assert tools.transcript == [("resident", "ek minute"), ("system", QUIET_NOTE)]


def tone(amplitude: int, samples: int = 320) -> bytes:
    """16-bit PCM alternating between +amplitude and -amplitude: as loud as speech, or as faint as a line."""
    return b"".join(sample.to_bytes(2, "little", signed=True) for sample in [amplitude, -amplitude] * (samples // 2))


def test_a_person_speaking_is_not_prompted_before_their_words_come_back_as_text():
    # Heard on a phone call (15 October): the caller paused, then gave a long answer; its text
    # came back only at the end, and the prompt went out over them in the middle of it.
    speaking = [(0.1 * step, tone(3000)) for step in range(2, 9)]  # 0.2 s to 0.8 s, no transcript
    session = FakeLiveSession([QUESTION])
    run(session, later=speaking, hang_up_after=0.9, quiet_seconds=0.3, quiet_limit=1)
    assert prompts(session) == []


def test_sound_with_no_words_for_long_is_logged_once_with_its_level(caplog):
    noise = [(0.05 * step, tone(900)) for step in range(2, 30)]  # 0.1 s to 1.45 s, never words
    session = FakeLiveSession([QUESTION])
    with caplog.at_level(logging.WARNING, logger="app.call.live"):
        _, tools, _, _ = run(session, later=noise, hang_up_after=1.5, quiet_seconds=0.2, quiet_limit=1)
    assert prompts(session) == []
    warnings = [r.getMessage() for r in caplog.records if "not quiet" in r.getMessage()]
    assert len(warnings) == 1 and "level 900" in warnings[0]
    assert ("held_by_sound", {"level": 900}) in tools.events


def test_a_faint_line_is_still_a_quiet_one():
    hiss = [(0.1 * step, tone(60)) for step in range(2, 9)]
    session = FakeLiveSession([QUESTION])
    run(session, later=hiss, hang_up_after=0.9, quiet_seconds=0.3, quiet_limit=1)
    assert prompts(session) == [QUIET_PROMPT]


def test_an_agent_that_is_speaking_is_not_prompted():
    turns = [QUESTION] + [[pause(0.15), audio(b"and another thing"), turn_complete()] for _ in range(4)]
    session = FakeLiveSession(turns)
    run(session, hang_up_after=0.6, quiet_seconds=0.4)
    assert prompts(session) == []


def test_after_the_limit_the_line_is_left_alone_until_the_person_speaks():
    def turns():
        return [QUESTION, [pause(1.2), said("resident", "haan"), turn_complete()]]

    session = FakeLiveSession(turns())
    run(session, hang_up_after=1.0, quiet_seconds=0.2, quiet_limit=2, max_seconds=5.0)
    assert prompts(session) == [QUIET_PROMPT] * 2  # at 0.2 s and 0.4 s, then nothing more
    session = FakeLiveSession(turns())
    run(session, hang_up_after=2.2, quiet_seconds=0.2, quiet_limit=2, max_seconds=5.0)
    assert prompts(session) == [QUIET_PROMPT] * 4  # the count started again when they spoke


def test_a_limit_of_nothing_means_no_prompt():
    session = FakeLiveSession([QUESTION])
    run(session, hang_up_after=0.5, quiet_seconds=0.1, quiet_limit=0)
    assert prompts(session) == []


def test_nothing_is_prompted_before_the_agents_first_turn_is_complete():
    session = FakeLiveSession([[audio(b"namaste"), pause(2.0)]])
    run(session, hang_up_after=0.6, quiet_seconds=0.2)
    assert prompts(session) == []


def test_nothing_is_prompted_while_the_greeting_guard_is_up():
    half_a_second = bytes(int(0.5 * live_module.MODEL_AUDIO_BYTES_PER_SECOND))
    session = FakeLiveSession([[audio(half_a_second), turn_complete()]])
    _, tools, _, _ = run(session, hang_up_after=1.2, quiet_seconds=0.2, quiet_limit=1, greeting_guard_seconds=5.0)
    kinds = [kind for kind, _ in tools.events]
    assert prompts(session) == [QUIET_PROMPT]
    assert kinds.index("guard_lifted") < kinds.index("prompt")


def test_an_agent_that_has_ended_the_call_is_not_prompted(monkeypatch):
    monkeypatch.setattr(live_module, "END_GRACE_SECONDS", 0.6)
    session = FakeLiveSession([QUESTION, [tool("c1", "end_call"), audio(b"bye"), interrupted(), turn_complete()]])
    reason, _, _, _ = run(session, quiet_seconds=0.2)
    assert reason == "ended_by_agent"
    assert prompts(session) == []


def test_after_the_questions_a_quiet_line_is_left_to_the_closing_silence():
    session = FakeLiveSession([QUESTION])
    tools = AnsweredTools()
    run(session, tools=tools, hang_up_after=0.7, quiet_seconds=0.1)
    assert prompts(session) == []
    assert tools.transcript == []


@pytest.mark.parametrize("name, args", [("flag_emergency", {"reason": "chest pain"}), ("end_call", {"outcome": "emergency"})])
def test_an_agent_in_the_middle_of_a_tool_call_is_not_prompted(name, args):
    session = FakeLiveSession([QUESTION, [tool("c1", name, **args)]])
    tools = FakeTools(delay=0.9)  # still running when the wait is over, and when the caller hangs up
    run(session, tools=tools, hang_up_after=0.5, quiet_seconds=0.1)
    assert tools.finished == 1
    assert prompts(session) == []
    assert tools.transcript == []


def test_the_wait_starts_again_when_a_tool_call_has_finished():
    session = FakeLiveSession([QUESTION, [tool("c1", "record_answer", question_id="q", value="v")]])
    tools = FakeTools(delay=0.5)
    run(session, tools=tools, hang_up_after=0.8, quiet_seconds=0.5, quiet_limit=1)  # due 1.0 s into the call
    assert prompts(session) == []


def test_nothing_is_sent_while_a_tool_call_from_elsewhere_is_running(quick_closing):
    # The closing silence ends a finished call with a tool call of its own; a prompt must not
    # be sent while that is being written.
    class EndsSlowly(FakeTools):
        def questions_done(self):
            return not self.calls  # so that only the running tool call holds the prompt back

    session = FakeLiveSession([QUESTION])
    tools = EndsSlowly(delay=0.7)
    reason, _, _, _ = run(session, tools=tools, quiet_seconds=0.5, max_seconds=5.0, **quick_closing)
    assert reason == "ended_by_agent"
    assert prompts(session) == [CLOSING_PROMPT]  # asked for its goodbye, and nothing else


def test_a_prompt_the_session_refuses_is_logged_by_type_and_the_call_goes_on(caplog):
    session = FakeLiveSession([QUESTION], reject_prompts=True)
    with caplog.at_level(logging.WARNING, logger="app.call.live"):
        reason, tools, _, _ = run(session, hang_up_after=0.6, quiet_seconds=0.1, quiet_limit=1)
    assert reason == "caller_hung_up"
    assert prompts(session) == []
    assert tools.transcript == []  # nothing was prompted, so nothing is noted
    assert [fields["sent"] for kind, fields in tools.events if kind == "prompt"] == [False]
    assert "ConnectionError" in caplog.text
    assert "refused the prompt" not in caplog.text


def test_a_prompt_that_hangs_does_not_delay_the_end_of_the_call():
    session = FakeLiveSession([QUESTION], hang_prompts=True)
    start = time.monotonic()
    reason, _, _, _ = run(session, hang_up_after=0.5, quiet_seconds=0.05, max_seconds=5.0)
    assert reason == "caller_hung_up"
    assert time.monotonic() - start < 3.0


def test_a_tool_call_in_flight_is_still_waited_for_with_the_new_tasks_running():
    session = FakeLiveSession([QUESTION, [tool("c1", "record_answer", question_id="q", value="v")]])
    tools = FakeTools(delay=0.4)
    reason, _, _, _ = run(
        session, tools=tools, hang_up_after=0.15, quiet_seconds=0.05, greeting_guard_seconds=1.0
    )
    assert tools.finished == 1
    assert reason == "caller_hung_up"
    assert prompts(session) == []  # no prompt while the tool call was running


def test_a_failure_in_the_prompting_never_ends_the_call(caplog):
    class NoSystemLines(FakeTools):
        def add_transcript(self, role, text):
            if role == "system":
                raise RuntimeError("transcript refused")
            super().add_transcript(role, text)

    session = FakeLiveSession([[audio(b"talking"), turn_complete()], [pause(0.5), audio(b"still here"), turn_complete()]])
    with caplog.at_level(logging.WARNING, logger="app.call.live"):
        reason, _, sent, _ = run(session, tools=NoSystemLines(), hang_up_after=0.9, quiet_seconds=0.1)
    assert reason == "caller_hung_up"
    assert sent == [b"talking", b"still here"]  # the call went on after the failure
    assert "RuntimeError" in caplog.text and "transcript refused" not in caplog.text


# --- what happened on the line, and when: never what was said ------------------------------


def events_of(tools, kind):
    return [fields for found, fields in tools.events if found == kind]


def test_each_turn_of_the_agent_is_noted_with_how_long_it_spoke():
    half_a_second = bytes(live_module.MODEL_AUDIO_BYTES_PER_SECOND // 2)
    session = FakeLiveSession(
        [
            [audio(half_a_second), audio(half_a_second), said("agent", "Namaste"), turn_complete()],
            [tool_batch(("a", "set_respondent", {"type": "self"}), ("b", "record_answer", {})), turn_complete()],
            [audio(half_a_second), interrupted(), turn_complete()],
        ]
    )
    _, tools, _, _ = run(session, hang_up_after=0.2)
    assert events_of(tools, "agent_turn") == [
        {"audio_seconds": 1.0, "interrupted": False, "tools": 0},
        {"audio_seconds": 0.0, "interrupted": False, "tools": 2},
        {"audio_seconds": 0.5, "interrupted": True, "tools": 0},
    ]
    assert events_of(tools, "interrupted") == [{"audio_seconds_so_far": 0.5}]


def test_the_start_of_each_reply_is_noted_without_its_words():
    session = FakeLiveSession(
        [
            [said("agent", "Kaisi tabiyat hai?"), said("resident", "theek"), said("resident", " hoon"), turn_complete()],
            [said("agent", "Paani?"), said("resident", "haan"), turn_complete()],
        ]
    )
    _, tools, _, _ = run(session, hang_up_after=0.2)
    assert events_of(tools, "heard") == [{}, {}]


def test_each_tool_call_is_noted_by_name_and_a_refusal_by_a_fixed_reason():
    class Refusing(FakeTools):
        def handle(self, name, args):
            super().handle(name, args)
            return {
                "record_answer": {"error": "no answer heard", "do": "Ask this one question on its own."},
                "set_respondent": {"error": "follow-ups not asked", "ask_next": ["Any dizziness or headache?"]},
                "flag_emergency": {"error": "some new error with theek hoon in it"},
                "end_call": {"result": "recorded", "accepted_after_refusals": 2},
            }.get(name, {"error": "unknown tool 'theek hoon'"})

    names = ["record_answer", "set_respondent", "flag_emergency", "end_call", "theek hoon"]
    args = {"question_id": "feeling", "value": "ok", "quote": "theek hoon"}
    session = FakeLiveSession([[tool(f"c{number}", name, **args)] for number, name in enumerate(names)])
    _, tools, _, _ = run(session, tools=Refusing(), hang_up_after=0.3)
    assert events_of(tools, "tool") == [
        {"name": "record_answer", "question": "feeling", "refused": "no_reply"},
        {"name": "set_respondent", "refused": "follow_ups"},
        {"name": "flag_emergency", "refused": "error"},
        {"name": "end_call", "accepted_after_refusals": 2},
        {"name": "unknown", "refused": "unknown_tool"},
    ]
    assert "theek hoon" not in repr(tools.events)
    # what the agent is told is what the handler said, less the note meant for the timeline
    assert [response.response for response in session.tool_responses][3] == {"result": "recorded"}
    assert session.tool_responses[0].response["do"] == "Ask this one question on its own."


def test_a_question_id_the_script_does_not_have_is_not_copied_into_the_timeline():
    session = FakeLiveSession([[tool("c1", "record_answer", question_id="meri beti ka naam", value="ok")]])
    _, tools, _, _ = run(session, hang_up_after=0.2)
    assert events_of(tools, "tool") == [{"name": "record_answer"}]


def test_the_call_the_closing_silence_ends_is_noted_as_such(quick_closing):
    session = FakeLiveSession([QUESTION])
    _, tools, _, _ = run(session, tools=AnsweredTools(), max_seconds=5, **quick_closing)
    assert events_of(tools, "tool") == [{"name": "end_call", "by": "closing_silence"}]


def test_the_lifting_of_the_greeting_guard_is_noted_with_its_cause():
    session = FakeLiveSession([[audio(b"namaste"), turn_complete()]])
    _, tools, _, _ = run(session, later=[(0.3, VOICE)], hang_up_after=0.5, greeting_guard_seconds=5.0)
    assert events_of(tools, "guard_lifted") == [{"by": "heard"}]
    session = FakeLiveSession([[audio(b"namaste"), pause(2.0)]])
    _, tools, _, _ = run(session, later=[(0.5, VOICE)], hang_up_after=0.7, greeting_guard_seconds=0.1)
    assert events_of(tools, "guard_lifted") == [{"by": "time"}]
    session = FakeLiveSession([[audio(b"namaste"), turn_complete()]])
    _, tools, _, _ = run(session, later=[(0.2, VOICE)], hang_up_after=0.4)
    assert events_of(tools, "guard_lifted") == []  # there was no guard


def test_a_timeline_that_cannot_be_written_does_not_disturb_the_call():
    class NoTimeline(FakeTools):
        def add_event(self, kind, **fields):
            raise RuntimeError("timeline refused")

    session = FakeLiveSession([[audio(b"bye"), said("resident", "haan"), tool("c9", "end_call"), turn_complete()]])
    reason, tools, sent, _ = run(session, tools=NoTimeline())
    assert reason == "ended_by_agent" and sent == [b"bye"]
    assert tools.transcript == [("resident", "haan")]


def test_nothing_in_the_timeline_is_a_word_that_was_said():
    session = FakeLiveSession(
        [
            [audio(b"x" * 4800), said("agent", "Namaste Sunita ji"), turn_complete()],
            [said("resident", "chakkar aa raha hai"), tool("c1", "record_answer", question_id="feeling", value="mild_symptoms", quote="chakkar aa raha hai"), turn_complete()],
            [audio(b"x" * 4800), interrupted(), turn_complete()],
        ]
    )
    _, tools, _, _ = run(session, hang_up_after=0.6, quiet_seconds=0.2, greeting_guard_seconds=0.1)
    assert {kind for kind, _ in tools.events} >= {"agent_turn", "heard", "tool", "interrupted", "prompt"}
    for kind, fields in tools.events:
        for value in fields.values():
            assert isinstance(value, (bool, int, float)) or (isinstance(value, str) and len(value) <= 20), (kind, fields)
    text = repr(tools.events)
    assert "chakkar" not in text and "Namaste" not in text and "Sunita" not in text


# --- how long the person waits for each reply ----------------------------------------------

GREETING = [audio(b"namaste"), turn_complete()]


def test_a_reply_is_noted_with_how_long_after_the_person_it_began(caplog):
    # The person speaks at 0.1 s; the agent's answer is first heard at 1.3 s.
    session = FakeLiveSession([GREETING, [pause(1.3), audio(b"reply"), turn_complete()]])
    with caplog.at_level(logging.INFO, logger="app.call.live"):
        _, tools, _, _ = run(session, later=[(0.1, tone(3000))], hang_up_after=1.5)
    replies = events_of(tools, "reply")  # the greeting followed nobody: it is not a reply
    assert len(replies) == 1 and replies[0]["tool"] is False
    assert replies[0]["gap_s"] == pytest.approx(1.2, abs=0.15)
    logged = re.findall(r"voice reply ([0-9.]+) s after the person stopped \(tool: False\)", caplog.text)
    assert len(logged) == 1 and float(logged[0]) == pytest.approx(1.2, abs=0.15)


def test_a_reply_after_a_tool_call_says_so():
    turn = [pause(0.6), tool("c1", "set_respondent", type="self"), pause(0.7), audio(b"reply"), turn_complete()]
    session = FakeLiveSession([GREETING, turn])
    _, tools, _, _ = run(session, later=[(0.1, tone(3000))], hang_up_after=1.5)
    replies = events_of(tools, "reply")
    assert len(replies) == 1 and replies[0]["tool"] is True
    assert replies[0]["gap_s"] == pytest.approx(1.2, abs=0.15)


def test_the_pickup_time_is_logged_once_with_the_first_audio(caplog):
    session = FakeLiveSession([GREETING, [audio(b"more"), turn_complete()]])
    with caplog.at_level(logging.INFO, logger="app.call.live"):
        run(session, hang_up_after=0.2)
    assert caplog.text.count("voice pickup ") == 1
    assert caplog.text.count("first model audio ") == 1


def test_the_pickup_is_timed_from_the_answer_and_says_how_the_session_opened(caplog):
    # A browser call: answered, then its session opened (here 0.4 s), then the greeting.
    session = FakeLiveSession([[pause(0.1), audio(b"hello"), turn_complete()]])
    session.opened = "opened now"

    async def scenario():
        answered_at = asyncio.get_running_loop().time()
        await asyncio.sleep(0.4)
        audio_in = asyncio.Queue()

        async def nothing(*_):
            pass

        asyncio.get_running_loop().call_later(0.3, audio_in.put_nowait, None)
        await run_call(session, FakeTools(), audio_in, nothing, nothing, "Greet them.", 2.0, answered_at=answered_at)

    with caplog.at_level(logging.INFO, logger="app.call.live"):
        asyncio.run(scenario())
    pickup = re.findall(r"voice pickup ([0-9.]+) s \(opened now\)", caplog.text)
    assert len(pickup) == 1 and float(pickup[0]) == pytest.approx(0.5, abs=0.15)
    ready = re.findall(r"first model audio ([0-9.]+) s after the session was ready", caplog.text)
    assert float(ready[0]) == pytest.approx(0.1, abs=0.1)


def test_a_call_is_timed_from_when_it_was_answered_not_when_its_session_was_ready(store, tz, now, caplog):
    from contextlib import asynccontextmanager

    from app.call.session import conduct_call
    from tests.fakes import make_deps
    from tests.helpers import queue_one_call

    _, call_id, _ = queue_one_call(store, tz, now)
    session = FakeLiveSession([[audio(b"hello"), tool("c1", "end_call", outcome="declined"), turn_complete()]])

    @asynccontextmanager
    async def slow_open(settings, instruction):
        await asyncio.sleep(0.5)  # the session opens after the answer, as on a browser call
        yield session

    async def scenario():
        async def nothing(*_):
            pass

        return await conduct_call(make_deps(store, now, tz, slow_open), call_id, asyncio.Queue(), nothing, nothing)

    with caplog.at_level(logging.INFO, logger="app.call.live"):
        asyncio.run(scenario())
    pickup = re.findall(r"voice pickup ([0-9.]+) s", caplog.text)
    assert len(pickup) == 1 and float(pickup[0]) == pytest.approx(0.5, abs=0.2)


def test_audio_counts_as_the_emergency_line_having_been_said(store, tz, now):
    # Review focus 1: the agent spoke the 108 line, then flagged and ended the call, all before
    # the words of what it said came back as text. Its audio is what the person heard.
    from app.call.tools import CallTools
    from app.dispatch import RecordingQueue, plan_round
    from app.ingest.alerts import record_alert
    from app.ingest.replay import make_replay_alert
    from tests.helpers import add_resident, add_ward

    add_ward(store)
    round_id = record_alert(store, make_replay_alert("d1", "heat", "red", now), tz).id
    add_resident(store, "r1", age=80)
    plan_round(store, RecordingQueue(), round_id, now, tz)
    call_id = f"{round_id}_r1_1"
    store.update("calls", call_id, {"status": "in_progress", "started_at": now})
    tools = CallTools(store, call_id, clock=lambda: now)
    session = FakeLiveSession(
        [[
            audio(b"call 108 now"), tool("c1", "flag_emergency", reason="chest pain"),
            tool("c2", "end_call", outcome="completed"), turn_complete(),
        ]]
    )
    reason, tools, _, _ = run(session, tools=tools)
    assert reason == "ended_by_agent" and tools.ended == "completed"
    assert "emergency_line_first" not in repr(tools.timeline())

# --- a tool result must not make the agent say its piece again ---------------------------
# Seen in the timeline of a real call (13 October), three times: the agent asked a question
# and, in the same turn, called a tool; our response made it start a new turn that asked the
# same question again in other words, which the caller's answer then interrupted.

SPOKEN_ALREADY = {"result": "recorded", "now": ALREADY_SPOKEN}


def responses(session):
    return [response.response for response in session.tool_responses]


def test_a_tool_called_after_speaking_is_answered_with_say_nothing_more():
    session = FakeLiveSession(
        [[audio(b"a question"), tool("c1", "record_answer", question_id="feeling", value="ok"), turn_complete()]]
    )
    _, tools, _, _ = run(session, hang_up_after=0.2)
    assert responses(session) == [SPOKEN_ALREADY]
    assert ALREADY_SPOKEN == (
        "You have already spoken in this turn. Say nothing more now and do not repeat your question. "
        "Wait for the person's reply."
    )
    assert events_of(tools, "tool") == [{"name": "record_answer", "question": "feeling", "after_speech": True}]


def test_a_tool_called_before_speaking_is_answered_as_the_handler_said():
    session = FakeLiveSession(
        [
            [audio(b"a question"), turn_complete()],  # speech in an earlier turn does not count
            [tool("c1", "set_respondent", type="self"), audio(b"the next question"), turn_complete()],
        ]
    )
    _, tools, _, _ = run(session, hang_up_after=0.2)
    assert responses(session) == [{"result": "recorded"}]
    assert events_of(tools, "tool") == [{"name": "set_respondent"}]


def test_each_call_in_a_turn_is_judged_by_what_came_before_it():
    session = FakeLiveSession(
        [[tool("c1", "set_respondent", type="self"), audio(b"a question"), tool("c2", "record_answer"), turn_complete()]]
    )
    run(session, hang_up_after=0.2)
    assert responses(session) == [{"result": "recorded"}, SPOKEN_ALREADY]


@pytest.mark.parametrize("refusal", [
    {"error": "no answer heard", "do": "Ask this one question on its own, stop, and wait."},
    {"error": "follow-ups not asked", "do": "Ask the remaining ones now.", "ask_next": ["Any dizziness or headache?"]},
])
def test_a_refusal_after_speaking_keeps_its_own_instruction(refusal):
    class Refusing(FakeTools):
        def handle(self, name, args):
            super().handle(name, args)
            return dict(refusal)

    session = FakeLiveSession([[audio(b"a question"), tool("c1", "record_answer", question_id="feeling"), turn_complete()]])
    _, tools, _, _ = run(session, tools=Refusing(), hang_up_after=0.2)
    assert responses(session) == [refusal]  # the agent has something to do: nothing tells it to be silent
    assert events_of(tools, "tool")[0]["after_speech"] is True and "refused" in events_of(tools, "tool")[0]


def test_ending_the_call_after_the_goodbye_leaves_nothing_more_to_say():
    session = FakeLiveSession([[audio(b"goodbye"), tool("c1", "end_call", outcome="completed"), turn_complete()]])
    reason, _, _, _ = run(session)
    assert reason == "ended_by_agent"
    assert responses(session) == [{"result": "ending", "now": ALREADY_SPOKEN}]


def test_an_emergency_flag_is_never_answered_with_say_nothing_more():
    # The procedure is: flag first, then tell them to call 108. Whatever the agent said before
    # the flag, the 108 line has still to be spoken.
    class Flagging(FakeTools):
        def handle(self, name, args):
            super().handle(name, args)
            return {"result": "flagged", "say": "Tell them now to call 108."}

    session = FakeLiveSession([[audio(b"oh"), tool("c1", "flag_emergency", reason="chest pain"), turn_complete()]])
    _, tools, _, _ = run(session, tools=Flagging(), hang_up_after=0.2)
    assert responses(session) == [{"result": "flagged", "say": "Tell them now to call 108."}]
    assert events_of(tools, "tool") == [{"name": "flag_emergency", "after_speech": True}]


# --- a finished call is asked for its goodbye before it is ended --------------------------
# Seen in the same timeline: after the last answer the agent said nothing, and five seconds
# later the call was ended for it, with no closing line and no goodbye.

LAST_ANSWER = [said("resident", "nahi"), tool("c1", "record_answer", question_id="help", value="none"), turn_complete()]


def test_a_finished_call_is_first_asked_for_its_goodbye(quick_closing):
    session = FakeLiveSession([LAST_ANSWER])
    tools = AnsweredTools()
    reason, _, _, _ = run(session, tools=tools, hang_up_after=0.8, max_seconds=5, **{**quick_closing, "closing_silence_seconds": 0.5})
    assert reason == "caller_hung_up"  # asked at 0.5 s, and not ended at that moment
    assert prompts(session) == [CLOSING_PROMPT]
    assert CLOSING_PROMPT == (
        "You have every answer you need. Say the closing line and goodbye now, then call end_call "
        'with outcome "completed".'
    )
    assert tools.transcript == [("resident", "nahi"), ("system", CLOSING_NOTE)] and CLOSING_NOTE == "[prompted to close the call]"
    assert events_of(tools, "prompt") == [{"closing": True, "sent": True}]
    assert tools.calls == [("record_answer", {"question_id": "help", "value": "none"})]


def test_an_agent_that_then_says_goodbye_ends_the_call_itself(quick_closing):
    goodbye = [pause(0.4), audio(b"goodbye"), tool("c2", "end_call", outcome="completed"), turn_complete()]
    session = FakeLiveSession([LAST_ANSWER, goodbye])
    tools = AnsweredTools()
    reason, _, sent, _ = run(session, tools=tools, max_seconds=5, **quick_closing)
    assert reason == "ended_by_agent"
    assert sent == [b"goodbye"]
    assert prompts(session) == [CLOSING_PROMPT]
    assert tools.calls[-1] == ("end_call", {"outcome": "completed"}) and len(tools.calls) == 2
    assert events_of(tools, "tool")[-1] == {"name": "end_call", "after_speech": True}  # not by the closing silence


def test_an_agent_that_still_says_nothing_has_the_call_ended_for_it(quick_closing):
    session = FakeLiveSession([LAST_ANSWER])
    tools = AnsweredTools()
    started = time.monotonic()
    reason, _, _, _ = run(session, tools=tools, max_seconds=5, **quick_closing)
    assert reason == "ended_by_agent"
    assert 0.6 <= time.monotonic() - started < 3
    assert prompts(session) == [CLOSING_PROMPT]  # asked once, never twice
    assert tools.calls[-1] == ("end_call", {"outcome": "completed"})
    assert events_of(tools, "tool")[-1] == {"name": "end_call", "by": "closing_silence"}


def test_the_second_wait_starts_when_the_goodbye_has_been_heard(quick_closing):
    one_second = bytes(live_module.MODEL_AUDIO_BYTES_PER_SECOND)
    session = FakeLiveSession([LAST_ANSWER, [pause(0.4), audio(one_second), turn_complete()]])
    started = time.monotonic()
    reason, _, _, _ = run(session, tools=AnsweredTools(), max_seconds=6, **quick_closing)
    assert reason == "ended_by_agent"
    assert time.monotonic() - started >= 1.6  # 0.4 s, the second of speech, then the wait


@pytest.mark.parametrize("fault", ["reject_prompts", "hang_prompts"])
def test_a_closing_prompt_that_fails_still_lets_the_call_be_ended(quick_closing, caplog, fault):
    session = FakeLiveSession([LAST_ANSWER], **{fault: True})
    tools = AnsweredTools()
    with caplog.at_level(logging.WARNING, logger="app.call.live"):
        reason, _, _, _ = run(session, tools=tools, max_seconds=5, **quick_closing)
    assert reason == "ended_by_agent"
    assert tools.calls[-1] == ("end_call", {"outcome": "completed"})
    assert tools.transcript == [("resident", "nahi")]  # nothing was prompted, so nothing is noted
    assert events_of(tools, "prompt") == [{"closing": True, "sent": False}]
    assert ("ConnectionError" if fault == "reject_prompts" else "TimeoutError") in caplog.text
    assert "refused the prompt" not in caplog.text


# --- a call the closing silence ends still gives the 108 line ----------------------------
# From a review: an emergency flagged in a turn with nothing said owes the 108 line, and
# end_call is refused until it is heard; the closing silence's own end_call was refused too,
# and the call ended with no outcome.


class OwingTools(AnsweredTools):
    """Refuses end_call, as CallTools does, while a flag raised with nothing said is owed."""

    def __init__(self) -> None:
        super().__init__()
        self.owed = False

    def handle(self, name, args):
        if name == "flag_emergency":
            self.owed = True  # flagged before anything was said in the turn
        if name == "end_call" and self.owed:
            self.calls.append((name, args))
            return {"error": "say the emergency line first", "say": "Tell them now to call 108."}
        return super().handle(name, args)

    def agent_spoke(self):
        super().agent_spoke()
        self.owed = False

    def end_for_the_agent(self, outcome):
        self.calls.append(("end_for_the_agent", {"outcome": outcome}))
        self.owed = False
        return self.handle("end_call", {"outcome": outcome})


SILENT_FLAG = [said("resident", "seene mein dard"), tool("c1", "flag_emergency", reason="chest pain"), turn_complete()]


def test_a_finished_call_owing_the_108_line_has_the_agent_give_it_before_it_ends(quick_closing):
    # closing prompt at 0.3 s, end_call refused at 0.6 s and the line asked for, heard at 0.9 s
    session = FakeLiveSession([SILENT_FLAG, [pause(0.9), audio(b"call 108"), turn_complete()]])
    tools = OwingTools()
    reason, _, sent, _ = run(session, tools=tools, max_seconds=5, **quick_closing)
    assert reason == "ended_by_agent"
    assert prompts(session) == [CLOSING_PROMPT, EMERGENCY_CLOSING_PROMPT]
    assert "108" in EMERGENCY_CLOSING_PROMPT and 'outcome "emergency"' in EMERGENCY_CLOSING_PROMPT
    assert sent == [b"call 108"]  # heard before the call was ended
    assert tools.ended == "emergency"
    assert ("end_for_the_agent", {"outcome": "emergency"}) not in tools.calls
    assert events_of(tools, "prompt") == [{"closing": True, "sent": True}, {"closing": True, "emergency": True, "sent": True}]
    assert events_of(tools, "tool")[-1] == {"name": "end_call", "by": "closing_silence", "emergency_line": "heard"}
    assert ("system", EMERGENCY_CLOSING_NOTE) in tools.transcript


def test_an_agent_that_never_gives_the_108_line_still_has_the_call_ended_with_an_outcome(quick_closing):
    session = FakeLiveSession([SILENT_FLAG])
    tools = OwingTools()
    started = time.monotonic()
    reason, _, _, _ = run(session, tools=tools, max_seconds=5, **quick_closing)
    assert reason == "ended_by_agent"
    assert 0.9 <= time.monotonic() - started < 4  # three waits: goodbye asked, line asked, ended
    assert prompts(session) == [CLOSING_PROMPT, EMERGENCY_CLOSING_PROMPT]
    assert tools.ended == "emergency"
    assert [name for name, _ in tools.calls] == ["flag_emergency", "end_call", "end_call", "end_for_the_agent", "end_call"]
    assert events_of(tools, "tool")[-1] == {"name": "end_call", "by": "closing_silence", "emergency_line": "not_heard"}


def test_with_no_emergency_owed_the_closing_silence_ends_the_call_as_before(quick_closing):
    session = FakeLiveSession([LAST_ANSWER])
    tools = OwingTools()
    reason, _, _, _ = run(session, tools=tools, max_seconds=5, **quick_closing)
    assert reason == "ended_by_agent"
    assert prompts(session) == [CLOSING_PROMPT]
    assert tools.ended == "completed"
    assert tools.calls == [("record_answer", {"question_id": "help", "value": "none"}), ("end_call", {"outcome": "completed"})]
    assert events_of(tools, "prompt") == [{"closing": True, "sent": True}]
    assert events_of(tools, "tool")[-1] == {"name": "end_call", "by": "closing_silence"}


def test_no_goodbye_is_asked_for_while_a_tool_call_is_running(quick_closing):
    class DoneAtOnce(AnsweredTools):
        def questions_done(self):
            return True

    session = FakeLiveSession([[tool("c1", "record_answer", question_id="help", value="none")]])
    tools = DoneAtOnce()
    tools.delay = 0.8
    run(session, tools=tools, hang_up_after=0.6, max_seconds=5, **quick_closing)
    assert prompts(session) == []


# --- the agent does not ask twice in a row ------------------------------------------------
# Seen in the timeline of real calls (13 October), five times on one of them: the agent asked
# a question and called a tool; the tool's result made it start a new turn that asked the same
# thing again, over the person's answer. Telling it not to did nothing, so the second turn is
# not passed on.

HALF_A_SECOND = bytes(24000)


def question_turn(*then):
    return [audio(b"question"), said("agent", "Kaisi tabiyat hai?"), *then, turn_complete()]


AGAIN = [audio(HALF_A_SECOND), said("agent", "Tabiyat kaisi hai aapki?"), turn_complete()]
RECORDED = tool("c1", "set_respondent", type="self")


def test_a_second_turn_after_a_question_nobody_has_answered_is_not_passed_on():
    session = FakeLiveSession([question_turn(RECORDED), AGAIN])
    _, tools, sent, _ = run(session, hang_up_after=0.3, mute_repeats=True)
    assert sent == [b"question"]
    assert tools.transcript == [("agent", "Kaisi tabiyat hai?")]
    assert events_of(tools, "agent_turn") == [
        {"audio_seconds": 0.0, "interrupted": False, "tools": 1},
        {"audio_seconds": 0.5, "interrupted": False, "tools": 0, "muted": True},
    ]
    assert tools.turns_heard == ["began", "ended"]  # the person heard one turn


def test_unless_asked_for_every_turn_is_passed_on_as_before():
    session = FakeLiveSession([question_turn(RECORDED), AGAIN])
    _, tools, sent, _ = run(session, hang_up_after=0.3)
    assert sent == [b"question", HALF_A_SECOND]
    assert tools.transcript == [("agent", "Kaisi tabiyat hai?"), ("agent", "Tabiyat kaisi hai aapki?")]
    assert "muted" not in events_of(tools, "agent_turn")[1]


def test_a_tool_only_turn_in_between_changes_nothing():
    session = FakeLiveSession([question_turn(), [RECORDED, turn_complete()], AGAIN, AGAIN])
    _, tools, sent, _ = run(session, hang_up_after=0.3, mute_repeats=True)
    assert sent == [b"question"]  # and every further turn, until the person is heard
    assert [event.get("muted", False) for event in events_of(tools, "agent_turn")] == [False, False, True, True]


def test_once_the_person_is_heard_the_agent_is_passed_on_again():
    answered = [said("resident", "theek hoon"), audio(b"next"), said("agent", "Paani piya?"), turn_complete()]
    session = FakeLiveSession([question_turn(RECORDED), AGAIN, answered])
    _, tools, sent, _ = run(session, hang_up_after=0.3, mute_repeats=True)
    assert sent == [b"question", b"next"]
    assert tools.transcript == [("agent", "Kaisi tabiyat hai?"), ("resident", "theek hoon"), ("agent", "Paani piya?")]


def test_a_reply_whose_words_arrive_late_lets_the_turn_through_from_its_start():
    # The service may send the person's words after the agent has begun to answer them.
    late = [audio(b"next-1"), said("agent", "Achha."), said("resident", "theek hoon"), audio(b"next-2"), said("agent", " Paani piya?"), turn_complete()]
    session = FakeLiveSession([question_turn(RECORDED), late])
    _, tools, sent, _ = run(session, hang_up_after=0.3, mute_repeats=True)
    assert sent == [b"question", b"next-1", b"next-2"]
    assert [text for role, text in tools.transcript if role == "agent"] == ["Kaisi tabiyat hai?", "Achha.", " Paani piya?"]
    assert "muted" not in events_of(tools, "agent_turn")[1]


def test_a_repeat_that_the_persons_answer_cuts_off_stays_unheard():
    # What happened on the calls: the person was answering the first question when the second
    # turn began, so the service cut that turn off; their words arrive after that.
    cut_off = [audio(b"again"), interrupted(), said("resident", "theek hoon"), turn_complete()]
    after = [audio(b"next"), said("agent", "Paani piya?"), turn_complete()]
    session = FakeLiveSession([question_turn(RECORDED), cut_off, after])
    _, tools, sent, _ = run(session, hang_up_after=0.3, mute_repeats=True)
    assert sent == [b"question", b"next"]
    assert events_of(tools, "agent_turn")[1] == {"audio_seconds": 0.0, "interrupted": True, "tools": 0, "muted": True}


def test_an_acknowledgement_before_the_tool_call_does_not_silence_the_question_after_it():
    acknowledged = [audio(b"ack"), said("agent", "Achha, sirdard hai."), RECORDED, turn_complete()]
    asked = [audio(b"next"), said("agent", "Paani piya?"), turn_complete()]
    session = FakeLiveSession([acknowledged, asked])
    _, _, sent, _ = run(session, hang_up_after=0.3, mute_repeats=True)
    assert sent == [b"ack", b"next"]  # the turn before did not end in a question


def test_a_question_whose_words_have_not_arrived_silences_nothing():
    session = FakeLiveSession([[audio(b"question"), RECORDED, turn_complete()], AGAIN])
    _, _, sent, _ = run(session, hang_up_after=0.3, mute_repeats=True)
    assert sent == [b"question", HALF_A_SECOND]


def test_a_question_that_was_cut_off_may_be_asked_again():
    session = FakeLiveSession([question_turn(interrupted()), AGAIN])
    _, _, sent, _ = run(session, hang_up_after=0.3, mute_repeats=True)
    assert sent == [b"question", HALF_A_SECOND]


def test_after_a_prompt_the_agent_is_passed_on():
    session = FakeLiveSession([question_turn(), [pause(0.6), *AGAIN]])
    _, tools, sent, _ = run(session, hang_up_after=0.9, mute_repeats=True, quiet_seconds=0.2, quiet_limit=1)
    assert prompts(session) == [QUIET_PROMPT]
    assert sent == [b"question", HALF_A_SECOND]


def test_after_the_closing_prompt_the_goodbye_is_passed_on(quick_closing):
    goodbye = [pause(0.7), audio(b"goodbye"), tool("c2", "end_call", outcome="completed"), turn_complete()]
    session = FakeLiveSession([question_turn(), goodbye])
    waits = {**quick_closing, "closing_silence_seconds": 0.5}  # asked at 0.5 s, ended at 1.0 s if nothing came
    reason, _, sent, _ = run(session, tools=AnsweredTools(), max_seconds=5, mute_repeats=True, **waits)
    assert reason == "ended_by_agent"
    assert sent == [b"question", b"goodbye"]


def test_after_a_refused_tool_call_the_agent_is_passed_on():
    class Refusing(FakeTools):
        def handle(self, name, args):
            super().handle(name, args)
            return {"error": "no answer heard", "do": "Ask this one question on its own."}

    session = FakeLiveSession([question_turn(tool("c1", "record_answer", question_id="feeling", value="ok")), AGAIN])
    _, _, sent, _ = run(session, tools=Refusing(), hang_up_after=0.3, mute_repeats=True)
    assert sent == [b"question", HALF_A_SECOND]  # the refusal told it to do something


def test_after_an_emergency_flag_nothing_is_ever_silenced():
    flagged = [audio(b"what"), said("agent", "Kya hua?"), tool("c1", "flag_emergency", reason="chest pain"), turn_complete()]
    told = [audio(b"108"), said("agent", "Abhi 108 par phone kijiye. Koi paas mein hai?"), turn_complete()]
    session = FakeLiveSession([flagged, told, AGAIN])
    _, _, sent, _ = run(session, hang_up_after=0.3, mute_repeats=True)
    assert sent == [b"what", b"108", HALF_A_SECOND]


def test_a_turn_that_is_not_passed_on_moves_no_clock():
    two_seconds = bytes(2 * live_module.MODEL_AUDIO_BYTES_PER_SECOND)
    session = FakeLiveSession([question_turn(), [audio(two_seconds), said("agent", "Kaisi hai tabiyat?"), turn_complete()]])
    _, tools, sent, _ = run(session, hang_up_after=0.9, mute_repeats=True, quiet_seconds=0.3, quiet_limit=1)
    assert sent == [b"question"]
    assert prompts(session) == [QUIET_PROMPT]  # 0.3 s after the question, not after two unheard seconds


def test_a_goodbye_that_was_not_passed_on_does_not_end_the_call(monkeypatch):
    monkeypatch.setattr(live_module, "END_GRACE_SECONDS", 0.4)
    unheard = [audio(b"bye"), tool("c2", "end_call", outcome="completed"), turn_complete()]
    session = FakeLiveSession([question_turn(), unheard])
    started = time.monotonic()
    reason, _, sent, _ = run(session, mute_repeats=True, max_seconds=5)
    assert reason == "ended_by_agent" and sent == [b"question"]
    assert time.monotonic() - started >= 0.4  # ended by the grace, not at once as a spoken goodbye would


# --- the agent's own voice is not a reply -------------------------------------------------
# On the same calls some "resident" lines were the agent's own words, coming back down the
# line, and answers were recorded straight after them.

WATER = "Kya aaj aapne paani piya hai?"


def test_the_agents_own_words_coming_back_are_not_kept_or_counted():
    session = FakeLiveSession(
        [
            [audio(b"q"), said("agent", WATER), turn_complete()],
            [said("resident", "kya aaj"), said("resident", " aapne paani"), said("resident", " piya hai"), turn_complete()],
        ]
    )
    _, tools, _, _ = run(session, hang_up_after=0.3)
    assert tools.transcript == [("agent", WATER)]
    assert events_of(tools, "heard") == [{}, {"echo": True}]  # heard, then recognised


def test_a_whole_line_of_echo_in_one_piece_is_not_kept_either():
    session = FakeLiveSession(
        [
            [said("resident", "haan ji"), audio(b"q"), said("agent", WATER), turn_complete()],
            [said("resident", "Kya aaj aapne paani piya hai"), turn_complete()],
        ]
    )
    _, tools, _, _ = run(session, hang_up_after=0.3)
    assert tools.transcript == [("resident", "haan ji"), ("agent", WATER)]  # the reply before it is untouched


def test_a_reply_that_reuses_the_questions_words_is_kept():
    session = FakeLiveSession(
        [[audio(b"q"), said("agent", WATER), turn_complete()], [said("resident", "haan,"), said("resident", " paani piya hai"), turn_complete()]]
    )
    _, tools, _, _ = run(session, hang_up_after=0.3)
    assert tools.transcript == [("agent", WATER), ("resident", "haan,"), ("resident", " paani piya hai")]
    assert events_of(tools, "heard") == [{}]


def test_an_echo_that_goes_on_in_the_persons_own_words_is_a_reply_after_all():
    session = FakeLiveSession(
        [[audio(b"q"), said("agent", WATER), turn_complete()], [said("resident", "aapne paani piya"), said("resident", " nahi abhi tak nahi piya"), turn_complete()]]
    )
    _, tools, _, _ = run(session, hang_up_after=0.3)
    assert tools.transcript == [("agent", WATER), ("resident", "aapne paani piya nahi abhi tak nahi piya")]


def test_an_echo_does_not_count_as_the_person_being_heard():
    echo = [pause(0.4), said("resident", "kya aaj aapne paani piya hai"), turn_complete()]
    session = FakeLiveSession([[audio(b"q"), said("agent", WATER), turn_complete()], echo, AGAIN])
    _, tools, sent, _ = run(session, hang_up_after=0.8, mute_repeats=True, quiet_seconds=0.5, quiet_limit=1)
    assert sent == [b"q"]  # still nobody has answered: the second turn is not passed on
    assert prompts(session) == [QUIET_PROMPT]  # and the quiet clock ran from the question, not from the echo


def test_the_greeting_is_remembered_for_the_whole_call_and_other_turns_for_a_minute(monkeypatch):
    monkeypatch.setattr(live_module, "ECHO_WINDOW_SECONDS", 0.2)
    greeting = "Namaste, main zila swasthya karyalaya se bol rahi hoon."
    session = FakeLiveSession(
        [
            [audio(b"g"), said("agent", greeting), turn_complete()],
            [audio(b"q"), said("agent", WATER), turn_complete()],
            [pause(0.5), said("resident", "main zila swasthya karyalaya se bol rahi hoon"), turn_complete()],
            [audio(b"x"), said("agent", "Achha."), turn_complete()],
            [said("resident", "kya aaj aapne paani piya hai"), turn_complete()],
        ]
    )
    _, tools, _, _ = run(session, hang_up_after=0.8)
    heard = [text for role, text in tools.transcript if role == "resident"]
    assert heard == ["kya aaj aapne paani piya hai"]  # too long ago to be its echo; the greeting never is
    assert live_module.ECHO_WINDOW_SECONDS == 0.2


def test_the_agents_words_are_compared_for_a_minute():
    assert live_module.ECHO_WINDOW_SECONDS == 60.0


@pytest.fixture(autouse=True)
def no_shared_client_between_tests(monkeypatch):
    # The voice model's client is shared by every call; a test that replaces it must get its own.
    monkeypatch.setattr(live_module, "_clients", {})


@pytest.fixture
def counting_client(monkeypatch):
    """Counts the Vertex clients made; each connects at once to a session that opens."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    made = []

    @asynccontextmanager
    async def connects(**kwargs):
        yield object()

    class FakeClient:
        def __init__(self, **kwargs):
            made.append(kwargs)
            self.aio = SimpleNamespace(live=SimpleNamespace(connect=connects))

    monkeypatch.setattr("google.genai.Client", FakeClient)
    monkeypatch.setattr(live_module, "_clients", {})
    return made


def test_every_call_shares_one_client(counting_client):
    from app.call.live import open_live_session

    settings = Settings(_env_file=None, gcp_project="p")

    async def two_calls():
        for _ in range(2):
            async with open_live_session(settings, "instruction"):
                pass

    asyncio.run(two_calls())
    assert len(counting_client) == 1  # a client per call paid its set-up again: about 1.3 s


def test_warm_up_opens_and_closes_a_session_and_never_raises(counting_client, monkeypatch):
    from app.call.live import warm_up

    settings = Settings(_env_file=None, gcp_project="p")
    asyncio.run(warm_up(settings))
    assert len(counting_client) == 1

    def broken(**kwargs):
        raise RuntimeError("no network")

    monkeypatch.setattr(live_module, "_clients", {})
    monkeypatch.setattr("google.genai.Client", broken)
    asyncio.run(warm_up(settings))  # a failed warm-up is logged, never raised: the first call simply pays


# --- the language the voice model is told -----------------------------------------------


@pytest.mark.parametrize(
    "language, code", [("hi", "hi-IN"), ("mwr", "hi-IN"), ("en", "en-IN"), ("mr", "mr-IN")]
)
def test_the_residents_language_sets_the_speech_and_transcription_language(language, code):
    config = live_config(Settings(_env_file=None), "INSTRUCTION", language)
    assert config["speech_config"]["language_code"] == code
    assert config["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"] == "Kore"
    assert config["input_audio_transcription"] == {"language_codes": [code]}
    assert config["output_audio_transcription"] == {}


def test_a_language_without_a_vertex_code_sends_none():
    for language in ("gu", None):
        config = live_config(Settings(_env_file=None), "INSTRUCTION", language)
        assert "language_code" not in config["speech_config"]
        assert config["input_audio_transcription"] == {}


def test_the_language_codes_can_be_switched_off():
    settings = Settings(_env_file=None, live_language_codes=False)
    config = live_config(settings, "INSTRUCTION", "hi")
    assert "language_code" not in config["speech_config"]
    assert config["input_audio_transcription"] == {}
    # With no voice either, there is no speech_config at all.
    no_voice = Settings(_env_file=None, live_voice="", live_language_codes=False)
    assert "speech_config" not in live_config(no_voice, "I", "hi")
    assert live_config(Settings(_env_file=None, live_voice=""), "I", "hi")["speech_config"] == {"language_code": "hi-IN"}


def test_a_resumption_handle_reaches_the_config_and_resumption_can_be_switched_off():
    assert live_config(Settings(_env_file=None), "I", resume_handle="h1")["session_resumption"] == {"handle": "h1"}
    assert "session_resumption" not in live_config(Settings(_env_file=None, live_resume=False), "I", resume_handle="h1")


def test_the_sdk_accepts_the_language_and_resumption_config():
    from google.genai import types

    parsed = types.LiveConnectConfig.model_validate(live_config(Settings(_env_file=None), "I", "mwr", "h1"))
    assert parsed.speech_config.language_code == "hi-IN"
    assert parsed.input_audio_transcription.language_codes == ["hi-IN"]
    assert parsed.session_resumption.handle == "h1"


def test_the_new_settings_have_their_defaults_and_refuse_bad_values():
    settings = Settings(_env_file=None)
    assert settings.live_language_codes is True and settings.live_resume is True
    assert settings.live_answer_scheduling == "default"
    assert settings.live_pre_open_seconds == 60.0
    with pytest.raises(ValueError):
        Settings(_env_file=None, live_answer_scheduling="quiet")
    with pytest.raises(ValueError):
        Settings(_env_file=None, live_pre_open_seconds=-1)


# --- the scheduling of tool results, and never an empty one ------------------------------


class RefusingTools(FakeTools):
    """Refuses record_answer, and returns nothing at all for set_respondent."""

    def handle(self, name, args):
        result = super().handle(name, args)
        if name == "record_answer":
            return {"error": "no answer heard"}
        if name == "set_respondent":
            return {}
        return result


def schedulings(session):
    return {(response.name, response.id): response.scheduling for response in session.tool_responses}


def answering_session():
    return FakeLiveSession(
        [
            [said("resident", " haan"), tool("c1", "set_respondent", type="self"), turn_complete()],
            [said("resident", " theek"), tool("c2", "record_answer", question_id="feeling", value="ok"), turn_complete()],
            [tool("c3", "flag_emergency", reason="x"), turn_complete()],
            [audio(b"bye"), tool("c4", "end_call", outcome="completed"), turn_complete()],
        ]
    )


@pytest.mark.parametrize("scheduling, expected", [("silent", "SILENT"), ("when_idle", "WHEN_IDLE")])
def test_accepted_answers_are_sent_with_the_chosen_scheduling(scheduling, expected):
    from google.genai import types

    session = answering_session()
    run(session, answer_scheduling=scheduling)
    wanted = types.FunctionResponseScheduling(expected)
    assert schedulings(session) == {
        ("set_respondent", "c1"): wanted,
        ("record_answer", "c2"): wanted,
        ("flag_emergency", "c3"): None,  # the 108 line must be spoken
        ("end_call", "c4"): None,
    }


def test_by_default_no_scheduling_is_sent():
    session = answering_session()
    run(session)
    assert set(schedulings(session).values()) == {None}


def test_the_scheduling_is_taken_from_the_session_when_not_given():
    session = answering_session()
    session.answer_scheduling = "silent"
    run(session)
    assert schedulings(session)[("record_answer", "c2")] is not None


def test_a_refusal_is_never_silent_and_an_empty_result_is_never_sent():
    session = answering_session()
    run(session, tools=RefusingTools(), answer_scheduling="silent")
    by_name = {response.name: response for response in session.tool_responses}
    assert by_name["record_answer"].scheduling is None  # the agent must act on the refusal
    assert by_name["record_answer"].response == {"error": "no answer heard"}
    assert by_name["set_respondent"].response == live_module.EMPTY_RESULT
    assert by_name["set_respondent"].scheduling is not None
    assert all(response.response for response in session.tool_responses)


def test_the_sdk_sends_the_scheduling_with_the_function_response():
    response = live_module._function_response("c1", "record_answer", {"result": "recorded"}, "silent")
    assert response.model_dump(exclude_none=True, mode="json") == {
        "id": "c1", "name": "record_answer", "response": {"result": "recorded"}, "scheduling": "SILENT",
    }
    assert live_module._function_response("c1", "end_call", None, "silent").response == live_module.EMPTY_RESULT


# --- a dropped connection is resumed once ------------------------------------------------


def resumable(handle="h1"):
    from types import SimpleNamespace

    return SimpleNamespace(
        data=None, server_content=None, tool_call=None,
        session_resumption_update=SimpleNamespace(resumable=True, new_handle=handle),
    )


def dropped(code=1006):
    from google.genai import errors

    return errors.APIError(code, {"message": "connection closed"})


def resuming(first, second, reopened):
    async def reopen(handle):
        reopened.append(handle)
        return second

    return live_module.ResumableSession(first, reopen)


def test_a_connection_that_drops_while_receiving_is_resumed_and_the_call_goes_on(caplog):
    first = FakeLiveSession([[resumable("h1"), audio(b"one"), resumable("h2"), dropped()]])
    second = FakeLiveSession([[audio(b"two"), tool("c9", "end_call", outcome="completed"), turn_complete()]])
    reopened = []
    session = resuming(first, second, reopened)
    with caplog.at_level(logging.INFO, logger="app.call.live"):
        reason, tools, sent, _ = run(session)
    assert reopened == ["h2"]  # the latest handle
    assert reason == "ended_by_agent" and sent == [b"one", b"two"]
    assert second.tool_responses and session.resumed == 1
    assert "voice session resumed" in caplog.text


def test_a_connection_that_drops_while_sending_is_resumed_and_the_audio_sent_again():
    closed = type("ConnectionClosedError", (Exception,), {})

    class DropsOnSend(FakeLiveSession):
        async def send_realtime_input(self, **kwargs):
            raise closed()

    first = DropsOnSend([[resumable("h1"), turn_complete()]])
    second = FakeLiveSession([])
    reopened = []
    session = resuming(first, second, reopened)
    reason, _, _, _ = run(session, later=[(0.05, b"\x01\x02")], hang_up_after=0.2)
    assert reopened == ["h1"]
    assert second.sent_audio == [b"\x01\x02"]
    assert reason == "caller_hung_up"


def test_a_drop_without_a_handle_or_a_second_drop_ends_the_call_as_an_error(caplog):
    reopened = []
    no_handle = resuming(FakeLiveSession([[audio(b"one"), dropped()]]), FakeLiveSession([]), reopened)
    with caplog.at_level(logging.WARNING, logger="app.call.live"):
        assert run(no_handle)[0] == "error"
    assert reopened == [] and "cannot be resumed (no handle)" in caplog.text

    twice = resuming(
        FakeLiveSession([[resumable(), dropped()]]), FakeLiveSession([[resumable("h2"), dropped()]]), reopened
    )
    assert run(twice)[0] == "error"
    assert reopened == ["h1"]  # once only


def test_an_error_that_is_not_a_dropped_connection_is_never_resumed():
    reopened = []
    for error in (dropped(400), dropped(500), RuntimeError("stream broke")):
        session = resuming(FakeLiveSession([[resumable(), error]]), FakeLiveSession([]), reopened)
        assert run(session)[0] == "error"
    assert reopened == []


def test_the_notice_that_the_connection_will_close_is_logged(caplog):
    from types import SimpleNamespace

    going = SimpleNamespace(data=None, server_content=None, tool_call=None, go_away=SimpleNamespace(time_left="50s"))
    session = live_module.ResumableSession(
        FakeLiveSession([[going, audio(b"x"), tool("c9", "end_call"), turn_complete()]])
    )
    with caplog.at_level(logging.WARNING, logger="app.call.live"):
        assert run(session)[0] == "ended_by_agent"
    assert "will close this connection in 50s" in caplog.text
