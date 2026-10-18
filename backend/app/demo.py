"""The rounds of the day: one real round for each district under the alert.

Each morning the job runs the heat scenario (app.heat_scenario) through the trigger rules for every
district that is not register_only, which raises its heat alert and opens that district's round
for the day. A district with no scenario curve gets a declared red alert instead (app.ingest.alerts.record_alert: opening it twice the
same day changes nothing, and a round the forecast opened first is kept). Nothing is scripted and
nothing is deleted: earlier rounds, their calls and their transcripts stay. Calls are placed by
/internal/rounds/{id}/dispatch or by a visitor's browser test call.

demo/current names the day's rounds; its round_id is the visitors' district's round, where
browser test calls are made.

Each round opened here is also given the day's replay of recorded AI-resident conversations
(app.replay), which the sweep releases as it falls due."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import Store
from app.heat_scenario import ALERT_HOURS, load_scenario, scenario_alert
from app.ingest.alerts import record_alert, round_id_for
from app.models import Alert
from app.weather import peak

DEMO_COLLECTION, DEMO_DOC = "demo", "current"
DEMO_PREFIX = "demo_"  # rounds of the old scripted demo, and of tests; the app makes none
SCENARIO_SOURCE = "demo_scenario"
SCENARIO_ICON = "https://maps.gstatic.com/weather/v1/sunny"


def pinned(store: Store) -> dict | None:
    return store.get(DEMO_COLLECTION, DEMO_DOC)


def pinned_round_id(store: Store) -> str | None:
    return (pinned(store) or {}).get("round_id")


def pinned_rounds(store: Store) -> dict[str, str]:
    return dict((pinned(store) or {}).get("rounds") or {})


def warned_districts(store: Store) -> list[str]:
    return sorted(d["id"] for d in store.query("districts") if not d.get("register_only"))


def raise_once(store: Store, alert: Alert, tz: ZoneInfo, day) -> dict:
    """The day's round for the alert. An alert already raised today is left as it is, so its
    times stay those of the first run; only a missing round is opened again.

    A round the old 06:00 job opened today on a declared alert is taken over by a new scenario
    alert: the round points at it and carries its peak and curve, and the alert is stamped as
    raised when the round opened, so that "raised at" and the round agree."""
    round_id = round_id_for(alert.district_id, alert.hazard, day)
    existing = store.get("rounds", round_id)
    if existing is not None and store.get("alerts", alert.id) is not None:
        return existing
    if existing is not None and alert.source == "scenario" and str(existing.get("alert_id") or "").startswith("declared_"):
        opened = existing.get("opened_at")
        if isinstance(opened, str):
            opened = datetime.fromisoformat(opened)
        stamped = alert.model_copy(update={"received_at": opened}) if isinstance(opened, datetime) else alert
        store.set("alerts", stamped.id, stamped.model_dump())
        store.update("rounds", round_id, {
            "alert_id": stamped.id, "peak_temp_c": alert.raw["peak_c"], "peak_hour_local": alert.raw["peak_hour"],
            "forecast_hours": alert.raw["forecast_hours"],
        })
        return store.get("rounds", round_id)
    return record_alert(store, alert, tz).model_dump()


def _scenario_hours(curve: dict) -> list[dict]:
    """The scenario's curve in the shape of a live snapshot's hours: only the temperature is known."""
    return [{
        "hour": h["hour"], "temp_c": h["temp_c"], "feels_like_c": None, "heat_index_c": None, "humidity_pct": None,
        "rain_pct": None, "condition_type": "CLEAR", "condition_text": "Sunny", "icon_uri": SCENARIO_ICON, "is_day": None,
    } for h in curve["hours"]]


def write_scenario_weather(store: Store, district_ids: list[str], now: datetime, tz: ZoneInfo) -> None:
    """Today's weather snapshots for each district with a scenario curve, and for its wards, read by
    app.weather_view as the live fetch's are. One snapshot per area and day: a rebuild overwrites it."""
    local = now.astimezone(tz)
    scenario = load_scenario()
    for district_id in district_ids:
        curve = scenario.get(district_id)
        district = store.get("districts", district_id)
        if curve is None or district is None:
            continue
        hours = _scenario_hours(curve)
        areas = [("district", district_id)] + [("ward", w["id"]) for w in store.query("wards", district_id=district_id)]
        for kind, area_id in areas:
            snap = {
                "id": f"{area_id}_{local:%Y%m%d}_scenario", "area_kind": kind, "area_id": area_id,
                "district_id": district_id, "state": district.get("state"), "fetched_at": now,
                "local_day": local.date().isoformat(), "hours": hours, "peak": peak(hours), "source": SCENARIO_SOURCE,
            }
            if kind == "district":
                snap["days"] = []
            store.set("weather", snap["id"], snap)


def open_rounds(store: Store, visitor_district_id: str, now: datetime, tz: ZoneInfo, settings=None) -> dict:
    """Open today's round in every district under the alert and point demo/current at them.
    Safe to run again at any time. Returns the pointer.

    Raises KeyError if the visitors' district has not been seeded or is not under the alert."""
    districts = warned_districts(store)
    if visitor_district_id not in districts:
        raise KeyError(f"districts/{visitor_district_id}")
    settings = settings or get_settings()
    day = now.astimezone(tz).date()
    rounds = {}
    for district_id in districts:
        alert = scenario_alert({"id": district_id}, now, tz, settings)
        if alert is not None:
            opened = raise_once(store, alert, tz, day)
            rounds[district_id] = opened["id"]
            if opened.get("peak_temp_c") is None:
                store.update("rounds", opened["id"], {
                    "peak_temp_c": alert.raw["peak_c"], "peak_hour_local": alert.raw["peak_hour"],
                    "forecast_hours": alert.raw["forecast_hours"],
                })
            continue
        alert = Alert(
            id=f"declared_{district_id}_heat_{day:%Y%m%d}", source="declared", hazard="heat", severity="red",
            district_id=district_id, valid_from=now, valid_to=now + timedelta(hours=ALERT_HOURS),
            raw={"note": "heat alert declared for the district"}, received_at=now,
        )
        rounds[district_id] = raise_once(store, alert, tz, day)["id"]
    write_scenario_weather(store, districts, now, tz)
    store.set(DEMO_COLLECTION, DEMO_DOC, {
        "id": DEMO_DOC, "round_id": rounds[visitor_district_id], "rounds": rounds, "built_at": now,
    })
    from app.replay import load_library, plan_replay  # here: app.replay imports this module
    from app.visitor import forget_old_usage  # here: app.visitor imports this module

    library = load_library()
    for round_id in rounds.values():
        plan_replay(store, round_id, library, day, tz)  # a round that has its plan keeps it

    forget_old_usage(store, now, tz)
    return pinned(store)
