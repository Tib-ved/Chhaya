from datetime import datetime

from app.case_actions import clean_note
from app.db import Store
from app.models import CONVERSATION_STATUSES, FAILED_STATUSES, MAX_ATTEMPTS, Case
from app.triage import SEVERITY, TIER_ORDER, TriageInput, triage

VISIT_OUTCOMES = {
    "advised_ok": "Advised, managing fine",
    "supplies_given": "Water, ORS or supplies given",
    "referred": "Referred to the health centre",
    "hospital": "Taken to hospital",
    "not_home": "Not at home",
}


class VisitRefused(Exception):
    """A visit that cannot be recorded. `reason` is unknown_outcome, green_case or already_visited."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def was_emergency(call: dict) -> bool:
    """An emergency was flagged, or the agent ended the call as an emergency. The ending counts
    on its own, so the case is red even if the flag itself could never be stored."""
    return bool((call.get("emergency") or {}).get("flagged")) or call.get("end_outcome") == "emergency"


def has_red_fact(call: dict) -> bool:
    """Whether the call document holds an emergency flag, an emergency ending or a red answer,
    whatever its status."""
    if was_emergency(call):
        return True
    answers = call.get("answers") or {}
    for question, red_value in (("feeling", "severe_symptoms"), ("help", "urgent")):
        if ((answers.get(question) or {}).get("value")) == red_value:
            return True
    return False


def upsert_case(store: Store, round_id: str, resident_id: str, now: datetime) -> dict | None:
    resident = store.get("residents", resident_id)
    if resident is None:
        raise KeyError(f"residents/{resident_id}")
    calls = store.query("calls", round_id=round_id, resident_id=resident_id)
    # A red fact is never lost: a call that holds one is a conversation whatever its stored
    # status (a tool call can land just after a hang-up was stored as no_answer), and it is then
    # not also counted as a failed attempt.
    talked = sorted(
        (c for c in calls if c["status"] in CONVERSATION_STATUSES or has_red_fact(c)),
        key=lambda c: c["attempt"],
    )
    failed = sum(1 for c in calls if c["status"] in FAILED_STATUSES and not has_red_fact(c))
    if not talked and failed < MAX_ATTEMPTS:
        return None

    answers: dict[str, str] = {}
    emergency = False
    respondent = "unknown"
    for call in talked:
        for question, answer in (call.get("answers") or {}).items():
            value, order = answer["value"], SEVERITY.get(question, [])
            kept = answers.get(question)
            # Across attempts the worse answer stands; a value outside the table follows the latest.
            if kept in order and value in order and order.index(kept) > order.index(value):
                continue
            answers[question] = value
        emergency = emergency or was_emergency(call)
        if call.get("respondent", "unknown") != "unknown":
            respondent = call["respondent"]

    result = triage(
        TriageInput(
            age=resident["age"],
            has_conditions=bool(resident.get("conditions")),
            answers=answers,
            emergency=emergency,
            respondent=respondent,
            failed_attempts=failed,
            conversation=bool(talked),
        )
    )

    case_id = f"{round_id}_{resident_id}"
    ward = store.get("wards", resident["ward_id"]) or {}

    def merged_with(existing: dict | None) -> dict:
        """The case to store, given the one that is stored now."""
        tier, reasons = result.tier, list(result.reasons)
        status, visited_at, outcome, note, note_tries = "open", None, None, "", 0
        opened_at = now
        if existing:
            note = existing.get("note", "")
            note_tries = existing.get("note_tries") or 0
            opened_at = existing.get("opened_at")  # stays None on a case stored before this field
            if TIER_ORDER[result.tier] <= TIER_ORDER[existing["tier"]]:
                status = existing["status"]
                visited_at = existing.get("visited_at")
                outcome = existing.get("outcome")
            if TIER_ORDER[result.tier] < TIER_ORDER[existing["tier"]]:
                # A tier never goes down within a round: keep it, and keep why it was raised.
                tier = existing["tier"]
                reasons = list(existing["reasons"])
                reasons += [r for r in result.reasons if r not in reasons]
                if len(reasons) > 1:
                    reasons = [r for r in reasons if r != "G1"]
            elif TIER_ORDER[result.tier] == TIER_ORDER[existing["tier"]]:
                # Same tier: other reasons follow the latest answers, but a red reason is never lost.
                reasons += [r for r in existing["reasons"] if r.startswith("R") and r not in reasons]
        # Red reasons first, each group keeping its order.
        reasons = [r for r in reasons if r.startswith("R")] + [r for r in reasons if not r.startswith("R")]
        carried = {key: (existing or {}).get(key) for key in ("escalation", "raised", "reassigned_from")}
        # Once an officer has reassigned a case, its worker is theirs to choose, not the ward's.
        worker_id = existing["worker_id"] if existing and existing.get("reassigned_from") else ward.get("worker_id")
        return Case(
            id=case_id,
            round_id=round_id,
            resident_id=resident_id,
            ward_id=resident["ward_id"],
            worker_id=worker_id,
            tier=tier,
            reasons=reasons,
            note=note,
            note_tries=note_tries,
            status=status,
            visited_at=visited_at,
            outcome=outcome,
            updated_at=now,
            opened_at=opened_at,
            visitor_id=resident.get("visitor_id"),
            visit_note=(existing or {}).get("visit_note") or "",
            **carried,
        ).model_dump()

    existing = store.get("cases", case_id)
    case = merged_with(existing)
    if existing and {**existing, "updated_at": now} == case:
        return existing  # nothing changed: a repeated callback leaves the case untouched
    # Read the case again immediately before writing. If another writer has stored a higher
    # tier meanwhile (a red fact opening the case during the call, while this amber result was
    # being computed), merge against that copy so the tier is never lowered. This narrows the
    # window but does not close it: the read and the write are not one transaction. A later
    # sweep re-runs this computation for recently finished calls.
    fresh = store.get("cases", case_id)
    if fresh and TIER_ORDER[fresh["tier"]] > TIER_ORDER[case["tier"]]:
        case = merged_with(fresh)
    store.set("cases", case_id, case)
    return case


def mark_visited(store: Store, case_id: str, outcome: str, now: datetime, note: str = "") -> dict:
    """Record a worker's visit. Raises KeyError for an unknown case and VisitRefused for an
    unlisted outcome, a green case, or a case that is not open (it was visited already)."""
    case = store.get("cases", case_id)
    if case is None:
        raise KeyError(f"cases/{case_id}")
    if outcome not in VISIT_OUTCOMES:
        raise VisitRefused("unknown_outcome", "the outcome must be one of: " + ", ".join(VISIT_OUTCOMES))
    if case["tier"] == "green":
        raise VisitRefused("green_case", "no visit is needed for a green case")
    visit = {"status": "visited", "visited_at": now, "outcome": outcome, "updated_at": now, "visit_note": clean_note(note)}
    # Written only if the case is still open, so that of two markers exactly one succeeds.
    if not store.transition("cases", case_id, "status", "open", visit):
        raise VisitRefused("already_visited", "this case has already been visited")
    return store.get("cases", case_id)
