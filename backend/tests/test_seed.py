import re
from collections import Counter

import pytest

from app.dispatch import eligible
from app.models import Resident
from app.risk import risk_score
from seed.seed import DISTRICT_ID, build_seed, load_seed, ward_ids


def test_shape():
    data = build_seed([])
    assert len(data["districts"]) == 1
    assert len(data["wards"]) == 3
    assert len(data["workers"]) == 3
    assert len(data["facilities"]) == 6
    sizes = Counter(r["ward_id"] for r in data["residents"])
    assert set(sizes) == set(ward_ids("churu")) and all(30 <= n <= 50 for n in sizes.values())
    assert [r["id"] for r in data["residents"]][:3] == ["r01", "r02", "r03"]


def test_residents_are_valid():
    for raw in build_seed([])["residents"]:
        resident = Resident(**raw)
        assert resident.district_id == DISTRICT_ID
        assert resident.risk_score == risk_score(
            resident.age, resident.lives_alone, resident.outdoor_worker, resident.conditions
        )


def test_all_five_languages_present():
    assert {r["language"] for r in build_seed([])["residents"][:30]} == {"hi", "en", "mr", "gu", "mwr"}


def test_deterministic():
    assert build_seed([]) == build_seed([])


def test_team_phones_replace_first_residents():
    data = build_seed(["+910000000901", "+910000000902"])
    assert [r["phone"] for r in data["residents"][:2]] == ["+910000000901", "+910000000902"]
    assert data["residents"][2]["phone"] == ""


def test_every_ward_has_an_outdoor_worker():
    residents = build_seed([])["residents"]
    for ward_id in ward_ids("churu"):
        assert any(r["outdoor_worker"] for r in residents if r["ward_id"] == ward_id)


def test_load_seed_writes_every_document(store):
    load_seed(store, build_seed([]))
    data = build_seed([])
    assert len(store.query("residents", district_id=DISTRICT_ID)) == len(data["residents"])
    first = ward_ids("churu")[0]
    assert store.get("wards", first)["worker_id"] == "churu-worker-" + first.rsplit("-", 1)[1]


# "0000000001" is a deliberately malformed, synthetic value (no leading plus sign).
@pytest.mark.parametrize(
    "bad", ["0000000001", "+0100000001", "+91 00000 00001", "+910000000001" + chr(10), "+9100", ""]
)
def test_malformed_team_phone_is_refused_and_named(bad):
    with pytest.raises(ValueError) as error:
        build_seed(["+910000000901", bad])
    assert repr(bad) in str(error.value)


def test_more_than_thirty_team_phones_are_refused():
    phones = [f"+9100000009{n:02d}" for n in range(31)]
    assert build_seed(phones[:30])["residents"][29]["phone"] == phones[29]
    with pytest.raises(ValueError, match="30"):
        build_seed(phones)


def test_reset_removes_everything_a_demo_day_made_and_keeps_the_register(store, tz, now):
    from app.models import VISITOR_USAGE
    from app.report import round_overview
    from seed.seed import build_seed, load_seed, reset_district
    from tests.helpers import add_resident, add_visitor_call, add_ward

    from tests.scripted_round import scripted_round

    # Nobody in the seed can be dialled, so the demo round needs team phones to have calls.
    load_seed(store, build_seed([f"+9199999{n:05d}" for n in range(30)]))
    demo = scripted_round(store, "churu", now, tz)
    add_visitor_call(store, demo, "visitor-aaaaaaaaaaaa", now, tz, resident_id="v-1", ward_id=ward_ids("churu")[0], feeling="ok")
    store.update("residents", "v-1", {"district_id": "churu"})
    store.set(VISITOR_USAGE, "u1", {"id": "u1", "day": "20260520"})
    add_ward(store)  # another district, with its own round, which must survive
    add_resident(store, "other")
    store.set("rounds", "d1_heat_20260520", {"id": "d1_heat_20260520", "district_id": "d1", "alert_id": "a-d1", "status": "open"})
    store.set("alerts", "a-d1", {"id": "a-d1", "district_id": "d1"})

    deleted = reset_district(store, "churu")

    assert deleted["rounds"] == 1 and deleted["alerts"] == 1
    assert deleted["calls"] >= 31 and deleted["cases"] >= 25
    assert deleted["residents"] == 1  # only the visitor's temporary one
    assert store.query("calls") == [] and store.query("cases") == []
    assert store.get("demo", "current") is None and store.query(VISITOR_USAGE) == []
    assert len(store.query("residents", district_id="churu")) == len(build_seed([])["residents"])
    assert store.get("rounds", "d1_heat_20260520") is not None and store.get("alerts", "a-d1") is not None
    assert store.get("residents", "other") is not None

    load_seed(store, build_seed([]))
    assert reset_district(store, "churu") == {"rounds": 0, "alerts": 0, "calls": 0, "cases": 0, "residents": 0, "usage": 0, "demo": 0}


from seed.regions import DISTRICTS, load_wards
from seed.seed import build_all, ward_ids

# Churu's scripted thirty as the seed made them before the wards were real (r01 first).
SCRIPTED_AGES = [78, 87, 77, 66, 77, 41, 78, 62, 75, 88, 74, 81, 41, 79, 83, 77, 68, 65, 65, 51, 79, 87, 80, 65, 63, 64, 47, 63, 60, 84]
SCRIPTED_LANGUAGE_INITIALS = "mhmmhmhhhhgegmeemhmgmhhmhhmhhh"
SCRIPTED_RISK = [3, 6, 3, 6, 4, 4, 6, 3, 3, 4, 4, 6, 3, 5, 6, 5, 3, 4, 4, 2, 3, 5, 3, 6, 2, 1, 3, 1, 4, 3]


def test_sixteen_districts_three_wards_each():
    data = build_all([])
    assert [d["id"] for d in data["districts"]] == [d for d, _, _ in DISTRICTS]
    assert len(data["wards"]) == 48 and len(data["workers"]) == 48 and len(data["facilities"]) == 96
    for district_id, _, _ in DISTRICTS:
        numbers = [w["number"] for w in load_wards()[district_id]["wards"]]
        assert ward_ids(district_id) == [f"{district_id}-ward-{n}" for n in numbers]


def test_every_ward_has_thirty_to_fifty_residents():
    residents = build_all([])["residents"]
    sizes = Counter(r["ward_id"] for r in residents)
    assert len(sizes) == 48 and all(30 <= size <= 50 for size in sizes.values()), sizes
    assert len({r["id"] for r in residents}) == len(residents)


def test_ids_follow_the_district():
    data = build_all([])
    for ward in data["wards"]:
        number = ward["id"].rsplit("-", 1)[1]
        assert ward["worker_id"] == f"{ward['district_id']}-worker-{number}"
    for resident in data["residents"]:
        if resident["district_id"] == "churu":
            assert re.fullmatch(r"r\d{2,3}", resident["id"])
        else:
            number = resident["ward_id"].rsplit("-", 1)[1]
            assert re.fullmatch(rf"{resident['district_id']}-{number}-\d{{2}}", resident["id"])


def test_only_rajasthan_can_be_warned():
    for district in build_all([])["districts"]:
        assert district.get("register_only", False) is (district["state"] != "Rajasthan"), district["id"]


def test_nobody_in_the_seed_can_be_dialled():
    data = build_all([])
    assert not any(eligible(r) for r in data["residents"])
    assert all(r["phone"] == "" and r["emergency_contact"]["phone"] == "" for r in data["residents"])
    assert all(w["phone"] == "" for w in data["workers"])


def test_churus_first_thirty_are_the_scripted_people():
    churu = [r for r in build_all([])["residents"] if r["district_id"] == "churu"][:30]
    assert [r["id"] for r in churu] == [f"r{n:02d}" for n in range(1, 31)]
    assert [r["age"] for r in churu] == SCRIPTED_AGES
    assert "".join(r["language"][0] for r in churu) == SCRIPTED_LANGUAGE_INITIALS
    assert [r["risk_score"] for r in churu] == SCRIPTED_RISK
    assert churu[0]["name"] == "Sunita Sharma" and churu[13]["name"] == "Ganesh Prajapat" and churu[13]["lives_alone"] is True
    wards = ward_ids("churu")
    assert [r["ward_id"] for r in churu[:6]] == [wards[0], wards[1], wards[2]] * 2


def test_names_fit_the_state_and_have_devanagari():
    data = build_all([])
    state_of = {d["id"]: d["state"] for d in data["districts"]}
    for person in data["residents"] + data["workers"]:
        assert person["name_hi"] and not person["name_hi"].isascii(), person["id"]
    telugu = [r for r in data["residents"] if state_of[r["district_id"]] == "Telangana"]
    assert Counter(r["language"] for r in telugu).most_common(1)[0][0] == "te"


def test_team_phones_replace_churus_first_numbers():
    data = build_all(["+919999900001", "+919999900002"])
    assert [r["phone"] for r in data["residents"][:2]] == ["+919999900001", "+919999900002"]


def test_the_whole_register_is_deterministic():
    assert build_all([]) == build_all([])


def test_a_reseed_removes_the_old_register_and_keeps_visitors(store):
    from seed.seed import load_seed, prune_register

    store.set("wards", "ward-a", {"id": "ward-a", "name": "Ward A", "district_id": "churu"})
    store.set("workers", "worker-a", {"id": "worker-a", "name": "Old"})
    store.set("facilities", "cool-a", {"id": "cool-a", "ward_id": "ward-a"})
    store.set("residents", "old", {"id": "old", "district_id": "churu"})
    store.set("residents", "v-1", {"id": "v-1", "district_id": "churu", "visitor_id": "visitor-aaaaaaaaaaaa"})
    data = build_all([])
    load_seed(store, data)

    deleted = prune_register(store, data)

    assert deleted == {"districts": 0, "wards": 1, "workers": 1, "facilities": 1, "residents": 1}
    assert store.get("wards", "ward-a") is None and store.get("residents", "v-1") is not None
    assert len(store.query("wards")) == 48
