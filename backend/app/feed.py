"""The glass box: what one call has recorded so far, in a form a screen can show.

Built field by field from the call document, so a ticket, a token or a phone number can never
ride along. The rules shown are computed here on the server, from the same list as final triage."""
from app.call.guidance import questions_for
from app.cases import was_emergency
from app.db import Store
from app.triage import REASON_LABELS, rule_tier, rules_so_far

QUESTION_LABELS = {
    "feeling": "Feeling",
    "fluids_food": {"heat": "Water", "cold": "Food"},
    "company": "Company",
    "shelter": "Shelter",
    "exposure": "Going out",
    "medicine": "Medicines",
    "help": "Help needed",
}

VALUE_LABELS = {
    "feeling": {
        "ok": "No symptoms", "mild_symptoms": "Mild symptoms",
        "severe_symptoms": "Severe symptoms", "unclear": "Unclear",
    },
    "fluids_food": {"enough": "Enough", "low": "Low", "none": "None"},
    "company": {"with_someone": "With someone", "alone": "Alone"},
    "shelter": {"adequate": "Adequate", "partial": "Partial", "none": "None"},
    "exposure": {"staying_in": "Staying in", "must_go_out": "Must go out"},
    "medicine": {"has": "Has them", "running_low": "Running low", "out": "Run out"},
    "help": {"none": "Nothing needed", "wants_visit": "Wants a visit", "urgent": "Urgent"},
}


def _question_label(question_id: str, hazard: str) -> str:
    label = QUESTION_LABELS.get(question_id, question_id)
    return label.get(hazard, question_id) if isinstance(label, dict) else label


def call_feed(call: dict, resident: dict, hazard: str, case: dict | None) -> dict:
    answers = call.get("answers") or {}
    has_conditions = bool(resident.get("conditions"))

    questions = []
    for question in questions_for(hazard, has_conditions):  # in the order the script asks them
        question_id = question["id"]
        answer = answers.get(question_id) or {}
        value = answer.get("value")
        questions.append(
            {
                "id": question_id,
                "label": _question_label(question_id, hazard),
                "value": value,
                "value_label": VALUE_LABELS.get(question_id, {}).get(value, value) if value is not None else None,
                "quote": answer.get("quote") or None,
                "at": answer.get("at"),
            }
        )

    so_far = rules_so_far(
        resident["age"],
        has_conditions,
        {question_id: answer.get("value") for question_id, answer in answers.items()},
        was_emergency(call),
        call.get("respondent", "unknown"),
    )
    emergency = call.get("emergency") or {}
    return {
        "call_id": call["id"],
        "resident_id": resident.get("id"),
        "card_id": resident.get("card_id"),
        "ward_id": resident.get("ward_id"),
        "status": call["status"],
        "started_at": call.get("started_at"),
        "ended_at": call.get("ended_at"),
        "end_outcome": call.get("end_outcome"),
        "language_used": call.get("language_used"),
        "respondent": call.get("respondent", "unknown"),
        "transcript": [
            {"role": entry.get("role"), "text": entry.get("text"), "at": entry.get("at")}
            for entry in call.get("transcript") or []
        ],
        "questions": questions,
        "emergency": {"reason": emergency.get("reason", ""), "at": emergency.get("at")} if emergency.get("flagged") else None,
        "rules_so_far": [
            {"id": rule, "tier": rule_tier(rule), "label": REASON_LABELS.get(rule, rule)}
            for rule in so_far.reasons
        ],
        "tier_so_far": so_far.tier,
        "case": {
            "id": case["id"],
            "tier": case["tier"],
            "reasons": case["reasons"],
            "reason_labels": [REASON_LABELS.get(rule, rule) for rule in case["reasons"]],
            "ward_id": case["ward_id"],
            "note": case.get("note") or None,
        } if case else None,
    }


def load_feed(store: Store, call: dict) -> dict:
    """The feed for a call document. Raises KeyError if its resident or round has gone."""
    resident = store.get("residents", call["resident_id"])
    if resident is None:
        raise KeyError(f"residents/{call['resident_id']}")
    current = store.get("rounds", call["round_id"])
    if current is None:
        raise KeyError(f"rounds/{call['round_id']}")
    case = store.get("cases", f"{call['round_id']}_{call['resident_id']}")
    return call_feed(call, resident, current["hazard"], case)
