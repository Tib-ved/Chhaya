"""The three views the screens read: round overview, a ward's cases, and the round report.

Each view loads a round's calls, cases, residents and wards once, then works in memory.
What every viewer is shown alike can be kept for a moment in a RoundCache; a viewer's own
records are read for that viewer alone, every time."""
import hashlib
import json
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Any

from app.cases import VISIT_OUTCOMES
from app.db import Store
from app.dispatch import eligible
from app.models import CONVERSATION_STATUSES, REACHED_STATUSES, WAITING_STATUSES
from app.risk import risk_factors
from app.triage import REASON_LABELS, TIER_ORDER

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)
UNASSIGNED_WARD = "Unassigned ward"


def current_round_id(store: Store) -> str | None:
    rounds = store.query("rounds")
    if not rounds:
        return None
    # Newest opened wins, so a round that is open but not yet dispatched is still current.
    return max(
        rounds, key=lambda r: (r.get("opened_at") or r.get("started_at") or _EPOCH, r["id"])
    )["id"]


def visible(doc: dict, visitor_id: str | None) -> bool:
    """Whether this viewer may see the record. A record made by a visitor's own call carries
    their id and is shown to nobody else; every other record is shown to everyone."""
    owner = doc.get("visitor_id")
    return not owner or owner == visitor_id


def _round(store: Store, round_id: str) -> dict:
    found = store.get("rounds", round_id)
    if found is None:
        raise KeyError(f"rounds/{round_id}")
    return found


@dataclass(frozen=True)
class RoundData:
    """One round as one viewer may see it, read from the store once."""

    round: dict
    calls: list[dict]
    cases: list[dict]
    residents: dict[str, dict]
    wards: dict[str, dict]

    def calls_by_resident(self) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for call in self.calls:
            grouped[call["resident_id"]].append(call)
        return grouped

    def latest_calls(self) -> dict[str, dict]:
        return {
            resident_id: max(calls, key=lambda c: c["attempt"])
            for resident_id, calls in self.calls_by_resident().items()
        }

    def reached(self) -> int:
        return len({c["resident_id"] for c in self.calls if c["status"] in REACHED_STATUSES})

    def unreachable(self) -> int:
        return sum(1 for c in self.cases if "A8" in c["reasons"])

    def targeted(self) -> int:
        """Residents with at least one call in the round: fixed at dispatch."""
        return len({c["resident_id"] for c in self.calls})

    def not_callable(self) -> int:
        """Residents who agreed to be called but whose phone number cannot be dialled."""
        return sum(
            1
            for r in self.residents.values()
            if not r.get("visitor_id") and (r.get("consent") or {}).get("given") and not eligible(r)
        )


def _own_call(calls: list[dict]) -> dict | None:
    """Of a viewer's own calls, the one the screens show: the most recent in which the agent
    was talking or had talked. A ticket nobody used and a call that reached nobody are not
    shown, or the round would count calls that are not happening."""
    spoken = [c for c in calls if c["status"] in CONVERSATION_STATUSES]
    return max(spoken, key=lambda c: (c.get("created_at") or _EPOCH, c["id"])) if spoken else None


class RoundCache:
    """What every viewer of a round is shown alike, kept for `seconds` so that a crowd of
    readers costs the store one read of the round and not one each. 0 keeps nothing.

    Only records that carry no visitor id are ever put here: a visitor's own records are read
    for that visitor on every request. Entries are keyed by round id, so a rebuilt demo round
    (a new id) is never answered from the old one, and storing an entry drops every expired
    one, so the cache holds the rounds read in the last `seconds`. What it returns is shared
    between requests and must not be changed. Safe to use from several threads: each part has its own
    lock, held while it is loaded, so readers of one part who arrive together wait for one load and do
    not each make it, while different parts load at the same time.
    """

    def __init__(self, seconds: float = 0, clock: Callable[[], float] = time.monotonic) -> None:
        self.seconds = seconds
        self._clock = clock
        self._lock = threading.Lock()  # guards the two dicts; never held during a load
        self._entries: dict[tuple[str, str], tuple[float, Any]] = {}
        self._loading: dict[tuple[str, str], threading.Lock] = {}

    def get(self, round_id: str, part: str, load: Callable[[], Any]) -> Any:
        """The named part of the round: the kept one while it is fresh, else `load()`, kept.
        An error from `load` is raised and nothing is kept."""
        if self.seconds <= 0:
            return load()
        key = (round_id, part)
        with self._lock:
            part_lock = self._loading.setdefault(key, threading.Lock())
        with part_lock:
            with self._lock:
                kept = self._entries.get(key)
                if kept is not None and self._clock() < kept[0]:
                    return kept[1]
            value = load()
            with self._lock:
                now = self._clock()
                self._entries = {k: entry for k, entry in self._entries.items() if entry[0] > now}
                self._entries[key] = (self._clock() + self.seconds, value)
                # A part's lock is dropped with its entry; one in use is kept until its entry expires.
                self._loading = {k: lock for k, lock in self._loading.items() if k in self._entries or lock.locked()}
            return value


_NO_CACHE = RoundCache(0)


def load_register(store: Store, district_id: str, registers: RoundCache | None = None) -> dict:
    """A district's register, without any visitor's record. Raises KeyError for an unknown district.
    Kept in `registers` (if given), since a register changes only on a reseed."""

    def load() -> dict:
        district = store.get("districts", district_id)
        if district is None:
            raise KeyError(f"districts/{district_id}")
        wards = {w["id"]: w for w in store.query("wards", district_id=district_id)}
        workers = {}
        for ward in wards.values():
            worker = store.get("workers", ward["worker_id"]) if ward.get("worker_id") else None
            if worker:
                workers[worker["id"]] = worker
        return {
            "district": district,
            "wards": wards,
            "workers": workers,
            "facilities": [f for ward_id in sorted(wards) for f in store.query("facilities", ward_id=ward_id)],
            "residents": [r for r in store.query("residents", district_id=district_id) if not r.get("visitor_id")],
        }

    return (registers or _NO_CACHE).get("register", district_id, load)


def _load_shared(store: Store, round_id: str, registers: RoundCache | None = None) -> RoundData:
    """The round as someone with no call of their own sees it: no visitor's record is in it.
    With a `registers` cache that keeps anything, its residents are the district's register as
    kept there, so that the residents of visitors' test calls, which are kept for good, are not
    read on every load. Without one, they are read here, as there is nothing to save."""
    current = _round(store, round_id)
    district_id = current["district_id"]

    def shown(docs: list[dict]) -> list[dict]:
        return [doc for doc in docs if visible(doc, None)]

    if registers is not None and registers.seconds > 0:
        residents = load_register(store, district_id, registers)["residents"]
    else:
        residents = shown(store.query("residents", district_id=district_id))
    return RoundData(
        round=current,
        calls=shown(store.query("calls", round_id=round_id)),
        cases=shown(store.query("cases", round_id=round_id)),
        residents={r["id"]: r for r in residents},
        wards={w["id"]: w for w in store.query("wards", district_id=district_id)},
    )


def _load_own(store: Store, round_id: str, district_id: str, visitor_id: str) -> tuple[dict, dict | None, dict | None] | None:
    """(call, case or None, resident or None) of the one call of this viewer that is shown, or
    None. Asked for by the viewer's id, so nobody else's records are read to find them."""

    def shown(docs: list[dict]) -> list[dict]:
        return [doc for doc in docs if visible(doc, visitor_id) and doc.get("visitor_id")]

    call = _own_call([c for c in shown(store.query("calls", visitor_id=visitor_id)) if c["round_id"] == round_id])
    if call is None:
        return None
    resident_id = call["resident_id"]
    cases = [
        c for c in shown(store.query("cases", visitor_id=visitor_id))
        if c["round_id"] == round_id and c["resident_id"] == resident_id
    ]
    residents = [
        r for r in shown(store.query("residents", visitor_id=visitor_id))
        if r["id"] == resident_id and r.get("district_id") == district_id
    ]
    return call, (cases[0] if cases else None), (residents[0] if residents else None)


def load_round(
    store: Store, round_id: str, visitor_id: str | None = None, cache: RoundCache | None = None,
    registers: RoundCache | None = None,
) -> RoundData:
    """The round as this viewer may see it: what everyone sees (from `cache` if one is given
    and still fresh, its residents from `registers` if given) and, for a visitor, the one call
    of their own that is shown."""
    shared = (cache or _NO_CACHE).get(round_id, "round", lambda: _load_shared(store, round_id, registers))
    own = _load_own(store, round_id, shared.round["district_id"], visitor_id) if visitor_id else None
    if own is None:
        return shared
    call, case, resident = own
    # New lists and a new dict: what the cache holds is never added to.
    return RoundData(
        round=shared.round,
        calls=[*shared.calls, call],
        cases=[*shared.cases, case] if case else shared.cases,
        residents={**shared.residents, resident["id"]: resident} if resident else shared.residents,
        wards=shared.wards,
    )


def shared_key(data: RoundData, alert: dict | None) -> str:
    """A short fingerprint of what every viewer sees alike in the round: it changes when a
    shared call or case changes, or the alert's severity or hazard (the summary names them),
    and never because of a visitor's own records."""
    def stamp(value):
        return value.isoformat() if hasattr(value, "isoformat") else value

    cases = sorted(
        (c["id"], c["tier"], c["status"], list(c.get("reasons") or []), stamp(c.get("opened_at")), stamp(c.get("visited_at")))
        for c in data.cases
        if not c.get("visitor_id")
    )
    calls = sorted(Counter(c["status"] for c in data.calls if not c.get("visitor_id")).items())
    warning = [(alert or {}).get("severity"), (alert or {}).get("hazard")]
    figures = [cases, calls, data.round.get("peak_temp_c"), warning]
    return hashlib.sha256(json.dumps(figures).encode()).hexdigest()[:16]


def _shared_doc(store: Store, cache: RoundCache | None, round_id: str, collection: str, doc_id: str) -> dict | None:
    """A record every viewer of the round is shown alike (its alert, its district)."""
    return (cache or _NO_CACHE).get(round_id, collection, lambda: store.get(collection, doc_id))


def _mask_phone(phone: str | None) -> str | None:
    """Keep the first 3 and last 2 characters, e.g. +910000000001 -> +91********01."""
    if not phone:
        return phone
    if len(phone) <= 5:
        return "*" * len(phone)
    return phone[:3] + "*" * (len(phone) - 5) + phone[-2:]


def _labels(reasons) -> list[str]:
    return [REASON_LABELS.get(r, r) for r in reasons]


def round_overview(
    store: Store, round_id: str, visitor_id: str | None = None, cache: RoundCache | None = None,
    registers: RoundCache | None = None,
) -> dict:
    data = load_round(store, round_id, visitor_id, cache, registers)
    cases = {c["resident_id"]: c for c in data.cases}
    latest = data.latest_calls()
    tiers = Counter(c["tier"] for c in data.cases)
    counts = {
        "targeted": data.targeted(),
        # a call that is dialing is counted as queued until someone picks up
        "queued": sum(1 for c in latest.values() if c["status"] in WAITING_STATUSES),
        "in_call": sum(1 for c in latest.values() if c["status"] == "in_progress"),
        "reached": data.reached(),
        "unreachable": data.unreachable(),
        "red": tiers["red"],
        "amber": tiers["amber"],
        "green": tiers["green"],
    }

    def row(resident_id: str, call: dict) -> dict:
        resident = data.residents.get(resident_id, {})
        ward = data.wards.get(resident.get("ward_id"), {})
        return {
            "call_id": call["id"],
            "resident_id": resident_id,
            "resident_name": resident.get("name", resident_id),
            "age": resident.get("age"),
            "ward_id": resident.get("ward_id"),
            "ward_name": ward.get("name"),
            "risk_score": resident.get("risk_score") or 0,
            "risk_factors": risk_factors(resident),
            "attempt": call["attempt"],
            "status": call["status"],
            "tier": cases[resident_id]["tier"] if resident_id in cases else None,
            # only the viewer's own tagged records are ever loaded
            "yours": bool(call.get("visitor_id")),
        }

    rows = [row(resident_id, call) for resident_id, call in latest.items()]
    # the order residents are called in: highest risk first, then oldest
    rows.sort(key=lambda r: (-r["risk_score"], -(r["age"] or 0), r["resident_id"]))
    return {
        "round": data.round,
        "alert": _shared_doc(store, cache, round_id, "alerts", data.round["alert_id"]),
        "counts": counts,
        "calls": rows,
    }


def _quote(calls: list[dict]) -> str:
    """Something the person said: how they feel if we have it, from the latest attempt first."""
    for call in sorted(calls, key=lambda c: -c["attempt"]):
        answers = call.get("answers") or {}
        if (answers.get("feeling") or {}).get("quote"):
            return answers["feeling"]["quote"]
        for answer in answers.values():
            if answer.get("quote"):
                return answer["quote"]
    return ""


def ward_cases(
    store: Store,
    ward_id: str,
    round_id: str,
    visitor_id: str | None = None,
    operator: bool = False,
    cache: RoundCache | None = None,
    registers: RoundCache | None = None,
) -> list[dict]:
    data = load_round(store, round_id, visitor_id, cache, registers)
    calls = data.calls_by_resident()
    enriched = []
    for case in data.cases:
        if case["ward_id"] != ward_id:
            continue
        resident = data.residents.get(case["resident_id"], {})
        yours = bool(case.get("visitor_id"))
        needs_visit = case["status"] == "open" and case["tier"] != "green"
        enriched.append(
            {
                **{key: value for key, value in case.items() if key != "visitor_id"},
                "opened_at": case.get("opened_at"),
                "resident": {
                    **{k: resident.get(k) for k in ("name", "age", "language", "lat", "lng", "risk_score")},
                    # even masked, a number is for the people who may have to ring it
                    "phone": _mask_phone(resident.get("phone")) if operator else None,
                },
                "reason_labels": _labels(case["reasons"]),
                "quote": _quote(calls.get(case["resident_id"], [])),
                "outcome_label": VISIT_OUTCOMES.get(case.get("outcome")),
                "yours": yours,
                # a visitor marks only the case from their own call; the team marks any
                "can_mark": needs_visit and (yours or operator),
            }
        )
    enriched.sort(
        key=lambda c: (-TIER_ORDER[c["tier"]], -(c["resident"]["risk_score"] or 0), c["id"])
    )
    return enriched


def build_report(
    store: Store, round_id: str, visitor_id: str | None = None, cache: RoundCache | None = None,
    registers: RoundCache | None = None,
) -> dict:
    data = load_round(store, round_id, visitor_id, cache, registers)
    current, cases = data.round, data.cases
    reached, unreachable, targeted = data.reached(), data.unreachable(), data.targeted()

    def tier_row(ward_id, name, in_row) -> dict:
        tiers = Counter(c["tier"] for c in cases if in_row(c))
        return {"ward_id": ward_id, "name": name, "red": tiers["red"], "amber": tiers["amber"], "green": tiers["green"]}

    tiers_by_ward = [
        tier_row(ward_id, data.wards[ward_id]["name"], lambda c, w=ward_id: c["ward_id"] == w)
        for ward_id in sorted(data.wards)
    ]
    if any(c["ward_id"] not in data.wards for c in cases):
        # so that the columns add up to the overall counts
        tiers_by_ward.append(tier_row(None, UNASSIGNED_WARD, lambda c: c["ward_id"] not in data.wards))

    reason_counts = Counter(r for c in cases for r in c["reasons"] if r != "G1")
    problems = [
        {"rule": rule, "label": REASON_LABELS.get(rule, rule), "count": count}
        for rule, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    needing_visit = [c for c in cases if c["tier"] != "green"]
    visited = [c for c in needing_visit if c["status"] in ("visited", "resolved")]
    minutes = []
    for case in visited:
        # from when the case opened; a case stored before that was recorded uses the round start
        since = case.get("opened_at") or current.get("started_at")
        if case.get("visited_at") and since:
            minutes.append((case["visited_at"] - since).total_seconds() / 60)

    open_red = [
        {
            "case_id": c["id"],
            "resident_name": data.residents.get(c["resident_id"], {}).get("name", c["resident_id"]),
            "ward_id": c["ward_id"],
            "reason_labels": _labels(c["reasons"]),
        }
        for c in sorted(cases, key=lambda c: c["id"])
        if c["tier"] == "red" and c["status"] == "open"
    ]

    alert = _shared_doc(store, cache, round_id, "alerts", current["alert_id"])
    stored = current.get("summary") or {}
    # Shown only while it still describes the shared figures; a newer one is written by the sweep.
    current_summary = (
        {"text": stored["text"], "written_at": stored.get("at")}
        if stored.get("text") and stored.get("key") == shared_key(data, alert)
        else None
    )

    return {
        "round": current,
        "alert": alert,
        "district": _shared_doc(store, cache, round_id, "districts", current["district_id"]),
        "includes_yours": any(c.get("visitor_id") for c in cases),
        "summary": current_summary,
        "coverage": {
            "targeted": targeted,
            "reached": reached,
            "unreachable": unreachable,
            "pending": targeted - reached - unreachable,
            # never dispatched, so not part of targeted or pending; someone must fix the number
            "not_callable": data.not_callable(),
        },
        "tiers_by_ward": tiers_by_ward,
        "problems": problems,
        "response": {
            "visited": len(visited),
            "open": len(needing_visit) - len(visited),
            "median_minutes_to_visit": median(minutes) if minutes else None,
            "escalated": sum(bool(c.get("escalation")) for c in cases),
            "acknowledged": sum(bool((c.get("escalation") or {}).get("acknowledged_at")) for c in cases),
            "raised": sum(bool(c.get("raised")) for c in cases),
            "reassigned": sum(bool(c.get("reassigned_from")) for c in cases),
        },
        "open_red_cases": open_red,
    }


def ward_directory(store: Store, round_id: str) -> list[dict]:
    """The wards of the round's district, each with its worker's name and its facilities.
    For the ward switcher and the map. No phone numbers."""
    current = _round(store, round_id)
    directory = []
    for ward in sorted(store.query("wards", district_id=current["district_id"]), key=lambda w: w["id"]):
        worker = store.get("workers", ward["worker_id"]) if ward.get("worker_id") else None
        facilities = sorted(store.query("facilities", ward_id=ward["id"]), key=lambda f: f["id"])
        directory.append(
            {
                "id": ward["id"],
                "name": ward["name"],
                "lat": ward.get("lat"),
                "lng": ward.get("lng"),
                "worker_name": (worker or {}).get("name"),
                "facilities": [
                    {k: facility.get(k) for k in ("id", "name", "type", "lat", "lng")}
                    for facility in facilities
                ],
            }
        )
    return directory
