"""The recorded AI-resident conversations, replayed into each day's rounds.

The library (seed/fixtures/ai_calls.json, written by scripts/record_ai_calls.py) holds
conversations between Chhaya's agent and an AI-played resident. When a round is opened, a plan
is drawn for it: twenty to thirty of the district's residents, in the order a real round would
ring them, each matched to a recorded conversation and given a time in the three hours from
08:00, or from ten minutes after the round opened if that is later (never past 20:00). A few of
the red and amber ones also get a simulated visit later in the day.

The sweep then releases what has fallen due: the call is stored as the conversation went,
through the same path as a real call, and the visit is marked on the case. Nothing is written
with a time later than now, nothing is released twice, and nothing replayed is ever dialled."""
import json
import logging
import random
import re
from collections import Counter
from functools import lru_cache
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.cases import VisitRefused, mark_visited
from app.config import get_settings
from app.db import Store
from app.demo import pinned_rounds
from app.dispatch import FINAL_STATUSES, RETRY_DELAYS_MIN, RecordingQueue, TaskQueue, call_id_for
from app.models import FAILED_STATUSES, MAX_ATTEMPTS, Call
from app.simulate import CallAlreadyFinished, apply_outcome
from app.triage import ALLOWED_VALUES

logger = logging.getLogger(__name__)

LIBRARY = Path(__file__).resolve().parent.parent / "seed" / "fixtures" / "ai_calls.json"
PERSONAS = Path(__file__).resolve().parent.parent / "seed" / "fixtures" / "ai_personas.json"
# scripts/record_ai_calls.py records every conversation for a resident of this district's first
# ward, with its facilities and its scenario peak; a replayed call is given the resident's own.
RECORDING_DISTRICT = ("churu", "Churu", "Rajasthan")
# The seed's Rajasthan first names (seed/regions.py), by gender: the only districts with rounds.
GENDER_OF_FIRST_NAME = {
    **dict.fromkeys(("Sunita", "Kamla", "Shanta", "Lata", "Pushpa", "Meena", "Savitri", "Usha", "Sarla",
                     "Indu", "Nirmala", "Rekha", "Geeta", "Kusum"), "f"),
    **dict.fromkeys(("Ramesh", "Mohan", "Vijay", "Suresh", "Anil", "Prakash", "Ganesh", "Dinesh", "Mahesh",
                     "Ashok", "Kishor", "Vimal", "Hari", "Balram", "Narayan", "Jagdish"), "m"),
}
DEVANAGARI = re.compile(r"[\u0900-\u097F]")
LATIN = re.compile(r"[A-Za-z]")
DEVANAGARI_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")
FIRST_HOUR, LAST_HOUR = 8, 20  # replayed calls start no earlier than 08:00 and end by 20:00
AFTER_OPENING_MIN = 10  # nor sooner than this after the round opened
SPREAD_HOURS = 3  # and are spread over this long from the first
SAMPLE_MIN, SAMPLE_EXTRA = 20, 10  # twenty to thirty residents a day
JITTER_SHARE = 0.6  # how far into its slot a call may move, so the times are not evenly spaced
FREE_SHARE = 0.1  # about one resident in ten is given a call nobody answers, where the library has one
VISIT_EVERY = 5  # one red or amber conversation in five gets a simulated visit
VISIT_AFTER_MIN = (60, 150)
MAX_TRIES = 3  # an item whose release fails this many times is given up
CALL_MINUTES = 2  # how long after it starts a replayed call ends
SECONDS_PER_LINE = 6
ANY_HOUR = {"start_hour": 0, "end_hour": 24}  # replayed calls are written, not placed
# When each attempt of a call nobody answers is made: the first, then after each retry delay.
ATTEMPT_MINUTES = [0]
for _attempt in range(1, MAX_ATTEMPTS):
    ATTEMPT_MINUTES.append(ATTEMPT_MINUTES[-1] + RETRY_DELAYS_MIN[_attempt])


def load_library() -> dict:
    """The recorded conversations. A missing or unreadable file reads as an empty library."""
    try:
        library = json.loads(LIBRARY.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("the AI-resident library %s is missing: no recorded calls will be replayed", LIBRARY)
        return {"conversations": []}
    except (OSError, ValueError):
        logger.warning("the AI-resident library at %s could not be read", LIBRARY)
        return {"conversations": []}
    if not isinstance(library, dict) or not isinstance(library.get("conversations"), list):
        logger.warning("the AI-resident library at %s has no list of conversations", LIBRARY)
        return {"conversations": []}
    return library


def _conversations(library: dict) -> list[dict]:
    return [c for c in library.get("conversations") or [] if isinstance(c, dict) and c.get("id")]


def _attempts(entry: dict) -> list[tuple[str, int]] | None:
    """The attempts of a conversation in which nobody was reached, as (status, minutes after
    the first), exactly MAX_ATTEMPTS of them; None for a conversation someone answered."""
    failures = entry.get("failures")
    if not failures:
        if entry.get("status") not in FAILED_STATUSES:
            return None
        failures = [[entry["status"], minutes] for minutes in ATTEMPT_MINUTES]
    attempts = [(str(status), int(minutes)) for status, minutes in failures][:MAX_ATTEMPTS]
    while len(attempts) < MAX_ATTEMPTS:
        attempts.append((attempts[-1][0], max(ATTEMPT_MINUTES[len(attempts)], attempts[-1][1] + 1)))
    return attempts


def _distance(resident: dict, profile: dict | None) -> int:
    profile = profile or {}
    return sum((
        bool(resident.get("lives_alone")) != bool(profile.get("lives_alone")),
        bool(resident.get("conditions")) != bool(profile.get("conditions")),
        bool(resident.get("outdoor_worker")) != bool(profile.get("outdoor_worker")),
    ))


@lru_cache(maxsize=1)
def persona_genders() -> dict[str, str]:
    """persona id -> "f" or "m", from the personas the library was recorded with. {} if unreadable."""
    try:
        personas = json.loads(PERSONAS.read_text(encoding="utf-8")).get("personas") or []
    except (OSError, ValueError, AttributeError):
        logger.warning("the AI-resident personas at %s could not be read", PERSONAS)
        return {}
    return {p["id"]: p["gender"] for p in personas if isinstance(p, dict) and p.get("id") and p.get("gender")}


def resident_gender(resident: dict) -> str | None:
    """"f" or "m" from the resident's first name; None for a name not in the seed's lists."""
    return GENDER_OF_FIRST_NAME.get((resident.get("name") or "").split(" ")[0])


def _fits_conditions(resident: dict, entry: dict) -> bool:
    """Whether a conversation can be replayed for the resident without triage finding a question
    missing or extra: a completed, non-emergency conversation asked about medicine exactly when
    the resident has conditions (else A12, call ended early). An emergency or a call cut short
    fits anyone: neither asks every question."""
    if entry.get("status") != "completed" or entry.get("emergency"):
        return True
    return ("medicine" in (entry.get("answers") or {})) == bool(resident.get("conditions"))


def _match(
    resident: dict, spoken: list[dict], free: list[dict], used: Counter, rng: random.Random,
    misfits: list | None = None,
) -> dict:
    """The conversation a resident is given: one whose medicine question fits the resident's
    conditions (a hard requirement, while the library has one), then their own language, then a
    persona of their gender, then the closest profile, then the one used least so far today.
    A resident for whom no conversation fits is added to `misfits` and matched as before."""
    if free and (not spoken or rng.random() < FREE_SHARE):
        return rng.choice(free)
    fitting = [c for c in spoken if _fits_conditions(resident, c)]
    if not fitting and misfits is not None:
        misfits.append(resident.get("id"))
    fitting = fitting or spoken
    pool = [c for c in fitting if c.get("language") == resident.get("language")] or fitting
    gender = resident_gender(resident)
    if gender:
        genders = persona_genders()
        pool = [c for c in pool if genders.get(c.get("persona_id")) == gender] or pool
    best = min(_distance(resident, c.get("profile")) for c in pool)
    closest = [c for c in pool if _distance(resident, c.get("profile")) == best]
    fewest = min(used[c["id"]] for c in closest)
    return rng.choice([c for c in closest if used[c["id"]] == fewest])


def plan_replay(store: Store, round_id: str, library: dict, day: date, tz: ZoneInfo | None = None) -> list[dict]:
    """Draw the day's replay for a round and store it on the round as `replay`. A round that
    already has one keeps it. Returns the plan; [] (and nothing stored) for an empty library.

    Raises KeyError for an unknown round."""
    current = store.get("rounds", round_id)
    if current is None:
        raise KeyError(f"rounds/{round_id}")
    if current.get("replay"):
        return current["replay"]
    conversations = _conversations(library)
    if not conversations:
        return []
    tz = tz or ZoneInfo(get_settings().local_tz)
    district_id = current["district_id"]
    rng = random.Random(f"{district_id}:{day}")
    free = [c for c in conversations if _attempts(c)]
    spoken = [c for c in conversations if not _attempts(c) and c.get("language")]
    # As plan_round orders them. A visitor's temporary resident is never part of a round.
    residents = [r for r in store.query("residents", district_id=district_id) if not r.get("visitor_id")]
    residents.sort(key=lambda r: (-r.get("risk_score", 0), -r["age"], r["id"]))
    chosen = residents[: min(SAMPLE_MIN + rng.randint(0, SAMPLE_EXTRA), len(residents))]
    if not chosen:
        return []

    start = datetime.combine(day, time(FIRST_HOUR), tzinfo=tz)
    opened = _as_datetime(current.get("opened_at"))
    if opened is not None:
        start = max(start, opened + timedelta(minutes=AFTER_OPENING_MIN))
    window = min(timedelta(hours=SPREAD_HOURS), datetime.combine(day, time(LAST_HOUR), tzinfo=tz) - start)
    if window <= timedelta(0):
        return []  # opened too late in the day for any replayed call
    slot = window.total_seconds() / len(chosen)
    visit_turn = rng.randrange(VISIT_EVERY)
    worrying = 0
    used: Counter = Counter()
    misfits: list = []
    plan = []
    for position, resident in enumerate(chosen):
        entry = _match(resident, spoken, free, used, rng, misfits)
        used[entry["id"]] += 1
        call_at = start + timedelta(seconds=int(slot * position + rng.uniform(0, slot * JITTER_SHARE)))
        visit = None
        if entry.get("tier") in ("red", "amber") and not _attempts(entry):
            if worrying % VISIT_EVERY == visit_turn:
                outcome = "hospital" if entry.get("emergency") else rng.choice(["supplies_given", "advised_ok"])
                visit_at = call_at + timedelta(minutes=rng.randint(*VISIT_AFTER_MIN))
                visit = {"at": visit_at.isoformat(), "outcome": outcome}
            worrying += 1
        plan.append({
            "resident_id": resident["id"], "conversation_id": entry["id"], "call_at": call_at.isoformat(),
            "visit": visit, "released": False, "visit_released": False,
        })
    if misfits:
        logger.warning(
            "replay: no conversation in the library fits the conditions of %d resident(s) of round %s (%s); "
            "they were given the closest one", len(misfits), round_id, ", ".join(map(str, misfits)),
        )
    fresh = store.get("rounds", round_id) or {}
    if fresh.get("replay"):
        return fresh["replay"]  # drawn meanwhile by another run
    store.update("rounds", round_id, {"replay": plan})
    return plan


def _as_datetime(value) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    return value if isinstance(value, datetime) else None


@lru_cache(maxsize=1)
def recording_places() -> dict:
    """What the recordings say of the place, as the seed builds it for the recording ward: the
    ward's display name and number, its facility names by type, and the scenario's peak."""
    from app.heat_scenario import load_scenario  # here: only the replay needs them
    from seed.seed import _district

    district_id, name, state = RECORDING_DISTRICT
    register = _district(0, district_id, name, state, [0])
    ward = register["wards"][0]
    return {
        "ward": ward["name"], "number": ward["number"],
        "facilities": {f["type"]: f["name"] for f in register["facilities"] if f["ward_id"] == ward["id"]},
        "peak": max(h["temp_c"] for h in load_scenario()[district_id]["hours"]),
    }


DIGIT = r"[\d०-९]"
NOT_A_DURATION = "(?!\\s*(?:hours?|घंटे))"


def _number_pattern(numbers) -> re.Pattern:
    """Any of the numbers standing alone: not part of a longer number, nor a number of hours."""
    alternatives = "|".join(re.escape(n) for n in sorted(numbers, key=len, reverse=True))
    return re.compile(rf"(?<!{DIGIT})(?<!{DIGIT}[.])({alternatives})(?![.]?{DIGIT}){NOT_A_DURATION}")


def localiser(store: Store, resident: dict, current: dict | None):
    """A function that makes a recorded line the resident's: the recording ward's name and its
    facilities become the resident's ward and facilities, and the recording's peak temperature
    (exact, or rounded as the agent says it) becomes the round's. Identity where nothing differs."""
    try:
        recorded = recording_places()
    except Exception:
        logger.exception("replay: the recording ward could not be built")
        return lambda text: text
    pairs: dict[str, str] = {}
    ward = store.get("wards", resident["ward_id"]) if resident.get("ward_id") else None
    if ward:
        facilities: dict = {}
        for facility in sorted(store.query("facilities", ward_id=ward["id"]), key=lambda f: f["id"]):
            facilities.setdefault(facility.get("type"), facility.get("name"))
        for kind, recorded_name in recorded["facilities"].items():
            if facilities.get(kind):
                pairs[recorded_name] = facilities[kind]
        if ward.get("name"):
            pairs[recorded["ward"]] = ward["name"]
        if ward.get("number") is not None:
            word = "वार्ड"  # "ward" in Devanagari
            pairs[f"{word} {recorded['number']}"] = f"{word} {ward['number']}"
    pairs = {old: new for old, new in pairs.items() if old != new}
    names = re.compile(
        r"(?<!\w)(" + "|".join(re.escape(old) for old in sorted(pairs, key=len, reverse=True)) + r")(?!\w)"
    ) if pairs else None

    temps: dict[str, str] = {}
    peak = (current or {}).get("peak_temp_c")
    if isinstance(peak, (int, float)) and peak != recorded["peak"]:
        temps = {f"{recorded['peak']:g}": f"{peak:g}", str(round(recorded["peak"])): str(round(peak))}
        temps.update({old.translate(DEVANAGARI_DIGITS): new.translate(DEVANAGARI_DIGITS) for old, new in list(temps.items())})
    numbers = _number_pattern(temps) if temps else None

    def localise(text):
        if not isinstance(text, str):
            return text
        if names:
            text = names.sub(lambda m: pairs[m.group(1)], text)
        if numbers:
            text = numbers.sub(lambda m: temps[m.group(1)], text)
        return text

    return localise


def _mostly_devanagari(text: str) -> bool:
    text = text.replace("{name}", "")
    return len(DEVANAGARI.findall(text)) > len(LATIN.findall(text))


def _fill(text, name: str, name_hi: str = "", localise=None):
    """A recorded line made the resident's: the name token becomes their name (in Devanagari in a
    line mostly in Devanagari), and the place and the heat become theirs."""
    if not isinstance(text, str):
        return text
    if localise:
        text = localise(text)
    return text.replace("{name}", name_hi if name_hi and _mostly_devanagari(text) else name)


def _mark(store: Store, call_id: str, marks: dict) -> None:
    store.update("calls", call_id, marks)


def _end(store: Store, queue: TaskQueue, call_id: str, status: str, at: datetime, tz: ZoneInfo, **outcome) -> None:
    """End a replayed call. One another run ended first is left as that run stored it."""
    try:
        apply_outcome(store, queue, call_id, status, at, tz, **outcome, **ANY_HOUR)
    except CallAlreadyFinished as finished:
        logger.info("replay: call %s was already ended as %s by another run", call_id, finished)


def _release_call(
    store: Store, round_id: str, item: dict, entry: dict | None, resident: dict | None,
    recorded_at, now: datetime, tz: ZoneInfo, current: dict | None = None,
) -> str:
    """Store a planned call if it is due. Returns "wait", "done", "already" (stored by another run)
    or "skipped"."""
    if entry is None or resident is None:
        return "skipped"  # the conversation is no longer in the library, or the resident is gone
    call_at = datetime.fromisoformat(item["call_at"])
    attempts = _attempts(entry)
    # A call nobody answered is released after its last attempt, so that no retry waits in the
    # round to be dialled and no attempt is stamped later than now.
    if call_at + timedelta(minutes=attempts[-1][1] if attempts else 0) > now:
        return "wait"
    resident_id = resident["id"]
    marks = {"simulated": "ai_resident", "conversation_id": entry["id"], "channel": "ai_resident"}
    if recorded_at:
        marks["recorded_at"] = recorded_at
    first_id = call_id_for(round_id, resident_id, 1)
    call = Call(
        id=first_id, round_id=round_id, resident_id=resident_id, created_at=call_at, not_before=call_at,
        channel="ai_resident", simulated="ai_resident", conversation_id=entry["id"],
    )
    mine = store.create("calls", first_id, call.model_dump())
    if not mine:
        existing = store.get("calls", first_id) or {}
        if existing.get("conversation_id") != entry["id"]:
            return "skipped"  # the round was called for real: its own call stands
    # Stored by another run (or one cut short): finished here if need be, but counted there.
    released = "done" if mine else "already"
    _mark(store, first_id, marks)
    nowhere = RecordingQueue()  # thrown away: every retry made here is ended here

    if attempts:
        planned_at = call_at
        for attempt, (status, minutes) in enumerate(attempts, start=1):
            call_id = call_id_for(round_id, resident_id, attempt)
            current = store.get("calls", call_id)
            if current is None:
                break  # the attempt before ended the resident's calls
            ended_at = call_at + timedelta(minutes=minutes)
            if current["status"] not in FINAL_STATUSES:
                _mark(store, call_id, {**marks, "created_at": current.get("created_at") or planned_at})
                _end(store, nowhere, call_id, status, ended_at, tz)
            planned_at = ended_at
        return released

    ended_at = min(call_at + timedelta(minutes=CALL_MINUTES), now)
    name, name_hi = resident.get("name") or "", resident.get("name_hi") or ""
    localise = localiser(store, resident, current)
    fill = lambda text: _fill(text, name, name_hi, localise)  # noqa: E731
    lines = [line for line in entry.get("transcript") or [] if isinstance(line, dict) and line.get("text")]
    span = (ended_at - call_at).total_seconds()
    step = min(SECONDS_PER_LINE, span / len(lines)) if lines else 0
    transcript = [
        {"role": line.get("role"), "text": fill(line["text"]), "at": call_at + timedelta(seconds=step * n)}
        for n, line in enumerate(lines)
    ]
    _mark(store, first_id, {
        "transcript": transcript, "started_at": call_at, "duration_s": int(span),
        "language_used": entry.get("language"),
    })
    if store.get("calls", first_id)["status"] not in FINAL_STATUSES:
        answers = {
            q: v for q, v in (entry.get("answers") or {}).items() if q in ALLOWED_VALUES and isinstance(v, str)
        }
        quotes = {
            q: fill(t) for q, t in (entry.get("quotes") or {}).items() if q in ALLOWED_VALUES and isinstance(t, str)
        }
        _end(
            store, nowhere, first_id, entry.get("status") or "completed", ended_at, tz,
            answers=answers, quotes=quotes, emergency_reason=fill(entry.get("emergency")) or None,
            respondent=entry.get("respondent") or "unknown",
        )
    # A recorded call with no sign of a person is stored as no_answer and makes a retry: end
    # those here too, so nothing replayed is left waiting to be dialled.
    for attempt in range(2, MAX_ATTEMPTS + 1):
        call_id = call_id_for(round_id, resident_id, attempt)
        current = store.get("calls", call_id)
        if current is None:
            break
        if current["status"] not in FINAL_STATUSES:
            # Its due time too: the retry was planned for later, but it ends now.
            _mark(store, call_id, {**marks, "created_at": ended_at, "not_before": ended_at})
            _end(store, nowhere, call_id, "no_answer", ended_at, tz)
    return released


def _release_visit(store: Store, round_id: str, item: dict, now: datetime) -> str:
    """Mark a planned visit if it is due. Returns "wait", "done" or "skipped"."""
    visit_at = datetime.fromisoformat(item["visit"]["at"])
    if visit_at > now:
        return "wait"
    case_id = f"{round_id}_{item['resident_id']}"
    case = store.get("cases", case_id)
    if case is None or case["status"] != "open" or case["tier"] == "green" or case.get("visitor_id"):
        return "skipped"  # no visit is owed, or a worker got there first
    try:
        mark_visited(store, case_id, item["visit"]["outcome"], visit_at)
    except VisitRefused:
        return "skipped"
    store.update("cases", case_id, {"simulated": "visit"})
    return "done"


def _call_due(item: dict, now: datetime) -> bool:
    return not item["released"] and datetime.fromisoformat(item["call_at"]) <= now


def _visit_due(item: dict, now: datetime) -> bool:
    visit = item.get("visit")
    return (
        bool(visit) and item["released"] and not item["visit_released"]
        and datetime.fromisoformat(visit["at"]) <= now
    )


def release_due(store: Store, queue: TaskQueue, now: datetime, tz: ZoneInfo) -> dict:
    """Release every planned call and visit of the day's rounds that is due by now. Safe to run
    at any time and as often as wanted. Returns how many calls and visits were released, and
    "errors" when some could not be.

    A round with nothing due costs one read. An item whose release fails MAX_TRIES times is
    marked failed and never tried again. `queue` is the dialling queue; it is never used: a
    replayed call is never dialled, and any retry its outcome makes is ended in the same pass."""
    counts = {"calls": 0, "visits": 0, "errors": 0}
    library = recorded_at = None
    for round_id in sorted(set(pinned_rounds(store).values())):
        current = store.get("rounds", round_id)
        plan = (current or {}).get("replay") or []
        due = [item for item in plan if _call_due(item, now) or _visit_due(item, now)]
        if not due:
            continue
        if library is None:
            loaded = load_library()
            library = {c["id"]: c for c in _conversations(loaded)}
            recorded_at = loaded.get("recorded_at")
        changed = False
        for item in due:
            try:
                if _call_due(item, now):
                    state = _release_call(
                        store, round_id, item, library.get(item["conversation_id"]),
                        store.get("residents", item["resident_id"]), recorded_at, now, tz, current,
                    )
                    if state != "wait":
                        item["released"] = changed = True
                        counts["calls"] += state == "done"
                if _visit_due(item, now):
                    state = _release_visit(store, round_id, item, now)
                    item["visit_released"] = changed = True
                    counts["visits"] += state == "done"
            except Exception:
                logger.exception("replay: could not release %s in round %s", item.get("resident_id"), round_id)
                counts["errors"] += 1
                item["tries"] = item.get("tries", 0) + 1
                changed = True
                if item["tries"] >= MAX_TRIES:
                    item.update(failed=True, released=True, visit_released=True)
        if changed:
            # Once for the round, at the end of the pass: a second runner at the same time
            # finds the calls already stored and releases nothing twice.
            store.update("rounds", round_id, {"replay": plan})
    if not counts["errors"]:
        del counts["errors"]
    return counts
