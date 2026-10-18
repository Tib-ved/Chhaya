"""One resident in full: who they are, what the call recorded, why the rules gave the tier
they did, and what has been done since. The end of the officer's drill-down and of a test call."""
import json
from datetime import datetime
from pathlib import Path

from app.case_actions import ESCALATION_REASONS, OBSERVATIONS
from app.cases import VISIT_OUTCOMES, has_red_fact
from app.db import Store
from app.feed import call_feed
from app.models import REACHED_STATUSES
from app.region import load_district, open_load, warning_of, worst_answers
from app.report import RoundCache, visible
from app.triage import REASON_LABELS, SEVERITY, rule_tier

LIBRARY = Path(__file__).resolve().parent.parent / "seed" / "fixtures" / "ai_calls.json"


def _recorded_at(call: dict | None):
    """When an AI-played resident's conversation was recorded: the call's own date if it has one,
    else the library's entry for the conversation, else the library's. None where there is no library."""
    if call is None:
        return None
    if call.get("recorded_at"):
        return call["recorded_at"]
    try:
        library = json.loads(LIBRARY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = next((c for c in library.get("conversations") or [] if c.get("id") == call.get("conversation_id")), {})
    return entry.get("recorded_at") or library.get("recorded_at")


# The question whose answer a rule was matched on, where it is one answer.
RULE_QUESTION = {
    "R2": "feeling", "R3": "help", "R4": "fluids_food", "A1": "feeling", "A2": "fluids_food",
    "A3": "shelter", "A4": "company", "A5": "medicine", "A6": "help", "A7": "exposure",
}
RED_ANSWERS = {("feeling", "severe_symptoms"), ("help", "urgent"), ("fluids_food", "none")}


def _worry(question_id: str, value: str | None) -> str | None:
    """How worrying an answer is on its own: None, "amber" or "red". For colouring, not triage."""
    order = SEVERITY.get(question_id, [])
    if value is None or value not in order or order.index(value) == 0:
        return None
    return "red" if (question_id, value) in RED_ANSWERS else "amber"


def resident_view(
    store: Store, resident_id: str, visitor_id: str | None, operator: bool, now: datetime,
    views: RoundCache | None = None, registers: RoundCache | None = None,
) -> dict:
    """Raises KeyError for a resident who does not exist or belongs to another visitor's call."""
    resident = store.get("residents", resident_id)
    if resident is None or not visible(resident, visitor_id):
        raise KeyError(f"residents/{resident_id}")
    load = load_district(store, resident["district_id"], visitor_id, now, views, registers)
    row = next((r for r in load.rows if r["id"] == resident_id), None)
    if row is None:  # a visitor's resident whose call is not one the screens show
        raise KeyError(f"residents/{resident_id}")
    register, data = load.register, load.data
    case = next((c for c in data.cases if c["resident_id"] == resident_id), None) if data else None
    calls = data.calls_by_resident().get(resident_id, []) if data else []
    # The conversations are counted as upsert_case counts them, so the answers shown are the
    # ones the tier was decided on; a call still in progress is not yet one of them.
    talked = [c for c in calls if c["status"] in REACHED_STATUSES or has_red_fact(c)]
    spoken = max(talked, key=lambda c: c["attempt"]) if talked else None
    feed = call_feed({**spoken, "answers": worst_answers(talked)}, resident, data.round["hazard"], case) if spoken else None

    questions = [
        {**{k: q[k] for k in ("id", "label", "value", "value_label", "quote")}, "worry": _worry(q["id"], q["value"])}
        for q in (feed["questions"] if feed else []) if q["value"] is not None
    ]
    answered = {q["id"]: q for q in questions}

    def rule(rule_id: str) -> dict:
        question = answered.get(RULE_QUESTION.get(rule_id))
        detail = f"{question['label']}: {question['value_label']}" if question else ""
        if rule_id == "R5" and case.get("raised"):
            detail = OBSERVATIONS.get(case["raised"].get("observation"), "")
        return {
            "id": rule_id, "label": REASON_LABELS.get(rule_id, rule_id), "tier": rule_tier(rule_id),
            "question_id": question["id"] if question else None, "detail": detail,
        }

    workers = register["workers"]
    load_by_worker = open_load(load.rows)
    ward = register["wards"].get(resident.get("ward_id"), {})
    centre = next((f for f in register["facilities"] if f["ward_id"] == resident.get("ward_id") and f["type"] == "health_centre"), None)
    flag, raised = (case or {}).get("escalation"), (case or {}).get("raised")
    worker_id = row["worker_id"]
    return {
        "resident": {
            **row,
            "language": resident.get("language"),
            "district_id": resident["district_id"],
            "district_name": register["district"]["name"],
            "worker_name": (workers.get(worker_id) or {}).get("name"),
            "emergency_contact_name": (resident.get("emergency_contact") or {}).get("name"),
        },
        "warning": warning_of(load),
        "call": {
            "status": row["call_status"],
            "attempts": row["attempts"],
            "first_at": min((c.get("ended_at") or c.get("started_at") or c.get("created_at") for c in calls if (c.get("ended_at") or c.get("started_at") or c.get("created_at"))), default=None),
            "answered_by": feed["respondent"] if feed else None,
            "language_used": feed["language_used"] if feed else None,
            "recorded_at": _recorded_at(next((c for c in calls if c.get("simulated") == "ai_resident"), None)),
        } if calls else None,
        "questions": questions,
        "rules": [rule(r) for r in case["reasons"] if r != "G1"] if case else [],
        "transcript": feed["transcript"] if feed else [],
        "note": (case or {}).get("note") or "",
        "case": {
            "id": case["id"],
            "tier": case["tier"],
            "status": row["case_status"],
            "opened_at": case.get("opened_at"),
            "visited_at": case.get("visited_at"),
            "outcome": case.get("outcome"),
            "outcome_label": VISIT_OUTCOMES.get(case.get("outcome")),
            "visit_note": case.get("visit_note") or "",
            "simulated_visit": row["simulated_visit"],
            "waiting_minutes": row["waiting_minutes"],
            "escalation": {**flag, "reason_label": ESCALATION_REASONS.get(flag.get("reason"), "")} if flag else None,
            "raised": {**raised, "observation_label": OBSERVATIONS.get(raised.get("observation"), "")} if raised else None,
            "worker_id": worker_id,
            "ward_worker_id": ward.get("worker_id"),
            "reassigned_from": case.get("reassigned_from"),
            # a visitor acts only on the case from their own call; the team on any
            "can_act": case["status"] == "open" and case["tier"] != "green" and (row["yours"] or operator),
            "can_acknowledge": bool(flag) and not flag.get("acknowledged_at") and (row["yours"] or operator),
        } if case else None,
        "health_centre": {k: centre.get(k) for k in ("name", "lat", "lng")} if centre else None,
        "workers": [
            {
                "id": w["id"], "name": w["name"], "name_hi": w.get("name_hi") or "",
                "ward_name": next((register["wards"][x]["name"] for x in w.get("ward_ids") or [] if x in register["wards"]), None),
                "open": load_by_worker.get(w["id"], 0),
            }
            for w in sorted(workers.values(), key=lambda w: w["id"])
        ],
    }
