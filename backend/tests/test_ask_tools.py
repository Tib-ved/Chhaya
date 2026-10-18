import asyncio

import pytest

from app.ask import context, tools
from evals.ask_chhaya.fixture import FIXTURE_NOW, INJECTION, KAMLA, KAMLA_CALL, build_store


@pytest.fixture(autouse=True)
def fixture_store():
    store = build_store()
    context.set_store(store)
    context.set_clock(lambda: FIXTURE_NOW)
    return store


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.mark.parametrize("text,kind,ident", [
    ("RJ", "state", "RJ"), ("Rajasthan", "state", "RJ"), ("rajasthan", "state", "RJ"),
    ("churu", "district", "churu"), ("Churu", "district", "churu"), ("churu-ward-2", "ward", "churu-ward-2"),
    ("", "all", "all"), ("all", "all", "all"),
])
def test_areas_resolve(text, kind, ident):
    area = tools.resolve_area(text)
    assert (area.kind, area.id) == (kind, ident)


def test_unknown_area_is_an_error_the_model_can_recover_from():
    result = run(tools.get_area_overview("atlantis"))
    assert result["error"].startswith("unknown_area")


def test_overview_counts_the_current_round(fixture_store):
    result = run(tools.get_area_overview("churu"))
    # today: ward 1 red,amber,amber,green,green; ward 2 red,red,amber,green; ward 3 amber,green x4
    assert result["cases"] == {"red": 3, "amber": 4, "green": 7}
    assert result["residents"] == 18
    assert result["calls"]["reached"] == 14
    assert result["active_alerts"] and result["rounds"][0]["id"] == "churu_heat_20261014"


def test_state_overview_sums_its_districts():
    state = run(tools.get_area_overview("RJ"))
    churu = run(tools.get_area_overview("churu"))
    sikar = run(tools.get_area_overview("sikar"))
    assert state["cases"]["red"] == churu["cases"]["red"] + sikar["cases"]["red"]


def test_list_cases_filters_by_tier_and_caps():
    red = run(tools.list_cases("RJ", tier="red"))
    assert red["items"] and all(c["tier"] == "red" for c in red["items"])
    capped = run(tools.list_cases("RJ", limit=2))
    assert len(capped["items"]) == 2 and capped["truncated"] is True
    huge = run(tools.list_cases("RJ", limit=10_000))
    assert len(huge["items"]) <= tools.MAX_LIMIT


def test_bad_case_id():
    assert run(tools.get_case("nope"))["error"].startswith("not_found")


def test_phones_are_masked():
    resident = run(tools.get_resident(KAMLA))
    assert "x" in resident["phone"] and resident["phone"][-2:].isdigit()
    assert "x" in resident["emergency_contact"]["phone"]
    staff = run(tools.get_ward_staff("churu-ward-1"))
    assert "x" in staff["worker"]["phone"]


def test_get_call_has_no_transcript():
    call = run(tools.get_call(KAMLA_CALL))
    assert "transcript" not in call and call["has_transcript"] and call["finished"]


def test_find_resident():
    found = run(tools.find_resident("kamla"))
    assert found["items"][0]["resident_id"] == KAMLA


def test_aggregate_by_ward_sums_to_overview():
    groups = run(tools.aggregate_cases("churu", "ward"))
    assert sum(g["count"] for g in groups["groups"]) == 14
    assert run(tools.aggregate_cases("churu", "colour"))["error"].startswith("bad_group_by")


def test_coverage_and_trend():
    rows = run(tools.coverage_by_area("churu"))["rows"]
    assert [r["area"] for r in rows] == ["churu-ward-1", "churu-ward-2", "churu-ward-3"]
    points = run(tools.trend("churu", "red_cases"))["points"]
    assert [p["label"] for p in points] == sorted(p["label"] for p in points) and len(points) == 2


def test_transcript_is_wrapped_and_refused_when_unfinished(fixture_store):
    result = run(tools.get_transcript(KAMLA_CALL))
    assert result["transcript"].startswith('<transcript untrusted="true">') and INJECTION in result["transcript"]
    running = next(c for c in fixture_store.query("calls") if c["status"] == "in_progress")
    assert run(tools.get_transcript(running["id"]))["error"].startswith("call_not_finished")


def test_tools_run_concurrently():
    async def many():
        return await asyncio.gather(*(tools.get_area_overview("RJ") for _ in range(5)))
    assert len(run(many())) == 5


def test_every_tool_is_documented_and_async():
    for tool in tools.ROOT_TOOLS + tools.ANALYST_TOOLS + tools.SUMMARIZER_TOOLS:
        assert asyncio.iscoroutinefunction(tool) and tool.__doc__ and "Args:" in tool.__doc__


def test_high_risk_uses_the_apps_own_threshold(fixture_store):
    from app.region import HIGH_RISK_SCORE

    expected = sum(1 for r in fixture_store.query("residents", district_id="churu") if r["risk_score"] >= HIGH_RISK_SCORE)
    assert run(tools.get_area_overview("churu"))["high_risk_residents"] == expected


def test_reason_codes_come_with_their_meaning(fixture_store):
    case_id = "churu_heat_20261014_churu-ward-1-r-1"
    fixture_store.update("cases", case_id, {"reasons": ["R1", "A12"]})
    row = run(tools.get_case(case_id))
    assert row["reasons"] == ["R1: Emergency flagged during the call", "A12: Call ended before all the questions were asked"]
    assert run(tools.list_cases("churu", reason="emergency"))["items"][0]["case_id"] == case_id
    keys = [g["key"] for g in run(tools.aggregate_cases("churu", "reason"))["groups"]]
    assert "R1: Emergency flagged during the call" in keys


def test_times_are_in_india_time():
    alert = run(tools.list_alerts("churu"))["items"][0]
    assert alert["valid_to"].endswith("+05:30")


def test_heat_status_of_the_live_fixture_gives_the_state_rule_and_no_scenario():
    # The evaluation fixture is a live store with no weather stored: alerts and rounds, no forecast.
    status = run(tools.get_heat_status(""))
    assert "thresholds_c" not in status and "hottest district in each state" in status["rule"]
    rows = {r["district_id"]: r for r in status["districts"]}
    assert rows["churu"]["heat_monitored"] and rows["churu"]["alert"] and rows["churu"]["round_open"]
    assert rows["churu"]["forecast"] is None and rows["churu"]["forecast_source"] is None
    assert rows["narayanpet"]["heat_monitored"] is True and rows["narayanpet"]["forecast"] is None
    assert rows["narayanpet"]["alert"] is None and not rows["narayanpet"]["round_open"]


# ---------- weather in get_heat_status ----------

def _weather_world():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from app.config import Settings
    from app.db import MemoryStore
    from app.ingest.live_weather import run_live_fetch
    from tests.test_live_weather import MORNING, hourly, world

    store = MemoryStore()
    world(store)
    tz = ZoneInfo("Asia/Kolkata")
    run_live_fetch(store, Settings(_env_file=None, demo_district_id="churu"), MORNING, tz, hourly([36, 35, 30]),
                   lambda lat, lng: {"forecastDays": []}, sleep=lambda s: None)
    return store, MORNING


def test_heat_status_carries_the_stored_live_weather_and_the_alert_reason():
    from datetime import datetime

    store, at = _weather_world()
    context.set_store(store)
    context.set_clock(lambda: at)
    rows = {r["district_id"]: r for r in run(tools.get_heat_status("churu"))["districts"]}
    churu = rows["churu"]
    assert churu["alert_reason"].startswith("Hottest in")
    assert churu["weather"]["source"] == "google_weather" and churu["weather"]["peak_c"] == 36
    assert churu["weather"]["now"] and "today" in churu["weather"] and "condition_text" in churu["weather"]
    assert datetime.fromisoformat(churu["weather"]["fetched_at"]) == at


def test_heat_status_of_a_ward_reads_the_wards_own_hours():
    store, at = _weather_world()
    store.set("weather", "churu-ward-0_20261015_0600", {
        **store.get("weather", "churu-ward-0_20261015_0600"), "hours": [{"hour": 14, "temp_c": 41.0}]})
    context.set_store(store)
    context.set_clock(lambda: at)
    row = run(tools.get_heat_status("churu-ward-0"))["districts"][0]
    assert row["weather"]["peak_c"] == 41.0 and row["weather"]["peak_hour"] == "14:00"


def test_heat_status_of_a_demo_store_says_demo_scenario(store, now, tz):
    from app.datasets import DatasetStore
    from app.demo import open_rounds
    from seed.seed import build_all, load_seed

    load_seed(store, build_all([]))
    demo = DatasetStore(store, "demo")
    open_rounds(demo, "churu", now, tz)
    context.set_store(demo)
    context.set_clock(lambda: now)
    row = next(r for r in run(tools.get_heat_status("churu"))["districts"] if r["district_id"] == "churu")
    assert row["weather"]["source"] == "demo_scenario" and row["weather"]["peak_c"] is not None
    assert row["alert_reason"]


def test_heat_status_answers_from_storage_when_the_weather_api_is_down():
    import app.ingest.live_weather as lw

    store, at = _weather_world()
    context.set_store(store)
    context.set_clock(lambda: at)
    before = run(tools.get_heat_status("churu"))
    assert before["districts"][0]["weather"]["source"] == "google_weather"
    # a later fetch whose fetchers raise changes nothing the tool reads
    from zoneinfo import ZoneInfo

    from app.config import Settings

    def boom(*a, **k):
        raise RuntimeError("down")

    lw.run_live_fetch(store, Settings(_env_file=None, demo_district_id="churu"), at, ZoneInfo("Asia/Kolkata"), boom, boom,
                      sleep=lambda s: None)
    assert run(tools.get_heat_status("churu"))["districts"][0]["weather"]["source"] == "google_weather"


def test_heat_status_shows_older_weather_with_its_age_until_the_fetch_is_overdue():
    from datetime import timedelta

    store, at = _weather_world()
    context.set_store(store)
    context.set_clock(lambda: at + timedelta(days=10))  # 06:00 IST, no snapshot today yet
    weather = run(tools.get_heat_status("churu"))["districts"][0]["weather"]
    assert weather["unavailable_since"] is None and weather["from_an_earlier_day"] is True
    assert weather["fetched_at"] and weather["peak_c"] is not None
    context.set_clock(lambda: at + timedelta(days=10, minutes=31))  # past 06:30
    weather = run(tools.get_heat_status("churu"))["districts"][0]["weather"]
    assert weather["unavailable_since"] is not None and weather["from_an_earlier_day"] is True


def test_heat_status_weather_of_today_is_not_marked_older():
    store, at = _weather_world()
    context.set_store(store)
    context.set_clock(lambda: at)
    assert run(tools.get_heat_status("churu"))["districts"][0]["weather"]["from_an_earlier_day"] is False


# ---------- get_heat_status, live and demo ----------

def _live_seed_after_a_fetch():
    """The real seed (12 of its 16 districts are register_only) after a fake live fetch."""
    from zoneinfo import ZoneInfo

    from app.db import MemoryStore
    from seed.seed import build_all, load_seed
    from tests.test_live_weather import MORNING
    from tests.test_region import _live_fetch_over_the_seed

    store = MemoryStore()
    load_seed(store, build_all([]))
    _live_fetch_over_the_seed(store, MORNING, ZoneInfo("Asia/Kolkata"))
    return store, MORNING


def test_live_heat_status_monitors_every_district_by_the_state_rule():
    store, at = _live_seed_after_a_fetch()
    context.set_store(store)
    context.set_clock(lambda: at)
    status = run(tools.get_heat_status(""))
    assert "thresholds_c" not in status and "hottest district in each state" in status["rule"]
    assert "not_monitored_means" not in status
    rows = {r["district_id"]: r for r in status["districts"]}
    assert len(rows) == 16 and all(r["heat_monitored"] for r in rows.values())
    for district_id in ("dewas", "sangli", "churu"):
        row = rows[district_id]
        assert row["weather"] and row["weather"]["source"] == "google_weather"
        assert row["forecast"]["peak_c"] == row["weather"]["peak_c"] < 40  # the stored weather, not the scenario
        assert row["forecast_source"] == "google weather"
    alerted = [r for r in rows.values() if r["alert"]]
    assert alerted and all(r["alert_reason"] for r in alerted)
    assert any(r["alert_reason"] for r in rows.values() if r["state"] == "Madhya Pradesh")


def test_live_heat_status_without_stored_weather_has_no_forecast():
    from datetime import timedelta

    store, at = _live_seed_after_a_fetch()
    context.set_store(store)
    context.set_clock(lambda: at - timedelta(days=2))  # nothing stored then
    rows = {r["district_id"]: r for r in run(tools.get_heat_status("RJ"))["districts"]}
    assert all(r["forecast"] is None and r["forecast_source"] is None for r in rows.values())


def test_live_heat_status_reads_each_state_once():
    import app.weather_view as wv

    store, at = _live_seed_after_a_fetch()
    context.set_store(store)
    context.set_clock(lambda: at)
    calls = {"weather_view": 0}
    original = wv.weather_view

    def counting(*args, **kwargs):
        calls["weather_view"] += 1
        return original(*args, **kwargs)

    wv.weather_view = counting
    try:
        run(tools.get_heat_status(""))
    finally:
        wv.weather_view = original
    assert calls["weather_view"] == 0  # one district_weather_views per state instead


def test_demo_heat_status_keeps_the_scenario_and_thresholds(store, now, tz):
    from app.datasets import DatasetStore
    from app.demo import open_rounds
    from seed.seed import build_all, load_seed

    load_seed(store, build_all([]))
    demo = DatasetStore(store, "demo")
    open_rounds(demo, "churu", now, tz)
    context.set_store(demo)
    context.set_clock(lambda: now)
    status = run(tools.get_heat_status(""))
    assert status["thresholds_c"] == {"orange": 45.0, "red": 47.0} and "orange threshold" in status["rule"]
    rows = {r["district_id"]: r for r in status["districts"]}
    assert rows["churu"]["forecast_source"] == "heat scenario" and rows["churu"]["forecast"]["peak_c"] >= 45
    assert rows["narayanpet"]["heat_monitored"] is False and rows["narayanpet"]["forecast"] is None
