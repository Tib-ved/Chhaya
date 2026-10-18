from datetime import datetime, timedelta, timezone

import pytest

from app.dispatch import RETRY_DELAYS_MIN, RecordingQueue, clamp_to_calling_hours, plan_round
from app.ingest.alerts import record_alert
from app.ingest.replay import make_replay_alert
from app.models import MAX_ATTEMPTS, Consent
from tests.helpers import add_resident, add_ward


def open_round(store, tz, now):
    add_ward(store)
    return record_alert(store, make_replay_alert("d1", "heat", "red", now), tz).id


def test_clamp_inside_hours_is_unchanged(tz, now):
    assert clamp_to_calling_hours(now, tz) == now


def test_clamp_early_morning_moves_to_eight(tz):
    early = datetime(2026, 5, 20, 0, 30, tzinfo=timezone.utc)  # 06:00 local
    assert clamp_to_calling_hours(early, tz) == datetime(2026, 5, 20, 2, 30, tzinfo=timezone.utc)


def test_clamp_at_closing_time_moves_to_next_morning(tz):
    closing = datetime(2026, 5, 20, 14, 30, tzinfo=timezone.utc)  # 20:00 local
    assert clamp_to_calling_hours(closing, tz) == datetime(2026, 5, 21, 2, 30, tzinfo=timezone.utc)


def test_plan_round_queues_highest_risk_first(store, tz, now):
    round_id = open_round(store, tz, now)
    add_resident(store, "low", age=60)
    add_resident(store, "high", age=80, lives_alone=True, conditions=["heart"])
    add_resident(store, "mid", age=70)
    queue = RecordingQueue()

    created = plan_round(store, queue, round_id, now, tz)

    assert created == [f"{round_id}_high_1", f"{round_id}_mid_1", f"{round_id}_low_1"]
    assert [call_id for call_id, _ in queue.items] == created
    assert queue.items[0][1] == now
    assert queue.items[0][1] < queue.items[1][1] < queue.items[2][1]
    assert store.get("calls", created[0])["status"] == "queued"
    assert store.get("rounds", round_id)["status"] == "calling"
    assert store.get("rounds", round_id)["started_at"] == now


def test_equal_risk_orders_oldest_first(store, tz, now):
    round_id = open_round(store, tz, now)
    add_resident(store, "a", age=66)
    add_resident(store, "b", age=72)
    created = plan_round(store, RecordingQueue(), round_id, now, tz)
    assert created == [f"{round_id}_b_1", f"{round_id}_a_1"]


def test_plan_round_skips_ineligible(store, tz, now):
    round_id = open_round(store, tz, now)
    add_resident(store, "ok")
    add_resident(store, "no_consent", consent=Consent(given=False))
    add_resident(store, "bad_phone", phone="98765")
    add_resident(store, "empty_phone", phone="")
    add_resident(store, "trailing_newline", phone="+910000000001\n")
    add_resident(store, "elsewhere", district_id="d2")
    created = plan_round(store, RecordingQueue(), round_id, now, tz)
    assert created == [f"{round_id}_ok_1"]


def test_plan_round_at_night_waits_for_morning(store, tz):
    night = datetime(2026, 5, 20, 16, 0, tzinfo=timezone.utc)  # 21:30 local
    round_id = open_round(store, tz, night)
    add_resident(store, "r1")
    queue = RecordingQueue()
    plan_round(store, queue, round_id, night, tz)
    assert queue.items[0][1] == datetime(2026, 5, 21, 2, 30, tzinfo=timezone.utc)


def test_every_staggered_slot_respects_calling_hours(store, tz):
    late = datetime(2026, 5, 20, 14, 29, 58, tzinfo=timezone.utc)  # 19:59:58 local
    round_id = open_round(store, tz, late)
    add_resident(store, "low", age=60)
    add_resident(store, "mid", age=70)
    add_resident(store, "high", age=80, lives_alone=True, conditions=["heart"])
    add_resident(store, "top", age=85, lives_alone=True, conditions=["heart", "diabetes"])
    queue = RecordingQueue()

    created = plan_round(store, queue, round_id, late, tz)

    assert created == [
        f"{round_id}_top_1",
        f"{round_id}_high_1",
        f"{round_id}_mid_1",
        f"{round_id}_low_1",
    ]
    assert [run_at for _, run_at in queue.items] == [
        datetime(2026, 5, 20, 14, 29, 58, tzinfo=timezone.utc),
        datetime(2026, 5, 20, 14, 29, 59, tzinfo=timezone.utc),
        datetime(2026, 5, 21, 2, 30, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 21, 2, 30, 0, tzinfo=timezone.utc),
    ]


def test_plan_round_skips_a_complete_round(store, tz, now):
    round_id = open_round(store, tz, now)
    add_resident(store, "r1")
    store.update("rounds", round_id, {"status": "complete"})
    queue = RecordingQueue()

    assert plan_round(store, queue, round_id, now, tz) == []
    assert queue.items == []
    assert store.query("calls") == []


def test_plan_round_is_idempotent(store, tz, now):
    round_id = open_round(store, tz, now)
    add_resident(store, "r1")
    queue = RecordingQueue()
    plan_round(store, queue, round_id, now, tz)
    started_at = store.get("rounds", round_id)["started_at"]
    assert plan_round(store, queue, round_id, now + timedelta(minutes=5), tz) == []
    assert len(queue.items) == 1
    assert len(store.query("calls")) == 1
    assert store.get("rounds", round_id)["started_at"] == started_at


def test_plan_round_unknown_round_raises(store, tz, now):
    with pytest.raises(KeyError):
        plan_round(store, RecordingQueue(), "nope", now, tz)


def test_every_attempt_that_can_be_retried_has_a_delay():
    assert set(RETRY_DELAYS_MIN) == set(range(1, MAX_ATTEMPTS))


def test_a_visitors_temporary_resident_is_never_dispatched(store, tz, now):
    from app.dispatch import RecordingQueue, plan_round
    from app.ingest.alerts import record_alert
    from app.ingest.replay import make_replay_alert
    from tests.helpers import add_resident, add_ward

    add_ward(store)
    round_id = record_alert(store, make_replay_alert("d1", "heat", "red", now), tz).id
    add_resident(store, "r1")
    add_resident(store, "v-1", visitor_id="visitor-aaaaaaaaaaaa")  # has a valid phone on purpose
    queue = RecordingQueue()
    assert plan_round(store, queue, round_id, now, tz) == [f"{round_id}_r1_1"]
    assert [call_id for call_id, _ in queue.items] == [f"{round_id}_r1_1"]


def test_planned_calls_and_retries_carry_their_due_time(store, tz, now):
    from datetime import timedelta

    from app.dispatch import RecordingQueue, handle_call_outcome
    from tests.helpers import queue_one_call

    round_id, call_id, queue = queue_one_call(store, tz, now)
    assert store.get("calls", call_id)["not_before"] == now
    handle_call_outcome(store, queue, call_id, "no_answer", now, tz)
    retry = store.get("calls", f"{round_id}_r1_2")
    assert retry["not_before"] == now + timedelta(minutes=20)
    assert queue.items[-1] == (retry["id"], retry["not_before"])
