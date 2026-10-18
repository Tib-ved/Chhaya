from datetime import timedelta

import pytest

from app.demo import open_rounds, pinned, pinned_round_id, pinned_rounds, warned_districts
from app.region import round_of_district
from seed.seed import build_all, load_seed

RAJASTHAN = ["bikaner", "churu", "jhunjhunu", "sikar"]


def opened(store, now, tz):
    load_seed(store, build_all([]))
    return open_rounds(store, "churu", now, tz)


def test_nothing_is_pinned_before_the_first_morning(store):
    assert pinned(store) is None and pinned_round_id(store) is None and pinned_rounds(store) == {}


def test_the_morning_opens_a_real_round_for_each_rajasthan_district(store, tz, now):
    pointer = opened(store, now, tz)
    day = now.astimezone(tz).date()
    assert warned_districts(store) == RAJASTHAN
    assert pointer["rounds"] == {d: f"{d}_heat_{day:%Y%m%d}" for d in RAJASTHAN}
    assert pointer["round_id"] == pointer["rounds"]["churu"]
    for district_id, round_id in pointer["rounds"].items():
        current = store.get("rounds", round_id)
        alert = store.get("alerts", current["alert_id"])
        assert alert["source"] == "scenario" and alert["severity"] in ("red", "orange")
        assert alert["valid_to"] - alert["valid_from"] == timedelta(hours=48)


def test_nothing_is_called_or_scripted(store, tz, now):
    opened(store, now, tz)
    assert store.query("calls") == [] and store.query("cases") == []


def test_no_round_outside_rajasthan(store, tz, now):
    opened(store, now, tz)
    assert not store.query("rounds", district_id="sangli")
    assert round_of_district(store, "sangli") is None
    with pytest.raises(KeyError):
        open_rounds(store, "sangli", now, tz)


def test_the_same_morning_twice_changes_nothing(store, tz, now):
    first = opened(store, now, tz)
    second = open_rounds(store, "churu", now + timedelta(minutes=5), tz)
    assert second["rounds"] == first["rounds"]
    assert len(store.query("rounds")) == 4


def test_the_next_morning_keeps_yesterdays_rounds_and_calls(store, tz, now):
    first = opened(store, now, tz)
    store.set("calls", "kept", {"id": "kept", "round_id": first["rounds"]["churu"], "transcript": [{"role": "agent", "text": "Namaste"}]})
    second = open_rounds(store, "churu", now + timedelta(days=1), tz)
    assert set(first["rounds"].values()).isdisjoint(second["rounds"].values())
    assert len(store.query("rounds")) == 8
    assert store.get("calls", "kept")["transcript"][0]["text"] == "Namaste"
    assert round_of_district(store, "churu") == second["rounds"]["churu"]


def test_a_round_the_forecast_opened_first_is_kept(store, tz, now):
    from app.ingest.alerts import record_alert
    from app.ingest.replay import make_replay_alert

    load_seed(store, build_all([]))
    early = record_alert(store, make_replay_alert("bikaner", "heat", "red", now), tz)
    pointer = open_rounds(store, "churu", now + timedelta(minutes=1), tz)
    assert pointer["rounds"]["bikaner"] == early.id
    assert store.get("rounds", early.id)["alert_id"] == early.alert_id


def test_an_old_pointer_still_shows_its_round(store, tz, now):
    pointer = opened(store, now, tz)
    store.set("demo", "current", {"id": "current", "round_id": pointer["round_id"], "built_at": now})
    assert round_of_district(store, "churu") == pointer["round_id"]
    assert round_of_district(store, "bikaner") is None


def test_a_round_the_old_job_declared_is_taken_over_by_the_scenario_alert(store, tz, now):
    from app.ingest.alerts import record_alert
    from app.models import Alert

    load_seed(store, build_all([]))
    day = now.astimezone(tz).date()
    six = now.astimezone(tz).replace(hour=6, minute=0)
    declared = Alert(
        id=f"declared_churu_heat_{day:%Y%m%d}", source="declared", hazard="heat", severity="red",
        district_id="churu", valid_from=six, valid_to=six + timedelta(hours=48),
        raw={"note": "heat alert declared for the district"}, received_at=six,
    )
    round_id = record_alert(store, declared, tz).id
    pointer = open_rounds(store, "churu", now, tz)
    assert pointer["rounds"]["churu"] == round_id
    current = store.get("rounds", round_id)
    assert current["alert_id"] == f"scenario_churu_heat_{day:%Y%m%d}"
    assert current["opened_at"] == six
    assert current["peak_temp_c"] == 47.5 and current["peak_hour_local"] == 15 and current["forecast_hours"]
    alert = store.get("alerts", current["alert_id"])
    assert alert["source"] == "scenario" and alert["received_at"] == six
    again = open_rounds(store, "churu", now + timedelta(minutes=5), tz)  # a second run changes nothing
    assert again["rounds"] == pointer["rounds"] and store.get("rounds", round_id) == current
