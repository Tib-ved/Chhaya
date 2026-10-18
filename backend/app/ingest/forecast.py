"""Opens a round when the forecast for a district crosses a threshold (main spec D13).

The thresholds are settings. One alert, and so one round, per district, hazard and local day:
running this again the same day changes nothing. With auto-dispatch on, a round is planned
once, while it is still open."""
import logging
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from app.db import Store
from app.dispatch import TaskQueue, plan_round
from app.ingest.alerts import record_alert, round_id_for
from app.models import Alert

logger = logging.getLogger(__name__)


RANK = {"orange": 1, "red": 2}


def extremes(payload: object, today: date | None = None) -> dict | None:
    """The highest and lowest forecast temperature and the local hour of each, or None if the
    response holds no usable temperatures. With `today`, only readings dated that local day
    count (a reading with no date at all is counted); the forecast also covers tomorrow."""
    hours = payload.get("forecastHours") if isinstance(payload, dict) else None
    readings = []
    for hour in hours if isinstance(hours, list) else []:
        if not isinstance(hour, dict):
            continue
        degrees = (hour.get("temperature") or {}).get("degrees")
        shown = hour.get("displayDateTime") or {}
        local_hour = shown.get("hours")
        if today is not None and {"year", "month", "day"} & set(shown):
            if (shown.get("year"), shown.get("month"), shown.get("day")) != (today.year, today.month, today.day):
                continue
        if isinstance(degrees, (int, float)) and isinstance(local_hour, int) and not isinstance(degrees, bool):
            readings.append((degrees, local_hour))
    if not readings:
        return None
    hottest = max(readings, key=lambda reading: reading[0])
    coldest = min(readings, key=lambda reading: reading[0])
    return {"max_c": hottest[0], "max_hour": hottest[1], "min_c": coldest[0], "min_hour": coldest[1]}


def alerts_for(district: dict, found: dict, now: datetime, tz: ZoneInfo, settings) -> list[Alert]:
    """The alerts this forecast calls for: at most one for heat and one for cold."""
    day = now.astimezone(tz).date()
    wanted = []
    if found["max_c"] >= settings.heat_red_c:
        wanted.append(("heat", "red"))
    elif found["max_c"] >= settings.heat_orange_c:
        wanted.append(("heat", "orange"))
    if found["min_c"] <= settings.cold_red_c:
        wanted.append(("cold", "red"))
    elif found["min_c"] <= settings.cold_orange_c:
        wanted.append(("cold", "orange"))
    return [
        Alert(
            id=f"weather_{district['id']}_{hazard}_{day:%Y%m%d}",
            source="weather", hazard=hazard, severity=severity, district_id=district["id"],
            valid_from=now, valid_to=now + timedelta(hours=24), raw=dict(found), received_at=now,
        )
        for hazard, severity in wanted
    ]


def ingest_forecast(
    store: Store,
    queue: TaskQueue,
    settings,
    fetch: Callable[[float, float], dict],
    now: datetime,
    tz: ZoneInfo,
) -> list[dict]:
    """Check every district's forecast and open the rounds it calls for. One district failing
    does not stop the others. Returns what happened, district by district."""
    results = []
    for district in sorted(store.query("districts"), key=lambda d: d["id"]):
        if district.get("register_only"):
            continue  # on the register for the screens; nobody there is ever called
        outcome = {"district_id": district["id"], "alerts": [], "rounds": [], "error": None}
        results.append(outcome)
        try:
            payload = fetch(district["lat"], district["lng"])
        except Exception as error:  # only the type: a message can carry the request and its key
            logger.error("no forecast for district %s (%s)", district["id"], type(error).__name__)
            outcome["error"] = type(error).__name__
            continue
        if extremes(payload) is None:
            logger.error("the forecast for district %s holds no temperatures", district["id"])
            outcome["error"] = "no temperatures in the forecast"
            continue
        found = extremes(payload, now.astimezone(tz).date())
        if found is None:
            logger.info("the forecast for district %s holds nothing for today", district["id"])
            outcome["error"] = "no temperatures in the forecast for today"
            continue
        for alert in alerts_for(district, found, now, tz, settings):
            existing = store.get("alerts", alert.id)
            round_id = round_id_for(alert.district_id, alert.hazard, now.astimezone(tz).date())
            upgrade = existing is not None and RANK[alert.severity] > RANK.get(existing["severity"], 0)
            outcome["alerts"].append(alert.id)
            if existing is not None and not upgrade:
                if store.get("rounds", round_id) is not None:
                    outcome["rounds"].append(round_id)
                continue  # a later forecast never lowers an alert
            opened = record_alert(store, alert, tz)
            if opened is None:
                continue
            outcome["rounds"].append(opened.id)
            if opened.status == "open" and (upgrade or opened.peak_temp_c is None):
                cold = alert.hazard == "cold"
                store.update("rounds", opened.id, {
                    "peak_temp_c": found["min_c"] if cold else found["max_c"],
                    "peak_hour_local": found["min_hour"] if cold else found["max_hour"],
                })
        if settings.ingest_auto_dispatch:
            for round_id in outcome["rounds"]:
                try:
                    # Only a round never yet dispatched. One that is being called belongs to
                    # the queue and the sweep: planning it again on every run would queue
                    # every call still waiting, all day.
                    if (store.get("rounds", round_id) or {}).get("status") != "open":
                        continue
                    plan_round(store, queue, round_id, now, tz, settings.call_start_hour, settings.call_end_hour)
                except Exception as error:
                    logger.error("could not plan round %s (%s)", round_id, type(error).__name__)
                    outcome["error"] = type(error).__name__
    return results
