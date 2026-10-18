"""What a health worker and an officer can do to a case, apart from visiting it.

None of these lowers a tier. Only raise_to_red changes one, and it does so by adding the fixed
rule R5, on what a trained person saw at the door. Each action reads the case, checks it and
writes: two people acting in the same instant are not serialised, which is accepted here."""
from datetime import datetime

from app.db import Store

MAX_NOTE = 200
ESCALATION_REASONS = {
    "transport": "Needs transport to hospital",
    "supplies": "Needs supplies I do not have",
    "missing": "Cannot find the person",
    "other": "Something else",
}
OBSERVATIONS = {
    "confused": "Confused or not making sense",
    "fainted": "Fainted or collapsed",
    "dry_skin": "Hot, dry skin, not sweating",
    "vomiting": "Vomiting again and again",
    "chest_breathing": "Chest pain or trouble breathing",
    "other": "Something else that worries me",
}


class ActionRefused(Exception):
    """An action that cannot be done. `reason` is one of not_open, already_flagged, no_flag,
    not_amber, green_case, bad_value, unknown_worker, same_worker."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def clean_note(text: object) -> str:
    """A note as it is stored: whitespace collapsed, at most MAX_NOTE characters. It is shown
    as plain text and never given to a text model."""
    return " ".join(str(text or "").split())[:MAX_NOTE]


def _open_case(store: Store, case_id: str) -> dict:
    case = store.get("cases", case_id)
    if case is None:
        raise KeyError(f"cases/{case_id}")
    if case["status"] != "open":
        raise ActionRefused("not_open", "this case has already been visited")
    return case


def _no_green(case: dict) -> None:
    if case["tier"] == "green":
        raise ActionRefused("green_case", "no visit is needed for a green case")


def _save(store: Store, case_id: str, fields: dict, now: datetime) -> dict:
    store.update("cases", case_id, {**fields, "updated_at": now})
    return store.get("cases", case_id)


def escalate(store: Store, case_id: str, reason: str, note: str, now: datetime) -> dict:
    """A worker tells the officer about a case. The case stays open and keeps its tier."""
    case = _open_case(store, case_id)
    _no_green(case)
    if reason not in ESCALATION_REASONS:
        raise ActionRefused("bad_value", "the reason must be one of: " + ", ".join(ESCALATION_REASONS))
    flag = case.get("escalation")
    if flag and not flag.get("acknowledged_at"):
        raise ActionRefused("already_flagged", "the officer has already been told about this case")
    return _save(store, case_id, {
        "escalation": {"reason": reason, "note": clean_note(note), "at": now, "acknowledged_at": None, "reply": ""},
    }, now)


def acknowledge(store: Store, case_id: str, reply: str, now: datetime) -> dict:
    """The officer has seen a worker's flag. Allowed whatever the case's status."""
    case = store.get("cases", case_id)
    if case is None:
        raise KeyError(f"cases/{case_id}")
    flag = case.get("escalation")
    if not flag or flag.get("acknowledged_at"):
        raise ActionRefused("no_flag", "there is nothing waiting to be acknowledged on this case")
    return _save(store, case_id, {"escalation": {**flag, "acknowledged_at": now, "reply": clean_note(reply)}}, now)


def reassign(store: Store, case_id: str, worker_id: str, now: datetime) -> dict:
    """The officer gives an open case to another worker of the same district."""
    case = _open_case(store, case_id)
    _no_green(case)
    if worker_id == case.get("worker_id"):
        raise ActionRefused("same_worker", "that worker already has this case")
    district_id = (store.get("wards", case["ward_id"]) or {}).get("district_id")
    worker = store.get("workers", worker_id) if isinstance(worker_id, str) and worker_id else None
    their_districts = {(store.get("wards", ward_id) or {}).get("district_id") for ward_id in (worker or {}).get("ward_ids") or []}
    if worker is None or district_id is None or district_id not in their_districts:
        raise ActionRefused("unknown_worker", "no such worker in this district")
    return _save(store, case_id, {"worker_id": worker_id, "reassigned_from": case.get("worker_id")}, now)


def raise_to_red(store: Store, case_id: str, observation: str, now: datetime) -> dict:
    """A worker who has seen the person raises an open amber case to red, under rule R5."""
    case = _open_case(store, case_id)
    if observation not in OBSERVATIONS:
        raise ActionRefused("bad_value", "what was seen must be one of: " + ", ".join(OBSERVATIONS))
    if case["tier"] != "amber":
        raise ActionRefused("not_amber", "only an amber case can be raised to red")
    reasons = ["R5", *[rule for rule in case["reasons"] if rule != "R5"]]
    return _save(store, case_id, {"tier": "red", "reasons": reasons, "raised": {"observation": observation, "at": now}}, now)
