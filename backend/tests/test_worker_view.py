import pytest

from app.case_actions import reassign
from app.worker_view import worker_view, workers_view
from seed.seed import ward_ids
from tests.helpers import add_visitor_call, seeded_region

ALICE = "3f2b8c1e-7a4d-4e2a-9b1c-5d6e7f8a9b0c"
WARDS = ward_ids("churu")
WORKER_2, WORKER_30, WORKER_58 = (w.replace("-ward-", "-worker-") for w in WARDS)
BIKANER_WORKER = ward_ids("bikaner")[0].replace("-ward-", "-worker-")


@pytest.fixture
def region(store, now, tz):
    store.round_id = seeded_region(store, now, tz)
    return store


def test_the_workers_of_a_district(region, now):
    workers = workers_view(region, "churu", None, now)["workers"]
    assert [(w["id"], w["name"], w["ward_name"]) for w in workers] == [
        (WORKER_2, "Sunita Kanwar", "Ward 2"), (WORKER_30, "Meena Bishnoi", "Ward 30"), (WORKER_58, "Kavita Saini", "Ward 58"),
    ]
    assert all(w["name_hi"] for w in workers)
    assert sum(w["open"] for w in workers) == 14 and sum(w["done"] for w in workers) == 5


def test_a_workers_residents_and_what_to_carry(region, now):
    view = worker_view(region, WORKER_30, None, now)
    rows = view["residents"]
    assert view["worker"]["name"] == "Meena Bishnoi" and view["warning"]["severity"] == "red"
    assert {r["ward_id"] for r in rows} == {WARDS[1]} and len(rows) == 41  # Ward 30's whole register
    to_visit = [r for r in rows if r["case_status"] == "open" and r["tier"] != "green"]
    assert view["counts"]["to_visit"] == len(to_visit)
    assert view["counts"]["done"] == sum(r["case_status"] == "visited" and r["tier"] != "green" for r in rows)
    assert view["counts"]["red_open"] == sum(r["tier"] == "red" for r in to_visit)
    assert view["counts"]["longest_wait_minutes"] == max(r["waiting_minutes"] for r in to_visit)
    for item in view["carry"]:
        assert item["count"] == sum(item["id"] in r["needs"] for r in to_visit) and item["label"]
    assert view["health_centre"]["name"] == "Ward 30 health centre"


def test_a_reassigned_case_moves_between_lists(region, now):
    before_a = worker_view(region, WORKER_2, None, now)["counts"]["to_visit"]
    before_b = worker_view(region, WORKER_30, None, now)["counts"]["to_visit"]
    reassign(region, f"{region.round_id}_r08", WORKER_2, now)  # r08 lives in Ward 30
    a, b = worker_view(region, WORKER_2, None, now), worker_view(region, WORKER_30, None, now)
    assert (a["counts"]["to_visit"], b["counts"]["to_visit"]) == (before_a + 1, before_b - 1)
    assert "r08" in {r["id"] for r in a["residents"]} and "r08" not in {r["id"] for r in b["residents"]}


def test_a_visitors_case_is_on_the_workers_list_for_them_alone(region, now, tz):
    add_visitor_call(region, region.round_id, ALICE, now, tz, ward_id=WARDS[0], district_id="churu", feeling="mild_symptoms")
    hers, others = worker_view(region, WORKER_2, ALICE, now), worker_view(region, WORKER_2, None, now)
    assert len(hers["residents"]) == len(others["residents"]) + 1
    assert [r["yours"] for r in hers["residents"]].count(True) == 1


def test_an_unknown_worker_and_a_district_without_a_warning(region, now):
    with pytest.raises(KeyError):
        worker_view(region, "nobody", None, now)
    quiet = worker_view(region, BIKANER_WORKER, None, now)
    assert quiet["warning"] is None and quiet["counts"]["to_visit"] == 0 and quiet["carry"] == []
    assert len(quiet["residents"]) == 42  # Bikaner Ward 1's whole register
