import json

from app.feed import QUESTION_LABELS, VALUE_LABELS, call_feed, load_feed
from app.triage import ALLOWED_VALUES

RESIDENT = {"id": "v-1", "age": 72, "conditions": ["diabetes"], "ward_id": "ward-a", "card_id": "alone_dizzy", "phone": "+910000000001"}


def a_call(now, **fields):
    base = {
        "id": "round_v-1_1", "round_id": "round", "resident_id": "v-1", "status": "in_progress",
        "started_at": now, "ended_at": None, "end_outcome": None, "language_used": None,
        "respondent": "unknown", "answers": {}, "emergency": None, "transcript": [],
        "browser_ticket": "ticket-value-that-must-not-leak", "stream_token": "token-value",
        "twilio_sid": "CA-secret-sid", "visitor_id": "visitor-aaaaaaaaaaaa",
    }
    base.update(fields)
    return base


def test_every_value_and_question_has_a_plain_label():
    for question, values in ALLOWED_VALUES.items():
        assert question in QUESTION_LABELS
        assert set(VALUE_LABELS[question]) == values


def test_a_call_that_has_just_started(now):
    feed = call_feed(a_call(now), RESIDENT, "heat", None)
    assert set(feed) == {
        "call_id", "resident_id", "card_id", "ward_id", "status", "started_at", "ended_at", "end_outcome",
        "language_used", "respondent", "transcript", "questions", "emergency", "rules_so_far",
        "tier_so_far", "case",
    }
    assert [q["id"] for q in feed["questions"]] == [
        "feeling", "fluids_food", "company", "shelter", "exposure", "medicine", "help",
    ]
    assert feed["questions"][1] == {
        "id": "fluids_food", "label": "Water", "value": None, "value_label": None, "quote": None, "at": None,
    }
    assert (feed["rules_so_far"], feed["tier_so_far"], feed["case"], feed["emergency"]) == ([], None, None, None)
    assert (feed["card_id"], feed["ward_id"]) == ("alone_dizzy", "ward-a")


def test_the_medicine_question_is_only_for_a_resident_with_a_condition(now):
    feed = call_feed(a_call(now), {**RESIDENT, "conditions": []}, "heat", None)
    assert "medicine" not in [q["id"] for q in feed["questions"]]


def test_the_cold_script_asks_about_food(now):
    feed = call_feed(a_call(now), RESIDENT, "cold", None)
    assert feed["questions"][1]["label"] == "Food"


def test_answers_fill_in_and_rules_follow(now):
    answers = {
        "feeling": {"value": "mild_symptoms", "quote": "thoda chakkar aa raha hai", "at": now},
        "company": {"value": "alone", "quote": "", "at": now},
    }
    feed = call_feed(a_call(now, answers=answers, respondent="self"), RESIDENT, "heat", None)
    assert feed["questions"][0] == {
        "id": "feeling", "label": "Feeling", "value": "mild_symptoms", "value_label": "Mild symptoms",
        "quote": "thoda chakkar aa raha hai", "at": now,
    }
    assert feed["rules_so_far"] == [
        {"id": "A1", "tier": "amber", "label": "Reported mild or unclear symptoms"},
        {"id": "A4", "tier": "amber", "label": "Alone and aged 70 or over"},
    ]
    assert feed["tier_so_far"] == "amber"
    assert feed["respondent"] == "self"


def test_an_emergency_shows_its_reason_and_turns_the_call_red(now):
    emergency = {"flagged": True, "reason": "chest pain", "at": now}
    feed = call_feed(a_call(now, emergency=emergency), RESIDENT, "heat", None)
    assert feed["emergency"] == {"reason": "chest pain", "at": now}
    assert feed["tier_so_far"] == "red"
    assert feed["rules_so_far"][0]["id"] == "R1"


def test_a_value_outside_the_list_is_shown_as_it_is(now):
    answers = {"feeling": {"value": "wobbly", "quote": "", "at": now}}
    feed = call_feed(a_call(now, answers=answers), RESIDENT, "heat", None)
    assert feed["questions"][0]["value_label"] == "wobbly"
    assert "A11" in [rule["id"] for rule in feed["rules_so_far"]]


def test_the_case_appears_once_the_call_has_one(now):
    case = {"id": "round_v-1", "tier": "amber", "reasons": ["A1", "A12"], "ward_id": "ward-a", "visitor_id": "visitor-aaaaaaaaaaaa", "status": "open"}
    feed = call_feed(a_call(now, status="dropped", ended_at=now), RESIDENT, "heat", case)
    assert feed["case"] == {
        "id": "round_v-1", "tier": "amber", "reasons": ["A1", "A12"], "ward_id": "ward-a",
        "reason_labels": ["Reported mild or unclear symptoms", "Call ended before all the questions were asked"],
        "note": None,
    }


def test_the_feed_never_carries_a_ticket_a_token_or_a_phone_number(now):
    transcript = [{"role": "agent", "text": "Namaste", "at": now, "stream_token": "sneaked-in"}]
    feed = call_feed(a_call(now, transcript=transcript), RESIDENT, "heat", None)
    text = json.dumps(feed, default=str)
    for forbidden in ("ticket", "token", "twilio", "phone", "visitor", "+91", "sneaked-in", "CA-secret-sid"):
        assert forbidden not in text
    assert feed["transcript"] == [{"role": "agent", "text": "Namaste", "at": now}]


def test_load_feed_reads_the_resident_the_round_and_the_case(store, tz, now):
    from seed.seed import ward_ids
    from tests.helpers import add_visitor_call, seeded_demo

    ward_30 = ward_ids("churu")[1]
    round_id = seeded_demo(store, now, tz)
    call_id = add_visitor_call(
        store, round_id, "visitor-aaaaaaaaaaaa", now, tz, resident_id="v-9", ward_id=ward_30,
        feeling="mild_symptoms",
    )
    feed = load_feed(store, store.get("calls", call_id))
    assert feed["call_id"] == call_id
    assert feed["status"] == "completed"
    assert feed["case"]["tier"] == "amber"
    assert feed["ward_id"] == ward_30
    assert feed["questions"][0]["value"] == "mild_symptoms"


def test_the_feed_names_its_resident():
    from app.feed import call_feed

    call = {"id": "c1", "status": "queued", "answers": {}, "transcript": []}
    resident = {"id": "v-abc", "age": 70, "conditions": [], "ward_id": "ward-a"}
    assert call_feed(call, resident, "heat", None)["resident_id"] == "v-abc"
