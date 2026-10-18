from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.db import MemoryStore
from fastapi.testclient import TestClient

from app.api import create_app
from app.dispatch import RecordingQueue
from app.ingest.live_weather import ALERT_SOURCE, latest_snapshot, prune_live, run_live_fetch, snapshot_id

TZ = ZoneInfo("Asia/Kolkata")
MORNING = datetime(2026, 10, 15, 0, 30, tzinfo=timezone.utc)   # 06:00 IST
AFTERNOON = datetime(2026, 10, 15, 8, 30, tzinfo=timezone.utc)  # 14:00 IST


def world(store):
    for state, districts in {"Rajasthan": ["churu", "sikar", "bikaner"], "Maharashtra": ["nagpur", "pune"]}.items():
        for n, district in enumerate(districts):
            store.set("districts", district, {"id": district, "name": district.title(), "state": state, "lat": 20 + n, "lng": 70 + n})
            for w in range(2):
                ward = f"{district}-ward-{w}"
                store.set("wards", ward, {"id": ward, "district_id": district, "name": ward, "lat": 20 + n + w / 100, "lng": 70 + n})


def hourly(peaks):
    """A fetcher whose peak temperature depends on the ward's district (by latitude)."""
    def fetch(lat, lng):
        base = peaks[int(lat) - 20] if isinstance(peaks, list) else peaks
        if base is None:
            raise RuntimeError("down")
        return {"forecastHours": [{"displayDateTime": {"year": 2026, "month": 10, "day": 15, "hours": h},
                                   "temperature": {"degrees": base - abs(15 - h) * 0.5},
                                   "weatherCondition": {"type": "CLEAR", "description": {"text": "Sunny"}, "iconBaseUri": "https://x/sunny"}}
                                  for h in range(6, 21)]}
    return fetch


def no_days(lat, lng):
    return {"forecastDays": []}


@pytest.fixture
def store():
    s = MemoryStore()
    world(s)
    return s


def settings():
    return Settings(_env_file=None, demo_district_id="churu")


def test_morning_fetch_snapshots_alerts_and_opens_rounds(store):
    # Rajasthan peaks churu 36, sikar 35, bikaner 30 (index by district order within each state's latitude)
    got = run_live_fetch(store, settings(), MORNING, TZ, hourly([36, 35, 30]), no_days, sleep=lambda s: None)
    alerts = {a["district_id"]: a for a in store.query("alerts")}
    assert alerts["churu"]["severity"] == "red" and alerts["churu"]["source"] == ALERT_SOURCE
    assert alerts["sikar"]["severity"] == "orange" and "bikaner" not in alerts
    assert store.get("rounds", "churu_heat_20261015") is not None
    assert store.get("demo", "current")["round_id"] == "churu_heat_20261015"
    assert store.query("weather", area_kind="district")
    assert got["alerts_new"] >= 2


def test_retries_then_gives_up_on_a_ward(store):
    calls = []

    def flaky(lat, lng):
        calls.append(1)
        raise RuntimeError("down")

    slept = []
    got = run_live_fetch(store, settings(), MORNING, TZ, flaky, no_days, sleep=slept.append)
    assert got["wards_failed"] == len(store.query("wards")) and got["alerts_new"] == 0
    assert slept[:2] == [1, 4]


def test_afternoon_never_downgrades_and_can_add_a_red(store):
    run_live_fetch(store, settings(), MORNING, TZ, hourly([36, 35, 30]), no_days, sleep=lambda s: None)
    run_live_fetch(store, settings(), AFTERNOON, TZ, hourly([31, 37, 30]), no_days, sleep=lambda s: None)
    alerts = {a["district_id"]: a for a in store.query("alerts")}
    assert alerts["churu"]["severity"] == "red"   # was red, stays red
    assert alerts["sikar"]["severity"] == "red"   # orange in the morning, hottest now: upgraded
    assert len(store.query("rounds")) == len({r["id"] for r in store.query("rounds")})


def test_a_district_with_no_data_keeps_its_morning_alert(store):
    run_live_fetch(store, settings(), MORNING, TZ, hourly([36, 35, 30]), no_days, sleep=lambda s: None)
    run_live_fetch(store, settings(), AFTERNOON, TZ, hourly([None, 33, 30]), no_days, sleep=lambda s: None)
    assert {a["district_id"]: a["severity"] for a in store.query("alerts")}["churu"] == "red"


def test_running_twice_changes_nothing(store):
    run_live_fetch(store, settings(), MORNING, TZ, hourly([36, 35, 30]), no_days, sleep=lambda s: None)
    before = (len(store.query("alerts")), len(store.query("rounds")))
    got = run_live_fetch(store, settings(), MORNING, TZ, hourly([36, 35, 30]), no_days, sleep=lambda s: None)
    assert (len(store.query("alerts")), len(store.query("rounds"))) == before and got["alerts_new"] == 0


def test_district_hours_are_the_mean_of_its_wards(store):
    def fetch(lat, lng):
        base = 30 + round((lat - int(lat)) * 100) * 2  # ward 0: 30, ward 1: 32
        return {"forecastHours": [{"displayDateTime": {"year": 2026, "month": 10, "day": 15, "hours": 12},
                                   "temperature": {"degrees": base}, "relativeHumidity": 21,
                                   "weatherCondition": {"type": "CLEAR", "description": {"text": "Sunny"}}}]}
    run_live_fetch(store, settings(), MORNING, TZ, fetch, no_days, sleep=lambda s: None)
    snap = latest_snapshot(store, "churu", "2026-10-15")
    assert snap["area_kind"] == "district" and snap["state"] == "Rajasthan"
    assert snap["hours"][0]["temp_c"] == 31.0 and snap["hours"][0]["humidity_pct"] == 21
    assert snap["hours"][0]["condition_text"] == "Sunny" and snap["peak"] == {"temp_c": 31.0, "hour": 12}
    assert latest_snapshot(store, "churu-ward-1", "2026-10-15")["hours"][0]["temp_c"] == 32


def test_latest_snapshot_is_the_newest_of_the_day(store):
    run_live_fetch(store, settings(), MORNING, TZ, hourly([36, 35, 30]), no_days, sleep=lambda s: None)
    run_live_fetch(store, settings(), AFTERNOON, TZ, hourly([31, 37, 30]), no_days, sleep=lambda s: None)
    assert latest_snapshot(store, "churu", "2026-10-15")["peak"]["temp_c"] == 31
    assert latest_snapshot(store, "churu", "2026-10-14") is None
    assert snapshot_id("churu", MORNING.astimezone(TZ)) != snapshot_id("churu", AFTERNOON.astimezone(TZ))


def test_the_afternoon_keeps_the_morning_rounds_in_the_pointer(store):
    run_live_fetch(store, settings(), MORNING, TZ, hourly([36, 35, 30]), no_days, sleep=lambda s: None)
    run_live_fetch(store, settings(), AFTERNOON, TZ, hourly([None, 33, 30]), no_days, sleep=lambda s: None)
    pointer = store.get("demo", "current")
    assert pointer["rounds"]["churu"] == "churu_heat_20261015" and pointer["rounds"]["sikar"] == "sikar_heat_20261015"
    assert pointer["round_id"] == "churu_heat_20261015"


def old_live_round(store, day, district="churu"):
    alert_id = f"forecast_{district}_heat_{day}"
    store.set("alerts", alert_id, {"id": alert_id, "source": ALERT_SOURCE})
    store.set("rounds", f"{district}_heat_{day}", {"id": f"{district}_heat_{day}", "alert_id": alert_id})


def test_prune_drops_records_older_than_thirty_days(store):
    store.set("weather", "old", {"id": "old", "local_day": "2026-09-01", "source": "google_weather"})
    store.set("weather", "new", {"id": "new", "local_day": "2026-10-01", "source": "google_weather"})
    old_live_round(store, "20260901")
    old_live_round(store, "20261001")
    store.set("cases", "c1", {"id": "c1", "round_id": "churu_heat_20260901"})
    store.set("calls", "k1", {"id": "k1", "round_id": "churu_heat_20260901"})
    store.set("calls", "k2", {"id": "k2", "round_id": "churu_heat_20261001"})
    assert prune_live(store, MORNING, TZ) == 5
    assert {w["id"] for w in store.query("weather")} == {"new"}
    assert [r["id"] for r in store.query("rounds")] == ["churu_heat_20261001"]
    assert [a["id"] for a in store.query("alerts")] == ["forecast_churu_heat_20261001"]
    assert [c["id"] for c in store.query("calls")] == ["k2"] and store.query("cases") == []


def test_prune_never_touches_demo_or_declared_records(store):
    store.set("alerts", "declared_sikar_heat_20260901", {"id": "declared_sikar_heat_20260901", "source": "declared"})
    store.set("rounds", "sikar_heat_20260901", {"id": "sikar_heat_20260901", "alert_id": "declared_sikar_heat_20260901"})
    store.set("calls", "k1", {"id": "k1", "round_id": "sikar_heat_20260901"})
    store.set("rounds", "demo_churu_heat_20260901", {"id": "demo_churu_heat_20260901", "alert_id": "forecast_x_heat_20260901"})
    store.set("cases", "demo_c1", {"id": "demo_c1", "round_id": "demo_churu_heat_20260901"})
    store.set("weather", "demo_w", {"id": "demo_w", "local_day": "2026-09-01", "source": "google_weather"})
    assert prune_live(store, MORNING, TZ) == 0
    assert len(store.query("rounds")) == 2 and store.query("calls") and store.query("cases") and store.query("weather")


def curves(by_district, hours):
    """A fetcher with a flat curve per district except where `by_district[d]` names an hour's own value."""
    def fetch(lat, lng):
        flat, spikes = by_district[int(lat) - 20]
        return {"forecastHours": [{"displayDateTime": {"year": 2026, "month": 10, "day": 15, "hours": h},
                                   "temperature": {"degrees": spikes.get(h, flat)}} for h in hours]}
    return fetch


def test_the_afternoon_peak_is_the_whole_day_not_what_is_left_of_it(store):
    # Morning: churu 40 at 13:00 (red), sikar 34 (orange, above the mean 33), bikaner 25.
    run_live_fetch(store, settings(), MORNING, TZ, curves([(30, {13: 40}), (34, {}), (25, {})], range(6, 24)),
                   no_days, sleep=lambda s: None)
    # 14:00: the fetch starts at 14:00. Sikar's 36 beats churu's afternoon 30, not its 40 at 13:00.
    run_live_fetch(store, settings(), AFTERNOON, TZ, curves([(30, {}), (36, {}), (25, {})], range(14, 24)),
                   no_days, sleep=lambda s: None)
    alerts = {a["district_id"]: a["severity"] for a in store.query("alerts")}
    assert alerts["churu"] == "red" and alerts["sikar"] == "orange"
    churu = latest_snapshot(store, "churu", "2026-10-15")
    assert churu["fetched_at"] == AFTERNOON and churu["peak"] == {"temp_c": 40, "hour": 13}
    assert [h["hour"] for h in churu["hours"]] == list(range(6, 24))


def test_the_alert_carries_when_it_was_fetched_and_the_round_its_peak(store):
    run_live_fetch(store, settings(), MORNING, TZ, hourly([36, 35, 30]), no_days, sleep=lambda s: None)
    assert store.get("alerts", "forecast_sikar_heat_20261015")["raw"]["fetched_at"] == MORNING
    assert store.get("rounds", "sikar_heat_20261015")["peak_temp_c"] == 35
    run_live_fetch(store, settings(), AFTERNOON, TZ, hourly([31, 37, 30]), no_days, sleep=lambda s: None)
    upgraded = store.get("alerts", "forecast_sikar_heat_20261015")
    assert upgraded["severity"] == "red" and upgraded["raw"]["fetched_at"] == AFTERNOON
    round_ = store.get("rounds", "sikar_heat_20261015")
    assert (round_["peak_temp_c"], round_["peak_hour_local"]) == (37, 15)


def test_the_route_needs_the_secret_and_returns_the_counts(store):
    s = Settings(_env_file=None, demo_district_id="churu", internal_secret="s3cret", weather_api_key="k")
    app = create_app(store, RecordingQueue(), s, clock=lambda: MORNING,
                     fetch_hours=hourly([36, 35, 30]), fetch_days=no_days)
    client = TestClient(app)
    assert client.post("/internal/weather/fetch").status_code == 401
    got = client.post("/internal/weather/fetch", headers={"X-Internal-Secret": "s3cret"})
    assert got.status_code == 200
    assert got.json()["wards_ok"] == 10 and got.json()["alerts_new"] >= 2


def test_the_route_refuses_without_a_key(store):
    s = Settings(_env_file=None, internal_secret="s3cret", weather_api_key="", gcp_project="")
    client = TestClient(create_app(store, RecordingQueue(), s, clock=lambda: MORNING))
    assert client.post("/internal/weather/fetch", headers={"X-Internal-Secret": "s3cret"}).status_code == 503


def test_the_route_reads_the_key_from_secret_manager_when_it_is_not_set(store, monkeypatch):
    import app.api as api

    asked = []
    monkeypatch.setattr(api, "read_secret", lambda project, name: asked.append((project, name)) or "from-secret-manager")
    s = Settings(_env_file=None, demo_district_id="churu", internal_secret="s3cret", weather_api_key="", gcp_project="chhaya-pilot")
    client = TestClient(create_app(store, RecordingQueue(), s, clock=lambda: MORNING,
                                   fetch_hours=hourly([36, 35, 30]), fetch_days=no_days))
    assert client.post("/internal/weather/fetch", headers={"X-Internal-Secret": "s3cret"}).status_code == 200
    assert asked == [("chhaya-pilot", "weather-api-key")]


def test_the_route_answers_502_when_no_ward_was_fetched_so_the_scheduler_retries(store):
    def down(lat, lng):
        raise RuntimeError("down")

    s = Settings(_env_file=None, demo_district_id="churu", internal_secret="s3cret", weather_api_key="k")
    import app.ingest.live_weather as lw
    original = lw.RETRY_SLEEPS
    lw.RETRY_SLEEPS = (0, 0)
    try:
        client = TestClient(create_app(store, RecordingQueue(), s, clock=lambda: MORNING, fetch_hours=down, fetch_days=no_days))
        got = client.post("/internal/weather/fetch", headers={"X-Internal-Secret": "s3cret"})
    finally:
        lw.RETRY_SLEEPS = original
    assert got.status_code == 502 and "0 of 10" in got.json()["detail"]


def test_a_fetch_past_its_time_budget_stops_trying_and_counts_the_rest_failed(store):
    clock = [0.0]

    def slow(lat, lng):
        clock[0] += 100  # each call takes 100 s
        return hourly([36, 35, 30])(lat, lng)

    got = run_live_fetch(store, settings(), MORNING, TZ, slow, no_days, sleep=lambda s: None,
                         budget_s=250, monotonic=lambda: clock[0])
    # three wards fetched (0, 100, 200 s), then the budget is spent: the rest are failed without a call
    assert got["wards_ok"] + got["wards_failed"] == 10 and got["wards_ok"] <= 3 and got["wards_failed"] >= 7
    assert clock[0] <= 400


def test_a_fetch_once_the_budget_is_spent_does_not_retry(store):
    clock, calls = [0.0], []

    def down(lat, lng):
        calls.append(1)
        clock[0] += 300
        raise RuntimeError("down")

    run_live_fetch(store, settings(), MORNING, TZ, down, no_days, sleep=lambda s: None,
                   budget_s=600, monotonic=lambda: clock[0])
    assert len(calls) <= 3


def test_an_alert_whose_round_is_gone_has_its_round_reopened(store):
    run_live_fetch(store, settings(), MORNING, TZ, hourly([36, 35, 30]), no_days, sleep=lambda s: None)
    store.delete("rounds", "churu_heat_20261015")
    got = run_live_fetch(store, settings(), MORNING, TZ, hourly([36, 35, 30]), no_days, sleep=lambda s: None)
    reopened = store.get("rounds", "churu_heat_20261015")
    assert reopened is not None and reopened["alert_id"] == "forecast_churu_heat_20261015"
    assert reopened["peak_temp_c"] == 36 and got["rounds_opened"] == 1
    assert store.get("demo", "current")["rounds"]["churu"] == "churu_heat_20261015"


def test_the_pointer_keeps_only_rounds_of_the_live_rule(store):
    from app.demo import DEMO_COLLECTION, DEMO_DOC
    # an earlier pointer of today naming a round some other alert opened (an old demo pointer, say)
    store.set("alerts", "scenario_pune_heat_20261015", {"id": "scenario_pune_heat_20261015", "source": "scenario"})
    store.set("rounds", "pune_heat_20261015", {"id": "pune_heat_20261015", "alert_id": "scenario_pune_heat_20261015", "district_id": "pune"})
    store.set(DEMO_COLLECTION, DEMO_DOC, {"id": DEMO_DOC, "round_id": "pune_heat_20261015",
                                          "rounds": {"pune": "pune_heat_20261015", "gone": "gone_heat_20261015"}, "built_at": MORNING})
    run_live_fetch(store, settings(), MORNING, TZ, hourly([36, 35, 30]), no_days, sleep=lambda s: None)
    rounds = store.get(DEMO_COLLECTION, DEMO_DOC)["rounds"]
    assert "pune" not in rounds and "gone" not in rounds and rounds["churu"] == "churu_heat_20261015"
