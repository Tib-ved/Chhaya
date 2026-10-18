"""A scripted round for tests: thirty Churu residents whose calls, cases and visits are written out in tests/demo_script.py. The app never makes one; tests use it to check the screens' arithmetic on a full round."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.case_actions import escalate
from app.cases import mark_visited
from app.db import Store
from app.demo import DEMO_COLLECTION, DEMO_DOC, DEMO_PREFIX, pinned
from app.dispatch import RecordingQueue, call_id_for, plan_round
from app.models import Alert, Round
from app.simulate import apply_outcome
from tests.demo_script import SCRIPT, answers_for
from tests.demo_transcript import scripted_transcript

OPENS_AT = (8, 2)  # local hour and minute at which the scripted round opens
SETTLE_HOURS = 3  # the last scripted visit is under three hours after the round opens
PEAK_TEMP_C, PEAK_HOUR_LOCAL = 47.0, 15
AS_OF_MINUTES = 60 * SETTLE_HOURS  # the round's fixed clock: every scripted event is before it
FORECAST_HOURS = [
    {"hour": hour, "temp_c": float(temp)}
    for hour, temp in zip(range(6, 21), (31, 33, 36, 39, 41, 43, 45, 46, 47, 47, 46, 44, 41, 38, 36))
]
ANY_HOUR = {"start_hour": 0, "end_hour": 24}  # scripted calls are written, not placed


def round_opening(now: datetime, tz: ZoneInfo) -> datetime:
    """The most recent 08:02 local that is far enough back for the whole script to be in the past."""
    local = now.astimezone(tz)
    opening = local.replace(hour=OPENS_AT[0], minute=OPENS_AT[1], second=0, microsecond=0)
    if local < opening + timedelta(hours=SETTLE_HOURS):
        opening -= timedelta(days=1)
    return opening.astimezone(timezone.utc)


def _play_script(store: Store, round_id: str, district_id: str, opened: datetime, tz: ZoneInfo) -> None:
    queue = RecordingQueue()  # thrown away: nothing built here is ever dialled
    in_call_order = plan_round(store, queue, round_id, opened, tz, **ANY_HOUR)
    calls = {call["id"]: call for call in store.query("calls", round_id=round_id)}
    residents = {r["id"]: r for r in store.query("residents", district_id=district_id)}
    for position, call_id in enumerate(in_call_order):
        resident = residents[calls[call_id]["resident_id"]]
        line = SCRIPT.get(resident["id"], {})
        rang_at = opened + timedelta(minutes=2 * position)
        if "failures" in line:
            for attempt, (status, minutes_later) in enumerate(line["failures"], start=1):
                apply_outcome(
                    store, queue, call_id_for(round_id, resident["id"], attempt), status,
                    rang_at + timedelta(minutes=minutes_later), tz, **ANY_HOUR,
                )
            continue
        answers = answers_for(resident, line)
        respondent = line.get("respondent", "self")
        stored = apply_outcome(
            store, queue, call_id, line.get("status", "completed"), rang_at + timedelta(minutes=2), tz,
            answers=answers, quotes=line.get("quotes"),
            emergency_reason=line.get("emergency"), respondent=respondent,
            **ANY_HOUR,
        )
        if stored in ("completed", "dropped"):
            spoken = scripted_transcript(
                resident, answers, line.get("quotes") or {}, bool(line.get("emergency")), respondent, rang_at,
            )
            if spoken:
                store.update("calls", call_id, {"transcript": spoken})
        case_id = f"{round_id}_{resident['id']}"
        if "note" in line and store.get("cases", case_id) is not None:
            store.update("cases", case_id, {"note": line["note"]})
        if "visit" in line:
            outcome, minutes_after_opening = line["visit"]
            mark_visited(store, case_id, outcome, opened + timedelta(minutes=minutes_after_opening))
        if "escalate" in line:
            reason, minutes_after_opening = line["escalate"]
            escalate(store, case_id, reason, "", opened + timedelta(minutes=minutes_after_opening))


def _delete_round(store: Store, round_id: str) -> None:
    """Remove a demo round with its calls and cases, and the temporary residents of the
    visitors who called in it. Refuses any round that is not a demo round."""
    if not round_id.startswith(DEMO_PREFIX):
        return
    for call in store.query("calls", round_id=round_id):
        if call.get("visitor_id"):
            store.delete("residents", call["resident_id"])
        store.delete("calls", call["id"])
    for case in store.query("cases", round_id=round_id):
        store.delete("cases", case["id"])
    old = store.get("rounds", round_id)
    if old is not None:
        store.delete("alerts", old["alert_id"])
        store.delete("rounds", round_id)


def scripted_round(store: Store, district_id: str, now: datetime, tz: ZoneInfo) -> str:
    """Build a fresh scripted round, point demo/current at it, and remove the scripted round it
    replaces together with every visitor's data. Returns the round id.

    Raises KeyError if the district has not been seeded."""
    if store.get("districts", district_id) is None:
        raise KeyError(f"districts/{district_id}")
    opened = round_opening(now, tz)
    round_id = f"{DEMO_PREFIX}{district_id}_heat_{now.astimezone(timezone.utc):%Y%m%dT%H%M%S}"
    alert = Alert(
        id=f"alert_{round_id}", source="weather", hazard="heat", severity="red",
        district_id=district_id, valid_from=opened, valid_to=opened + timedelta(hours=24),
        raw={"note": "scripted demo round", "forecast_max_c": PEAK_TEMP_C}, received_at=opened,
    )
    new_round = Round(
        id=round_id, alert_id=alert.id, district_id=district_id, hazard="heat",
        opened_at=opened, peak_temp_c=PEAK_TEMP_C, peak_hour_local=PEAK_HOUR_LOCAL,
        as_of=opened + timedelta(minutes=AS_OF_MINUTES), forecast_hours=FORECAST_HOURS,
    )
    # False means a build in this same second already made this round: leave it alone.
    if store.create("rounds", round_id, new_round.model_dump()):
        store.set("alerts", alert.id, alert.model_dump())
        _play_script(store, round_id, district_id, opened, tz)
        store.set(DEMO_COLLECTION, DEMO_DOC, {
            "id": DEMO_DOC, "round_id": round_id, "rounds": {district_id: round_id}, "built_at": now,
        })
        # The one this replaces, and any left behind by a build that was cut short.
        for old in store.query("rounds", district_id=district_id):
            if old["id"].startswith(DEMO_PREFIX) and old["id"] != round_id:
                _delete_round(store, old["id"])
    return round_id
