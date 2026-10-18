from seed.names_hi import WORKERS, name_hi
from seed.regions import ALERT_STATE, DISTRICTS, LANGUAGES, NAMES, load_wards, ward_display


def test_sixteen_districts_four_per_state():
    states = [state for _, _, state in DISTRICTS]
    assert len(DISTRICTS) == 16 and len({d for d, _, _ in DISTRICTS}) == 16
    assert {s: states.count(s) for s in set(states)} == {
        "Rajasthan": 4, "Telangana": 4, "Madhya Pradesh": 4, "Maharashtra": 4,
    }
    assert ALERT_STATE == "Rajasthan" and DISTRICTS[0][0] == "churu"


def test_every_district_has_three_sourced_wards():
    wards = load_wards()
    assert set(wards) == {d for d, _, _ in DISTRICTS}
    for district_id, entry in wards.items():
        assert entry["source_urls"], district_id
        assert isinstance(entry["lat"], float) and isinstance(entry["lng"], float)
        numbers = [w["number"] for w in entry["wards"]]
        assert len(numbers) == 3 and len(set(numbers)) == 3, district_id


def test_ward_names_come_only_from_the_fixture():
    assert ward_display({"number": 12, "name": None}) == "Ward 12"
    assert ward_display({"number": 7, "name": "Subhash Nagar"}) == "Ward 7 – Subhash Nagar"


def test_names_and_languages_cover_every_state():
    for state in {s for _, _, s in DISTRICTS}:
        first, last = NAMES[state]
        assert len(first) >= 20 and len(last) >= 10
        for f in first:
            for l in last:
                assert not name_hi(f"{f} {l}").isascii()
        assert LANGUAGES[state]
    for district_id, _, _ in DISTRICTS:
        assert len(WORKERS[district_id]) == 3
