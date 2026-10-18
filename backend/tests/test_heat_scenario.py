from datetime import date, timedelta

import pytest

from app.config import get_settings
from app.demo import open_rounds
from app.heat_scenario import forecast_payload, load_scenario, scenario_alert
from app.ingest.forecast import extremes
from seed.seed import build_all, load_seed

PEAKS = {"churu": 47.5, "bikaner": 47.0, "jhunjhunu": 46.0, "sikar": 45.5}
SEVERITY = {"churu": "red", "bikaner": "red", "jhunjhunu": "orange", "sikar": "orange"}


def test_each_curve_runs_from_six_to_twenty_and_peaks_in_the_afternoon():
    scenario = load_scenario()
    assert set(scenario) == set(PEAKS)
    for district_id, curve in scenario.items():
        hours = curve["hours"]
        assert [h["hour"] for h in hours] == list(range(6, 21))
        assert 30 <= hours[0]["temp_c"] <= 32
        top = max(hours, key=lambda h: h["temp_c"])
        assert top["temp_c"] == PEAKS[district_id] and top["hour"] in (14, 15)


@pytest.mark.parametrize("district_id", sorted(PEAKS))
def test_the_payload_reads_back_as_the_curve_through_extremes(district_id):
    day = date(2026, 10, 14)
    found = extremes(forecast_payload(district_id, day), day)
    assert found["max_c"] == PEAKS[district_id] and found["max_hour"] in (14, 15)
    assert extremes(forecast_payload(district_id, day), day + timedelta(days=1)) is None


@pytest.mark.parametrize("district_id", sorted(PEAKS))
def test_the_trigger_rules_decide_the_severity(district_id, tz, now):
    alert = scenario_alert({"id": district_id}, now, tz, get_settings())
    day = now.astimezone(tz).date()
    assert alert.id == f"scenario_{district_id}_heat_{day:%Y%m%d}"
    assert alert.source == "scenario" and alert.hazard == "heat" and alert.severity == SEVERITY[district_id]
    assert alert.valid_to - now == timedelta(hours=48)
    threshold = get_settings().heat_red_c if SEVERITY[district_id] == "red" else get_settings().heat_orange_c
    assert alert.raw["threshold_c"] == threshold and alert.raw["severity"] == SEVERITY[district_id]
    assert alert.raw["rule"] == f"forecast maximum at or above the {SEVERITY[district_id]} heat threshold"
    assert alert.raw["peak_c"] == PEAKS[district_id] and alert.raw["scenario"]
    assert len(alert.raw["forecast_hours"]) == 15


def test_a_curve_under_the_orange_threshold_gives_no_alert(tz, now, monkeypatch):
    cool = {"churu": {"name": "Mild day", "hours": [{"hour": h, "temp_c": 30.0 + (h == 14)} for h in range(6, 21)]}}
    monkeypatch.setattr("app.heat_scenario.load_scenario", lambda: cool)
    assert scenario_alert({"id": "churu"}, now, tz, get_settings()) is None


def test_open_rounds_raises_the_alert_through_the_rules(store, tz, now):
    load_seed(store, build_all([]))
    pointer = open_rounds(store, "churu", now, tz)
    for district_id, round_id in pointer["rounds"].items():
        current = store.get("rounds", round_id)
        alert = store.get("alerts", current["alert_id"])
        assert alert["source"] == "scenario" and alert["severity"] == SEVERITY[district_id]
        assert current["peak_temp_c"] == PEAKS[district_id]
        assert current["peak_hour_local"] in (14, 15)
        assert len(current["forecast_hours"]) == 15


def test_running_it_twice_the_same_day_changes_nothing(store, tz, now):
    load_seed(store, build_all([]))
    first = open_rounds(store, "churu", now, tz)
    rounds = {r["id"]: r for r in store.query("rounds")}
    second = open_rounds(store, "churu", now + timedelta(minutes=5), tz)
    assert second["rounds"] == first["rounds"]
    assert len(store.query("alerts")) == 4
    assert {r["id"]: r for r in store.query("rounds")} == rounds


def test_districts_outside_rajasthan_get_nothing(store, tz, now):
    load_seed(store, build_all([]))
    open_rounds(store, "churu", now, tz)
    assert not store.query("rounds", district_id="sangli")
    assert all(a["district_id"] in PEAKS for a in store.query("alerts"))


def test_a_second_run_leaves_the_alert_documents_as_the_first_wrote_them(store, tz, now):
    load_seed(store, build_all([]))
    open_rounds(store, "churu", now, tz)
    alerts = {a["id"]: a for a in store.query("alerts")}
    open_rounds(store, "churu", now + timedelta(minutes=30), tz)
    assert {a["id"]: a for a in store.query("alerts")} == alerts


def test_a_district_without_a_curve_keeps_the_declared_alert(store, tz, now, monkeypatch):
    load_seed(store, build_all([]))
    curves = load_scenario()
    del curves["sikar"]
    monkeypatch.setattr("app.heat_scenario.load_scenario", lambda: curves)
    pointer = open_rounds(store, "churu", now, tz)
    day = now.astimezone(tz).date()
    sikar = store.get("rounds", pointer["rounds"]["sikar"])
    alert = store.get("alerts", sikar["alert_id"])
    assert alert["id"] == f"declared_sikar_heat_{day:%Y%m%d}" and alert["source"] == "declared" and alert["severity"] == "red"
    assert sikar["peak_temp_c"] is None
    first = dict(alert)
    open_rounds(store, "churu", now + timedelta(minutes=30), tz)
    assert store.get("alerts", alert["id"]) == first
    assert store.get("alerts", store.get("rounds", pointer["rounds"]["churu"])["alert_id"])["source"] == "scenario"
