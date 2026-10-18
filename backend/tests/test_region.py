import json
from datetime import timedelta

import pytest

from app.region import AGE_BANDS, district_view, load_district, region_view, ward_view
from seed.regions import DISTRICTS
from seed.seed import ward_ids
from tests.helpers import add_visitor_call, seeded_region

WARD_2 = ward_ids("churu")[0]

ALICE, BOB = "3f2b8c1e-7a4d-4e2a-9b1c-5d6e7f8a9b0c", "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"


@pytest.fixture
def region(store, now, tz):
    store.round_id = seeded_region(store, now, tz)
    return store


@pytest.fixture
def demo_region(store, now, tz):
    """The region in the demo dataset: the heat scenario's curves and thresholds are the demo's only."""
    from app.datasets import DatasetStore

    demo = DatasetStore(store, "demo")
    demo.round_id = seeded_region(demo, now, tz)
    return demo


def test_the_region_lists_sixteen_districts_and_only_churu_is_warned(region, now):
    districts = region_view(region, None, now)["districts"]
    ids = [d["id"] for d in districts]
    assert len(ids) == 16 and sorted(ids) == sorted(d[0] for d in DISTRICTS)
    assert ids[0] == "churu" and ids[1:] == sorted(ids[1:])  # the warned one first, then by name
    churu, bikaner = districts[0], districts[1]
    assert churu["warning"]["severity"] == "red" and churu["warning"]["hazard"] == "heat"
    assert (churu["wards"], churu["residents"]) == (3, 119)  # 30 scripted + the uneventful rest of Churu
    assert churu["round"] == {
        "called": 30, "reached": 24, "no_answer": 4, "trying": 2, "red": 3, "amber": 16, "green": 9,
        "to_visit": 14, "visited": 5, "red_open": 2, "median_minutes_to_visit": 126.0,
    }
    assert bikaner["warning"] is None and bikaner["round"] is None and bikaner["residents"] == 130
    for other in districts[1:]:
        assert other["warning"] is None and other["round"] is None, other["id"]


def test_every_count_equals_the_rows_that_carry_it(region, now):
    view = district_view(region, "churu", None, now)
    rows, figures = view["residents"], view["round"]
    assert len(rows) == 119  # 30 scripted + the uneventful rest of Churu
    assert figures["reached"] == sum(r["call_status"] == "reached" for r in rows)
    assert figures["no_answer"] == sum(r["call_status"] == "no_answer" for r in rows)
    assert figures["trying"] == sum(r["call_status"] == "trying" for r in rows)
    for tier in ("red", "amber", "green"):
        assert figures[tier] == sum(r["tier"] == tier for r in rows)
    assert figures["to_visit"] == sum(r["case_status"] == "open" and r["tier"] != "green" for r in rows)
    for band in view["register"]["age_bands"]:
        assert band["count"] == sum(r["age_band"] == band["band"] for r in rows)
    assert [b["band"] for b in view["register"]["age_bands"]] == list(AGE_BANDS)
    groups = {g["id"]: g["count"] for g in view["register"]["groups"]}
    assert groups["high_risk"] == sum(r["high_risk"] for r in rows)
    assert groups["lives_alone"] == sum(r["lives_alone"] for r in rows)
    for problem in view["problems"]:
        assert problem["count"] == sum(problem["rule"] in r["reasons"] for r in rows)
    assert sum(w["residents"] for w in view["wards"]) == 119
    assert [w["worker_name"] for w in view["wards"]] == ["Sunita Kanwar", "Meena Bishnoi", "Kavita Saini"]


def test_the_rows_are_ranked_and_carry_what_the_screens_need(region, now):
    rows = district_view(region, "churu", None, now)["residents"]
    tiers = [r["tier"] for r in rows]
    assert tiers == sorted(tiers, key=lambda t: {"red": 0, "amber": 1, "green": 2, None: 3}[t])
    low_water = next(r for r in rows if r["id"] == "r08")
    assert low_water["needs"] == ["water_ors"] and low_water["name_hi"] and low_water["ward_name"] == "Ward 30"
    flagged = next(r for r in rows if r["id"] == "r18")
    assert flagged["escalated"] is True and flagged["raised"] is False and flagged["yours"] is False
    silent = next(r for r in rows if r["call_status"] == "no_answer")
    assert silent["attempts"] == 3 and silent["tier"] == "amber" and "A8" in silent["reasons"]
    trying = next(r for r in rows if r["call_status"] == "trying")
    assert trying["tier"] is None and trying["case_id"] is None and trying["waiting_minutes"] is None


def test_the_attention_list_puts_flagged_cases_first_then_the_longest_wait(region, now):
    attention = district_view(region, "churu", None, now)["attention"]
    assert attention[0]["id"] == "r18"
    reds = [r for r in attention[1:]]
    assert all(r["tier"] == "red" and r["case_status"] == "open" for r in reds) and len(reds) == 2
    assert [r["waiting_minutes"] for r in reds] == sorted((r["waiting_minutes"] for r in reds), reverse=True)


def test_the_progress_series_ends_at_the_rounds_own_figures(region, now):
    view = district_view(region, "churu", None, now)
    series = view["progress"]
    assert series["minutes"][0] == 0 and series["minutes"][-1] == 180
    assert series["reached"][-1] == 24 and series["visited"][-1] == 5
    assert series["reached"] == sorted(series["reached"])
    assert [h["hour"] for h in view["forecast"]] == list(range(6, 21))


def test_a_district_without_a_warning_has_no_round_figures(region, now):
    view = district_view(region, "bikaner", None, now)
    assert view["warning"] is None and view["round"] is None
    assert view["progress"] is None and view["attention"] == [] and view["problems"] == []
    assert len(view["residents"]) == 130  # Bikaner's whole register
    for row in view["residents"]:
        assert row["call_status"] == "not_called" and row["attempts"] == 0
        assert row["tier"] is None and row["case_id"] is None and row["reasons"] == [] and row["needs"] == []
    assert all(w["round"] is None for w in view["wards"])


def test_a_ward(region, now):
    view = ward_view(region, WARD_2, None, now)
    assert view["ward"]["name"] == "Ward 2" and view["ward"]["district_name"] == "Churu"
    assert view["ward"]["worker_name"] == "Sunita Kanwar"
    assert view["ward"]["health_centre"]["name"] == "Ward 2 health centre"
    assert len(view["residents"]) == 30 and {r["ward_id"] for r in view["residents"]} == {WARD_2}
    assert view["round"]["called"] == 10  # ten of the scripted thirty live in each ward
    with pytest.raises(KeyError):
        ward_view(region, "no-such-ward", None, now)
    with pytest.raises(KeyError):
        district_view(region, "no-such-district", None, now)


def test_seeded_waiting_times_do_not_move_with_the_real_clock(region, now, tz):
    add_visitor_call(region, region.round_id, ALICE, now, tz, ward_id=WARD_2, district_id="churu", feeling="mild_symptoms")
    later = now + timedelta(hours=9)

    def waits(moment):
        rows = district_view(region, "churu", ALICE, moment)["residents"]
        return {r["id"]: r["waiting_minutes"] for r in rows if r["waiting_minutes"] is not None}, rows

    before, _ = waits(now)
    after, rows = waits(later)
    mine = next(r["id"] for r in rows if r["yours"])
    assert {k: v for k, v in before.items() if k != mine} == {k: v for k, v in after.items() if k != mine}
    assert after[mine] - before[mine] == 9 * 60  # the visitor's own case runs on the real clock


def test_no_phone_number_or_visitor_id_leaves(region, now, tz):
    add_visitor_call(region, region.round_id, ALICE, now, tz, ward_id=WARD_2, district_id="churu", feeling="mild_symptoms")
    for view in (region_view(region, ALICE, now), district_view(region, "churu", ALICE, now), ward_view(region, WARD_2, ALICE, now)):
        text = json.dumps(view, default=str)
        assert "+91" not in text and "phone" not in text and "visitor_id" not in text and ALICE not in text


def test_a_visitors_own_resident_is_counted_for_them_alone(region, now, tz):
    add_visitor_call(region, region.round_id, ALICE, now, tz, ward_id=WARD_2, district_id="churu", feeling="mild_symptoms")
    hers, his = district_view(region, "churu", ALICE, now), district_view(region, "churu", BOB, now)
    assert len(hers["residents"]) == 120 and len(his["residents"]) == 119
    assert [r["yours"] for r in hers["residents"]].count(True) == 1
    assert hers["round"]["amber"] == his["round"]["amber"] + 1
    assert his == district_view(region, "churu", None, now)
    assert load_district(region, "bikaner", ALICE, now).data is None


def test_a_call_still_in_progress_is_not_yet_reached(region, now):
    from app.dispatch import call_id_for
    from app.models import Call
    from tests.helpers import add_resident

    add_resident(region, "v-1", name="Visitor Character", phone="", ward_id=WARD_2, district_id="churu", visitor_id=ALICE)
    call = Call(
        id=call_id_for(region.round_id, "v-1", 1), round_id=region.round_id, resident_id="v-1",
        channel="browser", visitor_id=ALICE, status="in_progress", created_at=now,
    )
    region.set("calls", call.id, call.model_dump())
    hers, anon = district_view(region, "churu", ALICE, now), district_view(region, "churu", None, now)
    row = next(r for r in hers["residents"] if r["yours"])
    assert row["call_status"] == "trying" and row["tier"] is None
    assert hers["round"]["reached"] == anon["round"]["reached"]
    assert hers["round"]["called"] == anon["round"]["called"] + 1
    assert hers["round"]["reached"] == hers["progress"]["reached"][-1]


def test_a_visitors_own_records_are_counted_at_the_last_point_of_the_series(region, now, tz):
    add_visitor_call(region, region.round_id, ALICE, now, tz, ward_id=WARD_2, district_id="churu", feeling="mild_symptoms")
    later = now + timedelta(hours=3)  # the real clock, past the round's fixed one
    hers, anon = district_view(region, "churu", ALICE, later), district_view(region, "churu", None, later)
    series = hers["progress"]
    assert series["reached"][-1] == hers["round"]["reached"] == anon["round"]["reached"] + 1
    assert series["opened"][-1] == sum(r["case_id"] is not None for r in hers["residents"])
    assert series["visited"][-1] == hers["round"]["visited"]
    for name in ("minutes", "reached", "opened", "visited"):
        assert series[name][:-1] == anon["progress"][name][:-1], name


def _scenario_alert(store, round_id, **raw):
    alert_id = store.get("rounds", round_id)["alert_id"]
    store.update("alerts", alert_id, {"source": "scenario", "raw": {
        "scenario": "Hot day, western Rajasthan", "peak_c": 47.5, "peak_hour": 15, "threshold_c": 47.0,
        "severity": "red", "rule": "forecast maximum at or above the red heat threshold", "forecast_hours": [], **raw,
    }})
    return alert_id


def test_a_scenario_warning_says_why_it_was_raised(region, now):
    alert_id = _scenario_alert(region, region.round_id)
    warning = district_view(region, "churu", None, now)["warning"]
    assert warning["scenario"] is True and warning["source"] == "scenario"
    assert warning["reason"] == "Forecast peak 47.5 °C at 15:00 is above the Red threshold (47 °C)."
    assert warning["threshold_c"] == 47.0
    assert warning["raised_at"] == region.get("alerts", alert_id)["received_at"]


def test_an_orange_scenario_reason_names_its_threshold(region, now):
    _scenario_alert(region, region.round_id, peak_c=45.5, peak_hour=14, threshold_c=45.0, severity="orange")
    assert district_view(region, "churu", None, now)["warning"]["reason"] == (
        "Forecast peak 45.5 °C at 14:00 is above the Amber threshold (45 °C).")


def test_a_warning_that_is_not_a_scenario_has_no_reason(region, now):
    region.update("alerts", region.get("rounds", region.round_id)["alert_id"], {"source": "declared", "raw": {"note": "x"}})
    warning = district_view(region, "churu", None, now)["warning"]
    assert warning["scenario"] is False and warning["reason"] is None and warning["threshold_c"] is None
    assert warning["raised_at"] is not None


def test_the_district_carries_the_thresholds_and_the_real_forecast(demo_region, now):
    view = district_view(demo_region, "churu", None, now)
    assert view["thresholds"] == {"orange": 45, "red": 47}
    assert view["real_forecast"] is None
    demo_region.update("rounds", demo_region.round_id, {"real_forecast_hours": [{"hour": 12, "temp_c": 41.0}, {"hour": 13, "temp_c": 42.5}]})
    assert district_view(demo_region, "churu", None, now)["real_forecast"] == [
        {"hour": 12, "temp_c": 41.0}, {"hour": 13, "temp_c": 42.5}]


def test_before_a_round_the_scenario_curve_and_the_call_plan_are_shown(demo_region, now):
    view = district_view(demo_region, "bikaner", None, now)
    assert view["warning"] is None and view["round"] is None
    assert max(h["temp_c"] for h in view["forecast"]) == 47.0 and view["forecast"][0]["hour"] == 6
    plan = view["call_plan"]
    assert len(plan) == 10
    scores = [(-p["risk_score"], -p["age"], p["id"]) for p in plan]
    assert scores == sorted(scores)
    assert all(p["reasons"] and p["name"] for p in plan)
    top = max(view["residents"], key=lambda r: (r["risk_score"], r["age"]))
    assert plan[0]["risk_score"] == top["risk_score"]
    assert "phone" not in json.dumps(plan)


def test_a_district_with_a_round_or_no_scenario_has_no_call_plan(demo_region, now):
    assert district_view(demo_region, "churu", None, now)["call_plan"] is None
    other = next(d[0] for d in DISTRICTS if d[0] not in ("churu", "bikaner", "jhunjhunu", "sikar"))
    view = district_view(demo_region, other, None, now)
    assert view["call_plan"] is None and view["forecast"] is None


def test_rows_mark_ai_residents_and_simulated_visits(region, now):
    rows = {r["id"]: r for r in district_view(region, "churu", None, now)["residents"]}
    assert all(r["simulated"] is None and r["simulated_visit"] is False for r in rows.values())
    case = next(c for c in region.query("cases") if c.get("round_id") == region.round_id and c["resident_id"] == "r08")
    region.update("cases", case["id"], {"simulated": "visit"})
    call = next(c for c in region.query("calls") if c["round_id"] == region.round_id and c["resident_id"] == "r08")
    region.update("calls", call["id"], {"simulated": "ai_resident", "conversation_id": "c1"})
    rows = {r["id"]: r for r in district_view(region, "churu", None, now)["residents"]}
    assert rows["r08"]["simulated"] == "ai_resident" and rows["r08"]["simulated_visit"] is True
    assert rows["r09"]["simulated"] is None and rows["r09"]["simulated_visit"] is False


def test_the_next_morning_before_the_alert_shows_the_pre_alert_state(store, now, tz):
    from app.demo import open_rounds
    from seed.seed import build_all, load_seed
    from tests.helpers import SCRIPTED_PHONES

    from app.datasets import DatasetStore

    load_seed(store, build_all(SCRIPTED_PHONES))
    store = DatasetStore(store, "demo")  # the heat scenario is the demo dataset's
    morning = now.astimezone(tz).replace(hour=7, minute=0)
    open_rounds(store, "churu", morning, tz)
    assert district_view(store, "churu", None, morning)["warning"] is not None  # today: the round is shown
    # 06:30 the next day: yesterday's round is still pinned, but it is not today's.
    next_morning = morning + timedelta(days=1) - timedelta(minutes=30)
    view = district_view(store, "churu", None, next_morning)
    assert view["warning"] is None and view["round"] is None and view["progress"] is None
    assert view["forecast_source"] == "scenario"
    assert max(h["temp_c"] for h in view["forecast"]) == 47.5
    assert view["call_plan"] and len(view["call_plan"]) == 10
    assert all(r["tier"] is None for r in view["residents"])
    churu = next(d for d in region_view(store, None, next_morning)["districts"] if d["id"] == "churu")
    assert churu["warning"] is None and churu["round"] is None
    assert ward_view(store, WARD_2, None, next_morning)["warning"] is None


def test_a_round_whose_id_names_another_day_is_not_shown(region, now, tz):
    from app.region import round_of_district

    day = now.astimezone(tz).date()
    region.update("demo", "current", {"rounds": {"churu": f"churu_heat_{day - timedelta(days=1):%Y%m%d}"}})
    assert round_of_district(region, "churu", now) is None
    region.update("demo", "current", {"rounds": {"churu": f"churu_heat_{day:%Y%m%d}"}})
    assert round_of_district(region, "churu", now) == f"churu_heat_{day:%Y%m%d}"


def test_the_forecast_says_where_it_comes_from(demo_region, now):
    assert district_view(demo_region, "churu", None, now)["forecast_source"] == "round"
    assert district_view(demo_region, "bikaner", None, now)["forecast_source"] == "scenario"
    other = next(d[0] for d in DISTRICTS if d[0] not in ("churu", "bikaner", "jhunjhunu", "sikar"))
    assert district_view(demo_region, other, None, now)["forecast_source"] is None


def test_a_visitors_call_before_the_alert_stays_on_their_screens(store, now, tz):
    from app.demo import open_rounds
    from app.resident_view import resident_view
    from app.worker_view import worker_view
    from seed.seed import build_all, load_seed
    from tests.helpers import SCRIPTED_PHONES

    load_seed(store, build_all(SCRIPTED_PHONES))
    morning = now.astimezone(tz).replace(hour=7, minute=0)
    pointer = open_rounds(store, "churu", morning, tz)
    # 06:30 the next day the visitor's call still goes to the pinned round, yesterday's.
    early = morning + timedelta(days=1) - timedelta(minutes=30)
    add_visitor_call(store, pointer["round_id"], ALICE, early, tz, ward_id=WARD_2, district_id="churu", feeling="mild_symptoms")
    mine = resident_view(store, "v-1", ALICE, False, early)
    assert mine["resident"]["id"] == "v-1" and mine["case"] is not None
    worker = store.get("wards", WARD_2)["worker_id"]
    assert "v-1" in {r["id"] for r in worker_view(store, worker, ALICE, early)["residents"]}
    assert any(r["yours"] for r in district_view(store, "churu", ALICE, early)["residents"])
    # Everyone else still sees the morning before the alert.
    assert district_view(store, "churu", None, early)["warning"] is None
    assert district_view(store, "churu", BOB, early)["warning"] is None


def test_a_peak_at_the_threshold_reaches_it(region, now):
    _scenario_alert(region, region.round_id, peak_c=47.0, peak_hour=14, threshold_c=47.0)
    assert district_view(region, "churu", None, now)["warning"]["reason"] == (
        "Forecast peak 47 °C at 14:00 reaches the Red threshold (47 °C).")


def _live_fetch_over_the_seed(store, at, tz):
    """A fake fetch (never the Weather API) over the seeded wards: hotter further north."""
    from app.config import Settings
    from app.ingest.live_weather import run_live_fetch

    local = at.astimezone(tz)

    def hours(lat, lng):
        return {"forecastHours": [{"displayDateTime": {"year": local.year, "month": local.month, "day": local.day, "hours": h},
                                   "temperature": {"degrees": round(20 + lat / 2 - abs(15 - h) * 0.5, 1)}}
                                  for h in range(6, 21)]}

    run_live_fetch(store, Settings(_env_file=None, demo_district_id="churu"), at, tz, hours,
                   lambda lat, lng: {"forecastDays": []}, sleep=lambda s: None)


def test_live_a_district_without_an_alert_shows_its_stored_weather_not_the_scenario(store, now, tz):
    from seed.seed import build_all, load_seed

    load_seed(store, build_all([]))
    _live_fetch_over_the_seed(store, now, tz)
    alerted = {a["district_id"] for a in store.query("alerts")}
    quiet = next(d for d in ("churu", "bikaner", "jhunjhunu", "sikar") if d not in alerted)
    view = district_view(store, quiet, None, now)
    assert view["warning"] is None and view["forecast_source"] == "weather"
    assert max(h["temp_c"] for h in view["forecast"]) < 40  # Google's curve, not the scenario's 47 °C
    assert view["thresholds"] is None and view["call_plan"] is None
    never = district_view(store, "churu", None, now - timedelta(days=3))  # no weather stored then
    assert never["forecast"] is None and never["forecast_source"] is None and never["thresholds"] is None


def test_live_a_district_never_fetched_has_no_forecast(region, now):
    view = district_view(region, "bikaner", None, now)
    assert view["forecast"] is None and view["forecast_source"] is None and view["call_plan"] is None
