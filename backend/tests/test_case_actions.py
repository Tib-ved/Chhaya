from datetime import timedelta

import pytest

from app.case_actions import ActionRefused, acknowledge, clean_note, escalate, raise_to_red, reassign
from app.cases import mark_visited, upsert_case
from app.simulate import apply_outcome
from app.triage import REASON_LABELS, rule_tier
from tests.helpers import queue_one_call

CALM = {"feeling": "ok", "fluids_food": "enough", "company": "with_someone", "shelter": "adequate", "exposure": "staying_in", "help": "none"}


def a_case(store, tz, now, **answers) -> tuple[str, str]:
    """One resident, r1, whose call has ended with these answers. Returns (round id, case id)."""
    round_id, call_id, queue = queue_one_call(store, tz, now, age=66)
    apply_outcome(store, queue, call_id, "completed", now, tz, answers={**CALM, **answers})
    return round_id, f"{round_id}_r1"


def refusal(action, *args) -> str:
    with pytest.raises(ActionRefused) as refused:
        action(*args)
    return refused.value.reason


def test_a_note_is_collapsed_and_cut():
    assert clean_note("  out   of\n\nORS  ") == "out of ORS"
    assert clean_note("<b>x</b>") == "<b>x</b>"  # stored as typed; the screen shows it as text
    assert len(clean_note("a" * 5000)) == 200
    assert clean_note(None) == ""


def test_telling_the_officer(store, tz, now):
    _, case_id = a_case(store, tz, now, feeling="mild_symptoms")
    stored = escalate(store, case_id, "supplies", "  out of   ORS ", now)
    assert stored["escalation"] == {"reason": "supplies", "note": "out of ORS", "at": now, "acknowledged_at": None, "reply": ""}
    assert stored["tier"] == "amber" and stored["status"] == "open"
    assert refusal(escalate, store, case_id, "transport", "", now) == "already_flagged"
    assert refusal(escalate, store, case_id, "nonsense", "", now) == "bad_value"


def test_acknowledging(store, tz, now):
    _, case_id = a_case(store, tz, now, feeling="mild_symptoms")
    assert refusal(acknowledge, store, case_id, "", now) == "no_flag"
    escalate(store, case_id, "missing", "", now)
    later = now + timedelta(minutes=5)
    stored = acknowledge(store, case_id, " on my   way ", later)
    assert stored["escalation"]["acknowledged_at"] == later and stored["escalation"]["reply"] == "on my way"
    assert refusal(acknowledge, store, case_id, "", later) == "no_flag"
    assert escalate(store, case_id, "transport", "", later)["escalation"]["reason"] == "transport"


def test_reassigning(store, tz, now):
    _, case_id = a_case(store, tz, now, feeling="mild_symptoms")
    store.set("wards", "w2", {"id": "w2", "name": "Ward 2", "district_id": "d1", "worker_id": "k2"})
    store.set("workers", "k2", {"id": "k2", "name": "Asha Two", "phone": "", "ward_ids": ["w2"]})
    store.set("wards", "far", {"id": "far", "name": "Far", "district_id": "d9", "worker_id": "k9"})
    store.set("workers", "k9", {"id": "k9", "name": "Asha Far", "phone": "", "ward_ids": ["far"]})
    assert refusal(reassign, store, case_id, "k1", now) == "same_worker"
    assert refusal(reassign, store, case_id, "nobody", now) == "unknown_worker"
    assert refusal(reassign, store, case_id, "k9", now) == "unknown_worker"  # another district
    stored = reassign(store, case_id, "k2", now)
    assert (stored["worker_id"], stored["reassigned_from"]) == ("k2", "k1")
    assert reassign(store, case_id, "k1", now)["reassigned_from"] == "k2"


def test_raising_to_red(store, tz, now):
    _, case_id = a_case(store, tz, now, feeling="mild_symptoms")
    assert refusal(raise_to_red, store, case_id, "nonsense", now) == "bad_value"
    stored = raise_to_red(store, case_id, "confused", now)
    assert stored["tier"] == "red" and stored["reasons"][0] == "R5" and "A1" in stored["reasons"]
    assert stored["raised"] == {"observation": "confused", "at": now}
    assert refusal(raise_to_red, store, case_id, "fainted", now) == "not_amber"
    assert rule_tier("R5") == "red" and REASON_LABELS["R5"] == "Raised to red by the health worker"


def test_only_an_open_amber_case_is_raised(store, tz, now):
    _, green = a_case(store, tz, now)
    assert refusal(raise_to_red, store, green, "confused", now) == "not_amber"


def test_nothing_is_done_to_a_visited_case(store, tz, now):
    _, case_id = a_case(store, tz, now, feeling="mild_symptoms")
    visited = mark_visited(store, case_id, "advised_ok", now, note="  all   fine ")
    assert visited["visit_note"] == "all fine"
    for action, value in ((escalate, "supplies"), (raise_to_red, "confused"), (reassign, "k1")):
        args = (store, case_id, value, "", now) if action is escalate else (store, case_id, value, now)
        assert refusal(action, *args) == "not_open"


def test_an_unknown_case(store, now):
    for action, args in ((escalate, ("supplies", "", now)), (acknowledge, ("", now)), (reassign, ("k1", now)), (raise_to_red, ("confused", now))):
        with pytest.raises(KeyError):
            action(store, "no-such-case", *args)


def test_a_later_triage_pass_keeps_what_the_worker_did(store, tz, now):
    round_id, case_id = a_case(store, tz, now, feeling="mild_symptoms")
    store.set("wards", "w2", {"id": "w2", "name": "Ward 2", "district_id": "d1", "worker_id": "k2"})
    store.set("workers", "k2", {"id": "k2", "name": "Asha Two", "phone": "", "ward_ids": ["w2"]})
    escalate(store, case_id, "supplies", "out of ORS", now)
    reassign(store, case_id, "k2", now)
    raise_to_red(store, case_id, "fainted", now)

    again = upsert_case(store, round_id, "r1", now + timedelta(minutes=10))  # what the sweep does

    assert again["tier"] == "red" and again["reasons"][0] == "R5"
    assert again["escalation"]["note"] == "out of ORS"
    assert again["raised"]["observation"] == "fainted"
    assert (again["worker_id"], again["reassigned_from"]) == ("k2", "k1")


def test_a_green_case_cannot_be_flagged_or_reassigned(store, tz, now):
    _, green = a_case(store, tz, now)
    assert refusal(escalate, store, green, "supplies", "", now) == "green_case"
    assert refusal(reassign, store, green, "w2", now) == "green_case"
    assert store.get("cases", green).get("escalation") is None
