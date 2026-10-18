import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from app.db import Store
from app.cases import upsert_case
from app.models import FAILED_STATUSES, MAX_ATTEMPTS, REACHED_STATUSES, VISITOR_USAGE, Call

logger = logging.getLogger(__name__)
PHONE_RE = re.compile(r"\+[1-9]\d{7,14}")
FINAL_STATUSES = FAILED_STATUSES | REACHED_STATUSES
STARTED_STATUSES = {"dialing", "in_progress"}
# End outcomes the agent only chooses because of something a person said or did.
PERSON_OUTCOMES = {"completed", "declined", "wrong_person", "unable_to_understand", "emergency"}
RETRY_DELAYS_MIN = {1: 20, 2: 60}  # minutes to wait after failed attempt N
assert set(RETRY_DELAYS_MIN) == set(range(1, MAX_ATTEMPTS)), (
    "RETRY_DELAYS_MIN needs one delay for each attempt from 1 to MAX_ATTEMPTS - 1"
)


class TaskQueue(Protocol):
    def enqueue(self, call_id: str, run_at: datetime) -> None:
        """Ask for the call to be dialled at run_at. Never raises. A repeat may queue the call
        twice: the dial endpoint claims a call atomically, so only one of them places it."""


class RecordingQueue:
    """Keeps queued calls in memory. Used in tests and local development."""

    def __init__(self) -> None:
        self.items: list[tuple[str, datetime]] = []

    def enqueue(self, call_id: str, run_at: datetime) -> None:
        if all(queued_id != call_id for queued_id, _ in self.items):
            self.items.append((call_id, run_at))


def call_id_for(round_id: str, resident_id: str, attempt: int) -> str:
    return f"{round_id}_{resident_id}_{attempt}"


def eligible(resident: dict) -> bool:
    consent = resident.get("consent") or {}
    return bool(consent.get("given")) and bool(PHONE_RE.fullmatch(resident.get("phone") or ""))


def clamp_to_calling_hours(
    when: datetime, tz: ZoneInfo, start_hour: int = 8, end_hour: int = 20
) -> datetime:
    local = when.astimezone(tz)
    if local.hour < start_hour:
        local = local.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    elif local.hour >= end_hour:
        local = (local + timedelta(days=1)).replace(
            hour=start_hour, minute=0, second=0, microsecond=0
        )
    return local.astimezone(timezone.utc)


def plan_round(
    store: Store,
    queue: TaskQueue,
    round_id: str,
    now: datetime,
    tz: ZoneInfo,
    start_hour: int = 8,
    end_hour: int = 20,
) -> list[str]:
    current = store.get("rounds", round_id)
    if current is None:
        raise KeyError(f"rounds/{round_id}")
    if current["status"] == "complete":
        return []
    # A resident with a visitor_id is the temporary one behind a visitor's demo call: never dispatched.
    residents = [
        r
        for r in store.query("residents", district_id=current["district_id"])
        if eligible(r) and not r.get("visitor_id")
    ]
    residents.sort(key=lambda r: (-r.get("risk_score", 0), -r["age"], r["id"]))
    first_slot = clamp_to_calling_hours(now, tz, start_hour, end_hour)
    created: list[str] = []
    for position, resident in enumerate(residents):
        run_at = clamp_to_calling_hours(
            first_slot + timedelta(seconds=position), tz, start_hour, end_hour
        )
        call = Call(
            id=call_id_for(round_id, resident["id"], 1),
            round_id=round_id,
            resident_id=resident["id"],
            not_before=run_at,
        )
        newly_created = store.create("calls", call.id, call.model_dump())
        if newly_created or store.get("calls", call.id)["status"] == "queued":
            queue.enqueue(call.id, run_at)
        if newly_created:
            created.append(call.id)
    if current["status"] == "open":
        store.update("rounds", round_id, {"status": "calling", "started_at": now})
    return created


def person_was_reached(call: dict) -> bool:
    """Whether the call document shows that a person was on the line.

    Any one of: a known respondent, a recorded answer, a flagged emergency, or an end outcome
    that the agent chose because of something a person said or did. Tolerates missing keys and
    None values."""
    if call.get("respondent") in ("self", "family", "other"):
        return True
    if call.get("answers"):
        return True
    if (call.get("emergency") or {}).get("flagged"):
        return True
    return call.get("end_outcome") in PERSON_OUTCOMES


def handle_call_outcome(
    store: Store,
    queue: TaskQueue,
    call_id: str,
    status: str,
    now: datetime,
    tz: ZoneInfo,
    start_hour: int = 8,
    end_hour: int = 20,
) -> None:
    if status not in FINAL_STATUSES:
        # e.g. Twilio's hyphenated "no-answer": storing it would lose the resident
        raise ValueError(f"not a final call status: {status!r}")
    requested = status
    for writes_left in (2, 1, 0):
        call = store.get("calls", call_id)
        if call is None:
            raise KeyError(f"calls/{call_id}")
        if call["status"] in FINAL_STATUSES:
            # The first final status wins: a duplicate, stale or late callback never changes
            # what is stored. The step below still runs for the STORED status, so work lost
            # to a crash after the first write gets done (both branches are idempotent).
            status = call["status"]
            break
        if not writes_left:
            raise RuntimeError(f"the status of calls/{call_id} kept changing; no outcome was recorded")
        status = requested
        if status in REACHED_STATUSES and not person_was_reached(call):
            status = "no_answer"  # the stream connected but nobody was on the line
        # Written only if the status is still the one just read, so that of two finishers
        # exactly one writes. The loser reads the call again and follows the stored status.
        if store.transition("calls", call_id, "status", call["status"], {"status": status, "ended_at": now}):
            break

    if call.get("visitor_id"):
        # A visitor's own demo call (in the browser, or on their own phone) is never retried.
        # One whose agent never started is counted only as a failed start.
        if status == "failed":
            # Kept, marked: it uses up nothing of the visitor's hour or of the day, but someone
            # who makes calls fail on purpose is still counted, by network address.
            try:
                store.update(VISITOR_USAGE, call_id, {"failed": True, "started": False})
            except KeyError:
                pass  # no record to mark
        if call.get("channel") == "phone":
            # A visitor phone call, as recorded on the call (a released browser call goes back
            # to "browser"). Its number is kept only while the call lasts (visitor calls spec T7).
            try:
                store.update("residents", call["resident_id"], {"phone": ""})
            except KeyError:
                pass  # the resident has gone already
        upsert_case(store, call["round_id"], call["resident_id"], now)  # no case unless someone spoke
        return
    if status in FAILED_STATUSES and call["attempt"] < MAX_ATTEMPTS:
        retry_at = clamp_to_calling_hours(
            now + timedelta(minutes=RETRY_DELAYS_MIN[call["attempt"]]), tz, start_hour, end_hour
        )
        retry = Call(
            id=call_id_for(call["round_id"], call["resident_id"], call["attempt"] + 1),
            round_id=call["round_id"],
            resident_id=call["resident_id"],
            attempt=call["attempt"] + 1,
            not_before=retry_at,
        )
        newly_created = store.create("calls", retry.id, retry.model_dump())
        if newly_created or store.get("calls", retry.id)["status"] == "queued":
            # A repeated callback may queue the same retry again. That is safe: the dial
            # endpoint claims a call atomically.
            queue.enqueue(retry.id, retry_at)
        return

    upsert_case(store, call["round_id"], call["resident_id"], now)


def mark_call_started(
    store: Store, call_id: str, status: str, now: datetime, live_settings: dict | None = None
) -> None:
    """Record a non-final transition (dialing, in_progress). Never ends a call or opens a case.

    `live_settings`, when given, is stored with it: the voice settings the conversation runs with."""
    if status not in STARTED_STATUSES:
        raise ValueError(f"not a call-started status: {status!r}")
    for writes_left in (2, 1, 0):
        call = store.get("calls", call_id)
        if call is None:
            raise KeyError(f"calls/{call_id}")
        if call["status"] in FINAL_STATUSES:
            return  # a late progress callback never reopens a finished call
        if not writes_left:
            break
        fields: dict = {"status": status}
        if status == "in_progress" and not call.get("started_at"):
            fields["started_at"] = now
        if live_settings is not None:
            fields["live_settings"] = live_settings
        # Written only if the status is still the one just read: a final status stored in
        # between is never overwritten.
        if store.transition("calls", call_id, "status", call["status"], fields):
            if status == "in_progress" and call.get("visitor_id"):
                _count_visitor_call(store, call_id)
            return
    raise RuntimeError(f"the status of calls/{call_id} kept changing; it was not marked {status}")


def _count_visitor_call(store: Store, call_id: str) -> None:
    """Mark a visitor call's ledger record as one whose agent was started: only those count
    toward the day's calls. Best effort: the call goes on whatever happens here."""
    try:
        store.update(VISITOR_USAGE, call_id, {"started": True})
    except KeyError:
        logger.warning("visitor call %s has no usage record to mark as started", call_id)
    except Exception as error:
        logger.error("could not mark visitor call %s as started (%s)", call_id, type(error).__name__)
