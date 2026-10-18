import pytest

from app.case_note import MAX_NOTE_TRIES, backfill_notes, clean_note, note_for_call, write_case_note
from app.cases import upsert_case
from tests.demo_script import SCRIPT
from app.dispatch import RecordingQueue, handle_call_outcome, plan_round
from tests.helpers import add_resident, queue_one_call

GOOD = "Says she is dizzy and has had little water. Lives alone.\nCheck fluids and whether she can manage today."


def a_case(store, tz, now, **answers):
    round_id, call_id, queue = queue_one_call(store, tz, now, age=74, lives_alone=True)
    store.update("calls", call_id, {
        **{f"answers.{q}": {"value": v, "quote": "thoda chakkar aa raha hai", "at": now} for q, v in answers.items()},
        "transcript": [{"role": "agent", "text": "Namaste", "at": now}, {"role": "resident", "text": "chakkar aa raha hai", "at": now}],
    })
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    return round_id, call_id


def model(reply, seen=None):
    def text_model(instruction, prompt):
        if seen is not None:
            seen.append((instruction, prompt))
        if isinstance(reply, Exception):
            raise reply
        return reply
    return text_model


def test_a_note_is_written_from_what_the_call_recorded(store, tz, now):
    round_id, call_id = a_case(store, tz, now, feeling="mild_symptoms", fluids_food="low")
    seen = []
    assert note_for_call(store, model(GOOD, seen), call_id) == GOOD
    assert store.get("cases", f"{round_id}_r1")["note"] == GOOD
    instruction, prompt = seen[0]
    assert "health worker" in instruction
    for expected in ("mild_symptoms", "chakkar aa raha hai", "Reported mild or unclear symptoms", "74"):
        assert expected in prompt
    assert "+91" not in prompt  # the phone number is never sent


def test_a_case_that_has_a_note_is_not_written_again(store, tz, now):
    round_id, call_id = a_case(store, tz, now, feeling="mild_symptoms")
    note_for_call(store, model(GOOD), call_id)
    seen = []
    assert note_for_call(store, model("Another note.", seen), call_id) is None
    assert seen == [] and store.get("cases", f"{round_id}_r1")["note"] == GOOD


@pytest.mark.parametrize("reply", [
    "", "   \n ", None, 42, "x" * 400,
    "She is fine, no need to visit.", "The resident is healthy.", "Likely diagnosis: heat stroke.",
    "Give paracetamol and rest.", RuntimeError("model unavailable"),
])
def test_an_unusable_note_is_never_stored(store, tz, now, reply):
    round_id, call_id = a_case(store, tz, now, feeling="mild_symptoms")
    before = store.get("cases", f"{round_id}_r1")
    assert note_for_call(store, model(reply), call_id) is None
    assert store.get("cases", f"{round_id}_r1") == {**before, "note_tries": 1}  # only the attempt is counted


def test_a_note_is_at_most_two_tidy_lines():
    assert clean_note("  One  thing.\n\n\nTwo   things.\nThree things. ") == "One thing.\nTwo things."


def test_an_unreached_resident_gets_no_note(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    for attempt in (1, 2, 3):
        handle_call_outcome(store, queue, f"{round_id}_r1_{attempt}", "no_answer", now, tz)
    seen = []
    assert write_case_note(store, model(GOOD, seen), round_id, "r1") is None  # amber, but nobody spoke
    assert seen == []


def test_a_green_case_gets_no_note(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    healthy = dict(feeling="ok", fluids_food="enough", company="with_someone", shelter="adequate", exposure="staying_in", help="none")
    store.update("calls", call_id, {f"answers.{q}": {"value": v, "quote": "", "at": now} for q, v in healthy.items()})
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    assert store.get("cases", f"{round_id}_r1")["tier"] == "green"
    seen = []
    assert note_for_call(store, model(GOOD, seen), call_id) is None
    assert seen == []


def test_the_tier_is_never_touched(store, tz, now):
    round_id, call_id = a_case(store, tz, now, feeling="severe_symptoms")
    note_for_call(store, model(GOOD), call_id)
    case = store.get("cases", f"{round_id}_r1")
    assert (case["tier"], case["status"]) == ("red", "open")
    assert "R2" in case["reasons"]


def test_backfill_writes_the_missing_notes_up_to_its_limit(store, tz, now):
    round_id, call_id = a_case(store, tz, now, feeling="mild_symptoms")
    seen = []
    assert backfill_notes(store, model(GOOD, seen), [round_id, "no-such-round"], limit=5) == 1
    assert backfill_notes(store, model(GOOD, seen), [round_id], limit=5) == 0
    assert len(seen) == 1
    assert backfill_notes(store, model(GOOD), [round_id], limit=0) == 0


def test_a_line_nobody_spoke_on_gets_no_note_even_with_the_agent_transcript(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    for attempt in (1, 2, 3):
        store.update("calls", f"{round_id}_r1_{attempt}", {"transcript": [{"role": "agent", "text": "Namaste", "at": now}]})
        handle_call_outcome(store, queue, f"{round_id}_r1_{attempt}", "no_answer", now, tz)
    seen = []
    assert write_case_note(store, model(GOOD, seen), round_id, "r1") is None
    assert seen == [] and store.get("cases", f"{round_id}_r1")["note"] == ""


BAD = "She is fine, no need to visit."


def two_cases(store, tz, now):
    """One round with two amber cases somebody spoke on, r1 and r2."""
    round_id, _ = a_case(store, tz, now, feeling="mild_symptoms")
    add_resident(store, "r2", age=71)
    queue = RecordingQueue()
    plan_round(store, queue, round_id, now, tz)
    store.update("calls", f"{round_id}_r2_1", {
        "answers.fluids_food": {"value": "low", "quote": "paani kam hai", "at": now},
        "transcript": [{"role": "resident", "text": "paani kam hai", "at": now}],
    })
    handle_call_outcome(store, queue, f"{round_id}_r2_1", "completed", now, tz)
    return round_id


def test_a_case_whose_note_keeps_failing_is_asked_about_at_most_twice(store, tz, now):
    round_id, call_id = a_case(store, tz, now, feeling="mild_symptoms")
    seen = []
    for _ in range(6):
        assert backfill_notes(store, model(BAD, seen), [round_id]) == 0
    assert len(seen) == MAX_NOTE_TRIES == 2
    assert note_for_call(store, model(GOOD, seen), call_id) is None  # the after-call step skips it too
    assert len(seen) == 2
    case = store.get("cases", f"{round_id}_r1")
    assert (case["note"], case["note_tries"]) == ("", 2)


def test_a_failing_case_does_not_starve_the_next_one(store, tz, now):
    round_id = two_cases(store, tz, now)
    seen = []

    def text_model(instruction, prompt):
        seen.append(prompt)
        return GOOD if "paani kam hai" in prompt else BAD  # r1 never gets a usable note

    for _ in range(2):  # one model call a pass: both go to r1, the first case in order
        assert backfill_notes(store, text_model, [round_id], limit=1) == 0
    assert backfill_notes(store, text_model, [round_id], limit=1) == 1  # r1 is passed over in this pass
    assert store.get("cases", f"{round_id}_r2")["note"] == GOOD
    assert store.get("cases", f"{round_id}_r1")["note"] == ""
    assert backfill_notes(store, text_model, [round_id], limit=1) == 0
    assert len(seen) == 3


def test_a_model_that_raises_is_counted_too(store, tz, now):
    round_id, call_id = a_case(store, tz, now, feeling="mild_symptoms")
    seen = []
    for _ in range(4):
        note_for_call(store, model(RuntimeError("down"), seen), call_id)
    assert len(seen) == 2


def test_a_skipped_case_is_not_counted(store, tz, now):
    round_id, call_id, queue = queue_one_call(store, tz, now)
    for attempt in (1, 2, 3):
        handle_call_outcome(store, queue, f"{round_id}_r1_{attempt}", "no_answer", now, tz)
    assert write_case_note(store, model(GOOD), round_id, "r1") is None  # nobody spoke
    assert store.get("cases", f"{round_id}_r1")["note_tries"] == 0


def test_the_count_survives_the_case_being_stored_again(store, tz, now):
    round_id, call_id = a_case(store, tz, now, feeling="mild_symptoms")
    assert note_for_call(store, model(BAD), call_id) is None
    store.update("calls", call_id, {"answers.feeling": {"value": "severe_symptoms", "quote": "", "at": now}})
    case = upsert_case(store, round_id, "r1", now)  # the tier rises: the whole case is written again
    assert (case["tier"], case["note_tries"]) == ("red", 1)
    assert store.get("cases", f"{round_id}_r1")["note_tries"] == 1
    assert note_for_call(store, model(BAD), call_id) is None
    seen = []
    assert note_for_call(store, model(GOOD, seen), call_id) is None and seen == []


REFUSED = [
    "She is doing well and has no concerns.",
    "Not urgent; she seems fine.",
    "Likely heat stroke; give ORS and a crocin.",
    "He feels fine today.",
    "She looks well and is eating.",
    "He seems okay.\nLook in tomorrow.",
    "She is all right now.",
    "Says he's fine. Check again later.",
    "She was fine when we spoke.",
    "He is doing okay; no concern at present.",
    "Her condition is stable.",
    "Probably heatstroke.",
    "Looks dehydrated. Bring water.",
    "May be an infection; she has a fever.",
    "Signs of hypothermia in the night.",
    "Take one tablet after food.",
    "Give a dose of 500 mg twice a day.",
    "Give him two Dolo and some syrup.",
    "She should take an antibiotic.",
    "THE RESIDENT IS FINE.",
]

ACCEPTED = [
    "Says she feels dizzy and has had little water. Check fluids today.",
    "Reports a headache and weakness.\nCheck how she is now and whether she is drinking water.",
    "Has had no water since morning; the well water is far. Lives alone.\nBring drinking water.",
    "Asked about the wellness camp and wants someone to come by.\nAsk what he needs.",
    "Feels unstable on her feet and keeps the doors shut all day.\nCheck for a cooler room.",
    "Her blood pressure tablets have run out.\nCheck what she takes and help arrange a supply.",
    "A neighbour answered and could not say how he is.\nSee him in person today.",
    "Says he is managing but must take the cattle out at noon.\nAdvise shade and water.",
]


@pytest.mark.parametrize("text", REFUSED)
def test_reassurance_a_diagnosis_or_a_medicine_is_refused(text):
    assert clean_note(text) is None


@pytest.mark.parametrize("text", ACCEPTED)
def test_an_ordinary_note_is_accepted(text):
    assert clean_note(text) == text


def test_every_scripted_note_passes_the_checks_unchanged():
    notes = {resident_id: line["note"] for resident_id, line in SCRIPT.items() if "note" in line}
    assert len(notes) == 15
    for resident_id, note in notes.items():
        assert clean_note(note) == note, resident_id
