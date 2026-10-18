"""The officer's drill-down: the region, a district, a ward.

Every district has a register. A district has a round while one of the day's rounds is its own.
Everything a screen counts is counted here, from the same rows the screen is given."""
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from app.cases import VISIT_OUTCOMES
from app.config import get_settings
from app.db import Store
from app.demo import DEMO_PREFIX, pinned_round_id, pinned_rounds
from app.heat_scenario import load_scenario
from app.ingest.live_weather import ALERT_SOURCE
from app.ingest.state_rule import reason_text
from app.models import REACHED_STATUSES
from app.report import RoundCache, RoundData, load_register, load_round
from app.risk import CONDITION_WORDS, risk_factors
from app.triage import REASON_LABELS, SEVERITY, TIER_ORDER

AGE_BANDS = ("Under 55", "55 to 64", "65 to 74", "75 and over")
HIGH_RISK_SCORE = 5
NEED_LABELS = {
    "water_ors": "Water and ORS",
    "medicine_check": "Medicine check",
    "cooling_centre": "Cooling centre details",
}
PROGRESS_STEP_MINUTES = 10
MAX_PROGRESS_STEPS = 100  # a round is a day's work; a longer span is not charted
_UNCACHED = RoundCache(0)


def age_band(age: int) -> str:
    return AGE_BANDS[3] if age >= 75 else AGE_BANDS[2] if age >= 65 else AGE_BANDS[1] if age >= 55 else AGE_BANDS[0]


def worst_answers(calls: list[dict]) -> dict[str, dict]:
    """Across a resident's attempts, the most worrying answer to each question, as cases.py keeps it."""
    kept: dict[str, dict] = {}
    for call in sorted(calls, key=lambda c: c["attempt"]):
        for question, answer in (call.get("answers") or {}).items():
            order, value, old = SEVERITY.get(question, []), answer.get("value"), kept.get(question)
            if old and old.get("value") in order and value in order and order.index(old["value"]) > order.index(value):
                continue
            kept[question] = answer
    return kept


def needs_of(answers: dict[str, dict]) -> list[str]:
    """What a worker should carry or bring up, from what the resident said."""
    value = lambda question: (answers.get(question) or {}).get("value")  # noqa: E731
    needs = []
    if value("fluids_food") in ("low", "none"):
        needs.append("water_ors")
    if value("medicine") in ("running_low", "out"):
        needs.append("medicine_check")
    if value("shelter") == "none":
        needs.append("cooling_centre")
    return needs


_DAY_SUFFIX = re.compile(r"_(\d{8})$")


def round_day(round_id: str, current: dict | None, tz: ZoneInfo) -> date | None:
    """The local day a round belongs to: from its `_YYYYMMDD` id suffix, else from when it opened.
    None when neither says."""
    found = _DAY_SUFFIX.search(round_id)
    if found:
        try:
            return datetime.strptime(found.group(1), "%Y%m%d").date()
        except ValueError:
            pass
    opened = (current or {}).get("opened_at")
    if isinstance(opened, str):
        try:
            opened = datetime.fromisoformat(opened)
        except ValueError:
            return None
    return opened.astimezone(tz).date() if isinstance(opened, datetime) else None


def round_of_district(
    store: Store, district_id: str, now: datetime | None = None, views: RoundCache | None = None,
    visitor_id: str | None = None,
) -> str | None:
    """The round a district's screens show: its own among the day's rounds. A pointer written
    before there were several rounds names one round only; then it is shown for its district.
    Given `now`, a round of another local day than today is no round: the screens show the
    morning before the alert until today's round is opened (the visitors' test calls still go
    to the pinned round, app.visitor). A visitor who has a call in that round still sees it, so
    a test call made before 07:00 stays on their screens; everyone else sees the morning before
    the alert. A scripted round (DEMO_PREFIX) is always shown."""
    rounds = pinned_rounds(store)
    current = None
    if rounds:
        round_id = rounds.get(district_id)
    else:
        round_id = pinned_round_id(store)
        current = store.get("rounds", round_id) if round_id else None
        if not (current and current.get("district_id") == district_id):
            return None
    if round_id is None or now is None or round_id.startswith(DEMO_PREFIX):
        return round_id  # a scripted round (tests, the old demo) is held at its own fixed clock
    tz = ZoneInfo(get_settings().local_tz)
    if current is None and not _DAY_SUFFIX.search(round_id):
        current = (views or _UNCACHED).get(round_id, "round_doc", lambda: store.get("rounds", round_id))
    day = round_day(round_id, current, tz)
    if day is None or day == now.astimezone(tz).date():
        return round_id
    if visitor_id and any(c.get("round_id") == round_id for c in store.query("calls", visitor_id=visitor_id)):
        return round_id  # the visitor's own test call is in it
    return None


def _row(resident: dict, ward: dict, case: dict | None, calls: list[dict], current: dict | None, now: datetime) -> dict:
    if not calls:
        call_status = "not_called"
    elif any(c["status"] in REACHED_STATUSES for c in calls):
        call_status = "reached"
    elif case and "A8" in case["reasons"]:
        call_status = "no_answer"
    else:
        call_status = "trying"
    waiting = None
    if case and case["status"] == "open" and case["tier"] != "green" and case.get("opened_at"):
        # A seeded case is timed against the demo round's fixed clock; a visitor's own, the real one.
        clock = now if case.get("visitor_id") else (current or {}).get("as_of") or now
        waiting = max(0, int((clock - case["opened_at"]).total_seconds() // 60))
    flag = (case or {}).get("escalation") or {}
    score = resident.get("risk_score") or 0
    return {
        "id": resident["id"],
        "name": resident["name"],
        "name_hi": resident.get("name_hi") or "",
        "age": resident["age"],
        "ward_id": resident.get("ward_id"),
        "ward_name": ward.get("name"),
        "worker_id": (case or {}).get("worker_id") or ward.get("worker_id"),
        "risk_score": score,
        "risk_factors": risk_factors(resident),
        "lives_alone": bool(resident.get("lives_alone")),
        "outdoor_worker": bool(resident.get("outdoor_worker")),
        "conditions": list(resident.get("conditions") or []),
        "age_band": age_band(resident["age"]),
        "high_risk": score >= HIGH_RISK_SCORE,
        "lat": resident.get("lat"),
        "lng": resident.get("lng"),
        "call_status": call_status,
        "attempts": max((c["attempt"] for c in calls), default=0),
        "tier": case["tier"] if case else None,
        "reasons": [r for r in case["reasons"] if r != "G1"] if case else [],
        "reason_labels": [REASON_LABELS.get(r, r) for r in case["reasons"] if r != "G1"] if case else [],
        "needs": needs_of(worst_answers(calls)) if case else [],
        "case_id": case["id"] if case else None,
        "case_status": ("open" if case["status"] == "open" else "visited") if case else None,
        "opened_at": case.get("opened_at") if case else None,
        "visited_at": case.get("visited_at") if case else None,
        "outcome": case.get("outcome") if case else None,
        "outcome_label": VISIT_OUTCOMES.get(case.get("outcome")) if case else None,
        "waiting_minutes": waiting,
        "escalated": bool(flag) and not flag.get("acknowledged_at"),
        "raised": bool((case or {}).get("raised")),
        "yours": bool(resident.get("visitor_id")),
        "simulated": "ai_resident" if any(c.get("simulated") == "ai_resident" for c in calls) else None,
        "simulated_visit": bool(case) and case.get("simulated") == "visit",
    }


@dataclass(frozen=True)
class DistrictLoad:
    """A district as one viewer may see it: its register, its round if it has one, and a row per resident."""

    register: dict
    data: RoundData | None
    alert: dict | None
    rows: list[dict]


def load_district(
    store: Store, district_id: str, visitor_id: str | None, now: datetime,
    views: RoundCache | None = None, registers: RoundCache | None = None,
) -> DistrictLoad:
    register = load_register(store, district_id, registers)
    round_id = round_of_district(store, district_id, now, views, visitor_id)
    data, alert = None, None
    if round_id:
        try:
            data = load_round(store, round_id, visitor_id, views, registers)
            alert = (views or _UNCACHED).get(round_id, "alerts", lambda: store.get("alerts", data.round["alert_id"]))
        except KeyError:
            data = None  # the pointer names a round that has gone: for a moment, during a rebuild
    residents = list(register["residents"])
    cases, calls = {}, {}
    if data:
        residents += [r for r in data.residents.values() if r.get("visitor_id")]
        cases = {c["resident_id"]: c for c in data.cases}
        calls = data.calls_by_resident()
    rows = [
        _row(r, register["wards"].get(r.get("ward_id"), {}), cases.get(r["id"]), calls.get(r["id"], []), data.round if data else None, now)
        for r in residents
    ]
    rows.sort(key=lambda r: (-TIER_ORDER.get(r["tier"], -1), -r["risk_score"], -r["age"], r["id"]))
    return DistrictLoad(register=register, data=data, alert=alert, rows=rows)


def scenario_reason(alert: dict) -> str | None:
    """One plain sentence on why a scenario alert was raised, from what it recorded. None for an alert that is not one."""
    raw = alert.get("raw") or {}
    if alert.get("source") != "scenario" or raw.get("peak_c") is None or raw.get("peak_hour") is None or raw.get("threshold_c") is None:
        return None
    # The numbers are known, so the sentence says which it is: at the threshold, or over it.
    relation = "reaches" if raw["peak_c"] == raw["threshold_c"] else "is above"
    return (
        f"Forecast peak {raw['peak_c']:g} °C at {int(raw['peak_hour']):02d}:00 {relation} the "
        f"{_level_word(raw.get('severity') or alert.get('severity'))} threshold ({raw['threshold_c']:g} °C)."
    )


def _level_word(severity: str | None) -> str:
    """The warning level as the screens write it: "Red", "Amber" (the "orange" level), "Yellow"."""
    return "Amber" if severity == "orange" else (severity or "").capitalize()


def warning_of(load: DistrictLoad) -> dict | None:
    if load.data is None:
        return None
    current, alert = load.data.round, load.alert or {}
    raw = alert.get("raw") or {}
    from_state_rule = alert.get("source") == ALERT_SOURCE and bool(raw.get("rule"))
    return {
        "scenario": alert.get("source") == "scenario",
        "reason": reason_text(raw, load.register["district"]["name"]) if from_state_rule else scenario_reason(alert),
        "threshold_c": (alert.get("raw") or {}).get("threshold_c") if alert.get("source") == "scenario" else None,
        "raised_at": alert.get("received_at"),
        "hazard": current.get("hazard"),
        "severity": alert.get("severity"),
        "source": alert.get("source"),
        "peak_temp_c": current.get("peak_temp_c"),
        "peak_hour_local": current.get("peak_hour_local"),
        "opened_at": current.get("opened_at"),
    }


def figures(rows: list[dict]) -> dict:
    """The round's figures for these rows. Every one is a count of rows, or a median over them."""
    needing = [r for r in rows if r["tier"] in ("red", "amber")]
    minutes = [
        (r["visited_at"] - r["opened_at"]).total_seconds() / 60
        for r in needing if r["case_status"] == "visited" and r["visited_at"] and r["opened_at"]
    ]
    return {
        "called": sum(r["call_status"] != "not_called" for r in rows),
        "reached": sum(r["call_status"] == "reached" for r in rows),
        "no_answer": sum(r["call_status"] == "no_answer" for r in rows),
        "trying": sum(r["call_status"] == "trying" for r in rows),
        "red": sum(r["tier"] == "red" for r in rows),
        "amber": sum(r["tier"] == "amber" for r in rows),
        "green": sum(r["tier"] == "green" for r in rows),
        "to_visit": sum(r["case_status"] == "open" for r in needing),
        "visited": sum(r["case_status"] == "visited" for r in needing),
        "red_open": sum(r["tier"] == "red" and r["case_status"] == "open" for r in rows),
        "median_minutes_to_visit": median(minutes) if minutes else None,
    }


def register_profile(rows: list[dict]) -> dict:
    conditions = Counter(c for r in rows for c in r["conditions"])
    groups = [
        {"id": "high_risk", "label": f"High risk (score {HIGH_RISK_SCORE} or more)", "count": sum(r["high_risk"] for r in rows)},
        {"id": "lives_alone", "label": "Lives alone", "count": sum(r["lives_alone"] for r in rows)},
    ]
    groups += [
        {"id": condition, "label": f"Has a {CONDITION_WORDS.get(condition, condition)}", "count": count}
        for condition, count in sorted(conditions.items())
    ]
    return {
        "residents": len(rows),
        "age_bands": [{"band": band, "count": sum(r["age_band"] == band for r in rows)} for band in AGE_BANDS],
        "groups": groups,
    }


def problems(rows: list[dict]) -> list[dict]:
    counts = Counter(rule for r in rows for rule in r["reasons"])
    return [
        {"rule": rule, "label": REASON_LABELS.get(rule, rule), "count": count}
        for rule, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def open_load(rows: list[dict]) -> dict[str, int]:
    """How many open cases needing a visit each worker has."""
    return dict(Counter(r["worker_id"] for r in rows if r["case_status"] == "open" and r["tier"] != "green" and r["worker_id"]))


def _progress(data: RoundData, now: datetime) -> dict | None:
    """Cumulative residents reached, cases opened and visits done, every ten minutes from the
    round's opening to its clock. A visitor's own records are stamped with the real clock, which is
    later than the round's; they are counted at the last point, so it agrees with the headline figures."""
    start, until = data.round.get("opened_at"), data.round.get("as_of") or now
    if not start or until <= start or (until - start) > timedelta(minutes=PROGRESS_STEP_MINUTES * MAX_PROGRESS_STEPS):
        return None
    def stamp(record: dict, field: str) -> datetime:
        return min(record[field], until) if record.get("visitor_id") else record[field]

    reached_at: dict[str, datetime] = {}
    for call in data.calls:
        if call["status"] in REACHED_STATUSES and call.get("ended_at"):
            ended = stamp(call, "ended_at")
            first = reached_at.get(call["resident_id"])
            reached_at[call["resident_id"]] = min(first, ended) if first else ended
    series = {"minutes": [], "reached": [], "opened": [], "visited": []}
    moment = start
    while moment <= until:
        series["minutes"].append(int((moment - start).total_seconds() // 60))
        series["reached"].append(sum(at <= moment for at in reached_at.values()))
        series["opened"].append(sum(bool(c.get("opened_at")) and stamp(c, "opened_at") <= moment for c in data.cases))
        series["visited"].append(sum(bool(c.get("visited_at")) and stamp(c, "visited_at") <= moment for c in data.cases))
        moment += timedelta(minutes=PROGRESS_STEP_MINUTES)
    return series


def _attention(rows: list[dict]) -> list[dict]:
    """What needs an officer's decision: cases a worker has flagged, then red cases not yet
    visited, the longest wait first."""
    waiting = [r for r in rows if r["escalated"] or (r["tier"] == "red" and r["case_status"] == "open")]
    return sorted(waiting, key=lambda r: (not r["escalated"], -(r["waiting_minutes"] or 0), r["id"]))


def _district_card(load: DistrictLoad) -> dict:
    district, profile = load.register["district"], register_profile(load.rows)
    groups = {g["id"]: g["count"] for g in profile["groups"]}
    return {
        "id": district["id"],
        "name": district["name"],
        "state": district.get("state"),
        "wards": len(load.register["wards"]),
        "residents": profile["residents"],
        "high_risk": groups["high_risk"],
        "living_alone": groups["lives_alone"],
        "aged_75_plus": profile["age_bands"][3]["count"],
        "warning": warning_of(load),
        "round": figures(load.rows) if load.data else None,
    }


REGION_READERS = 8  # districts read at once for the region's cards


def region_view(
    store: Store, visitor_id: str | None, now: datetime,
    views: RoundCache | None = None, registers: RoundCache | None = None,
) -> dict:
    # Each district is read on its own (register, round, alert): together, the page waits for the slowest
    # district, not for all of them one after another.
    with ThreadPoolExecutor(max_workers=REGION_READERS) as pool:
        cards = list(pool.map(
            lambda d: _district_card(load_district(store, d["id"], visitor_id, now, views, registers)),
            store.query("districts"),
        ))
    cards.sort(key=lambda c: (c["warning"] is None, c["name"]))  # the warned first
    return {"districts": cards}


def _ward_cards(load: DistrictLoad) -> list[dict]:
    cards = []
    for ward_id in sorted(load.register["wards"]):
        ward = load.register["wards"][ward_id]
        rows = [r for r in load.rows if r["ward_id"] == ward_id]
        worker = load.register["workers"].get(ward.get("worker_id")) or {}
        cards.append({
            "id": ward_id,
            "name": ward["name"],
            "worker_id": ward.get("worker_id"),
            "worker_name": worker.get("name"),
            "residents": len(rows),
            "high_risk": sum(r["high_risk"] for r in rows),
            "round": figures(rows) if load.data else None,
        })
    return cards


CALL_PLAN_SIZE = 10


def _call_plan(store: Store, load: DistrictLoad) -> list[dict]:
    """Who the round will call first, in the order plan_round dispatches them (by risk, then age; a register without phone numbers is still planned, since replayed calls need none), for the page shown before the alert."""
    residents = [r for r in store.query("residents", district_id=load.register["district"]["id"]) if (r.get("consent") or {}).get("given") and not r.get("visitor_id")]
    residents.sort(key=lambda r: (-r.get("risk_score", 0), -r["age"], r["id"]))
    rows = {r["id"]: r for r in load.rows}
    return [
        {
            "id": r["id"], "name": r["name"], "name_hi": r.get("name_hi") or "", "age": r["age"],
            "ward_name": rows[r["id"]]["ward_name"] if r["id"] in rows else None,
            "risk_score": r.get("risk_score") or 0,
            "reasons": risk_factors(r),
        }
        for r in residents[:CALL_PLAN_SIZE]
    ]


def _stored_hours(store: Store, district_id: str, now: datetime, tz: ZoneInfo) -> list[dict] | None:
    """The hourly temperatures of the district's newest stored weather up to today (live fetch)."""
    from app.weather_view import newest_snapshot  # here: app.weather_view imports this module

    snap = newest_snapshot(store, district_id, now.astimezone(tz).date().isoformat())
    hours = [{"hour": h["hour"], "temp_c": h["temp_c"]} for h in (snap or {}).get("hours") or []
             if isinstance(h.get("hour"), int) and h.get("temp_c") is not None]
    return hours or None


def district_view(
    store: Store, district_id: str, visitor_id: str | None, now: datetime,
    views: RoundCache | None = None, registers: RoundCache | None = None,
) -> dict:
    load = load_district(store, district_id, visitor_id, now, views, registers)
    district = load.register["district"]
    settings = get_settings()
    demo = getattr(store, "dataset", "live") == "demo"
    # The heat scenario and its thresholds are the demo dataset's only. Live, a district's curve is
    # its round's, else the stored weather's newest snapshot up to today, else none.
    scenario = load_scenario().get(district["id"]) if demo and not district.get("register_only") else None
    before_round = load.data is None and scenario is not None
    forecast = (load.data.round.get("forecast_hours") or None) if load.data else None
    source = "round" if forecast else None
    if forecast is None and scenario is not None and load.data is None:
        forecast, source = scenario["hours"], "scenario"
    if forecast is None and not demo:
        forecast = _stored_hours(store, district["id"], now, ZoneInfo(settings.local_tz))
        source = "weather" if forecast else None
    return {
        "district": {"id": district["id"], "name": district["name"], "state": district.get("state")},
        "warning": warning_of(load),
        "as_of": (load.data.round.get("as_of") or now) if load.data else None,
        "register": register_profile(load.rows),
        "round": figures(load.rows) if load.data else None,
        "wards": _ward_cards(load),
        "problems": problems(load.rows),
        "progress": _progress(load.data, now) if load.data else None,
        "forecast": forecast,
        "forecast_source": source,
        "real_forecast": (load.data.round.get("real_forecast_hours") or None) if load.data else None,
        "thresholds": {"orange": settings.heat_orange_c, "red": settings.heat_red_c} if demo else None,
        "call_plan": _call_plan(store, load) if before_round else None,
        "attention": _attention(load.rows),
        "residents": load.rows,
    }


def ward_view(
    store: Store, ward_id: str, visitor_id: str | None, now: datetime,
    views: RoundCache | None = None, registers: RoundCache | None = None,
) -> dict:
    ward = store.get("wards", ward_id)
    if ward is None:
        raise KeyError(f"wards/{ward_id}")
    load = load_district(store, ward["district_id"], visitor_id, now, views, registers)
    rows = [r for r in load.rows if r["ward_id"] == ward_id]
    worker = load.register["workers"].get(ward.get("worker_id")) or {}
    centre = next((f for f in load.register["facilities"] if f["ward_id"] == ward_id and f["type"] == "health_centre"), None)
    return {
        "ward": {
            "id": ward_id,
            "name": ward["name"],
            "district_id": ward["district_id"],
            "district_name": load.register["district"]["name"],
            "worker_id": ward.get("worker_id"),
            "worker_name": worker.get("name"),
            "health_centre": {k: centre.get(k) for k in ("name", "lat", "lng")} if centre else None,
        },
        "warning": warning_of(load),
        "register": register_profile(rows),
        "round": figures(rows) if load.data else None,
        "problems": problems(rows),
        "residents": rows,
    }
