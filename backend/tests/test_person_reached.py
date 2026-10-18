import pytest

from app.dispatch import PERSON_OUTCOMES, person_was_reached


def test_a_fresh_call_document_reached_nobody():
    assert person_was_reached({}) is False
    assert person_was_reached(
        {"respondent": "unknown", "answers": {}, "emergency": None, "end_outcome": None}
    ) is False


def test_the_five_person_outcomes():
    assert PERSON_OUTCOMES == {"completed", "declined", "wrong_person", "unable_to_understand", "emergency"}


@pytest.mark.parametrize(
    "call",
    [
        {"end_outcome": "no_person"},
        {"answers": {}},
        {"answers": None},
        {"emergency": None},
        {"emergency": {"flagged": False, "reason": "", "at": None}},
        {"respondent": "unknown"},
        {"end_outcome": None},
        {"end_outcome": "something_else"},
    ],
)
def test_no_evidence_of_a_person(call):
    assert person_was_reached(call) is False


@pytest.mark.parametrize(
    "call",
    [
        *({"respondent": kind} for kind in ("self", "family", "other")),
        {"answers": {"feeling": {"value": "ok", "quote": "", "at": None}}},
        {"emergency": {"flagged": True, "reason": "fainted", "at": None}},
        *({"end_outcome": outcome} for outcome in sorted(PERSON_OUTCOMES)),
    ],
)
def test_each_single_piece_of_evidence_is_enough(call):
    assert person_was_reached(call) is True
