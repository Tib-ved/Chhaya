from datetime import timedelta

from app.ingest.alerts import record_alert
from app.ingest.replay import make_replay_alert


def test_red_alert_opens_a_round(store, tz, now):
    alert = make_replay_alert("d1", "heat", "red", now)
    rnd = record_alert(store, alert, tz)
    assert rnd.id == "d1_heat_20260520"
    assert rnd.status == "open"
    assert store.get("alerts", alert.id)["severity"] == "red"
    assert store.get("rounds", rnd.id)["alert_id"] == alert.id


def test_yellow_alert_is_stored_but_opens_no_round(store, tz, now):
    alert = make_replay_alert("d1", "heat", "yellow", now)
    assert record_alert(store, alert, tz) is None
    assert store.get("alerts", alert.id) is not None
    assert store.query("rounds") == []


def test_duplicate_alert_same_day_one_round(store, tz, now):
    first = record_alert(store, make_replay_alert("d1", "heat", "orange", now), tz)
    second = record_alert(store, make_replay_alert("d1", "heat", "red", now + timedelta(hours=2)), tz)
    assert second.id == first.id
    assert second.alert_id == first.alert_id
    assert len(store.query("rounds")) == 1
    assert len(store.query("alerts")) == 2


def test_cold_and_heat_get_separate_rounds(store, tz, now):
    record_alert(store, make_replay_alert("d1", "heat", "red", now), tz)
    record_alert(store, make_replay_alert("d1", "cold", "red", now), tz)
    assert len(store.query("rounds")) == 2


def test_round_day_uses_local_date(store, tz, now):
    # 19:00 UTC on the 20th is 00:30 on the 21st in Asia/Kolkata
    late = now.replace(hour=19, minute=0)
    rnd = record_alert(store, make_replay_alert("d1", "heat", "red", late), tz)
    assert rnd.id == "d1_heat_20260521"


def test_round_records_when_it_was_opened(store, tz, now):
    first = record_alert(store, make_replay_alert("d1", "heat", "orange", now), tz)
    assert first.opened_at == now
    assert store.get("rounds", first.id)["opened_at"] == now
    again = record_alert(store, make_replay_alert("d1", "heat", "red", now + timedelta(hours=2)), tz)
    assert again.opened_at == now
