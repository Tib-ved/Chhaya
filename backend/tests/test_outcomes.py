from datetime import datetime, timedelta, timezone

import pytest

from app.cases import VISIT_OUTCOMES, VisitRefused, mark_visited, upsert_case
from app.dispatch import RecordingQueue, handle_call_outcome, mark_call_started, plan_round
from app.ingest.alerts import record_alert
from app.ingest.replay import make_replay_alert
from tests.helpers import add_resident, add_ward


def start(store, tz, now, **resident):
    add_ward(store)
    round_id = record_alert(store, make_replay_alert("d1", "heat", "red", now), tz).id
    add_resident(store, "r1", **resident)
    queue = RecordingQueue()
    plan_round(store, queue, round_id, now, tz)
    return round_id, queue


def answer(store, call_id, now, **values):
    store.update(
        "calls",
        call_id,
        {f"answers.{q}": {"value": v, "quote": f"said {v}", "at": now} for q, v in values.items()},
    )


HEALTHY = dict(
    feeling="ok", fluids_food="enough", company="with_someone",
    shelter="adequate", exposure="staying_in", help="none",
)


def test_first_failure_schedules_retry_in_20_minutes(store, tz, now):
    round_id, queue = start(store, tz, now)
    handle_call_outcome(store, queue, f"{round_id}_r1_1", "no_answer", now, tz)
    assert store.get("calls", f"{round_id}_r1_1")["status"] == "no_answer"
    assert store.get("calls", f"{round_id}_r1_2")["attempt"] == 2
    assert queue.items[-1] == (f"{round_id}_r1_2", now + timedelta(minutes=20))
    assert store.query("cases") == []


def test_second_failure_schedules_retry_in_60_minutes(store, tz, now):
    round_id, queue = start(store, tz, now)
    handle_call_outcome(store, queue, f"{round_id}_r1_1", "busy", now, tz)
    later = now + timedelta(minutes=21)
    handle_call_outcome(store, queue, f"{round_id}_r1_2", "failed", later, tz)
    assert queue.items[-1] == (f"{round_id}_r1_3", later + timedelta(minutes=60))


def test_duplicate_failure_callback_creates_one_retry(store, tz, now):
    round_id, queue = start(store, tz, now)
    handle_call_outcome(store, queue, f"{round_id}_r1_1", "no_answer", now, tz)
    handle_call_outcome(store, queue, f"{round_id}_r1_1", "no_answer", now, tz)
    assert len(queue.items) == 2  # the first call plus one retry
    assert len(store.query("calls")) == 2


def test_duplicate_completed_callback_leaves_one_case(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, feeling="mild_symptoms")
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    handle_call_outcome(store, queue, call_id, "completed", now + timedelta(minutes=1), tz)
    assert len(store.query("cases")) == 1
    assert len(store.query("calls")) == 1
    assert store.get("calls", call_id)["ended_at"] == now


def test_retry_after_hours_moves_to_morning(store, tz):
    evening = datetime(2026, 5, 20, 14, 20, tzinfo=timezone.utc)  # 19:50 local
    round_id, queue = start(store, tz, evening)
    handle_call_outcome(store, queue, f"{round_id}_r1_1", "no_answer", evening, tz)
    assert queue.items[-1][1] == datetime(2026, 5, 21, 2, 30, tzinfo=timezone.utc)


def test_third_failure_creates_amber_unreachable_case(store, tz, now):
    round_id, queue = start(store, tz, now)
    for attempt in (1, 2, 3):
        handle_call_outcome(store, queue, f"{round_id}_r1_{attempt}", "no_answer", now, tz)
    case = store.get("cases", f"{round_id}_r1")
    assert case["tier"] == "amber"
    assert case["reasons"] == ["A8"]
    assert case["ward_id"] == "w1"
    assert case["worker_id"] == "k1"
    assert case["status"] == "open"
    assert len(store.query("calls")) == 3


def test_completed_call_with_severe_symptoms_is_red(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, feeling="severe_symptoms", company="alone")
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    case = store.get("cases", f"{round_id}_r1")
    assert case["tier"] == "red"
    assert "R2" in case["reasons"]


def test_dropped_call_keeps_partial_answers(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, fluids_food="low")
    handle_call_outcome(store, queue, call_id, "dropped", now, tz)
    case = store.get("cases", f"{round_id}_r1")
    assert case["tier"] == "amber"
    assert set(case["reasons"]) == {"A2", "A9", "A12"}


def test_emergency_flag_mid_call_creates_red_case(store, tz, now):
    round_id, _ = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    store.update(
        "calls",
        call_id,
        {"status": "in_progress", "emergency": {"flagged": True, "reason": "fainted", "at": now}},
    )
    case = upsert_case(store, round_id, "r1", now)
    assert case["tier"] == "red"
    assert "R1" in case["reasons"]


def test_no_case_while_attempts_remain(store, tz, now):
    round_id, _ = start(store, tz, now)
    assert upsert_case(store, round_id, "r1", now) is None


def test_answer_after_two_failures_uses_the_conversation(store, tz, now):
    round_id, queue = start(store, tz, now)
    handle_call_outcome(store, queue, f"{round_id}_r1_1", "no_answer", now, tz)
    handle_call_outcome(store, queue, f"{round_id}_r1_2", "no_answer", now, tz)
    third = f"{round_id}_r1_3"
    answer(
        store, third, now,
        **HEALTHY,
    )
    handle_call_outcome(store, queue, third, "completed", now, tz)
    case = store.get("cases", f"{round_id}_r1")
    assert case["tier"] == "green"
    assert "A8" not in case["reasons"]


def test_mark_visited_then_same_tier_keeps_visited(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, feeling="mild_symptoms")
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    visited = mark_visited(store, f"{round_id}_r1", "supplies_given", now + timedelta(hours=1))
    assert visited["status"] == "visited"
    again = upsert_case(store, round_id, "r1", now + timedelta(hours=2))
    assert again["status"] == "visited"
    assert again["outcome"] == "supplies_given"
    assert again["visited_at"] == now + timedelta(hours=1)


def test_escalation_after_visit_reopens_case(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, feeling="mild_symptoms")
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    mark_visited(store, f"{round_id}_r1", "advised_ok", now)
    answer(store, call_id, now, feeling="severe_symptoms")
    reopened = upsert_case(store, round_id, "r1", now)
    assert reopened["tier"] == "red"
    assert reopened["status"] == "open"
    assert reopened["visited_at"] is None


class FlakyQueue(RecordingQueue):
    """Raises on the first enqueue, then behaves like RecordingQueue."""

    def __init__(self) -> None:
        super().__init__()
        self.failures_left = 1

    def enqueue(self, call_id, run_at) -> None:
        if self.failures_left:
            self.failures_left -= 1
            raise RuntimeError("queue unavailable")
        super().enqueue(call_id, run_at)


def test_recording_queue_ignores_a_repeated_call_id():
    queue = RecordingQueue()
    first = datetime(2026, 5, 20, 5, 0, tzinfo=timezone.utc)
    queue.enqueue("c1", first)
    queue.enqueue("c1", first + timedelta(hours=1))
    assert queue.items == [("c1", first)]


def test_plan_round_recovers_after_a_failed_enqueue(store, tz, now):
    add_ward(store)
    round_id = record_alert(store, make_replay_alert("d1", "heat", "red", now), tz).id
    add_resident(store, "r1")
    queue = FlakyQueue()
    with pytest.raises(RuntimeError):
        plan_round(store, queue, round_id, now, tz)
    assert plan_round(store, queue, round_id, now, tz) == []
    assert [item[0] for item in queue.items] == [f"{round_id}_r1_1"]


def test_retry_enqueue_recovers_after_a_failed_enqueue(store, tz, now):
    round_id, _ = start(store, tz, now)
    queue = FlakyQueue()
    queue.failures_left = 1
    call_id = f"{round_id}_r1_1"
    with pytest.raises(RuntimeError):
        handle_call_outcome(store, queue, call_id, "no_answer", now, tz)
    handle_call_outcome(store, queue, call_id, "no_answer", now, tz)
    retries = [c for c in store.query("calls") if c["attempt"] == 2]
    assert len(retries) == 1
    assert queue.items == [(f"{round_id}_r1_2", now + timedelta(minutes=20))]


def test_stale_failure_after_completed_changes_nothing(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, feeling="mild_symptoms")
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    case_before = store.get("cases", f"{round_id}_r1")
    queued_before = list(queue.items)
    handle_call_outcome(store, queue, call_id, "no_answer", now + timedelta(minutes=5), tz)
    assert store.get("calls", call_id)["status"] == "completed"
    assert len(store.query("calls")) == 1
    assert queue.items == queued_before
    assert store.get("cases", f"{round_id}_r1") == case_before


def test_late_completed_after_failure_changes_nothing(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    handle_call_outcome(store, queue, call_id, "no_answer", now, tz)
    handle_call_outcome(store, queue, call_id, "completed", now + timedelta(minutes=5), tz)
    assert store.get("calls", call_id)["status"] == "no_answer"
    assert store.query("cases") == []
    assert len(store.query("calls")) == 2


@pytest.mark.parametrize("status", ["no-answer", "in_progress", "exploded"])
def test_unknown_or_non_final_status_is_rejected_and_writes_nothing(store, tz, now, status):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    before = store.get("calls", call_id)
    queued_before = list(queue.items)
    with pytest.raises(ValueError):
        handle_call_outcome(store, queue, call_id, status, now, tz)
    assert store.get("calls", call_id) == before
    assert len(store.query("calls")) == 1
    assert queue.items == queued_before
    assert store.query("cases") == []


def test_mark_call_started_sets_started_at_once(store, tz, now):
    round_id, _ = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    mark_call_started(store, call_id, "dialing", now)
    dialing = store.get("calls", call_id)
    assert dialing["status"] == "dialing"
    assert dialing["started_at"] is None
    mark_call_started(store, call_id, "in_progress", now + timedelta(seconds=10))
    mark_call_started(store, call_id, "in_progress", now + timedelta(seconds=40))
    call = store.get("calls", call_id)
    assert call["status"] == "in_progress"
    assert call["started_at"] == now + timedelta(seconds=10)
    assert call["ended_at"] is None
    assert store.query("cases") == []


def test_mark_call_started_can_store_the_settings_of_the_voice_session(store, tz, now):
    round_id, _ = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    mark_call_started(store, call_id, "dialing", now)
    assert store.get("calls", call_id)["live_settings"] == {}
    mark_call_started(store, call_id, "in_progress", now, live_settings={"live_voice": "Kore"})
    assert store.get("calls", call_id)["live_settings"] == {"live_voice": "Kore"}
    # a later progress callback from the phone line carries none, and wipes nothing
    mark_call_started(store, call_id, "in_progress", now)
    assert store.get("calls", call_id)["live_settings"] == {"live_voice": "Kore"}


def test_mark_call_started_is_a_no_op_on_a_finalised_call(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, feeling="mild_symptoms")
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    before = store.get("calls", call_id)
    mark_call_started(store, call_id, "in_progress", now + timedelta(minutes=1))
    mark_call_started(store, call_id, "dialing", now + timedelta(minutes=1))
    assert store.get("calls", call_id) == before


def test_mark_call_started_rejects_other_statuses_and_unknown_calls(store, tz, now):
    round_id, _ = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    before = store.get("calls", call_id)
    for status in ("completed", "queued", "in-progress"):
        with pytest.raises(ValueError):
            mark_call_started(store, call_id, status, now)
    assert store.get("calls", call_id) == before
    with pytest.raises(KeyError):
        mark_call_started(store, "nope", "dialing", now)


def test_different_final_status_after_dropped_still_creates_the_case(store, tz, now):
    # The process died after storing "dropped" and before the case was written.
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, feeling="severe_symptoms")
    store.update("calls", call_id, {"status": "dropped", "ended_at": now})
    assert store.query("cases") == []
    handle_call_outcome(store, queue, call_id, "completed", now + timedelta(minutes=5), tz)
    call = store.get("calls", call_id)
    assert call["status"] == "dropped"
    assert call["ended_at"] == now
    case = store.get("cases", f"{round_id}_r1")
    assert case["tier"] == "red"
    assert "R2" in case["reasons"]


def test_different_final_status_after_no_answer_still_creates_the_retry(store, tz, now):
    # The process died after storing "no_answer" and before the retry was created.
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    store.update("calls", call_id, {"status": "no_answer", "ended_at": now})
    later = now + timedelta(minutes=5)
    handle_call_outcome(store, queue, call_id, "busy", later, tz)
    handle_call_outcome(store, queue, call_id, "completed", later, tz)
    call = store.get("calls", call_id)
    assert call["status"] == "no_answer"
    assert call["ended_at"] == now
    retries = [c for c in store.query("calls") if c["attempt"] == 2]
    assert len(retries) == 1
    assert [item for item in queue.items if item[0] == f"{round_id}_r1_2"] == [
        (f"{round_id}_r1_2", later + timedelta(minutes=20))
    ]
    assert store.query("cases") == []


def test_red_case_stays_red_when_later_answers_are_healthy(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, feeling="severe_symptoms")
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    first = store.get("cases", f"{round_id}_r1")
    assert first["tier"] == "red"
    answer(store, call_id, now, **HEALTHY)
    again = upsert_case(store, round_id, "r1", now + timedelta(minutes=5))
    assert again["tier"] == "red"
    assert again["reasons"] == first["reasons"]
    assert "R2" in again["reasons"]
    assert "G1" not in again["reasons"]
    assert store.get("cases", f"{round_id}_r1")["tier"] == "red"


def test_amber_case_stays_amber_when_later_answers_are_healthy(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, **{**HEALTHY, "fluids_food": "low"})
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    assert store.get("cases", f"{round_id}_r1")["reasons"] == ["A2"]
    answer(store, call_id, now, **HEALTHY)
    again = upsert_case(store, round_id, "r1", now + timedelta(minutes=5))
    assert again["tier"] == "amber"
    assert again["reasons"] == ["A2"]


def test_lower_tier_keeps_old_reasons_and_adds_new_ones(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, **{**HEALTHY, "feeling": "severe_symptoms"})
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    answer(store, call_id, now, **{**HEALTHY, "shelter": "none"})
    again = upsert_case(store, round_id, "r1", now + timedelta(minutes=5))
    assert again["tier"] == "red"
    assert again["reasons"] == ["R2", "A3"]


def test_visited_red_case_stays_visited_when_later_answers_are_healthy(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, feeling="severe_symptoms")
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    mark_visited(store, f"{round_id}_r1", "referred", now + timedelta(hours=1))
    answer(store, call_id, now, **HEALTHY)
    again = upsert_case(store, round_id, "r1", now + timedelta(hours=2))
    assert again["tier"] == "red"
    assert again["status"] == "visited"
    assert again["outcome"] == "referred"
    assert again["visited_at"] == now + timedelta(hours=1)


def test_non_string_answer_value_makes_an_amber_case_not_a_crash(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, **{**HEALTHY, "shelter": ["adequate"]})
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    case = store.get("cases", f"{round_id}_r1")
    assert case["tier"] == "amber"
    assert case["reasons"] == ["A11"]


def test_equal_tier_recompute_keeps_earlier_red_reasons(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    store.update("calls", call_id, {"status": "in_progress"})
    answer(store, call_id, now, feeling="severe_symptoms")
    first = upsert_case(store, round_id, "r1", now)
    assert "R2" in first["reasons"]
    # the stored answer changes to a different red trigger
    answer(store, call_id, now, feeling="ok", help="urgent")
    second = upsert_case(store, round_id, "r1", now)
    assert second["tier"] == "red"
    assert "R3" in second["reasons"]
    assert "R2" in second["reasons"]


def test_equal_tier_recompute_drops_stale_amber_reasons(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    store.update("calls", call_id, {"status": "in_progress"})
    answer(store, call_id, now, feeling="severe_symptoms")
    first = upsert_case(store, round_id, "r1", now)
    assert "A12" in first["reasons"]  # questions still unanswered mid-call
    answer(
        store, call_id, now, fluids_food="enough", company="with_someone",
        shelter="adequate", exposure="staying_in", help="none",
    )
    second = upsert_case(store, round_id, "r1", now)
    assert second["tier"] == "red"
    assert "A12" not in second["reasons"]


def test_reasons_list_red_codes_first(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    store.update("calls", call_id, {"status": "in_progress"})
    answer(store, call_id, now, feeling="severe_symptoms")
    reasons = upsert_case(store, round_id, "r1", now)["reasons"]
    assert any(not r.startswith("R") for r in reasons)
    first_other = next(i for i, r in enumerate(reasons) if not r.startswith("R"))
    assert all(not r.startswith("R") for r in reasons[first_other:])


def test_a_retry_is_always_a_phone_call(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    store.update("calls", call_id, {"channel": "browser"})
    handle_call_outcome(store, queue, call_id, "failed", now, tz)
    assert store.get("calls", f"{round_id}_r1_2")["channel"] == "phone"


# ---- a call that reached nobody is not a conversation

def test_a_dropped_call_with_no_evidence_is_an_unanswered_attempt(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    handle_call_outcome(store, queue, call_id, "dropped", now, tz)
    assert store.get("calls", call_id)["status"] == "no_answer"
    assert store.get("calls", f"{round_id}_r1_2")["attempt"] == 2
    assert queue.items[-1] == (f"{round_id}_r1_2", now + timedelta(minutes=20))
    assert store.query("cases") == []


def test_a_completed_call_ended_as_no_person_is_an_unanswered_attempt(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    store.update("calls", call_id, {"end_outcome": "no_person"})
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    assert store.get("calls", call_id)["status"] == "no_answer"
    assert store.get("calls", f"{round_id}_r1_2")["status"] == "queued"
    assert store.query("cases") == []


def test_three_connected_calls_that_reached_nobody_make_an_amber_did_not_answer_case(store, tz, now):
    round_id, queue = start(store, tz, now)
    for attempt in (1, 2, 3):
        handle_call_outcome(store, queue, f"{round_id}_r1_{attempt}", "dropped", now, tz)
    case = store.get("cases", f"{round_id}_r1")
    assert case["tier"] == "amber"
    assert case["reasons"] == ["A8"]
    assert len(store.query("calls")) == 3
    assert store.get("calls", f"{round_id}_r1_4") is None


def test_a_dropped_call_with_one_answer_stays_dropped_with_a_case(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, fluids_food="low")
    handle_call_outcome(store, queue, call_id, "dropped", now, tz)
    assert store.get("calls", call_id)["status"] == "dropped"
    assert len(store.query("calls")) == 1
    assert store.get("cases", f"{round_id}_r1") is not None


def test_someone_who_asked_to_stop_is_never_retried(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    store.update("calls", call_id, {"end_outcome": "declined"})
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    assert store.get("calls", call_id)["status"] == "completed"
    assert len(store.query("calls")) == 1
    assert [item[0] for item in queue.items] == [call_id]
    case = store.get("cases", f"{round_id}_r1")
    assert case["tier"] == "amber"
    # a completed call with no answers: no feeling was recorded (A9) and questions are missing (A12)
    assert case["reasons"] == ["A9", "A12"]


def test_a_late_completed_after_no_answer_stays_no_answer(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    handle_call_outcome(store, queue, call_id, "no_answer", now, tz)
    handle_call_outcome(store, queue, call_id, "completed", now + timedelta(minutes=5), tz)
    assert store.get("calls", call_id)["status"] == "no_answer"
    assert store.query("cases") == []


def test_evidence_arriving_after_the_conversion_does_not_resurrect_the_call(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    handle_call_outcome(store, queue, call_id, "dropped", now, tz)
    assert store.get("calls", call_id)["status"] == "no_answer"
    answer(store, call_id, now, feeling="mild_symptoms")
    handle_call_outcome(store, queue, call_id, "completed", now + timedelta(minutes=1), tz)
    assert store.get("calls", call_id)["status"] == "no_answer"
    assert store.query("cases") == []
    assert len(store.query("calls")) == 2


# ---- fix round 1: a red fact is never lost to the "nobody reached" rule

def stored_no_answer(store, queue, tz, now, call_id):
    handle_call_outcome(store, queue, call_id, "dropped", now, tz)
    assert store.get("calls", call_id)["status"] == "no_answer"
    assert store.query("cases") == []


def test_an_emergency_flagged_after_the_conversion_still_opens_a_red_case(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    stored_no_answer(store, queue, tz, now, call_id)
    store.update("calls", call_id, {"emergency": {"flagged": True, "reason": "fainted", "at": now}})
    case = upsert_case(store, round_id, "r1", now)
    assert case["tier"] == "red"
    assert "R1" in case["reasons"]
    assert store.get("calls", call_id)["status"] == "no_answer"


@pytest.mark.parametrize(
    "values, code", [({"feeling": "severe_symptoms"}, "R2"), ({"help": "urgent"}, "R3")]
)
def test_a_red_answer_written_after_the_conversion_still_opens_a_red_case(store, tz, now, values, code):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    stored_no_answer(store, queue, tz, now, call_id)
    answer(store, call_id, now, **values)
    case = upsert_case(store, round_id, "r1", now)
    assert case["tier"] == "red"
    assert code in case["reasons"]
    assert store.get("calls", call_id)["status"] == "no_answer"


def test_flag_emergency_through_the_tools_on_a_no_answer_call_opens_a_red_case(store, tz, now):
    from app.call.tools import CallTools

    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    stored_no_answer(store, queue, tz, now, call_id)
    CallTools(store, call_id, lambda: now).handle("flag_emergency", {"reason": "collapsed"})
    case = store.get("cases", f"{round_id}_r1")
    assert case["tier"] == "red"
    assert "R1" in case["reasons"]


def test_an_ordinary_answer_after_the_conversion_creates_no_case(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    stored_no_answer(store, queue, tz, now, call_id)
    answer(store, call_id, now, fluids_food="low")
    assert upsert_case(store, round_id, "r1", now) is None
    assert store.query("cases") == []


def test_a_red_call_is_a_conversation_and_not_also_a_failed_attempt(store, tz, now):
    round_id, queue = start(store, tz, now)
    first = f"{round_id}_r1_1"
    stored_no_answer(store, queue, tz, now, first)
    store.update("calls", first, {"emergency": {"flagged": True, "reason": "fainted", "at": now}})
    handle_call_outcome(store, queue, f"{round_id}_r1_2", "no_answer", now, tz)
    handle_call_outcome(store, queue, f"{round_id}_r1_3", "no_answer", now, tz)
    case = store.get("cases", f"{round_id}_r1")
    assert case["tier"] == "red"
    assert "R1" in case["reasons"]
    # attempt 1 counts as the conversation, so only two attempts failed and the resident is
    # not "unreachable": there is no A8
    assert "A8" not in case["reasons"]


@pytest.mark.parametrize(
    "call, expected",
    [
        ({}, False),
        ({"answers": None, "emergency": None}, False),
        ({"answers": {"feeling": None, "help": None}}, False),
        ({"answers": {"feeling": {"value": "ok"}, "help": {"value": "none"}}}, False),
        ({"emergency": {"flagged": False}}, False),
        ({"emergency": {"flagged": True}}, True),
        ({"answers": {"feeling": {"value": "severe_symptoms"}}}, True),
        ({"answers": {"help": {"value": "urgent"}}}, True),
    ],
)
def test_has_red_fact(call, expected):
    from app.cases import has_red_fact

    assert has_red_fact(call) is expected


# ---- fix round 1: smaller items

def test_evidence_beats_a_no_person_end_outcome(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    store.update("calls", call_id, {"end_outcome": "no_person", "respondent": "self"})
    answer(store, call_id, now, fluids_food="low")
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    assert store.get("calls", call_id)["status"] == "completed"
    assert len(store.query("calls")) == 1
    assert store.get("cases", f"{round_id}_r1") is not None


def test_a_second_unanswered_attempt_converted_by_the_rule_retries_in_60_minutes(store, tz, now):
    round_id, queue = start(store, tz, now)
    handle_call_outcome(store, queue, f"{round_id}_r1_1", "no_answer", now, tz)
    later = now + timedelta(minutes=21)
    handle_call_outcome(store, queue, f"{round_id}_r1_2", "dropped", later, tz)
    assert store.get("calls", f"{round_id}_r1_2")["status"] == "no_answer"
    assert queue.items[-1] == (f"{round_id}_r1_3", later + timedelta(minutes=60))


def test_the_outcomes_that_count_as_a_person_plus_no_person_are_every_end_outcome():
    from app.call.guidance import END_CALL_OUTCOMES
    from app.dispatch import PERSON_OUTCOMES

    assert PERSON_OUTCOMES | {"no_person"} == set(END_CALL_OUTCOMES)


def test_a_red_fact_that_opens_no_case_is_logged_with_the_call_id(store, tz, now, monkeypatch, caplog):
    import logging

    import app.call.tools as tools_module

    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    monkeypatch.setattr(tools_module, "upsert_case", lambda *args: None)
    with caplog.at_level(logging.ERROR):
        tools_module.CallTools(store, call_id, lambda: now).handle("flag_emergency", {"reason": "x"})
    assert any(call_id in r.getMessage() for r in caplog.records)


# ---- final review: an agent that ended the call as an emergency is a red fact on its own

def test_an_emergency_end_outcome_is_a_red_fact():
    from app.cases import has_red_fact

    assert has_red_fact({"end_outcome": "emergency"}) is True
    assert has_red_fact({"end_outcome": "completed"}) is False
    assert has_red_fact({"end_outcome": None, "emergency": None, "answers": None}) is False


def test_a_call_whose_only_red_signal_is_its_emergency_ending_gives_a_red_case(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    store.update("calls", call_id, {"end_outcome": "emergency"})
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    assert store.get("calls", call_id)["status"] == "completed"
    case = store.get("cases", f"{round_id}_r1")
    assert case["tier"] == "red"
    assert case["reasons"][0] == "R1"


def interfere_before_transition(store, monkeypatch, interfere, times=1):
    """Run `interfere()` just before each of the first `times` transitions on a call, as a
    competing writer landing between handle_call_outcome's read and its write would."""
    real = store.transition
    left = [times]

    def transition(collection, doc_id, field, expected, fields):
        if collection == "calls" and field == "status" and left[0]:
            left[0] -= 1
            interfere()
        return real(collection, doc_id, field, expected, fields)

    monkeypatch.setattr(store, "transition", transition)


def test_a_failure_stored_between_read_and_write_wins_and_its_retry_is_made_once(store, tz, now, monkeypatch):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, feeling="mild_symptoms")
    earlier = now - timedelta(seconds=5)
    interfere_before_transition(
        store, monkeypatch, lambda: store.update("calls", call_id, {"status": "no_answer", "ended_at": earlier})
    )
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    call = store.get("calls", call_id)
    assert (call["status"], call["ended_at"]) == ("no_answer", earlier)
    assert [c["id"] for c in store.query("calls") if c["attempt"] == 2] == [f"{round_id}_r1_2"]
    assert [item for item in queue.items if item[0] == f"{round_id}_r1_2"] == [
        (f"{round_id}_r1_2", now + timedelta(minutes=20))
    ]
    assert store.query("cases") == []


def test_a_conversation_stored_between_read_and_write_wins_and_its_case_is_made_once(store, tz, now, monkeypatch):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, feeling="mild_symptoms")
    earlier = now - timedelta(seconds=5)
    interfere_before_transition(
        store, monkeypatch, lambda: store.update("calls", call_id, {"status": "completed", "ended_at": earlier})
    )
    handle_call_outcome(store, queue, call_id, "no_answer", now, tz)
    call = store.get("calls", call_id)
    assert (call["status"], call["ended_at"]) == ("completed", earlier)
    assert len(store.query("calls")) == 1  # no retry
    assert [item[0] for item in queue.items] == [call_id]
    assert [case["tier"] for case in store.query("cases")] == ["amber"]


def test_a_non_final_change_between_read_and_write_is_tried_once_more(store, tz, now, monkeypatch):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    interfere_before_transition(store, monkeypatch, lambda: store.update("calls", call_id, {"status": "dialing"}))
    handle_call_outcome(store, queue, call_id, "busy", now, tz)
    assert store.get("calls", call_id)["status"] == "busy"
    assert store.get("calls", f"{round_id}_r1_2")["status"] == "queued"


def test_the_nobody_reached_rule_uses_the_document_read_for_the_write_that_succeeds(store, tz, now, monkeypatch):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    # Between the first read and the write, the call starts and a person answers a question.
    interfere_before_transition(
        store, monkeypatch,
        lambda: store.update(
            "calls", call_id,
            {"status": "in_progress", "answers.fluids_food": {"value": "low", "quote": "", "at": now}},
        ),
    )
    handle_call_outcome(store, queue, call_id, "dropped", now, tz)
    assert store.get("calls", call_id)["status"] == "dropped"  # not converted to no_answer
    assert len(store.query("calls")) == 1
    assert store.get("cases", f"{round_id}_r1")["tier"] == "amber"


def test_a_status_that_keeps_changing_raises_and_stores_no_final_status(store, tz, now, monkeypatch):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    flip = iter(["dialing", "in_progress"])
    interfere_before_transition(
        store, monkeypatch, lambda: store.update("calls", call_id, {"status": next(flip)}), times=2
    )
    with pytest.raises(RuntimeError):
        handle_call_outcome(store, queue, call_id, "busy", now, tz)
    call = store.get("calls", call_id)
    assert call["status"] == "in_progress" and call["ended_at"] is None
    assert len(store.query("calls")) == 1 and store.query("cases") == []
    handle_call_outcome(store, queue, call_id, "busy", now, tz)  # the caller's retry then succeeds
    assert store.get("calls", call_id)["status"] == "busy"


def test_mark_call_started_does_not_reopen_a_call_finished_between_read_and_write(store, tz, now, monkeypatch):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    interfere_before_transition(
        store, monkeypatch, lambda: store.update("calls", call_id, {"status": "completed", "ended_at": now})
    )
    mark_call_started(store, call_id, "in_progress", now + timedelta(seconds=1))
    call = store.get("calls", call_id)
    assert call["status"] == "completed"
    assert call["started_at"] is None


def test_mark_call_started_follows_a_non_final_change_between_read_and_write(store, tz, now, monkeypatch):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    interfere_before_transition(store, monkeypatch, lambda: store.update("calls", call_id, {"status": "dialing"}))
    mark_call_started(store, call_id, "in_progress", now)
    call = store.get("calls", call_id)
    assert (call["status"], call["started_at"]) == ("in_progress", now)


def test_a_red_case_written_while_an_amber_one_was_being_computed_is_not_overwritten(store, tz, now, monkeypatch):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    case_id = f"{round_id}_r1"
    store.update("calls", call_id, {"status": "completed"})
    answer(store, call_id, now, fluids_food="low")
    red = {
        "id": case_id, "round_id": round_id, "resident_id": "r1", "ward_id": "w1", "worker_id": "k1",
        "tier": "red", "reasons": ["R1"], "note": "", "status": "open", "visited_at": None,
        "outcome": None, "updated_at": now,
    }
    real_get = store.get
    reads = []

    def get(collection, doc_id):
        if collection == "cases":
            reads.append(doc_id)
            if len(reads) == 2:  # the read just before the write: someone else got there first
                store.set("cases", case_id, red)
        return real_get(collection, doc_id)

    monkeypatch.setattr(store, "get", get)
    written = upsert_case(store, round_id, "r1", now + timedelta(seconds=1))
    assert len(reads) == 2
    stored = real_get("cases", case_id)
    assert written == stored
    assert stored["tier"] == "red"
    assert stored["reasons"][0] == "R1"
    assert "A2" in stored["reasons"]


def test_an_emergency_ending_on_a_call_stored_as_no_answer_still_opens_a_red_case(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    stored_no_answer(store, queue, tz, now, call_id)
    store.update("calls", call_id, {"end_outcome": "emergency"})
    case = upsert_case(store, round_id, "r1", now)
    assert case["tier"] == "red"
    assert "R1" in case["reasons"]


def test_a_case_remembers_when_it_was_opened(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, feeling="mild_symptoms")
    opened = now + timedelta(minutes=7)
    handle_call_outcome(store, queue, call_id, "completed", opened, tz)
    assert store.get("cases", f"{round_id}_r1")["opened_at"] == opened
    answer(store, call_id, now, feeling="severe_symptoms")
    later = upsert_case(store, round_id, "r1", opened + timedelta(minutes=30))
    assert later["tier"] == "red"
    assert later["opened_at"] == opened


def test_a_case_carries_the_visitor_tag_of_its_resident(store, tz, now):
    round_id, queue = start(store, tz, now)
    store.update("residents", "r1", {"visitor_id": "visitor-aaaaaaaaaaaa"})
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, feeling="mild_symptoms")
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    assert store.get("cases", f"{round_id}_r1")["visitor_id"] == "visitor-aaaaaaaaaaaa"


def amber_case(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, feeling="mild_symptoms")
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    return f"{round_id}_r1"


@pytest.mark.parametrize("outcome", sorted(VISIT_OUTCOMES))
def test_every_listed_outcome_is_accepted(store, tz, now, outcome):
    case_id = amber_case(store, tz, now)
    visited = mark_visited(store, case_id, outcome, now + timedelta(hours=1))
    assert (visited["status"], visited["outcome"]) == ("visited", outcome)
    assert visited["visited_at"] == now + timedelta(hours=1)


def test_a_visit_is_refused_for_an_unlisted_outcome(store, tz, now):
    case_id = amber_case(store, tz, now)
    with pytest.raises(VisitRefused) as refusal:
        mark_visited(store, case_id, "had a nice chat", now)
    assert refusal.value.reason == "unknown_outcome"
    assert store.get("cases", case_id)["status"] == "open"


def test_a_second_visit_is_refused_and_changes_nothing(store, tz, now):
    case_id = amber_case(store, tz, now)
    mark_visited(store, case_id, "referred", now + timedelta(hours=1))
    before = store.get("cases", case_id)
    with pytest.raises(VisitRefused) as refusal:
        mark_visited(store, case_id, "hospital", now + timedelta(hours=2))
    assert refusal.value.reason == "already_visited"
    assert store.get("cases", case_id) == before


def test_a_green_case_cannot_be_visited(store, tz, now):
    round_id, queue = start(store, tz, now)
    call_id = f"{round_id}_r1_1"
    answer(store, call_id, now, **HEALTHY)
    handle_call_outcome(store, queue, call_id, "completed", now, tz)
    assert store.get("cases", f"{round_id}_r1")["tier"] == "green"
    with pytest.raises(VisitRefused) as refusal:
        mark_visited(store, f"{round_id}_r1", "advised_ok", now)
    assert refusal.value.reason == "green_case"


def test_visiting_an_unknown_case_is_a_key_error(store, now):
    with pytest.raises(KeyError):
        mark_visited(store, "nope", "advised_ok", now)


def _two_conversations(store, tz, now, first, second):
    round_id, queue = start(store, tz, now)
    one, two = f"{round_id}_r1_1", f"{round_id}_r1_2"
    store.set("calls", two, {**store.get("calls", one), "id": two, "attempt": 2})
    answer(store, one, now, feeling=first)
    answer(store, two, now, feeling=second)
    store.update("calls", one, {"status": "dropped", "ended_at": now})
    store.update("calls", two, {"status": "completed", "ended_at": now})
    upsert_case(store, round_id, "r1", now)
    return store.get("cases", f"{round_id}_r1")


@pytest.mark.parametrize("first,second", [("severe_symptoms", "ok"), ("ok", "severe_symptoms")])
def test_a_red_answer_on_either_attempt_is_never_lost(store, tz, now, first, second):
    case = _two_conversations(store, tz, now, first, second)
    assert case["tier"] == "red" and "R2" in case["reasons"]
