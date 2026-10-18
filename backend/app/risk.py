def risk_score(age: int, lives_alone: bool, outdoor_worker: bool, conditions: list[str]) -> int:
    score = 0
    if age >= 75:
        score += 3
    elif age >= 65:
        score += 2
    if lives_alone:
        score += 2
    if outdoor_worker:
        score += 2
    return score + len(conditions)


CONDITION_WORDS = {
    "heart": "heart condition",
    "diabetes": "diabetes",
    "kidney": "kidney condition",
    "respiratory": "breathing condition",
    "pregnancy": "pregnancy",
    "disability": "disability",
}


def risk_factors(resident: dict) -> list[str]:
    """Why a resident is called early, in plain words, in the order the score is built."""
    factors: list[str] = []
    age = resident.get("age") or 0
    if age >= 75:
        factors.append("75+")
    elif age >= 65:
        factors.append("65 to 74")
    if resident.get("lives_alone"):
        factors.append("lives alone")
    if resident.get("outdoor_worker"):
        factors.append("outdoor worker")
    return factors + [CONDITION_WORDS.get(c, c) for c in resident.get("conditions") or []]
