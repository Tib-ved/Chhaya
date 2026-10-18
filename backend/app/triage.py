from dataclasses import dataclass, field

from app.models import MAX_ATTEMPTS

TIER_ORDER = {"green": 0, "amber": 1, "red": 2}

# Asked of everyone; "medicine" is also required for residents with a health condition.
REQUIRED_QUESTIONS = ("feeling", "fluids_food", "company", "shelter", "exposure", "help")

ALLOWED_VALUES = {
    "feeling": {"ok", "mild_symptoms", "severe_symptoms", "unclear"},
    "fluids_food": {"enough", "low", "none"},
    "company": {"with_someone", "alone"},
    "shelter": {"adequate", "partial", "none"},
    "exposure": {"staying_in", "must_go_out"},
    "medicine": {"has", "running_low", "out"},
    "help": {"none", "wants_visit", "urgent"},
}

# Each question's values, from least to most worrying. A worse answer is never overwritten.
SEVERITY = {
    "feeling": ["ok", "unclear", "mild_symptoms", "severe_symptoms"],
    "fluids_food": ["enough", "low", "none"],
    "company": ["with_someone", "alone"],
    "shelter": ["adequate", "partial", "none"],
    "exposure": ["staying_in", "must_go_out"],
    "medicine": ["has", "running_low", "out"],
    "help": ["none", "wants_visit", "urgent"],
}

REASON_LABELS = {
    "R1": "Emergency flagged during the call",
    "R2": "Reported severe symptoms",
    "R3": "Asked for urgent help",
    "R4": "No water or food, and alone",
    "R5": "Raised to red by the health worker",
    "A1": "Reported mild or unclear symptoms",
    "A2": "Low on water or food",
    "A3": "No fan, cooling or warm shelter",
    "A4": "Alone and aged 70 or over",
    "A5": "Regular medicines running low or finished",
    "A6": "Asked for a visit",
    "A7": "Must go outside despite age or health condition",
    "A8": "Did not answer after three attempts",
    "A9": "Call ended before we learned how they feel",
    "A10": "Someone else answered and could not say how they are",
    "A11": "An answer could not be understood",
    "A12": "Call ended before all the questions were asked",
    "G1": "No concerns reported",
}


@dataclass(frozen=True)
class TriageInput:
    age: int
    has_conditions: bool
    answers: dict[str, str] = field(default_factory=dict)
    emergency: bool = False
    respondent: str = "unknown"
    failed_attempts: int = 0
    conversation: bool = False


@dataclass(frozen=True)
class TriageResult:
    tier: str
    reasons: tuple[str, ...]


# Rules about how a call ended. They cannot be judged while it is still going on.
PROVISIONAL_EXCLUDED = frozenset({"A8", "A9", "A12"})


@dataclass(frozen=True)
class Provisional:
    tier: str | None  # None, "amber" or "red": green is never shown before a call has ended
    reasons: tuple[str, ...]


def rule_tier(rule_id: str) -> str:
    return {"R": "red", "G": "green"}.get(rule_id[:1], "amber")


def _matched(i: TriageInput) -> list[str]:
    """Every rule the input matches, red rules first. The one place the rules are written."""
    a = i.answers
    feeling = a.get("feeling")
    reasons: list[str] = []

    if i.emergency:
        reasons.append("R1")
    if feeling == "severe_symptoms":
        reasons.append("R2")
    if a.get("help") == "urgent":
        reasons.append("R3")
    if a.get("fluids_food") == "none" and a.get("company") == "alone":
        reasons.append("R4")

    if feeling in ("mild_symptoms", "unclear"):
        reasons.append("A1")
    if a.get("fluids_food") in ("low", "none"):
        reasons.append("A2")
    if a.get("shelter") == "none":
        reasons.append("A3")
    if a.get("company") == "alone" and i.age >= 70:
        reasons.append("A4")
    if a.get("medicine") in ("running_low", "out"):
        reasons.append("A5")
    if a.get("help") == "wants_visit":
        reasons.append("A6")
    if a.get("exposure") == "must_go_out" and (i.age >= 65 or i.has_conditions):
        reasons.append("A7")
    if not i.conversation and i.failed_attempts >= MAX_ATTEMPTS:
        reasons.append("A8")
    if i.conversation and feeling is None:
        reasons.append("A9")
    if i.conversation and i.respondent in ("family", "other") and feeling in (None, "unclear"):
        reasons.append("A10")
    if any(
        q in ALLOWED_VALUES and (not isinstance(v, str) or v not in ALLOWED_VALUES[q])
        for q, v in a.items()
    ):
        reasons.append("A11")
    required = REQUIRED_QUESTIONS + (("medicine",) if i.has_conditions else ())
    if i.conversation and any(q not in a for q in required):
        reasons.append("A12")
    return reasons


def _tier_of(reasons: list[str]) -> str | None:
    if any(r.startswith("R") for r in reasons):
        return "red"
    return "amber" if reasons else None


def triage(i: TriageInput) -> TriageResult:
    if not i.conversation and i.failed_attempts < MAX_ATTEMPTS:
        raise ValueError("cannot triage: nobody was reached and attempts remain")
    reasons = _matched(i)
    tier = _tier_of(reasons)
    if tier is None:
        return TriageResult("green", ("G1",))
    return TriageResult(tier, tuple(reasons))


def rules_so_far(
    age: int,
    has_conditions: bool,
    answers: dict[str, str],
    emergency: bool = False,
    respondent: str = "unknown",
) -> Provisional:
    """The rules matched by what a call has recorded up to now, for showing while it runs.

    Uses the same rule list as triage(), minus the rules about how a call ended. Its reasons
    are always a subset of what triage() gives for the same answers, so its tier is never
    higher than the final one."""
    reasons = _matched(TriageInput(
        age=age, has_conditions=has_conditions, answers=dict(answers), emergency=emergency,
        respondent=respondent, conversation=True,
    ))
    reasons = [r for r in reasons if r not in PROVISIONAL_EXCLUDED]
    if "feeling" not in answers:
        # "could not say how they are" means nothing before the question has been asked
        reasons = [r for r in reasons if r != "A10"]
    return Provisional(_tier_of(reasons), tuple(reasons))
