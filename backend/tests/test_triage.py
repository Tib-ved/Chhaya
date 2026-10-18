import random

import pytest

from app import triage as triage_module
from app.models import MAX_ATTEMPTS
from app.triage import (
    ALLOWED_VALUES, PROVISIONAL_EXCLUDED, REASON_LABELS, TIER_ORDER, TriageInput, rule_tier,
    rules_so_far, triage,
)


def run(answers=None, **kw):
    kw.setdefault("age", 60)
    kw.setdefault("has_conditions", False)
    kw.setdefault("conversation", True)
    return triage(TriageInput(answers=answers or {}, **kw))


HEALTHY = {
    "feeling": "ok", "fluids_food": "enough", "company": "with_someone",
    "shelter": "adequate", "exposure": "staying_in", "help": "none",
}


def test_healthy_resident_is_green():
    result = run(HEALTHY)
    assert result.tier == "green"
    assert result.reasons == ("G1",)


@pytest.mark.parametrize(
    "answers, kw, rule",
    [
        ({}, {"emergency": True}, "R1"),
        ({"feeling": "severe_symptoms"}, {}, "R2"),
        ({"feeling": "ok", "help": "urgent"}, {}, "R3"),
        ({"feeling": "ok", "fluids_food": "none", "company": "alone"}, {}, "R4"),
    ],
)
def test_red_rules(answers, kw, rule):
    result = run(answers, **kw)
    assert result.tier == "red"
    assert rule in result.reasons


@pytest.mark.parametrize(
    "answers, kw, rule",
    [
        ({"feeling": "mild_symptoms"}, {}, "A1"),
        ({"feeling": "unclear"}, {}, "A1"),
        ({"feeling": "ok", "fluids_food": "low"}, {}, "A2"),
        ({"feeling": "ok", "shelter": "none"}, {}, "A3"),
        ({"feeling": "ok", "company": "alone"}, {"age": 70}, "A4"),
        ({"feeling": "ok", "medicine": "out"}, {}, "A5"),
        ({"feeling": "ok", "medicine": "running_low"}, {}, "A5"),
        ({"feeling": "ok", "help": "wants_visit"}, {}, "A6"),
        ({"feeling": "ok", "exposure": "must_go_out"}, {"age": 65}, "A7"),
        ({"feeling": "ok", "exposure": "must_go_out"}, {"has_conditions": True}, "A7"),
        ({}, {"conversation": False, "failed_attempts": 3}, "A8"),
        ({"fluids_food": "enough"}, {}, "A9"),
        ({"feeling": "unclear"}, {"respondent": "family"}, "A10"),
        ({}, {"respondent": "other"}, "A10"),
    ],
)
def test_amber_rules(answers, kw, rule):
    result = run(answers, **kw)
    assert result.tier == "amber"
    assert rule in result.reasons


def test_alone_under_70_is_not_a4():
    assert "A4" not in run({"feeling": "ok", "company": "alone"}, age=69).reasons


def test_must_go_out_young_and_healthy_is_green():
    assert run({**HEALTHY, "exposure": "must_go_out"}, age=40).tier == "green"


def test_red_wins_and_keeps_amber_reasons():
    result = run({"feeling": "severe_symptoms", "shelter": "none"})
    assert result.tier == "red"
    assert set(result.reasons) >= {"R2", "A3"}


def test_unrecognised_value_is_amber():
    result = run({**HEALTHY, "feeling": "fine i guess"})
    assert result.tier == "amber"
    assert "A11" in result.reasons


def test_every_single_bad_answer_is_never_green():
    for question in ALLOWED_VALUES:
        result = run({**HEALTHY, question: "???"})
        assert result.tier != "green", question


def test_every_reason_has_a_label():
    for rule in ["R1", "R2", "R3", "R4", "G1"] + [f"A{n}" for n in range(1, 13)]:
        assert REASON_LABELS[rule]


def test_partial_call_is_amber_with_a12():
    result = run({"feeling": "ok"})
    assert result.tier == "amber"
    assert "A12" in result.reasons


def test_full_healthy_call_is_still_green_without_a12():
    result = run(HEALTHY)
    assert result.tier == "green"
    assert result.reasons == ("G1",)


def test_unreached_after_three_attempts_has_only_a8():
    result = run({}, conversation=False, failed_attempts=3)
    assert result.tier == "amber"
    assert result.reasons == ("A8",)


@pytest.mark.parametrize("attempts", [0, 2])
def test_unreached_with_attempts_remaining_is_refused(attempts):
    with pytest.raises(ValueError):
        run({}, conversation=False, failed_attempts=attempts)


def test_missing_exposure_answer_is_amber_with_a12():
    answers = {q: v for q, v in HEALTHY.items() if q != "exposure"}
    result = run(answers)
    assert result.tier == "amber"
    assert result.reasons == ("A12",)


def test_resident_with_conditions_must_be_asked_about_medicine():
    result = run(HEALTHY, has_conditions=True)
    assert result.tier == "amber"
    assert result.reasons == ("A12",)
    assert run({**HEALTHY, "medicine": "has"}, has_conditions=True).tier == "green"


def test_resident_without_conditions_needs_no_medicine_answer():
    result = run(HEALTHY, has_conditions=False)
    assert result.tier == "green"
    assert result.reasons == ("G1",)


@pytest.mark.parametrize("bad", [["ok"], None, 3, {"value": "ok"}])
@pytest.mark.parametrize("question", sorted(ALLOWED_VALUES))
def test_non_string_answer_value_is_unrecognised_not_an_error(question, bad):
    result = run({**HEALTHY, "medicine": "has", question: bad})
    assert result.tier == "amber"
    assert "A11" in result.reasons


def test_triage_uses_the_shared_attempt_limit():
    assert triage_module.MAX_ATTEMPTS is MAX_ATTEMPTS
    assert not hasattr(triage_module, "MAX_ATTEMPTS_BEFORE_UNREACHABLE")
    with pytest.raises(ValueError):
        run({}, conversation=False, failed_attempts=MAX_ATTEMPTS - 1)
    assert run({}, conversation=False, failed_attempts=MAX_ATTEMPTS).reasons == ("A8",)


def test_nothing_recorded_yet_shows_no_rules_and_no_tier():
    so_far = rules_so_far(age=72, has_conditions=True, answers={})
    assert so_far.tier is None
    assert so_far.reasons == ()


def test_rules_appear_as_answers_arrive():
    so_far = rules_so_far(72, True, {"feeling": "mild_symptoms", "fluids_food": "low", "company": "alone"})
    assert so_far.tier == "amber"
    assert so_far.reasons == ("A1", "A2", "A4")


def test_an_emergency_is_red_at_once():
    assert rules_so_far(68, False, {}, emergency=True).tier == "red"
    assert rules_so_far(68, False, {}, emergency=True).reasons == ("R1",)


def test_family_on_the_line_is_not_a_rule_until_feeling_is_recorded():
    assert rules_so_far(80, False, {}, respondent="family").reasons == ()
    unclear = rules_so_far(80, False, {"feeling": "unclear"}, respondent="family")
    assert unclear.reasons == ("A1", "A10")


def test_rule_tier():
    assert [rule_tier(r) for r in ("R4", "A12", "G1")] == ["red", "amber", "green"]


def test_the_provisional_tier_is_never_above_the_final_tier():
    rng = random.Random(3)
    questions = sorted(ALLOWED_VALUES)
    for _ in range(600):
        answers = {}
        for question in rng.sample(questions, rng.randint(0, len(questions))):
            values = sorted(ALLOWED_VALUES[question]) + ["???"]
            answers[question] = rng.choice(values)
        age = rng.choice([40, 66, 72, 88])
        has_conditions = rng.random() < 0.5
        emergency = rng.random() < 0.1
        respondent = rng.choice(["unknown", "self", "family", "other"])

        so_far = rules_so_far(age, has_conditions, answers, emergency, respondent)
        final = triage(TriageInput(
            age=age, has_conditions=has_conditions, answers=answers, emergency=emergency,
            respondent=respondent, conversation=True,
        ))

        assert so_far.tier in (None, "amber", "red")
        assert not PROVISIONAL_EXCLUDED & set(so_far.reasons)
        assert set(so_far.reasons) <= set(final.reasons), (answers, respondent)
        assert TIER_ORDER[so_far.tier or "green"] <= TIER_ORDER[final.tier]
