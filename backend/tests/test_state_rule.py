from app.ingest.state_rule import alerts_by_state, reason_text


def d(i, state, peak, hour=15):
    return {"district_id": i, "state": state, "peak_c": peak, "peak_hour": hour}


def test_hottest_is_red_and_above_mean_is_orange_in_every_state():
    got = {a["district_id"]: a for a in alerts_by_state([
        d("churu", "Rajasthan", 36.8), d("sikar", "Rajasthan", 35.1), d("bikaner", "Rajasthan", 33.0), d("jhunjhunu", "Rajasthan", 32.7),
        d("nagpur", "Maharashtra", 34.0), d("pune", "Maharashtra", 30.0), d("nashik", "Maharashtra", 31.0), d("thane", "Maharashtra", 32.0),
    ])}
    assert got["churu"]["severity"] == "red" and got["churu"]["reason"]["rule"] == "hottest_in_state"
    assert got["sikar"]["severity"] == "orange"
    assert "bikaner" not in got and "jhunjhunu" not in got
    assert got["nagpur"]["severity"] == "red"
    assert got["churu"]["reason"]["state_mean_c"] == round((36.8 + 35.1 + 33.0 + 32.7) / 4, 1)


def test_a_tie_for_hottest_goes_to_the_earlier_peak_then_the_id():
    got = alerts_by_state([d("b", "S", 35.0, 14), d("a", "S", 35.0, 15), d("c", "S", 30.0)])
    reds = [a["district_id"] for a in got if a["severity"] == "red"]
    assert reds == ["b"]
    got = alerts_by_state([d("b", "S", 35.0, 14), d("a", "S", 35.0, 14), d("c", "S", 30.0)])
    assert [a["district_id"] for a in got if a["severity"] == "red"] == ["a"]


def test_districts_without_data_are_left_out_and_a_state_needs_two():
    got = alerts_by_state([d("a", "S", 35.0), d("b", "S", None, None), d("x", "T", 33.0)])
    assert got == []  # S has one district with data, T has one


def test_reason_text_reads_in_one_line():
    red = alerts_by_state([d("churu", "Rajasthan", 36.8), d("sikar", "Rajasthan", 34.4)])[0]
    assert reason_text(red["reason"], "Churu") == "Hottest in Rajasthan today: 36.8 °C at 15:00, 1.2 °C above the state average"


def test_a_district_at_the_rounded_mean_gets_no_orange():
    # mean 33.96, which the text gives as 34.0: a district at 34.0 is not above that average
    got = {a["district_id"]: a for a in alerts_by_state([d("a", "S", 36.0), d("b", "S", 34.0), d("c", "S", 31.88)])}
    assert "b" not in got and got["a"]["reason"]["state_mean_c"] == 34.0
    for alert in got.values():
        assert "0.0 °C above" not in reason_text(alert["reason"], alert["district_id"])
