import pytest

from app.simulate import CallAlreadyFinished, apply_outcome
from tests.helpers import queue_one_call


def test_it_writes_what_was_learned_then_records_the_outcome(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    stored = apply_outcome(
        store, queue, call_id, "completed", now, tz,
        answers={"feeling": "mild_symptoms"}, quotes={"feeling": "thoda chakkar"}, respondent="self",
    )
    assert stored == "completed"
    call = store.get("calls", call_id)
    assert call["answers"]["feeling"] == {"value": "mild_symptoms", "quote": "thoda chakkar", "at": now}
    assert store.get("cases", f"{round_id}_r1")["tier"] == "amber"


def test_an_emergency_reason_flags_the_call(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    apply_outcome(store, queue, call_id, "completed", now, tz, emergency_reason="collapsed")
    assert store.get("calls", call_id)["emergency"]["flagged"] is True
    assert store.get("cases", f"{round_id}_r1")["tier"] == "red"


def test_a_reached_status_with_no_sign_of_a_person_is_stored_as_no_answer(store, tz, now):
    _, call_id, queue = queue_one_call(store, tz, now)
    assert apply_outcome(store, queue, call_id, "completed", now, tz) == "no_answer"


def test_refusals_write_nothing(store, tz, now):
    _, call_id, queue = queue_one_call(store, tz, now)
    before = store.get("calls", call_id)
    with pytest.raises(ValueError, match="unknown question ids"):
        apply_outcome(store, queue, call_id, "completed", now, tz, answers={"mood": "ok"})
    with pytest.raises(KeyError):
        apply_outcome(store, queue, "nope", "completed", now, tz)
    assert store.get("calls", call_id) == before
    apply_outcome(store, queue, call_id, "completed", now, tz, answers={"feeling": "ok"})
    finished = store.get("calls", call_id)
    with pytest.raises(CallAlreadyFinished, match="completed"):
        apply_outcome(store, queue, call_id, "completed", now, tz, answers={"feeling": "severe_symptoms"})
    assert store.get("calls", call_id) == finished
