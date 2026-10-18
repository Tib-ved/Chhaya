"""Puts right what a crash, a lost callback or a refused task left behind, and closes rounds.

Run every few minutes. Everything it does is safe to do twice. It only ever finishes a call
through handle_call_outcome and only ever asks the dial endpoint (through the queue) to place
one, so the three-attempt limit, the allowlist and the calling hours hold as they do anywhere.
Demo rounds, and every visitor's own call, are left alone, except that a visitor's phone call
left unfinished is ended (which erases the number). A round older than ROUND_LIFE is
closed as it stands: nothing in it is queued, finished or repaired."""
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.cases import has_red_fact, upsert_case
from app.db import Store
from app.demo import DEMO_PREFIX
from app.dispatch import (
    FINAL_STATUSES, TaskQueue, clamp_to_calling_hours, eligible, handle_call_outcome,
)
from app.models import FAILED_STATUSES, MAX_ATTEMPTS, REACHED_STATUSES, VISITOR_USAGE

logger = logging.getLogger(__name__)
DIALING_STALE = timedelta(minutes=3)  # a ring and a connection take far less
TALKING_GRACE_SECONDS = 120  # beyond the call's own time limit
VISITOR_PHONE_STALE = timedelta(minutes=15)  # from created_at; such a call lasts minutes (spec 5.4)
QUEUED_OVERDUE = timedelta(minutes=10)  # a task that has not fired by now is not going to
ROUND_LIFE = timedelta(hours=36)  # from when a round opened; after that nobody in it is rung
COUNTS = ("stale_dialing", "stale_in_progress", "requeued", "repaired", "completed", "expired", "errors")


def _shape(case: dict | None) -> tuple | None:
    return None if case is None else (case["tier"], tuple(case["reasons"]), case["status"])


def _sweep_round(
    store: Store, queue: TaskQueue, settings, current: dict, now: datetime, tz: ZoneInfo, can_dial: bool
) -> Counter:
    round_id = current["id"]
    hours = (settings.call_start_hour, settings.call_end_hour)
    allowlist = {number.strip() for number in settings.dial_allowlist.split(",") if number.strip()}
    talking_limit = timedelta(seconds=settings.max_call_seconds + TALKING_GRACE_SECONDS)
    within_hours = clamp_to_calling_hours(now, tz, *hours) == now
    calls = [call for call in store.query("calls", round_id=round_id) if not call.get("visitor_id")]
    residents = {r["id"]: r for r in store.query("residents", district_id=current["district_id"])}
    cases = {c["resident_id"]: c for c in store.query("cases", round_id=round_id)}
    done: Counter = Counter()
    more_to_come = False  # whether anything can still happen in this round

    def finish(call: dict, status: str, moved_on_from: str | None = None, stamp: str | None = None) -> bool:
        """Finish a call. With `moved_on_from`, only if it is still in that status with that stamp
        (dialing_at or started_at): it may have been answered since the pass began."""
        if moved_on_from is not None:
            fresh = store.get("calls", call["id"])
            if fresh is None or fresh["status"] != moved_on_from or fresh.get(stamp) != call.get(stamp):
                return False
        handle_call_outcome(store, queue, call["id"], status, now, tz, *hours)
        return True

    for call in calls:
        try:
            status = call["status"]
            if status == "dialing":
                more_to_come = True
                since = call.get("dialing_at")
                if since is None or now - since > DIALING_STALE:
                    # never back to queued: the call may in fact have been placed
                    if finish(call, "failed", "dialing", "dialing_at"):
                        done["stale_dialing"] += 1
            elif status == "in_progress":
                more_to_come = True
                since = call.get("started_at")
                if since is None or now - since > talking_limit:
                    if finish(call, "dropped", "in_progress", "started_at"):
                        done["stale_in_progress"] += 1
            elif status == "queued":
                resident = residents.get(call["resident_id"])
                if resident is None or not eligible(resident) or resident.get("phone") not in allowlist:
                    continue  # will never be dialled: it does not keep the round open
                more_to_come = True
                # Only a call with a due time was planned by this code; one without is left alone.
                due = call.get("not_before")
                # With no way to place a call the dial endpoint answers 503 and the queue
                # would try each task five times for nothing: the call waits, and keeps the
                # round open until the round expires.
                if can_dial and within_hours and due is not None and now - due > QUEUED_OVERDUE:
                    store.update("calls", call["id"], {"not_before": now})  # first, so it is not queued again next time
                    queue.enqueue(call["id"], now)
                    done["requeued"] += 1
        except Exception:
            logger.exception("sweep: could not handle call %s in round %s", call.get("id"), round_id)
            done["errors"] += 1

    by_resident: dict[str, list[dict]] = defaultdict(list)
    for call in calls:
        if "resident_id" in call:
            by_resident[call["resident_id"]].append(call)
    for resident_id, own in by_resident.items():
        try:
            last = max(own, key=lambda call: call["attempt"])
            if last["status"] not in FINAL_STATUSES:
                continue
            red_fact = any(has_red_fact(call) for call in own)
            talked = red_fact or any(call["status"] in REACHED_STATUSES for call in own)
            if last["status"] in FAILED_STATUSES and last["attempt"] < MAX_ATTEMPTS:
                # The retry was never created: the process died after the status was stored.
                finish(last, last["status"])
                done["repaired"] += 1
                more_to_come = True
            case = cases.get(resident_id)
            owed = talked or (last["status"] in FAILED_STATUSES and last["attempt"] >= MAX_ATTEMPTS)
            if (owed and case is None) or (case is not None and red_fact and case["tier"] != "red"):
                after = upsert_case(store, round_id, resident_id, now)
                # Only a case that really changed is a repair; otherwise the next pass would find
                # the same thing again and the round could never close.
                if after is not None and _shape(after) != _shape(case):
                    done["repaired"] += 1
        except Exception:
            logger.exception("sweep: could not repair resident %s in round %s", resident_id, round_id)
            done["errors"] += 1

    if calls and not more_to_come and not any(done.values()):
        store.update("rounds", round_id, {"status": "complete", "completed_at": now})
        done["completed"] += 1
    return done


def _expired(current: dict, now: datetime) -> bool:
    """Whether the round's working life is over. A round with no opening time is one too old
    to have one."""
    opened = current.get("opened_at") or current.get("started_at")
    return opened is None or now - opened > ROUND_LIFE


def _end_stale_visitor_phone_calls(
    store: Store, queue: TaskQueue, settings, now: datetime, tz: ZoneInfo
) -> Counter:
    """End every visitor phone call left unfinished, whatever its round (demo rounds too).
    Ending it through handle_call_outcome also erases the visitor's number. The calls are found
    through the visitor ledger (today's and yesterday's phone entries), never by a call's channel:
    a released browser call may carry channel "phone", and resident calls are not read at all."""
    from app.visitor import _day

    done: Counter = Counter()
    days = sorted({_day(now, tz), _day(now - timedelta(days=1), tz)})
    entries = [e for day in days for e in store.query(VISITOR_USAGE, day=day) if e.get("channel") == "phone"]
    for entry in entries:
        call = store.get("calls", entry["id"])
        if call is None or not call.get("visitor_id") or call["status"] in FINAL_STATUSES:
            continue
        created = call.get("created_at")
        if created is not None and now - created <= VISITOR_PHONE_STALE:
            continue
        try:
            handle_call_outcome(
                store, queue, call["id"], "failed", now, tz, settings.call_start_hour, settings.call_end_hour
            )
            done["stale_dialing"] += 1
        except Exception:
            logger.exception("sweep: could not end visitor phone call %s", call.get("id"))
            done["errors"] += 1
    return done


def sweep(
    store: Store, queue: TaskQueue, settings, now: datetime, tz: ZoneInfo, can_dial: bool = True
) -> dict:
    """Sweep every round that is being called. Returns what was done, as counts.
    `can_dial` is whether a phone call can be placed at all; without it nothing is re-queued."""
    totals: Counter = Counter()
    rounds = [r for r in store.query("rounds", status="calling") if not r["id"].startswith(DEMO_PREFIX)]
    for current in sorted(rounds, key=lambda r: r["id"]):
        try:
            if _expired(current, now):
                # Closed as it stands. Its queued calls are never rung: the dial endpoint
                # refuses a call whose round is complete.
                store.update("rounds", current["id"], {"status": "complete", "completed_at": now})
                totals["expired"] += 1
                continue
            totals += _sweep_round(store, queue, settings, current, now, tz, can_dial)
        except Exception:
            logger.exception("could not sweep round %s", current.get("id"))
            totals["errors"] += 1
    totals += _end_stale_visitor_phone_calls(store, queue, settings, now, tz)
    return {"rounds": len(rounds), **{name: totals[name] for name in COUNTS}}
