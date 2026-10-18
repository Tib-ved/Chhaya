"""One read of the stored weather of a state, district or ward (spec §3, §6), for live and demo alike.

It reads only what is stored: the live fetch's snapshots (app.ingest.live_weather) or the demo
scenario's (app.demo.open_rounds), whichever dataset the store is. It never calls the Weather API.

A ward shows its own hours and its district's days and alert; a state shows the hours, days and
alert of its hottest district. Each area shows its newest snapshot up to today: before today's
data is due (the morning fetch, or the demo rebuild) that is the previous day's. Once the morning
fetch is overdue (LIVE_FETCH_HOURS[0] plus FETCH_GRACE_MINUTES) with no snapshot of the area today,
the live dataset says it is unavailable since the fetch was due, and still shows the last day it has."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.db import Store
from app.ingest.alerts import round_id_for
from app.ingest.live_weather import ALERT_SOURCE, FETCH_GRACE_MINUTES, LIVE_FETCH_HOURS
from app.ingest.state_rule import reason_text
from app.region import scenario_reason

LIVE_SOURCE, DEMO_SOURCE = "google_weather", "demo_scenario"
NOW_FIELDS = ("temp_c", "feels_like_c", "condition_text", "icon_uri", "is_day")
HOUR_FIELDS = ("hour", "temp_c", "feels_like_c", "heat_index_c", "humidity_pct", "rain_pct",
               "condition_type", "condition_text", "icon_uri", "is_day")
DAY_FIELDS = ("date", "max_c", "min_c", "condition_type", "condition_text", "icon_uri", "rain_pct")


def _dataset(store: Store) -> str:
    return getattr(store, "dataset", "live")


def newest_snapshot(store: Store, area_id: str, local_day: str) -> dict | None:
    """The newest snapshot of an area on `local_day` (YYYY-MM-DD) or, when it has none that day, any
    day before it. Today's are read first: the older scan reads up to KEEP_DAYS of them."""
    found = store.query("weather", area_id=area_id, local_day=local_day)
    if not found:
        found = [s for s in store.query("weather", area_id=area_id) if str(s.get("local_day") or "") <= local_day]
    return max(found, key=lambda s: s["fetched_at"]) if found else None


def _day_of(snapshot: dict | None, today: date) -> date:
    """The local day a snapshot is of; today without one."""
    try:
        return date.fromisoformat(str((snapshot or {})["local_day"]))
    except (KeyError, ValueError):
        return today


def _day_alert(store: Store, district_id: str, day: date) -> dict | None:
    """The alert behind the district's heat round of `day`, or the live fetch's alert of that day.
    The screens pass the day of the weather they show, so overnight the alert beside the last
    day's weather is that day's, as the top bar's round is."""
    current = store.get("rounds", round_id_for(district_id, "heat", day))
    alert = store.get("alerts", current["alert_id"]) if current and current.get("alert_id") else None
    return alert or store.get("alerts", f"forecast_{district_id}_heat_{day:%Y%m%d}")


def _alert_shown(alert: dict | None, district_name: str) -> dict | None:
    if alert is None:
        return None
    raw = alert.get("raw") or {}
    text = reason_text(raw, district_name) if alert.get("source") == ALERT_SOURCE and raw.get("rule") else scenario_reason(alert)
    return {"severity": alert.get("severity"), "reason_text": text, "raw": raw}


def _now(hours: list[dict], hour: int) -> dict | None:
    if not hours:
        return None
    nearest = min(hours, key=lambda h: (abs(h["hour"] - hour), h["hour"]))
    return {"hour": nearest["hour"], **{field: nearest.get(field) for field in NOW_FIELDS}}


def _today(hours: list[dict], days: list[dict], local_day: str) -> dict | None:
    if not hours and not days:
        return None
    day = next((d for d in days if d.get("date") == local_day), days[0] if days else {})
    temps = [h["temp_c"] for h in hours if h.get("temp_c") is not None]
    humidity = [h["humidity_pct"] for h in hours if h.get("humidity_pct") is not None]
    rain = [h["rain_pct"] for h in hours if h.get("rain_pct") is not None]
    return {
        "max_c": day.get("max_c") if day.get("max_c") is not None else (max(temps) if temps else None),
        "min_c": day.get("min_c") if day.get("min_c") is not None else (min(temps) if temps else None),
        "humidity_pct": round(sum(humidity) / len(humidity)) if humidity else None,
        "rain_pct": max(rain) if rain else None,
    }


def _state_block(store: Store, state: str, districts: list[dict], local_day: str, day) -> tuple[dict, dict]:
    """The state's districts, hottest first, and each district's newest snapshot up to today."""
    rows, snaps = [], {}
    for district in districts:
        snap = newest_snapshot(store, district["id"], local_day)
        snaps[district["id"]] = snap
        top = (snap or {}).get("peak") or {}
        alert = _day_alert(store, district["id"], _day_of(snap, day))
        rows.append({"id": district["id"], "name": district["name"], "peak_c": top.get("temp_c"),
                     "peak_hour": top.get("hour"), "alert": (alert or {}).get("severity")})
    rows.sort(key=lambda r: (r["peak_c"] is None, -(r["peak_c"] or 0), r["peak_hour"] if r["peak_hour"] is not None else 99, r["id"]))
    peaks = [r["peak_c"] for r in rows if r["peak_c"] is not None]
    return {
        "name": state,
        "mean_peak_c": round(sum(peaks) / len(peaks), 1) if peaks else None,
        "max_peak_c": max(peaks) if peaks else None,
        "districts": rows,
    }, snaps


def _in_state(store: Store, state: str) -> list[dict]:
    in_state = sorted((d for d in store.query("districts") if d.get("state") == state), key=lambda d: d["id"])
    if not in_state:
        raise KeyError(f"states/{state}")
    return in_state


def weather_view(store: Store, state: str | None, district_id: str | None, ward_id: str | None,
                 now: datetime, tz: ZoneInfo) -> dict:
    """The response of GET /api/weather for the narrowest area named: a ward, else a district, else a
    state. Raises KeyError for an unknown area, or when none is named."""
    local = now.astimezone(tz)
    ward = district = None
    if ward_id:
        ward = store.get("wards", ward_id)
        if ward is None:
            raise KeyError(f"wards/{ward_id}")
        district_id = ward.get("district_id")
    if district_id:
        district = store.get("districts", district_id)
        if district is None:
            raise KeyError(f"districts/{district_id}")
        state = district.get("state")
    if not state:
        raise KeyError("state")
    in_state = _in_state(store, state)
    state_block, snaps = _state_block(store, state, in_state, local.date().isoformat(), local.date())
    return _view(store, state, in_state, state_block, snaps, district, ward, local, tz)


def district_weather_views(store: Store, state: str, now: datetime, tz: ZoneInfo) -> dict[str, dict]:
    """weather_view of every district of a state, by district id, reading the state's weather once
    (weather_view per district reads the whole state each time). Raises KeyError for an unknown state."""
    local = now.astimezone(tz)
    in_state = _in_state(store, state)
    state_block, snaps = _state_block(store, state, in_state, local.date().isoformat(), local.date())
    return {d["id"]: _view(store, state, in_state, state_block, snaps, d, None, local, tz) for d in in_state}


def _view(store: Store, state: str, in_state: list[dict], state_block: dict, snaps: dict,
          district: dict | None, ward: dict | None, local: datetime, tz: ZoneInfo) -> dict:
    day, local_day = local.date(), local.date().isoformat()
    if ward is not None:
        area = {"kind": "ward", "id": ward["id"], "name": ward.get("name") or ward["id"], "state": state}
    elif district is not None:
        area = {"kind": "district", "id": district["id"], "name": district["name"], "state": state}
    else:
        area = {"kind": "state", "id": state, "name": state, "state": state}
        hottest = next((r["id"] for r in state_block["districts"] if r["peak_c"] is not None), None)
        district = next((d for d in in_state if d["id"] == hottest), None)

    district_snap = snaps.get(district["id"]) if district else None
    shown = newest_snapshot(store, ward["id"], local_day) if ward is not None else district_snap
    hours = [{f: h.get(f) for f in HOUR_FIELDS} for h in (shown or {}).get("hours") or [] if isinstance(h.get("hour"), int)]
    days = [{f: d.get(f) for f in DAY_FIELDS} for d in ((district_snap or {}).get("days") or [] if shown else []) if d.get("date")]
    shown_day = _day_of(shown or district_snap, day)
    alert = _alert_shown(_day_alert(store, district["id"], shown_day), district["name"]) if district else None
    if alert is None and area["kind"] == "state":
        alert = next((a for a in (_alert_shown(_day_alert(store, d["id"], _day_of(snaps.get(d["id"]), day)), d["name"])
                                  for d in in_state) if a), None)

    due = datetime.combine(day, time(LIVE_FETCH_HOURS[0]), tzinfo=tz)
    has_today = (shown or {}).get("local_day") == local_day
    overdue = _dataset(store) == "live" and not has_today and local >= due + timedelta(minutes=FETCH_GRACE_MINUTES)
    return {
        "area": area,
        "source": DEMO_SOURCE if _dataset(store) == "demo" else LIVE_SOURCE,
        "fetched_at": (shown or {}).get("fetched_at"),
        "now": _now(hours, local.hour),
        "today": _today(hours, days, local_day),
        "hours": hours,
        "days": days,
        "state": state_block,
        "alert": alert,
        "unavailable_since": due if overdue else None,
    }
