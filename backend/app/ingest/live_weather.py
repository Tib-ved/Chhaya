"""The twice-daily live fetch (spec §5, §7): Google's forecast for every ward, a district's hours as
the mean of its wards', the state rule's alerts, and a live round for each district under one.

An alert is raised once a day and only ever goes up (orange to red), never down nor away: an
afternoon run that finds a district cooler, or finds no data for it, leaves the morning's alert as
it was. Records older than KEEP_DAYS are pruned at the end of each run. Logs carry counts only."""
import logging
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from app.db import Store
from app.demo import DEMO_COLLECTION, DEMO_DOC, DEMO_PREFIX
from app.ingest.alerts import record_alert, round_id_for
from app.ingest.state_rule import alerts_by_state
from app.models import Alert, Round
from app.weather import parse_days, parse_hours, peak

logger = logging.getLogger(__name__)

KEEP_DAYS = 30
ALERT_SOURCE = "forecast_state_rule"
SOURCE = "google_weather"
RETRY_SLEEPS = (1, 4)  # three tries in all
# Cloud Run gives a request 900 s. Past this many seconds of a run no call is begun nor retried:
# the wards left are counted failed, and the run still writes what it has.
FETCH_BUDGET_S = 600
ALERT_HOURS = 24
# The scheduler's fetches (scripts/cloud_setup.sh), local hours. Until the first has had its grace,
# the screens show the newest earlier snapshot without calling it unavailable (app.weather_view).
LIVE_FETCH_HOURS = (6, 14)
FETCH_GRACE_MINUTES = 30
NUMERIC = ("temp_c", "feels_like_c", "heat_index_c", "humidity_pct", "rain_pct")
CONDITION = ("condition_type", "condition_text", "icon_uri", "is_day")
DATE_SUFFIX = re.compile(r"_(\d{8})$")


def snapshot_id(area_id: str, fetched_local: datetime) -> str:
    return f"{area_id}_{fetched_local:%Y%m%d_%H%M}"


def latest_snapshot(store: Store, area_id: str, local_day: str) -> dict | None:
    """The newest snapshot of an area on a local day (`YYYY-MM-DD`), or None."""
    found = store.query("weather", area_id=area_id, local_day=local_day)
    return max(found, key=lambda s: s["fetched_at"]) if found else None


class BudgetSpent(Exception):
    """The run's time budget is spent: no call is begun."""


def _with_retries(call: Callable, sleep: Callable, spent: Callable[[], bool] = lambda: False):
    """call(); after a failure wait 1 s, then 4 s. Raises the last error after the third, and
    BudgetSpent instead of beginning a try once `spent()`."""
    for pause in (*RETRY_SLEEPS, None):
        if spent():
            raise BudgetSpent()
        try:
            return call()
        except Exception:
            if pause is None or spent():
                raise
            sleep(pause)


def _with_earlier_hours(fetched: list[dict], earlier_snapshot: dict | None) -> list[dict]:
    """The local day's whole curve: the hours before the first fetched hour come from the day's
    earlier snapshot (the 14:00 fetch starts at 14:00), and where both have an hour the fetch wins."""
    if not earlier_snapshot:
        return fetched
    first = min((h["hour"] for h in fetched), default=24)
    merged = {h["hour"]: h for h in earlier_snapshot.get("hours") or [] if h.get("hour", 24) < first}
    merged.update({h["hour"]: h for h in fetched})
    return [merged[hour] for hour in sorted(merged)]


def _ward_snapshot(ward: dict, district: dict, hours: list[dict], now: datetime, local: datetime) -> dict:
    return {
        "id": snapshot_id(ward["id"], local), "area_kind": "ward", "area_id": ward["id"],
        "district_id": ward["district_id"], "state": district.get("state"), "fetched_at": now,
        "local_day": local.date().isoformat(), "hours": hours, "peak": peak(hours), "source": SOURCE,
    }


def _district_hours(ward_snapshots: list[dict]) -> list[dict]:
    """Per hour: the mean of the wards' numeric fields (1 dp); the conditions of the first ward that has them."""
    by_hour: dict[int, list[dict]] = defaultdict(list)
    for snap in ward_snapshots:
        for hour in snap["hours"]:
            by_hour[hour["hour"]].append(hour)
    out = []
    for hour, items in sorted(by_hour.items()):
        merged = {"hour": hour}
        for field in NUMERIC:
            values = [i[field] for i in items if i.get(field) is not None]
            merged[field] = round(sum(values) / len(values), 1) if values else None
        for field in CONDITION:
            merged[field] = next((i[field] for i in items if i.get(field) is not None), None)
        out.append(merged)
    return out


def _apply_alerts(store: Store, results: list[dict], now: datetime, tz: ZoneInfo, day: date) -> dict:
    """Raise each new alert (opening its round and planning its replay); upgrade orange to red."""
    from app.replay import load_library, plan_replay  # here: app.replay imports app.demo

    counts = {"alerts_new": 0, "alerts_upgraded": 0, "rounds_opened": 0}
    library = None
    for result in results:
        district_id = result["district_id"]
        alert_id = f"forecast_{district_id}_heat_{day:%Y%m%d}"
        existing = store.get("alerts", alert_id)
        round_id = round_id_for(district_id, "heat", day)
        raw = {**result["reason"], "fetched_at": now}
        if existing is None:
            had_round = store.get("rounds", round_id) is not None
            record_alert(store, Alert(
                id=alert_id, source=ALERT_SOURCE, hazard="heat", severity=result["severity"],
                district_id=district_id, valid_from=now, valid_to=now + timedelta(hours=ALERT_HOURS),
                raw=raw, received_at=now,
            ), tz)
            _set_round_peak(store, round_id, alert_id, result["reason"])
            counts["alerts_new"] += 1
            counts["rounds_opened"] += 0 if had_round else 1
            library = library if library is not None else load_library()
            plan_replay(store, round_id, library, day, tz)
            continue
        if existing.get("severity") == "orange" and result["severity"] == "red":
            store.update("alerts", alert_id, {"severity": "red", "raw": raw})
            _set_round_peak(store, round_id, alert_id, result["reason"])
            counts["alerts_upgraded"] += 1
        if store.get("rounds", round_id) is None:
            # The alert stands but its round has gone (cleared, or a write failed after the alert's):
            # reopen it, as the alert opened it, with the peak it has now.
            store.create("rounds", round_id, Round(
                id=round_id, alert_id=alert_id, district_id=district_id, hazard="heat",
                opened_at=existing.get("received_at") or now).model_dump())
            _set_round_peak(store, round_id, alert_id, (store.get("alerts", alert_id) or {}).get("raw") or result["reason"])
            counts["rounds_opened"] += 1
            library = library if library is not None else load_library()
            plan_replay(store, round_id, library, day, tz)
    return counts


def _set_round_peak(store: Store, round_id: str, alert_id: str, reason: dict) -> None:
    """The round shows the peak of the alert behind it, as a scenario round does. A round some
    other alert opened first keeps its own."""
    current = store.get("rounds", round_id)
    if current is not None and current.get("alert_id") == alert_id:
        store.update("rounds", round_id, {"peak_temp_c": reason["district_peak_c"], "peak_hour_local": reason["peak_hour"]})


def _write_pointer(store: Store, settings, now: datetime, tz: ZoneInfo, day: date, alerted: list[str]) -> None:
    """demo/current names today's rounds, keeping those an earlier run of the same day named."""
    rounds = {}
    previous = store.get(DEMO_COLLECTION, DEMO_DOC) or {}
    built = previous.get("built_at")
    if isinstance(built, str):
        built = datetime.fromisoformat(built)
    if isinstance(built, datetime) and built.astimezone(tz).date() == day:
        rounds.update(previous.get("rounds") or {})
    for district_id in alerted:
        rounds[district_id] = round_id_for(district_id, "heat", day)
    rounds = {d: r for d, r in rounds.items() if _opened_by_the_rule(store, r)}
    chosen = rounds.get(settings.demo_district_id) or (sorted(rounds.values())[0] if rounds else None)
    store.set(DEMO_COLLECTION, DEMO_DOC, {"id": DEMO_DOC, "round_id": chosen, "rounds": rounds, "built_at": now})


def _opened_by_the_rule(store: Store, round_id: str) -> bool:
    """The round exists and its alert is this module's (the pointer names live rounds only)."""
    current = store.get("rounds", round_id) if round_id else None
    alert = store.get("alerts", current.get("alert_id") or "") if current and current.get("alert_id") else None
    return bool(alert) and alert.get("source") == ALERT_SOURCE


def _dated(doc_id: str) -> date | None:
    match = DATE_SUFFIX.search(doc_id or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def _is_live_round(store: Store, round_doc: dict) -> bool:
    """A round this module opened: its alert is a live forecast alert (or, once that alert is gone, has its id)."""
    alert_id = str(round_doc.get("alert_id") or "")
    alert = store.get("alerts", alert_id) if alert_id else None
    if alert is not None:
        return alert.get("source") == ALERT_SOURCE
    return alert_id.startswith("forecast_")


def prune_live(store: Store, now: datetime, tz: ZoneInfo, keep_days: int = KEEP_DAYS) -> int:
    """Delete the live fetch's weather, alerts and rounds, and those rounds' cases and calls, from
    more than `keep_days` before today. Never anything with a demo_ id, nor an alert from another
    source or a round such an alert opened, whatever store it is handed."""
    cutoff = now.astimezone(tz).date() - timedelta(days=keep_days)
    old = lambda d: d is not None and d < cutoff  # noqa: E731
    mine = lambda doc_id: not str(doc_id or "").startswith(DEMO_PREFIX)  # noqa: E731
    doomed: list[tuple[str, str]] = []
    for snap in store.query("weather"):
        try:
            day = date.fromisoformat(str(snap.get("local_day")))
        except ValueError:
            continue
        if mine(snap.get("id")) and snap.get("source") == SOURCE and old(day):
            doomed.append(("weather", snap["id"]))
    old_rounds = set()
    for doc in store.query("rounds"):
        if mine(doc.get("id")) and old(_dated(str(doc.get("id")))) and _is_live_round(store, doc):
            old_rounds.add(doc["id"])
            doomed.append(("rounds", doc["id"]))
    for doc in store.query("alerts"):
        if mine(doc.get("id")) and doc.get("source") == ALERT_SOURCE and old(_dated(str(doc.get("id")))):
            doomed.append(("alerts", doc["id"]))
    for collection in ("cases", "calls"):
        for doc in store.query(collection):
            if mine(doc.get("id")) and doc.get("round_id") in old_rounds:
                doomed.append((collection, doc["id"]))
    for collection, doc_id in doomed:
        store.delete(collection, doc_id)
    return len(doomed)


def run_live_fetch(store: Store, settings, now: datetime, tz: ZoneInfo, fetch_hours: Callable,
                   fetch_days: Callable, sleep: Callable = time.sleep, budget_s: float = FETCH_BUDGET_S,
                   monotonic: Callable[[], float] = time.monotonic) -> dict:
    started = monotonic()
    spent = lambda: monotonic() - started >= budget_s  # noqa: E731
    local = now.astimezone(tz)
    day = local.date()
    districts = {d["id"]: d for d in store.query("districts")}
    wards_ok, wards_failed = 0, 0
    by_district: dict[str, list[dict]] = defaultdict(list)
    for ward in sorted(store.query("wards"), key=lambda w: w["id"]):
        district = districts.get(ward.get("district_id"))
        if district is None:
            continue
        try:
            payload = _with_retries(lambda: fetch_hours(ward["lat"], ward["lng"]), sleep, spent)
        except Exception:
            wards_failed += 1
            continue
        earlier = latest_snapshot(store, ward["id"], day.isoformat())
        hours = _with_earlier_hours(parse_hours(payload, day), earlier)
        snap = _ward_snapshot(ward, district, hours, now, local)
        store.set("weather", snap["id"], snap)
        by_district[ward["district_id"]].append(snap)
        wards_ok += 1

    rule_input = []
    for district_id, ward_snaps in sorted(by_district.items()):
        district = districts[district_id]
        try:
            days = parse_days(_with_retries(lambda: fetch_days(district["lat"], district["lng"]), sleep, spent))
        except Exception:
            days = []
        hours = _district_hours(ward_snaps)
        top = peak(hours)
        snap = {
            "id": snapshot_id(district_id, local), "area_kind": "district", "area_id": district_id,
            "district_id": district_id, "state": district.get("state"), "fetched_at": now,
            "local_day": day.isoformat(), "hours": hours, "days": days, "peak": top, "source": SOURCE,
        }
        store.set("weather", snap["id"], snap)
        rule_input.append({"district_id": district_id, "state": district.get("state"),
                           "peak_c": top["temp_c"] if top else None, "peak_hour": top["hour"] if top else None})

    results = alerts_by_state(rule_input)
    counts = _apply_alerts(store, results, now, tz, day)
    _write_pointer(store, settings, now, tz, day, [r["district_id"] for r in results])
    pruned = prune_live(store, now, tz)
    summary = {"wards_ok": wards_ok, "wards_failed": wards_failed, "districts_ok": len(rule_input), **counts, "pruned": pruned}
    logger.info("live weather fetch: %s", summary)
    return summary
