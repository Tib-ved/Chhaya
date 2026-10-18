import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.dispatch import RecordingQueue
from app.ingest.forecast import alerts_for, extremes, ingest_forecast
from app.weather import fetch_forecast
from tests.helpers import add_resident, add_ward

SAVED = json.loads((Path(__file__).parent.parent / "seed" / "fixtures" / "weather_hours.json").read_text(encoding="utf-8"))
SECRET = {"X-Internal-Secret": "s3cret"}
DISTRICT = {"id": "d1", "name": "Testpur", "lat": 21.1, "lng": 79.1}


def hours(*temps_by_hour):
    """A forecast with the given (local hour, degrees) pairs."""
    return {"forecastHours": [
        {"temperature": {"unit": "CELSIUS", "degrees": degrees}, "displayDateTime": {"hours": hour}}
        for hour, degrees in temps_by_hour
    ]}


@pytest.fixture
def settings():
    return Settings(_env_file=None, internal_secret="s3cret", weather_api_key="test-key-not-real")


def test_the_saved_response_is_read(settings):
    found = extremes(SAVED)
    assert set(found) == {"max_c", "max_hour", "min_c", "min_hour"}
    assert found["min_c"] <= found["max_c"]
    assert 0 <= found["max_hour"] <= 23 and 0 <= found["min_hour"] <= 23


@pytest.mark.parametrize("payload", [{}, {"forecastHours": []}, {"forecastHours": [{"temperature": {}}]}, {"forecastHours": "soon"}, None])
def test_a_response_with_no_temperatures_gives_nothing(payload):
    assert extremes(payload) is None


@pytest.mark.parametrize("peak,expected", [
    (44.9, []), (45, [("heat", "orange")]), (46.9, [("heat", "orange")]), (47, [("heat", "red")]),
])
def test_heat_thresholds(settings, tz, now, peak, expected):
    found = extremes(hours((6, 30), (15, peak), (22, 33)))
    alerts = alerts_for(DISTRICT, found, now, tz, settings)
    assert [(a.hazard, a.severity) for a in alerts] == expected
    for alert in alerts:
        assert alert.source == "weather"
        assert alert.id == "weather_d1_heat_20260520"
        assert alert.raw == {"max_c": peak, "max_hour": 15, "min_c": 30, "min_hour": 6}


@pytest.mark.parametrize("low,expected", [(4.1, []), (4, [("cold", "orange")]), (2, [("cold", "red")]), (-1, [("cold", "red")])])
def test_cold_thresholds(settings, tz, now, low, expected):
    alerts = alerts_for(DISTRICT, extremes(hours((4, low), (14, 18))), now, tz, settings)
    assert [(a.hazard, a.severity) for a in alerts] == expected


def test_a_hot_forecast_opens_one_round_with_the_peak_on_it(store, settings, tz, now):
    add_ward(store)
    add_resident(store, "r1")
    queue = RecordingQueue()
    result = ingest_forecast(store, queue, settings, lambda lat, lng: hours((15, 47.5), (5, 31)), now, tz)
    assert result == [{"district_id": "d1", "alerts": ["weather_d1_heat_20260520"], "rounds": ["d1_heat_20260520"], "error": None}]
    opened = store.get("rounds", "d1_heat_20260520")
    assert (opened["status"], opened["peak_temp_c"], opened["peak_hour_local"]) == ("open", 47.5, 15)
    assert store.get("alerts", "weather_d1_heat_20260520")["severity"] == "red"
    assert store.query("calls") == [] and queue.items == []  # opened, not dispatched


def test_a_second_run_the_same_day_opens_no_second_round(store, settings, tz, now):
    add_ward(store)
    fetch = lambda lat, lng: hours((15, 47.5))
    ingest_forecast(store, RecordingQueue(), settings, fetch, now, tz)
    again = ingest_forecast(store, RecordingQueue(), settings, fetch, now, tz)
    assert again[0]["rounds"] == ["d1_heat_20260520"]
    assert len(store.query("rounds")) == 1 and len(store.query("alerts")) == 1


def test_a_mild_forecast_opens_nothing(store, settings, tz, now):
    add_ward(store)
    result = ingest_forecast(store, RecordingQueue(), settings, lambda lat, lng: hours((15, 38), (5, 22)), now, tz)
    assert result == [{"district_id": "d1", "alerts": [], "rounds": [], "error": None}]
    assert store.query("rounds") == [] and store.query("alerts") == []


def test_a_failing_forecast_opens_nothing(store, settings, tz, now, caplog):
    add_ward(store)

    def down(lat, lng):
        raise ConnectionError("weather unavailable with key test-key-not-real")

    result = ingest_forecast(store, RecordingQueue(), settings, down, now, tz)
    assert result == [{"district_id": "d1", "alerts": [], "rounds": [], "error": "ConnectionError"}]
    assert store.query("rounds") == []
    assert "test-key-not-real" not in caplog.text

    odd = ingest_forecast(store, RecordingQueue(), settings, lambda lat, lng: {"unexpected": True}, now, tz)
    assert odd[0]["error"] == "no temperatures in the forecast"


def test_dispatch_happens_only_when_asked_for(store, tz, now):
    add_ward(store)
    add_resident(store, "r1")
    settings = Settings(_env_file=None, internal_secret="s3cret", weather_api_key="k", ingest_auto_dispatch=True)
    queue = RecordingQueue()
    ingest_forecast(store, queue, settings, lambda lat, lng: hours((15, 47.5)), now, tz)
    assert [call_id for call_id, _ in queue.items] == ["d1_heat_20260520_r1_1"]
    assert store.get("rounds", "d1_heat_20260520")["status"] == "calling"


def test_a_round_already_being_called_is_not_planned_again(store, tz, now):
    add_ward(store)
    add_resident(store, "r1")
    settings = Settings(_env_file=None, internal_secret="s3cret", weather_api_key="k", ingest_auto_dispatch=True)
    first = RecordingQueue()
    ingest_forecast(store, first, settings, lambda lat, lng: hours((15, 47.5)), now, tz)
    assert [call_id for call_id, _ in first.items] == ["d1_heat_20260520_r1_1"]  # the first run dispatches
    add_resident(store, "r2")  # registered after the round was dispatched
    before = store.query("calls")
    for forecast in (hours((15, 47.5)), hours((15, 49))):  # the same forecast, then a hotter one
        again = RecordingQueue()
        result = ingest_forecast(store, again, settings, lambda lat, lng: forecast, now, tz)
        assert result[0]["rounds"] == ["d1_heat_20260520"] and result[0]["error"] is None
        assert again.items == []
    assert store.query("calls") == before
    store.update("rounds", "d1_heat_20260520", {"status": "complete"})
    done = RecordingQueue()
    ingest_forecast(store, done, settings, lambda lat, lng: hours((15, 47.5)), now, tz)
    assert done.items == []


def test_a_round_opened_without_dispatch_is_planned_once_dispatch_is_on(store, settings, tz, now):
    add_ward(store)
    add_resident(store, "r1")
    ingest_forecast(store, RecordingQueue(), settings, lambda lat, lng: hours((15, 47.5)), now, tz)
    assert store.get("rounds", "d1_heat_20260520")["status"] == "open"
    calling = Settings(_env_file=None, internal_secret="s3cret", weather_api_key="k", ingest_auto_dispatch=True)
    queue = RecordingQueue()
    ingest_forecast(store, queue, calling, lambda lat, lng: hours((15, 47.5)), now, tz)
    assert [call_id for call_id, _ in queue.items] == ["d1_heat_20260520_r1_1"]


def test_the_key_travels_in_a_header_never_in_the_address():
    seen = {}

    class Answer:
        status_code = 200

        def json(self):
            return {"forecastHours": []}

        def raise_for_status(self):
            pass

    def get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=params, headers=headers, timeout=timeout)
        return Answer()

    assert fetch_forecast("test-key-not-real", 28.2925, 74.967, get=get) == {"forecastHours": []}
    assert seen["url"] == "https://weather.googleapis.com/v1/forecast/hours:lookup"
    assert seen["headers"] == {"X-Goog-Api-Key": "test-key-not-real"}
    assert seen["params"] == {"location.latitude": 28.2925, "location.longitude": 74.967, "hours": 24}
    assert "test-key-not-real" not in json.dumps(seen["params"]) + seen["url"]
    assert seen["timeout"] == 20


def test_the_old_threshold_endpoint_is_gone(store, now):
    # The live fetch (/internal/weather/fetch) replaced it; an old scheduler job must not run it on live data.
    add_ward(store)
    settings = Settings(_env_file=None, internal_secret="s3cret", weather_api_key="test-key-not-real")
    fetched = []
    app = create_app(store, RecordingQueue(), settings, clock=lambda: now,
                     fetch_forecast=lambda lat, lng: fetched.append(1) or hours((15, 46)))
    client = TestClient(app)
    assert client.post("/internal/ingest").status_code == 401
    got = client.post("/internal/ingest", headers=SECRET)
    assert got.status_code == 410 and "/internal/weather/fetch" in got.json()["detail"]
    assert fetched == [] and store.query("alerts") == [] and store.query("rounds") == []


def dated(day, *temps_by_hour):
    """Like hours(), with each reading dated 2026-05-<day>."""
    return {"forecastHours": [
        {"temperature": {"unit": "CELSIUS", "degrees": degrees},
         "displayDateTime": {"year": 2026, "month": 5, "day": day, "hours": hour}}
        for hour, degrees in temps_by_hour
    ]}


def merged(*forecasts):
    return {"forecastHours": [h for f in forecasts for h in f["forecastHours"]]}


def test_orange_then_red_the_same_day_keeps_red_and_the_red_peak(store, settings, tz, now):
    add_ward(store)
    ingest_forecast(store, RecordingQueue(), settings, lambda lat, lng: hours((15, 45.5)), now, tz)
    ingest_forecast(store, RecordingQueue(), settings, lambda lat, lng: hours((14, 48)), now, tz)
    assert store.get("alerts", "weather_d1_heat_20260520")["severity"] == "red"
    assert len(store.query("rounds")) == 1
    opened = store.get("rounds", "d1_heat_20260520")
    assert (opened["peak_temp_c"], opened["peak_hour_local"]) == (48, 14)


def test_red_then_orange_never_lowers_the_alert(store, settings, tz, now):
    add_ward(store)
    ingest_forecast(store, RecordingQueue(), settings, lambda lat, lng: hours((14, 48)), now, tz)
    before = store.get("alerts", "weather_d1_heat_20260520")
    later = now.replace(hour=16)
    result = ingest_forecast(store, RecordingQueue(), settings, lambda lat, lng: hours((15, 46)), later, tz)
    assert store.get("alerts", "weather_d1_heat_20260520") == before
    assert result[0]["rounds"] == ["d1_heat_20260520"]
    opened = store.get("rounds", "d1_heat_20260520")
    assert (opened["peak_temp_c"], opened["peak_hour_local"]) == (48, 14)


def test_an_upgrade_never_changes_a_round_already_being_called(store, settings, tz, now):
    add_ward(store)
    add_resident(store, "r1")
    calling = Settings(_env_file=None, internal_secret="s3cret", weather_api_key="k", ingest_auto_dispatch=True)
    ingest_forecast(store, RecordingQueue(), calling, lambda lat, lng: hours((15, 45.5)), now, tz)
    ingest_forecast(store, RecordingQueue(), calling, lambda lat, lng: hours((14, 48)), now, tz)
    assert store.get("alerts", "weather_d1_heat_20260520")["severity"] == "red"
    opened = store.get("rounds", "d1_heat_20260520")
    assert (opened["status"], opened["peak_temp_c"], opened["peak_hour_local"]) == ("calling", 45.5, 15)


def test_a_crossing_dated_tomorrow_opens_nothing(store, settings, tz, now):
    add_ward(store)
    forecast = merged(dated(20, (10, 35)), dated(21, (15, 48)))
    result = ingest_forecast(store, RecordingQueue(), settings, lambda lat, lng: forecast, now, tz)
    assert result == [{"district_id": "d1", "alerts": [], "rounds": [], "error": None}]
    assert store.query("rounds") == []


def test_only_today_is_used_when_tomorrow_is_hotter(store, settings, tz, now):
    add_ward(store)
    forecast = merged(dated(20, (15, 46)), dated(21, (15, 49)))
    ingest_forecast(store, RecordingQueue(), settings, lambda lat, lng: forecast, now, tz)
    assert store.get("rounds", "d1_heat_20260520")["peak_temp_c"] == 46


def test_nothing_dated_today_is_a_quiet_distinct_outcome(store, settings, tz, now):
    add_ward(store)
    result = ingest_forecast(store, RecordingQueue(), settings, lambda lat, lng: dated(21, (15, 49)), now, tz)
    assert result[0]["error"] == "no temperatures in the forecast for today"
    assert store.query("rounds") == []


def test_the_saved_fixture_is_read_for_the_date_it_carries():
    first = SAVED["forecastHours"][0]["displayDateTime"]
    day = date(first["year"], first["month"], first["day"])
    today = extremes(SAVED, day)
    assert today is not None and today["min_c"] >= extremes(SAVED)["min_c"] and today["max_c"] <= extremes(SAVED)["max_c"]
    assert extremes(SAVED, date(1999, 1, 1)) is None


def test_one_district_failing_to_plan_does_not_stop_the_others(store, tz, now, monkeypatch):
    add_ward(store)
    store.set("districts", "d2", {"id": "d2", "name": "Otherpur", "state": "MH", "lat": 22.0, "lng": 80.0})
    calling = Settings(_env_file=None, internal_secret="s3cret", weather_api_key="k", ingest_auto_dispatch=True)
    planned = []

    def plan(store_, queue, round_id, *rest):
        if round_id.startswith("d1_"):
            raise RuntimeError("queue down")
        planned.append(round_id)

    monkeypatch.setattr("app.ingest.forecast.plan_round", plan)
    result = ingest_forecast(store, RecordingQueue(), calling, lambda lat, lng: hours((15, 48)), now, tz)
    assert [r["error"] for r in result] == ["RuntimeError", None]
    assert planned == ["d2_heat_20260520"]


def test_a_register_only_district_is_never_checked(store, tz, now):
    from app.config import Settings
    from app.dispatch import RecordingQueue
    from app.ingest.forecast import ingest_forecast

    store.set("districts", "quiet", {"id": "quiet", "name": "Quiet", "lat": 1.0, "lng": 2.0, "register_only": True})

    def fetch(lat, lng):
        raise AssertionError("the forecast was asked for a register-only district")

    settings = Settings(_env_file=None, internal_secret="s3cret")
    assert ingest_forecast(store, RecordingQueue(), settings, fetch, now, tz) == []
