from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.dispatch import RecordingQueue, handle_call_outcome
from app.sweep import sweep
from seed.seed import ward_ids
from tests.helpers import add_resident, add_visitor_call, add_ward, queue_one_call, seeded_demo

PHONE = "+910000000001"
SECRET = {"X-Internal-Secret": "s3cret"}
NOTHING = {"stale_dialing": 0, "stale_in_progress": 0, "requeued": 0, "repaired": 0, "completed": 0, "expired": 0, "errors": 0}


@pytest.fixture
def settings():
    return Settings(_env_file=None, internal_secret="s3cret", dial_allowlist=PHONE, max_call_seconds=300)


def started(store, tz, now):
    """One round, one resident on the allowlist, one queued call due now."""
    round_id, call_id, _ = queue_one_call(store, tz, now)
    return round_id, call_id


def run(store, settings, now, tz, queue=None):
    return sweep(store, queue or RecordingQueue(), settings, now, tz)


def test_a_claim_that_went_nowhere_becomes_a_failed_attempt(store, settings, tz, now):
    round_id, call_id = started(store, tz, now)
    store.update("calls", call_id, {"status": "dialing", "dialing_at": now})
    queue = RecordingQueue()
    result = run(store, settings, now + timedelta(minutes=4), tz, queue)
    assert result["stale_dialing"] == 1
    assert store.get("calls", call_id)["status"] == "failed"
    retry = store.get("calls", f"{round_id}_r1_2")
    assert retry["status"] == "queued" and queue.items == [(retry["id"], retry["not_before"])]


def test_a_call_cut_off_mid_conversation_becomes_dropped_with_a_case(store, settings, tz, now):
    round_id, call_id = started(store, tz, now)
    store.update("calls", call_id, {
        "status": "in_progress", "started_at": now,
        "answers.feeling": {"value": "mild_symptoms", "quote": "", "at": now},
    })
    result = run(store, settings, now + timedelta(seconds=421), tz)
    assert result["stale_in_progress"] == 1
    assert store.get("calls", call_id)["status"] == "dropped"
    assert store.get("cases", f"{round_id}_r1")["tier"] == "amber"


def test_a_call_inside_its_limits_is_left_alone(store, settings, tz, now):
    round_id, call_id = started(store, tz, now)
    store.update("calls", call_id, {"status": "dialing", "dialing_at": now})
    assert run(store, settings, now + timedelta(minutes=2), tz) == {"rounds": 1, **NOTHING}
    store.update("calls", call_id, {"status": "in_progress", "started_at": now})
    assert run(store, settings, now + timedelta(seconds=419), tz) == {"rounds": 1, **NOTHING}
    assert store.get("calls", call_id)["status"] == "in_progress"
    assert store.get("rounds", round_id)["status"] == "calling"


def test_an_overdue_queued_call_is_queued_again_once(store, settings, tz, now):
    _, call_id = started(store, tz, now)
    queue = RecordingQueue()
    assert run(store, settings, now + timedelta(minutes=9), tz, queue)["requeued"] == 0
    later = now + timedelta(minutes=11)
    assert run(store, settings, later, tz, queue)["requeued"] == 1
    assert queue.items == [(call_id, later)]
    assert store.get("calls", call_id)["not_before"] == later
    assert run(store, settings, later + timedelta(minutes=5), tz, RecordingQueue())["requeued"] == 0


def test_nothing_is_queued_outside_calling_hours(store, settings, tz, now):
    started(store, tz, now)
    night = now + timedelta(hours=12)  # 22:00 local
    queue = RecordingQueue()
    assert run(store, settings, night, tz, queue)["requeued"] == 0
    assert queue.items == []


def test_a_resident_off_the_allowlist_is_never_queued(store, tz, now):
    _, call_id = started(store, tz, now)
    settings = Settings(_env_file=None, internal_secret="s3cret", dial_allowlist="+910000000099")
    queue = RecordingQueue()
    result = run(store, settings, now + timedelta(minutes=30), tz, queue)
    assert result["requeued"] == 0 and queue.items == []
    assert result["completed"] == 1  # nothing more can happen in this round
    assert store.get("calls", call_id)["status"] == "queued"  # still shown as pending


def test_a_missing_retry_is_created(store, settings, tz, now):
    round_id, call_id = started(store, tz, now)
    store.update("calls", call_id, {"status": "no_answer", "ended_at": now})  # the crash came before the retry
    queue = RecordingQueue()
    assert run(store, settings, now + timedelta(minutes=1), tz, queue)["repaired"] == 1
    assert store.get("calls", f"{round_id}_r1_2")["status"] == "queued"
    assert store.get("rounds", round_id)["status"] == "calling"


def test_a_missing_case_is_opened(store, settings, tz, now):
    round_id, call_id = started(store, tz, now)
    store.update("calls", call_id, {
        "status": "completed", "ended_at": now, "respondent": "self",
        "answers.feeling": {"value": "severe_symptoms", "quote": "", "at": now},
    })
    assert run(store, settings, now + timedelta(minutes=1), tz)["repaired"] == 1
    assert store.get("cases", f"{round_id}_r1")["tier"] == "red"


def test_a_red_fact_under_a_case_that_is_not_red_is_put_right(store, settings, tz, now):
    round_id, call_id = started(store, tz, now)
    store.update("calls", call_id, {"answers.feeling": {"value": "mild_symptoms", "quote": "", "at": now}})
    handle_call_outcome(store, RecordingQueue(), call_id, "completed", now, tz)
    store.update("calls", call_id, {"emergency": {"flagged": True, "reason": "collapsed", "at": now}})
    assert store.get("cases", f"{round_id}_r1")["tier"] == "amber"
    assert run(store, settings, now + timedelta(minutes=1), tz)["repaired"] == 1
    assert store.get("cases", f"{round_id}_r1")["tier"] == "red"


def test_a_round_with_nothing_left_to_happen_is_completed(store, settings, tz, now):
    round_id, call_id = started(store, tz, now)
    store.update("calls", call_id, {"answers.feeling": {"value": "ok", "quote": "", "at": now}})
    handle_call_outcome(store, RecordingQueue(), call_id, "completed", now, tz)
    later = now + timedelta(minutes=3)
    assert run(store, settings, later, tz)["completed"] == 1
    finished = store.get("rounds", round_id)
    assert (finished["status"], finished["completed_at"]) == ("complete", later)


def test_a_second_sweep_does_nothing_more(store, settings, tz, now):
    _, call_id = started(store, tz, now)
    store.update("calls", call_id, {"status": "dialing", "dialing_at": now})
    later = now + timedelta(minutes=4)
    run(store, settings, later, tz)
    snapshot = (store.query("calls"), store.query("cases"), store.query("rounds"))
    queue = RecordingQueue()
    assert run(store, settings, later, tz, queue) == {"rounds": 1, **NOTHING}
    assert (store.query("calls"), store.query("cases"), store.query("rounds")) == snapshot
    assert queue.items == []


def test_demo_rounds_and_visitor_calls_are_never_touched(store, settings, tz, now):
    demo = seeded_demo(store, now, tz)
    add_visitor_call(store, demo, "visitor-aaaaaaaaaaaa", now, tz, resident_id="v-1", ward_id=ward_ids("churu")[0], feeling="ok")
    before = (store.query("calls"), store.query("cases"), store.query("rounds"))
    assert run(store, settings, now + timedelta(days=2), tz) == {"rounds": 0, **NOTHING}
    assert (store.query("calls"), store.query("cases"), store.query("rounds")) == before

    round_id, _ = started(store, tz, now)
    stuck = add_visitor_call(store, round_id, "visitor-bbbbbbbbbbbb", now, tz, resident_id="v-2")
    store.update("calls", stuck, {"status": "in_progress", "started_at": now})
    run(store, settings, now + timedelta(hours=1), tz)
    assert store.get("calls", stuck)["status"] == "in_progress"


def test_one_broken_round_does_not_stop_the_others(store, settings, tz, now):
    _, call_id = started(store, tz, now)
    store.set("rounds", "aaa_broken", {"id": "aaa_broken", "status": "calling", "opened_at": now})  # no district_id
    store.update("calls", call_id, {"status": "dialing", "dialing_at": now})
    result = run(store, settings, now + timedelta(minutes=4), tz)
    assert result["errors"] == 1 and result["stale_dialing"] == 1


def test_the_endpoint_sweeps_and_then_writes_notes_and_summaries(store, tz, now):
    demo = seeded_demo(store, now, tz)
    add_visitor_call(store, demo, "visitor-aaaaaaaaaaaa", now, tz, resident_id="v-1", ward_id=ward_ids("churu")[0], feeling="mild_symptoms")
    store.update("calls", store.query("calls", resident_id="v-1")[0]["id"], {"transcript": [{"role": "resident", "text": "chakkar", "at": now}]})
    def text_model(instruction, prompt):
        return "Says she feels dizzy. Check how she is now." if "health worker" in instruction else "24 were reached."

    settings = Settings(_env_file=None, internal_secret="s3cret")
    client = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now, text_model=text_model))
    assert client.post("/internal/sweep").status_code == 401
    body = client.post("/internal/sweep", headers=SECRET).json()
    assert body["calls"]["rounds"] == 0
    assert (body["notes"], body["summaries"]) == (1, 1)
    assert client.post("/internal/sweep", headers=SECRET).json()["notes"] == 0

    without = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now))
    assert without.post("/internal/sweep", headers=SECRET).json() == {"calls": {"rounds": 0, **NOTHING}, "notes": 0, "summaries": 0, "replay": {"calls": 0, "visits": 0}}


def _copy_call(store, source_id, resident_id, attempt=1, **fields):
    source = store.get("calls", source_id)
    new_id = f"{source['round_id']}_{resident_id}_{attempt}"
    store.set("calls", new_id, {**source, "id": new_id, "resident_id": resident_id, "attempt": attempt, **fields})
    return new_id


def test_a_red_answer_on_an_earlier_attempt_converges(store, settings, tz, now):
    round_id, first = started(store, tz, now)
    second = _copy_call(store, first, "r1", 2)
    store.update("calls", first, {"status": "no_answer", "ended_at": now,
                                  "answers.feeling": {"value": "severe_symptoms", "quote": "", "at": now}})
    store.update("calls", second, {"status": "completed", "ended_at": now, "respondent": "self",
                                   "answers.feeling": {"value": "ok", "quote": "", "at": now}})
    assert run(store, settings, now + timedelta(minutes=1), tz)["repaired"] == 1
    assert store.get("cases", f"{round_id}_r1")["tier"] == "red"
    again = run(store, settings, now + timedelta(minutes=2), tz)
    assert again["repaired"] == 0 and again["completed"] == 1
    assert store.get("rounds", round_id)["status"] == "complete"


def test_a_case_owed_but_not_openable_is_not_a_repair_every_pass(store, settings, tz, now):
    round_id, first = started(store, tz, now)
    store.update("calls", first, {"attempt": 3, "status": "no_answer", "ended_at": now})
    for minutes in (1, 2):
        assert run(store, settings, now + timedelta(minutes=minutes), tz)["repaired"] == 0
    assert store.get("cases", f"{round_id}_r1") is None


def test_a_call_answered_during_the_pass_is_not_failed(store, settings, tz, now):
    round_id, call_id = started(store, tz, now)
    store.update("calls", call_id, {"status": "dialing", "dialing_at": now})
    real_query, moved = store.query, []

    def query(collection, **equals):
        found = real_query(collection, **equals)
        if collection == "calls" and not moved:
            moved.append(True)
            store.update("calls", call_id, {"status": "in_progress", "started_at": now})
        return found

    store.query = query
    result = run(store, settings, now + timedelta(minutes=4), tz)
    assert result["stale_dialing"] == 0
    assert store.get("calls", call_id)["status"] == "in_progress"
    assert store.get("calls", f"{round_id}_r1_2") is None


def test_one_missing_resident_does_not_stop_the_rest_of_the_round(store, settings, tz, now):
    round_id, first = started(store, tz, now)
    store.update("calls", first, {"status": "completed", "ended_at": now, "respondent": "self"})
    store.delete("residents", "r1")  # its case can no longer be opened
    add_resident(store, "r2")
    add_resident(store, "r3")
    stale = _copy_call(store, first, "r2", status="dialing", dialing_at=now, ended_at=None)
    _copy_call(store, first, "r3", status="no_answer")
    result = run(store, settings, now + timedelta(minutes=4), tz)
    assert result["errors"] == 1 and result["stale_dialing"] == 1 and result["completed"] == 0
    assert store.get("calls", stale)["status"] == "failed"
    assert store.get("calls", f"{round_id}_r3_2")["status"] == "queued"
    assert store.get("rounds", round_id)["status"] == "calling"


def test_a_round_that_finishes_still_gets_its_last_note_and_its_summary(store, tz, now):
    add_ward(store)
    add_resident(store, "r1", age=74, lives_alone=True)
    asked = []

    def text_model(instruction, prompt):
        asked.append(instruction)
        return "Says she feels dizzy. Check how she is now." if "health worker" in instruction else "One resident was reached."

    settings = Settings(_env_file=None, internal_secret="s3cret", dial_allowlist=PHONE)
    client = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now, text_model=text_model))
    replay = {"district_id": "d1", "hazard": "heat", "severity": "red"}
    round_id = client.post("/admin/alerts/replay", json=replay, headers=SECRET).json()["round_id"]
    call_id = client.post(f"/internal/rounds/{round_id}/dispatch", headers=SECRET).json()["queued"][0]
    assert store.get("demo", "current") is None  # a real round: nothing is pinned
    store.update("calls", call_id, {"transcript": [{"role": "resident", "text": "chakkar aa raha hai", "at": now}]})
    outcome = {"status": "completed", "answers": {"feeling": "mild_symptoms"}, "respondent": "self"}
    assert client.post(f"/admin/calls/{call_id}/simulate", json=outcome, headers=SECRET).status_code == 200
    assert store.get("cases", f"{round_id}_r1")["tier"] == "amber"

    body = client.post("/internal/sweep", headers=SECRET).json()
    assert body["calls"]["completed"] == 1 and (body["notes"], body["summaries"]) == (1, 1)
    assert store.get("rounds", round_id)["status"] == "complete"
    assert store.get("cases", f"{round_id}_r1")["note"] == "Says she feels dizzy. Check how she is now."
    report = client.get(f"/api/rounds/{round_id}/report", headers=SECRET).json()
    assert report["summary"]["text"] == "One resident was reached."

    asked.clear()
    snapshot = (store.query("calls"), store.query("cases"), store.query("rounds"))
    again = client.post("/internal/sweep", headers=SECRET).json()
    assert (again["calls"]["rounds"], again["notes"], again["summaries"]) == (0, 0, 0)
    assert asked == []
    assert (store.query("calls"), store.query("cases"), store.query("rounds")) == snapshot


def test_real_rounds_are_looked_after_before_the_demo_round(store, tz, now, monkeypatch):
    import app.api as api_module

    demo = seeded_demo(store, now, tz)
    store.set("rounds", "zzz_real", {**store.get("rounds", demo), "id": "zzz_real", "status": "calling"})
    seen = {}
    monkeypatch.setattr(api_module, "backfill_notes", lambda store, model, round_ids: seen.setdefault("notes", list(round_ids)) and 0)
    monkeypatch.setattr(api_module, "refresh_summaries", lambda store, model, round_ids, now, registers=None: seen.setdefault("summaries", list(round_ids)) and 0)
    settings = Settings(_env_file=None, internal_secret="s3cret")
    client = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now, text_model=lambda i, p: ""))
    assert client.post("/internal/sweep", headers=SECRET).status_code == 200
    assert seen == {"notes": ["zzz_real", demo], "summaries": ["zzz_real", demo]}


def legacy_round(store, tz, now):
    """A round as an earlier build left it: opened days ago, still being called, its call
    queued with no due time."""
    round_id, call_id = started(store, tz, now)
    store.update("rounds", round_id, {"opened_at": now - timedelta(days=3), "started_at": now - timedelta(days=3)})
    store.update("calls", call_id, {"not_before": None})
    return round_id, call_id


def test_an_old_round_is_closed_and_nobody_in_it_is_rung(store, settings, tz, now):
    round_id, call_id = legacy_round(store, tz, now)
    add_resident(store, "r2", phone=PHONE)
    stale = _copy_call(store, call_id, "r2", status="dialing", dialing_at=None)
    before = (store.query("calls"), store.query("cases"))
    queue = RecordingQueue()
    result = run(store, settings, now, tz, queue)  # 10:00 local: inside calling hours
    assert result == {"rounds": 1, **NOTHING, "expired": 1}
    assert queue.items == []
    closed = store.get("rounds", round_id)
    assert (closed["status"], closed["completed_at"]) == ("complete", now)
    assert (store.query("calls"), store.query("cases")) == before  # nothing finished, nothing repaired
    assert store.get("calls", stale)["status"] == "dialing"
    assert run(store, settings, now, tz, queue) == {"rounds": 0, **NOTHING}


@pytest.mark.parametrize("opened, started_ago", [
    (None, None),  # neither time: too old to have one
    (None, timedelta(hours=37)),  # falls back to when calling started
    (timedelta(hours=37), timedelta(hours=1)),  # the opening time is the one that counts
])
def test_a_round_is_judged_by_when_it_opened_or_else_started(store, settings, tz, now, opened, started_ago):
    round_id, call_id = started(store, tz, now)
    later = now + timedelta(minutes=11)
    store.update("rounds", round_id, {
        "opened_at": later - opened if opened else None,
        "started_at": later - started_ago if started_ago else None,
    })
    queue = RecordingQueue()
    assert run(store, settings, later, tz, queue)["expired"] == 1
    assert queue.items == [] and store.get("rounds", round_id)["status"] == "complete"


def test_a_round_inside_its_working_life_is_swept_as_before(store, settings, tz, now):
    round_id, call_id = started(store, tz, now)
    queue = RecordingQueue()
    later = now + timedelta(hours=1)
    result = run(store, settings, later, tz, queue)
    assert (result["expired"], result["requeued"]) == (0, 1)
    assert queue.items == [(call_id, later)]
    assert store.get("rounds", round_id)["status"] == "calling"
    store.update("rounds", round_id, {"opened_at": later - timedelta(hours=36)})  # exactly its life: not over yet
    assert run(store, settings, later, tz, RecordingQueue())["expired"] == 0


def test_a_queued_call_with_no_due_time_is_left_alone(store, settings, tz, now):
    round_id, call_id = started(store, tz, now)
    store.update("calls", call_id, {"not_before": None})
    before = store.get("calls", call_id)
    queue = RecordingQueue()
    assert run(store, settings, now + timedelta(hours=1), tz, queue) == {"rounds": 1, **NOTHING}
    assert queue.items == [] and store.get("calls", call_id) == before
    assert store.get("rounds", round_id)["status"] == "calling"  # it keeps the round open until the round expires


def test_overdue_calls_are_not_queued_when_no_phone_call_can_be_placed(store, tz, now):
    round_id, call_id = started(store, tz, now)
    later = now + timedelta(minutes=11)
    queue = RecordingQueue()
    assert sweep(store, queue, Settings(_env_file=None, internal_secret="s3cret", dial_allowlist=PHONE), later, tz, can_dial=False) == {"rounds": 1, **NOTHING}
    assert queue.items == [] and store.get("calls", call_id)["not_before"] == now
    assert store.get("rounds", round_id)["status"] == "calling"  # the call still keeps the round open


@pytest.mark.parametrize("host, dialler, requeued", [
    ("", True, 0), ("voice.example.test", False, 0), ("voice.example.test", True, 1),
])
def test_the_endpoint_queues_overdue_calls_only_when_the_dial_endpoint_could_place_them(store, tz, now, host, dialler, requeued):
    round_id, call_id = started(store, tz, now)
    later = now + timedelta(minutes=11)
    settings = Settings(_env_file=None, internal_secret="s3cret", dial_allowlist=PHONE, public_host=host)
    queue = RecordingQueue()
    client = TestClient(create_app(
        store, queue, settings, clock=lambda: later,
        place_call=(lambda to, twiml, status_callback: "CA1") if dialler else None,
    ))
    assert client.post("/internal/sweep", headers=SECRET).json()["calls"]["requeued"] == requeued
    assert queue.items == ([(call_id, later)] if requeued else [])
    dial = client.post(f"/internal/calls/{call_id}/dial", headers=SECRET)
    assert dial.status_code == (200 if requeued else 503)  # the same condition, both ways
    assert store.get("rounds", round_id)["status"] == "calling"
