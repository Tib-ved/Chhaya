from app.risk import risk_factors, risk_score


def test_oldest_alone_with_conditions_scores_highest():
    assert risk_score(80, True, False, ["heart", "diabetes"]) == 7


def test_younger_senior_outdoor_worker():
    assert risk_score(70, False, True, []) == 4


def test_boundaries():
    assert risk_score(64, False, False, []) == 0
    assert risk_score(65, False, False, []) == 2
    assert risk_score(74, False, False, []) == 2
    assert risk_score(75, False, False, []) == 3


def test_risk_factors_in_plain_words():
    resident = {"age": 81, "lives_alone": True, "outdoor_worker": False, "conditions": ["heart", "diabetes"]}
    assert risk_factors(resident) == ["75+", "lives alone", "heart condition", "diabetes"]
    assert risk_factors({"age": 66, "outdoor_worker": True}) == ["65 to 74", "outdoor worker"]
    assert risk_factors({"age": 40}) == []
