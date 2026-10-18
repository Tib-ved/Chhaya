import json
from datetime import date
from pathlib import Path

from app.weather import parse_days, parse_hours, peak

FIXTURES = Path(__file__).resolve().parent.parent / "seed" / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_hours_of_the_day_with_condition_and_icon():
    payload = load("weather_hours.json")
    first = payload["forecastHours"][0]["displayDateTime"]
    day = date(first["year"], first["month"], first["day"])
    hours = parse_hours(payload, day)
    assert hours and hours == sorted(hours, key=lambda h: h["hour"])
    h = hours[0]
    assert set(h) == {"hour", "temp_c", "feels_like_c", "heat_index_c", "humidity_pct", "rain_pct",
                      "condition_type", "condition_text", "icon_uri", "is_day"}
    assert isinstance(h["temp_c"], (int, float)) and h["icon_uri"].startswith("https://")


def test_hours_of_another_day_are_left_out():
    assert parse_hours(load("weather_hours.json"), date(1999, 1, 1)) == []


def test_days_have_highs_lows_and_conditions():
    days = parse_days(load("weather_days.json"))
    assert len(days) == 5 and days[0]["date"] == "2026-10-15"
    assert days[0]["max_c"] >= days[0]["min_c"] and days[0]["condition_text"]


def test_missing_fields_become_none_not_errors():
    assert parse_hours({"forecastHours": [{"displayDateTime": {"hours": 9}}]}, date(2026, 10, 15))[0]["temp_c"] is None
    assert parse_days({}) == [] and parse_hours({}, date(2026, 10, 15)) == []


def test_peak_is_the_hottest_hour_and_the_earlier_on_a_tie():
    hours = [{"hour": 13, "temp_c": 36.0}, {"hour": 15, "temp_c": 36.0}, {"hour": 9, "temp_c": 30.0}]
    assert peak(hours) == {"temp_c": 36.0, "hour": 13}
    assert peak([{"hour": 9, "temp_c": None}]) is None
