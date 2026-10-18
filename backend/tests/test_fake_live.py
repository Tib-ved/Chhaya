import asyncio

import pytest

from app.call.fake_live import DEFAULT_SCRIPT, SCRIPTS, open_fake_session
from app.call.session import conduct_call
from app.config import Settings, check_settings
from app.demo_cards import CARDS
from app.triage import ALLOWED_VALUES
from app.visitor import create_visitor_call
from tests.fakes import make_deps
from tests.helpers import queue_one_call, seeded_demo

EXPECTED = {
    "alone_dizzy": ("amber", {"A1", "A2", "A4"}),
    "must_work": ("amber", {"A7"}),
    "family_answers": ("amber", {"A1", "A10"}),
    "emergency": ("red", {"R1", "R2", "A12"}),
}


def fast_settings():
    return Settings(
        _env_file=None, internal_secret="s3cret", max_call_seconds=20,
        fake_live=True, fake_live_step_seconds=0, transcript_save_seconds=0,
    )


def run_call(deps, call_id):
    async def scenario():
        sent = []

        async def send_audio(pcm):
            sent.append(pcm)

        async def clear_audio():
            pass

        status = await conduct_call(deps, call_id, asyncio.Queue(), send_audio, clear_audio)
        return status, sent

    return asyncio.run(scenario())


def test_there_is_a_script_for_every_card():
    assert set(SCRIPTS) == set(CARDS)


@pytest.mark.parametrize("script", [*SCRIPTS.values(), DEFAULT_SCRIPT])
def test_every_scripted_answer_is_one_the_rules_know(script):
    for step in script:
        if step[0] == "tool" and step[1] == "record_answer":
            assert step[2]["value"] in ALLOWED_VALUES[step[2]["question_id"]]
    assert script[-1][:2] == ("tool", "end_call")


@pytest.mark.parametrize("card_id", sorted(EXPECTED))
def test_each_card_plays_through_to_its_expected_tier(store, tz, now, card_id):
    round_id = seeded_demo(store, now, tz)
    settings = fast_settings()
    made = create_visitor_call(store, settings, card_id, "hi", "visitor-aaaaaaaaaaaa", "203.0.113.7", now, tz)
    deps = make_deps(store, now, tz, open_fake_session, settings=settings)

    status, sent = run_call(deps, made["call_id"])

    assert status == "completed"
    assert sent, "the scripted agent sends audio, so the page shows it speaking"
    call = store.get("calls", made["call_id"])
    case = store.get("cases", f"{round_id}_{call['resident_id']}")
    tier, reasons = EXPECTED[card_id]
    assert case["tier"] == tier
    assert set(case["reasons"]) == reasons
    assert {entry["role"] for entry in call["transcript"]} == {"agent", "resident"}
    assert CARDS[card_id]["name"] in call["transcript"][0]["text"]  # the greeting asks for them by name


def test_a_resident_who_is_not_a_card_gets_the_default_script(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    deps = make_deps(store, now, tz, open_fake_session, queue, settings=fast_settings())
    status, _ = run_call(deps, call_id)
    assert status == "completed"
    assert store.get("cases", f"{round_id}_r1")["tier"] == "green"


def test_the_fake_agent_is_refused_on_cloud_run():
    settings = Settings(
        _env_file=None, internal_secret="a-long-random-value-not-in-any-repository",
        allow_open_access=True, fake_live=True,
    )
    check_settings(settings, env={})
    with pytest.raises(RuntimeError, match="FAKE_LIVE"):
        check_settings(settings, env={"K_SERVICE": "chhaya"})


@pytest.mark.parametrize("script", [*SCRIPTS.values(), DEFAULT_SCRIPT])
def test_the_scripted_agent_asks_one_question_in_a_turn(script):
    for step in script:
        if step[0] == "agent":
            assert step[1].count("?") <= 1, step[1]


@pytest.mark.parametrize("card_id", sorted(EXPECTED))
def test_no_scripted_answer_is_held_back_for_a_follow_up_nobody_asked(store, tz, now, card_id):
    seeded_demo(store, now, tz)
    settings = fast_settings()
    made = create_visitor_call(store, settings, card_id, "hi", "visitor-aaaaaaaaaaaa", "203.0.113.7", now, tz)
    deps = make_deps(store, now, tz, open_fake_session, settings=settings)
    run_call(deps, made["call_id"])
    call = store.get("calls", made["call_id"])
    recorded = [step[2]["question_id"] for step in SCRIPTS[card_id] if step[0] == "tool" and step[1] == "record_answer"]
    assert set(call["answers"]) == set(recorded)
    assert not [event for event in call["timeline"] if event.get("refused")]


def test_the_default_script_asks_the_follow_ups_before_it_records_ok(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    deps = make_deps(store, now, tz, open_fake_session, queue, settings=fast_settings())
    run_call(deps, call_id)
    call = store.get("calls", call_id)
    assert call["answers"]["feeling"]["value"] == "ok"
    assert not [event for event in call["timeline"] if event.get("refused")]
