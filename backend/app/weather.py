"""Asks the Google Weather API for a place's hourly forecast."""
from datetime import date
from typing import Callable

import requests

FORECAST_URL = "https://weather.googleapis.com/v1/forecast/hours:lookup"


def fetch_forecast(api_key: str, lat: float, lng: float, hours: int = 24, get: Callable = requests.get) -> dict:
    """The next `hours` hours for a place. The key goes in a header, so that it is never part
    of an address that a log or an error message might show. Raises if the request fails."""
    response = get(
        FORECAST_URL,
        params={"location.latitude": lat, "location.longitude": lng, "hours": hours},
        headers={"X-Goog-Api-Key": api_key},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


DAYS_URL = "https://weather.googleapis.com/v1/forecast/days:lookup"


def fetch_days(api_key: str, lat: float, lng: float, days: int = 5, get: Callable = requests.get) -> dict:
    """The next `days` days for a place. The key goes in a header, never in the address."""
    response = get(DAYS_URL, params={"location.latitude": lat, "location.longitude": lng, "days": days},
                   headers={"X-Goog-Api-Key": api_key}, timeout=20)
    response.raise_for_status()
    return response.json()


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _condition(block: dict) -> tuple:
    condition = (block or {}).get("weatherCondition") or {}
    return condition.get("type"), (condition.get("description") or {}).get("text"), condition.get("iconBaseUri")


def parse_hours(payload: dict, day: date) -> list[dict]:
    hours = payload.get("forecastHours") if isinstance(payload, dict) else None
    out = []
    for hour in hours if isinstance(hours, list) else []:
        if not isinstance(hour, dict):
            continue
        shown = hour.get("displayDateTime") or {}
        if {"year", "month", "day"} <= set(shown) and (shown["year"], shown["month"], shown["day"]) != (day.year, day.month, day.day):
            continue
        if not isinstance(shown.get("hours"), int):
            continue
        kind, text, icon = _condition(hour)
        out.append({
            "hour": shown["hours"],
            "temp_c": _num((hour.get("temperature") or {}).get("degrees")),
            "feels_like_c": _num((hour.get("feelsLikeTemperature") or {}).get("degrees")),
            "heat_index_c": _num((hour.get("heatIndex") or {}).get("degrees")),
            "humidity_pct": _num(hour.get("relativeHumidity")),
            "rain_pct": _num(((hour.get("precipitation") or {}).get("probability") or {}).get("percent")),
            "condition_type": kind, "condition_text": text, "icon_uri": icon,
            "is_day": hour.get("isDaytime") if isinstance(hour.get("isDaytime"), bool) else None,
        })
    return sorted(out, key=lambda h: h["hour"])


def parse_days(payload: dict) -> list[dict]:
    days = payload.get("forecastDays") if isinstance(payload, dict) else None
    out = []
    for item in days if isinstance(days, list) else []:
        shown = (item or {}).get("displayDate") or {}
        if not {"year", "month", "day"} <= set(shown):
            continue
        daytime = item.get("daytimeForecast") or {}
        kind, text, icon = _condition(daytime)
        out.append({
            "date": f"{shown['year']:04d}-{shown['month']:02d}-{shown['day']:02d}",
            "max_c": _num((item.get("maxTemperature") or {}).get("degrees")),
            "min_c": _num((item.get("minTemperature") or {}).get("degrees")),
            "condition_type": kind, "condition_text": text, "icon_uri": icon,
            "rain_pct": _num(((daytime.get("precipitation") or {}).get("probability") or {}).get("percent")),
        })
    return out


def peak(hours: list[dict]) -> dict | None:
    known = [h for h in hours if h.get("temp_c") is not None]
    if not known:
        return None
    best = min(known, key=lambda h: (-h["temp_c"], h["hour"]))
    return {"temp_c": best["temp_c"], "hour": best["hour"]}
