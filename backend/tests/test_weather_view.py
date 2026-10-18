from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.datasets import DatasetStore
from app.db import MemoryStore
from app.demo import open_rounds
from app.dispatch import RecordingQueue
from app.ingest.live_weather import run_live_fetch
from app.schemas import WeatherView
from app.weather_view import weather_view
from tests.test_live_weather import MORNING, hourly, world

TZ = ZoneInfo("Asia/Kolkata")
AT_TWO = datetime(2026, 10, 15, 8, 30, tzinfo=timezone.utc)  # 14:00 IST


def days(lat, lng):
    return {"forecastDays": [{
        "displayDate": {"year": 2026, "month": 10, "day": 15},
        "maxTemperature": {"degrees": 36.8}, "minTemperature": {"degrees": 22.1},
        "daytimeForecast": {"weatherCondition": {"type": "CLEAR", "description": {"text": "Sunny"}, "iconBaseUri": "https://x/sunny"},
                            "precipitation": {"probability": {"percent": 0}}},
    }]}


def settings():
    return Settings(_env_file=None, demo_district_id="churu")


@pytest.fixture
def live():
    store = MemoryStore()
    world(store)
    run_live_fetch(store, settings(), MORNING, TZ, hourly([36, 35, 30]), days, sleep=lambda s: None)
    return store


def test_a_live_district_has_its_hours_days_alert_and_state(live):
    got = weather_view(live, "Rajasthan", "churu", None, AT_TWO, TZ)
    WeatherView.model_validate(got)
    assert got["area"] == {"kind": "district", "id": "churu", "name": "Churu", "state": "Rajasthan"}
    assert got["source"] == "google_weather" and got["unavailable_since"] is None
    assert got["fetched_at"] == MORNING
    assert got["alert"]["severity"] == "red" and got["alert"]["reason_text"].startswith("Hottest in")
    assert got["alert"]["raw"]["rule"] == "hottest_in_state"
    assert [d["id"] for d in got["state"]["districts"]] == ["churu", "sikar", "bikaner"]
    assert [d["alert"] for d in got["state"]["districts"]] == ["red", "orange", None]
    assert got["state"]["max_peak_c"] == 36 and got["state"]["mean_peak_c"] == round((36 + 35 + 30) / 3, 1)
    assert len(got["hours"]) == 15 and got["now"]["hour"] == 14
    assert got["today"]["max_c"] == 36.8 and got["today"]["rain_pct"] is None
    assert got["days"][0]["date"] == "2026-10-15"


def test_a_ward_has_its_own_hours_and_its_district_days_and_alert(live):
    live.set("weather", "churu-ward-0_20261015_0600", {
        **live.get("weather", "churu-ward-0_20261015_0600"),
        "hours": [{"hour": 14, "temp_c": 41.0}],
    })
    got = weather_view(live, None, None, "churu-ward-0", AT_TWO, TZ)
    WeatherView.model_validate(got)
    assert got["area"]["kind"] == "ward" and got["area"]["id"] == "churu-ward-0"
    assert [h["temp_c"] for h in got["hours"]] == [41.0]
    assert got["days"][0]["max_c"] == 36.8 and got["alert"]["severity"] == "red"


def test_a_state_takes_the_hours_of_its_hottest_district(live):
    got = weather_view(live, "Rajasthan", None, None, AT_TWO, TZ)
    WeatherView.model_validate(got)
    assert got["area"] == {"kind": "state", "id": "Rajasthan", "name": "Rajasthan", "state": "Rajasthan"}
    assert max(h["temp_c"] for h in got["hours"]) == 36 and got["alert"]["severity"] == "red"


NEXT_NIGHT = datetime(2026, 10, 15, 20, 30, tzinfo=timezone.utc)   # 02:00 IST on 16 Oct: before the fetch
NEXT_EARLY = datetime(2026, 10, 16, 0, 50, tzinfo=timezone.utc)    # 06:20 IST: due, within the grace
NEXT_LATE = datetime(2026, 10, 16, 1, 5, tzinfo=timezone.utc)      # 06:35 IST: overdue


@pytest.mark.parametrize("at", [NEXT_NIGHT, NEXT_EARLY])
def test_before_the_fetch_is_due_the_last_day_is_shown(live, at):
    for got in (weather_view(live, None, "churu", None, at, TZ), weather_view(live, "Rajasthan", None, None, at, TZ)):
        WeatherView.model_validate(got)
        assert got["unavailable_since"] is None and got["fetched_at"] == MORNING
        assert len(got["hours"]) == 15 and got["days"][0]["date"] == "2026-10-15"


def test_once_the_fetch_is_overdue_the_area_is_unavailable_but_keeps_its_last_day(live):
    for got in (weather_view(live, None, "churu", None, NEXT_LATE, TZ),
                weather_view(live, None, None, "churu-ward-0", NEXT_LATE, TZ),
                weather_view(live, "Rajasthan", None, None, NEXT_LATE, TZ)):
        WeatherView.model_validate(got)
        assert got["unavailable_since"] == datetime(2026, 10, 16, 6, 0, tzinfo=TZ)
        assert got["fetched_at"] == MORNING and len(got["hours"]) == 15


def test_an_area_never_fetched_is_unavailable_with_nothing_to_show(live):
    live.set("districts", "jaipur", {"id": "jaipur", "name": "Jaipur", "state": "Rajasthan", "lat": 26, "lng": 75})
    got = weather_view(live, None, "jaipur", None, NEXT_LATE, TZ)
    WeatherView.model_validate(got)
    assert got["unavailable_since"] is not None and got["hours"] == [] and got["days"] == []
    assert got["now"] is None and got["fetched_at"] is None


def test_the_demo_view_before_the_rebuild_shows_the_previous_demo_day(store, now, tz):
    from seed.seed import build_all, load_seed

    load_seed(store, build_all([]))
    demo = DatasetStore(store, "demo")
    open_rounds(demo, "churu", now, tz)
    next_night = now.astimezone(tz).replace(hour=3) + timedelta(days=1)
    got = weather_view(demo, None, "churu", None, next_night, tz)
    assert got["source"] == "demo_scenario" and len(got["hours"]) == 15
    assert got["fetched_at"] == now and got["unavailable_since"] is None
    late = weather_view(demo, None, "churu", None, next_night.replace(hour=10), tz)
    assert late["unavailable_since"] is None and len(late["hours"]) == 15  # the demo is never "live forecast unavailable"


def test_the_demo_view_reads_the_scenario_open_rounds_wrote(store, now, tz):
    from seed.seed import build_all, load_seed

    load_seed(store, build_all([]))
    demo = DatasetStore(store, "demo")
    open_rounds(demo, "churu", now, tz)
    got = weather_view(demo, None, "churu", None, now, tz)
    WeatherView.model_validate(got)
    assert got["source"] == "demo_scenario" and len(got["hours"]) == 15 and got["unavailable_since"] is None
    assert got["hours"][0]["condition_type"] == "CLEAR" and got["hours"][0]["icon_uri"] == "https://maps.gstatic.com/weather/v1/sunny"
    assert got["alert"]["severity"] in ("orange", "red")
    ward = next(w["id"] for w in store.query("wards", district_id="churu"))
    assert len(weather_view(demo, None, None, ward, now, tz)["hours"]) == 15
    assert DatasetStore(store, "live").query("weather") == []  # demo weather stays in the demo dataset


def test_unknown_areas_raise_key_error(live):
    with pytest.raises(KeyError):
        weather_view(live, None, "nowhere", None, AT_TWO, TZ)
    with pytest.raises(KeyError):
        weather_view(live, "Atlantis", None, None, AT_TWO, TZ)


def test_the_route_reads_stored_weather_and_never_calls_the_api(live):
    def boom(*args, **kwargs):
        raise AssertionError("called")

    s = Settings(_env_file=None, demo_district_id="churu", weather_api_key="k")
    client = TestClient(create_app(live, RecordingQueue(), s, clock=lambda: AT_TWO, fetch_hours=boom, fetch_days=boom))
    got = client.get("/api/weather", params={"district_id": "churu"})
    assert got.status_code == 200 and got.json()["alert"]["severity"] == "red"
    assert client.get("/api/districts/churu").status_code == 200
    assert client.get("/api/weather", params={"district_id": "nowhere"}).status_code == 404
    assert client.get("/api/weather").json()["area"]["kind"] == "state"  # the demo district's state


def test_district_view_gives_the_state_rule_reason(live):
    s = Settings(_env_file=None, demo_district_id="churu")
    client = TestClient(create_app(live, RecordingQueue(), s, clock=lambda: AT_TWO))
    warning = client.get("/api/districts/churu").json()["warning"]
    assert warning["source"] == "forecast_state_rule" and warning["reason"].startswith("Hottest in Rajasthan today: 36")


def test_overnight_the_alert_shown_is_the_one_of_the_day_shown(live):
    # 02:00 on 16 Oct: the screens still show 15 Oct's weather, so the alert beside it is 15 Oct's
    for got in (weather_view(live, None, "churu", None, NEXT_NIGHT, TZ),
                weather_view(live, None, None, "churu-ward-0", NEXT_NIGHT, TZ),
                weather_view(live, "Rajasthan", None, None, NEXT_NIGHT, TZ)):
        assert got["fetched_at"] == MORNING
        assert got["alert"] is not None and got["alert"]["severity"] == "red"
        assert [d["alert"] for d in got["state"]["districts"]] == ["red", "orange", None]


class CountingStore:
    """A store that records each weather query it is asked."""

    def __init__(self, base):
        self.base, self.weather_queries = base, []

    def __getattr__(self, name):
        return getattr(self.base, name)

    def query(self, collection, **equals):
        if collection == "weather":
            self.weather_queries.append(equals)
        return self.base.query(collection, **equals)


def test_with_a_snapshot_today_only_today_is_read(live):
    counting = CountingStore(live)
    weather_view(counting, None, "churu", None, AT_TWO, TZ)
    assert counting.weather_queries and all(q.get("local_day") == "2026-10-15" for q in counting.weather_queries)
    counting.weather_queries.clear()
    got = weather_view(counting, None, "churu", None, NEXT_NIGHT, TZ)  # none today: the older scan
    assert got["fetched_at"] == MORNING and any("local_day" not in q for q in counting.weather_queries)
