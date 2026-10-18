import threading
from datetime import timedelta

import pytest

from app.cases import mark_visited
from app.config import Settings
from app.dispatch import RecordingQueue, call_id_for, handle_call_outcome, plan_round
from app.db import MemoryStore
from app.ingest.alerts import record_alert
from app.ingest.replay import make_replay_alert
from app.models import Call, Consent
from app.report import (
    RoundCache, build_report, current_round_id, load_register, load_round, round_overview, ward_cases, ward_directory,
)
from tests.helpers import add_resident, add_visitor_call, add_ward


@pytest.fixture
def scenario(store, tz, now):
    """Four eligible residents: red, amber, unreachable, and one still queued."""
    add_ward(store)
    round_id = record_alert(store, make_replay_alert("d1", "heat", "red", now), tz).id
    add_resident(store, "sick", name="Sita", age=82, lives_alone=True)
    add_resident(store, "thirsty", name="Tara", age=66)
    add_resident(store, "silent", name="Sohan", age=61)
    add_resident(store, "waiting", name="Wasim", age=60)
    add_resident(store, "optout", consent=Consent(given=False))
    queue = RecordingQueue()
    plan_round(store, queue, round_id, now, tz)

    def finish(resident_id, status, **answers):
        call_id = f"{round_id}_{resident_id}_1"
        store.update(
            "calls",
            call_id,
            {f"answers.{q}": {"value": v, "quote": f"{q} is {v}", "at": now} for q, v in answers.items()},
        )
        handle_call_outcome(store, queue, call_id, status, now, tz)

    finish("sick", "completed", feeling="severe_symptoms", company="alone")
    finish("thirsty", "completed", feeling="ok", fluids_food="low")
    for attempt in (1, 2, 3):
        handle_call_outcome(store, queue, f"{round_id}_silent_{attempt}", "no_answer", now, tz)
    mark_visited(store, f"{round_id}_thirsty", "supplies_given", now + timedelta(minutes=90))
    return round_id


def test_current_round_id(store, scenario):
    assert current_round_id(store) == scenario


def test_current_round_id_none_when_empty(store):
    assert current_round_id(store) is None


def test_round_overview_counts(store, scenario):
    overview = round_overview(store, scenario)
    assert overview["counts"] == {
        "targeted": 4, "queued": 1, "in_call": 0, "reached": 2, "unreachable": 1,
        "red": 1, "amber": 2, "green": 0,
    }
    by_resident = {c["resident_id"]: c for c in overview["calls"]}
    assert by_resident["silent"]["attempt"] == 3
    assert by_resident["sick"]["tier"] == "red"
    assert by_resident["waiting"]["tier"] is None
    assert by_resident["sick"]["resident_name"] == "Sita"


def test_ward_cases_sorted_and_enriched(store, scenario):
    cases = ward_cases(store, "w1", scenario)
    assert [c["resident_id"] for c in cases] == ["sick", "thirsty", "silent"]
    top = cases[0]
    assert top["resident"]["name"] == "Sita"
    assert "Reported severe symptoms" in top["reason_labels"]
    assert top["quote"] == "feeling is severe_symptoms"
    assert cases[2]["quote"] == ""


def test_build_report(store, scenario):
    report = build_report(store, scenario)
    assert report["district"]["name"] == "Testpur"
    assert report["coverage"] == {"targeted": 4, "reached": 2, "unreachable": 1, "pending": 1, "not_callable": 0}
    assert report["tiers_by_ward"] == [
        {"ward_id": "w1", "name": "Ward 1", "red": 1, "amber": 2, "green": 0}
    ]
    counts = [p["count"] for p in report["problems"]]
    assert counts == sorted(counts, reverse=True)
    assert {"rule": "A8", "label": "Did not answer after three attempts", "count": 1} in report["problems"]
    assert {
        "rule": "A12", "label": "Call ended before all the questions were asked", "count": 2
    } in report["problems"]
    assert {k: report["response"][k] for k in ("visited", "open", "median_minutes_to_visit")} == {
        "visited": 1, "open": 2, "median_minutes_to_visit": 90.0,
    }
    assert [report["response"][k] for k in ("escalated", "acknowledged", "raised", "reassigned")] == [0, 0, 0, 0]
    assert [c["resident_name"] for c in report["open_red_cases"]] == ["Sita"]


def test_targeted_is_fixed_at_dispatch_when_consent_is_withdrawn(store, scenario):
    store.update("residents", "sick", {"consent.given": False})
    report = build_report(store, scenario)
    assert report["coverage"] == {"targeted": 4, "reached": 2, "unreachable": 1, "pending": 1, "not_callable": 0}
    assert round_overview(store, scenario)["counts"]["targeted"] == 4


def test_targeted_ignores_residents_added_after_dispatch(store, scenario):
    add_resident(store, "late", name="Latika")
    report = build_report(store, scenario)
    assert report["coverage"] == {"targeted": 4, "reached": 2, "unreachable": 1, "pending": 1, "not_callable": 0}
    assert round_overview(store, scenario)["counts"]["targeted"] == 4


def test_undispatched_round_has_no_targets(store, tz, now):
    add_ward(store)
    round_id = record_alert(store, make_replay_alert("d1", "heat", "red", now), tz).id
    add_resident(store, "waiting", name="Wasim")
    assert build_report(store, round_id)["coverage"] == {
        "targeted": 0, "reached": 0, "unreachable": 0, "pending": 0, "not_callable": 0,
    }
    assert round_overview(store, round_id)["counts"]["targeted"] == 0


def test_unknown_round_raises(store):
    with pytest.raises(KeyError):
        build_report(store, "nope")
    with pytest.raises(KeyError):
        round_overview(store, "nope")


def test_ward_cases_mask_the_phone_number(store, scenario):
    cases = ward_cases(store, "w1", scenario, operator=True)
    assert {c["resident"]["phone"] for c in cases} == {"+91********01"}


def test_only_the_team_gets_even_the_masked_phone_number(store, scenario, tz, now):
    add_visitor_call(store, scenario, ALICE, now, tz, feeling="mild_symptoms")
    for viewer in (None, ALICE, BOB):
        cases = ward_cases(store, "w1", scenario, viewer)
        assert cases and {c["resident"]["phone"] for c in cases} == {None}


def test_current_round_is_the_newest_opened_even_before_dispatch(store, tz, now):
    add_ward(store)
    add_resident(store, "r1")
    yesterday = now - timedelta(days=1)
    old = record_alert(store, make_replay_alert("d1", "heat", "red", yesterday), tz)
    plan_round(store, RecordingQueue(), old.id, yesterday, tz)
    new = record_alert(store, make_replay_alert("d1", "heat", "red", now), tz)
    assert new.id != old.id
    assert store.get("rounds", new.id)["started_at"] is None
    assert current_round_id(store) == new.id


def test_current_round_falls_back_to_started_at_then_id(store, now):
    base = {"alert_id": "a", "district_id": "d1", "hazard": "heat", "status": "calling"}
    store.set("rounds", "z_old", {**base, "id": "z_old", "started_at": now - timedelta(days=2)})
    store.set("rounds", "a_new", {**base, "id": "a_new", "started_at": now})
    assert current_round_id(store) == "a_new"
    store.set("rounds", "b_bare", {**base, "id": "b_bare"})
    store.set("rounds", "c_bare", {**base, "id": "c_bare", "opened_at": None, "started_at": None})
    assert current_round_id(store) == "a_new"
    store.set("rounds", "m_open", {**base, "id": "m_open", "opened_at": now + timedelta(hours=1)})
    assert current_round_id(store) == "m_open"


def test_consenting_resident_with_unusable_phone_is_reported_not_callable(store, tz, now):
    add_ward(store)
    round_id = record_alert(store, make_replay_alert("d1", "heat", "red", now), tz).id
    add_resident(store, "ok", name="Omkar")
    add_resident(store, "badphone", name="Bina", phone="98765")
    add_resident(store, "optout", phone="98765", consent=Consent(given=False))
    add_resident(store, "elsewhere", phone="98765", district_id="d2")
    assert build_report(store, round_id)["coverage"]["not_callable"] == 1
    plan_round(store, RecordingQueue(), round_id, now, tz)
    assert build_report(store, round_id)["coverage"] == {
        "targeted": 1, "reached": 0, "unreachable": 0, "pending": 1, "not_callable": 1,
    }


def test_dialing_call_is_counted_as_queued(store, scenario):
    store.update("calls", f"{scenario}_waiting_1", {"status": "dialing"})
    counts = round_overview(store, scenario)["counts"]
    assert counts["queued"] == 1
    assert counts["in_call"] == 0


def test_a_resident_whose_three_calls_connected_but_reached_nobody_is_unreachable(store, tz, now):
    add_ward(store)
    round_id = record_alert(store, make_replay_alert("d1", "heat", "red", now), tz).id
    add_resident(store, "voicemail", name="Vimal")
    queue = RecordingQueue()
    plan_round(store, queue, round_id, now, tz)
    for attempt in (1, 2, 3):
        # the audio stream connected each time, but nobody was on the line
        handle_call_outcome(store, queue, f"{round_id}_voicemail_{attempt}", "dropped", now, tz)
    counts = round_overview(store, round_id)["counts"]
    assert (counts["reached"], counts["unreachable"]) == (0, 1)
    coverage = build_report(store, round_id)["coverage"]
    assert (coverage["reached"], coverage["unreachable"]) == (0, 1)
    cases = ward_cases(store, "w1", round_id)
    assert [(c["resident_id"], c["tier"], c["reasons"]) for c in cases] == [("voicemail", "amber", ["A8"])]


ALICE = "visitor-aaaaaaaaaaaa"
BOB = "visitor-bbbbbbbbbbbb"


def test_a_visitors_records_are_shown_only_to_that_visitor(store, scenario, tz, now):
    before = {
        "overview": round_overview(store, scenario),
        "cases": ward_cases(store, "w1", scenario),
        "report": build_report(store, scenario),
    }
    add_visitor_call(store, scenario, ALICE, now, tz, feeling="mild_symptoms")
    for viewer in (None, BOB):
        assert round_overview(store, scenario, viewer) == before["overview"]
        assert ward_cases(store, "w1", scenario, viewer) == before["cases"]
        assert build_report(store, scenario, viewer) == before["report"]

    overview = round_overview(store, scenario, ALICE)
    assert overview["counts"]["targeted"] == 5
    assert overview["counts"]["reached"] == 3
    assert overview["counts"]["amber"] == 3
    assert [row["resident_id"] for row in overview["calls"] if row["yours"]] == ["v-1"]

    cases = ward_cases(store, "w1", scenario, ALICE)
    mine = [c for c in cases if c["yours"]]
    assert [c["resident_id"] for c in mine] == ["v-1"]
    assert mine[0]["can_mark"] is True
    assert all("visitor_id" not in c for c in cases)

    report = build_report(store, scenario, ALICE)
    assert report["includes_yours"] is True
    assert report["coverage"]["targeted"] == 5
    assert before["report"]["includes_yours"] is False


def three_views(store, round_id, viewer=None) -> dict:
    return {
        "overview": round_overview(store, round_id, viewer),
        "cases": ward_cases(store, "w1", round_id, viewer),
        "report": build_report(store, round_id, viewer),
    }


def add_ticket(store, round_id, visitor_id, now, resident_id="v-ticket") -> str:
    """A visitor's call that was asked for and never taken: its resident and a queued call."""
    add_resident(store, resident_id, name="Visitor Character", phone="", visitor_id=visitor_id)
    call = Call(
        id=call_id_for(round_id, resident_id, 1), round_id=round_id, resident_id=resident_id,
        channel="browser", visitor_id=visitor_id, created_at=now,
    )
    store.set("calls", call.id, call.model_dump())
    return call.id


def test_a_ticket_nobody_used_changes_nothing_the_visitor_sees(store, scenario, tz, now):
    anonymous = three_views(store, scenario)
    add_ticket(store, scenario, ALICE, now)
    assert three_views(store, scenario, ALICE) == anonymous


def test_a_call_that_reached_nobody_changes_nothing_the_visitor_sees(store, scenario, tz, now):
    anonymous = three_views(store, scenario)
    call_id = add_visitor_call(store, scenario, ALICE, now, tz)  # nothing was said
    assert store.get("calls", call_id)["status"] == "no_answer"
    assert three_views(store, scenario, ALICE) == anonymous


def test_of_two_finished_calls_a_visitor_sees_only_the_later_one(store, scenario, tz, now):
    anonymous = three_views(store, scenario)
    add_visitor_call(store, scenario, ALICE, now, tz, resident_id="v-1", feeling="severe_symptoms")
    add_visitor_call(store, scenario, ALICE, now + timedelta(minutes=5), tz, resident_id="v-2", feeling="mild_symptoms")
    add_ticket(store, scenario, ALICE, now + timedelta(minutes=9))  # and a newer ticket, never taken
    mine = three_views(store, scenario, ALICE)

    assert [row["resident_id"] for row in mine["overview"]["calls"] if row["yours"]] == ["v-2"]
    assert len(mine["overview"]["calls"]) == len(anonymous["overview"]["calls"]) + 1
    counts, before = mine["overview"]["counts"], anonymous["overview"]["counts"]
    assert counts == {**before, "targeted": before["targeted"] + 1, "reached": before["reached"] + 1, "amber": before["amber"] + 1}

    assert [c["resident_id"] for c in mine["cases"] if c["yours"]] == ["v-2"]
    assert len(mine["cases"]) == len(anonymous["cases"]) + 1

    coverage, before = mine["report"]["coverage"], anonymous["report"]["coverage"]
    assert coverage == {**before, "targeted": before["targeted"] + 1, "reached": before["reached"] + 1}
    assert mine["report"]["includes_yours"] is True
    assert mine["report"]["open_red_cases"] == anonymous["report"]["open_red_cases"]  # the first call's red case is not shown


def test_an_unanswered_call_does_not_hide_the_finished_one_before_it(store, scenario, tz, now):
    add_visitor_call(store, scenario, ALICE, now, tz, resident_id="v-1", feeling="mild_symptoms")
    alone = three_views(store, scenario, ALICE)
    later = add_visitor_call(store, scenario, ALICE, now + timedelta(minutes=5), tz, resident_id="v-2")
    assert store.get("calls", later)["status"] == "no_answer"
    assert three_views(store, scenario, ALICE) == alone
    assert [row["resident_id"] for row in alone["overview"]["calls"] if row["yours"]] == ["v-1"]


def test_a_visitors_call_in_progress_is_the_one_shown(store, scenario, tz, now):
    add_visitor_call(store, scenario, ALICE, now, tz, resident_id="v-1", feeling="mild_symptoms")
    before = round_overview(store, scenario, ALICE)["counts"]
    talking = add_ticket(store, scenario, ALICE, now + timedelta(minutes=5), resident_id="v-2")
    store.update("calls", talking, {"status": "in_progress", "started_at": now + timedelta(minutes=5)})
    overview = round_overview(store, scenario, ALICE)
    assert [(row["resident_id"], row["status"]) for row in overview["calls"] if row["yours"]] == [("v-2", "in_progress")]
    assert overview["counts"] == {**before, "in_call": 1, "reached": before["reached"] - 1, "amber": before["amber"] - 1}
    assert [c for c in ward_cases(store, "w1", scenario, ALICE) if c["yours"]] == []  # the earlier call's case went with it


def test_two_calls_made_at_the_same_moment_are_told_apart_by_id(store, scenario, tz, now):
    for resident_id in ("v-b", "v-a"):
        add_visitor_call(store, scenario, ALICE, now, tz, resident_id=resident_id, feeling="mild_symptoms")
    rows = round_overview(store, scenario, ALICE)["calls"]
    assert [row["resident_id"] for row in rows if row["yours"]] == ["v-b"]


def test_a_visitors_resident_is_never_counted_as_not_callable(store, scenario, tz, now):
    add_visitor_call(store, scenario, ALICE, now, tz, feeling="ok")
    for viewer in (None, ALICE, BOB):
        assert build_report(store, scenario, viewer)["coverage"]["not_callable"] == 0


def test_only_the_team_or_the_owner_may_mark_a_case(store, scenario):
    for_visitor = ward_cases(store, "w1", scenario)
    assert [c["can_mark"] for c in for_visitor] == [False, False, False]
    for_team = {c["resident_id"]: c["can_mark"] for c in ward_cases(store, "w1", scenario, operator=True)}
    assert for_team == {"sick": True, "thirsty": False, "silent": True}  # thirsty was visited already


def test_ward_cases_carry_times_and_the_outcome_label(store, scenario, now):
    by_resident = {c["resident_id"]: c for c in ward_cases(store, "w1", scenario)}
    assert by_resident["sick"]["opened_at"] == now
    assert by_resident["thirsty"]["outcome"] == "supplies_given"
    assert by_resident["thirsty"]["outcome_label"] == "Water, ORS or supplies given"
    assert by_resident["thirsty"]["visited_at"] == now + timedelta(minutes=90)
    assert by_resident["sick"]["outcome_label"] is None


def test_overview_rows_are_in_call_order_with_the_reasons_for_it(store, scenario):
    rows = round_overview(store, scenario)["calls"]
    assert [row["resident_id"] for row in rows] == ["sick", "thirsty", "silent", "waiting"]
    top = rows[0]
    assert (top["age"], top["ward_id"], top["ward_name"]) == (82, "w1", "Ward 1")
    assert top["risk_factors"] == ["75+", "lives alone"]
    assert top["risk_score"] == 5
    assert top["yours"] is False


def test_time_to_visit_is_measured_from_when_the_case_opened(store, scenario, now):
    store.update("cases", f"{scenario}_thirsty", {"opened_at": now + timedelta(minutes=30)})
    assert build_report(store, scenario)["response"]["median_minutes_to_visit"] == 60.0


def test_time_to_visit_falls_back_to_the_round_start_for_an_older_case(store, scenario):
    store.update("cases", f"{scenario}_thirsty", {"opened_at": None})
    assert build_report(store, scenario)["response"]["median_minutes_to_visit"] == 90.0


def test_a_case_without_a_known_ward_gets_its_own_row_so_totals_agree(store, scenario):
    store.update("cases", f"{scenario}_silent", {"ward_id": "ghost"})
    rows = build_report(store, scenario)["tiers_by_ward"]
    assert rows[-1] == {"ward_id": None, "name": "Unassigned ward", "red": 0, "amber": 1, "green": 0}
    overall = round_overview(store, scenario)["counts"]
    for tier in ("red", "amber", "green"):
        assert sum(row[tier] for row in rows) == overall[tier]


class CountingStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0
        self.asked: list[tuple] = []  # (collection, the fields a query named)

    def get(self, collection, doc_id):
        self.reads += 1
        self.asked.append((collection, ("id",)))
        return super().get(collection, doc_id)

    def query(self, collection, **equals):
        self.reads += 1
        self.asked.append((collection, tuple(sorted(equals))))
        return super().query(collection, **equals)

    def forget(self) -> None:
        self.reads, self.asked = 0, []


@pytest.mark.parametrize("residents", [3, 40])
def test_each_view_reads_each_collection_once_however_many_residents(tz, now, residents):
    store = CountingStore()
    add_ward(store)
    round_id = record_alert(store, make_replay_alert("d1", "heat", "red", now), tz).id
    for n in range(residents):
        add_resident(store, f"r{n:02d}")
    queue = RecordingQueue()
    plan_round(store, queue, round_id, now, tz)
    for n in range(residents):
        call_id = f"{round_id}_r{n:02d}_1"
        store.update("calls", call_id, {"answers.feeling": {"value": "mild_symptoms", "quote": "x", "at": now}})
        handle_call_outcome(store, queue, call_id, "completed", now, tz)

    for view, most in (
        (lambda: round_overview(store, round_id), 6),
        (lambda: ward_cases(store, "w1", round_id), 5),
        (lambda: build_report(store, round_id), 7),
    ):
        store.forget()
        view()
        assert store.reads <= most


class Ticking:
    """A clock that moves only when told to."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def counted(tz, now):
    """A dispatched round with three residents, one of them reached. Returns (store, round_id)."""
    store = CountingStore()
    add_ward(store)
    round_id = record_alert(store, make_replay_alert("d1", "heat", "red", now), tz).id
    for n in range(3):
        add_resident(store, f"r{n}")
    queue = RecordingQueue()
    plan_round(store, queue, round_id, now, tz)
    store.update("calls", f"{round_id}_r0_1", {"answers.feeling": {"value": "mild_symptoms", "quote": "x", "at": now}})
    handle_call_outcome(store, queue, f"{round_id}_r0_1", "completed", now, tz)
    store.forget()
    return store, round_id


def views_with(store, round_id, cache, viewer=None) -> dict:
    return {
        "overview": round_overview(store, round_id, viewer, cache=cache),
        "cases": ward_cases(store, "w1", round_id, viewer, cache=cache),
        "report": build_report(store, round_id, viewer, cache=cache),
    }


def test_with_the_cache_on_a_second_read_does_not_touch_the_store(counted):
    store, round_id = counted
    clock = Ticking()
    cache = RoundCache(2, clock)
    first = views_with(store, round_id, cache)
    assert store.reads == 7  # the round, its four collections, its alert and its district: once each
    store.forget()
    clock.now += 1.9
    assert views_with(store, round_id, cache) == first
    assert store.asked == []


def test_after_the_window_the_round_is_read_again(counted, tz, now):
    store, round_id = counted
    clock = Ticking()
    cache = RoundCache(2, clock)
    assert round_overview(store, round_id, cache=cache)["counts"]["reached"] == 1
    store.update("calls", f"{round_id}_r1_1", {"answers.feeling": {"value": "ok", "quote": "x", "at": now}})
    handle_call_outcome(store, RecordingQueue(), f"{round_id}_r1_1", "completed", now, tz)
    clock.now += 1.9
    assert round_overview(store, round_id, cache=cache)["counts"]["reached"] == 1  # still the kept copy
    assert round_overview(store, round_id)["counts"]["reached"] == 2  # without the cache: the store
    clock.now += 0.1
    assert round_overview(store, round_id, cache=cache)["counts"]["reached"] == 2


def test_a_visitors_own_case_appears_at_once_despite_the_cache(counted, tz, now):
    store, round_id = counted
    cache = RoundCache(60, Ticking())
    before = views_with(store, round_id, cache, ALICE)
    assert before == views_with(store, round_id, None)  # no call of her own yet
    add_visitor_call(store, round_id, ALICE, now, tz, feeling="mild_symptoms")
    store.forget()
    mine = views_with(store, round_id, cache, ALICE)
    assert [row["resident_id"] for row in mine["overview"]["calls"] if row["yours"]] == ["v-1"]
    assert [c["resident_id"] for c in mine["cases"] if c["yours"]] == ["v-1"]
    assert mine["report"]["includes_yours"] is True
    assert mine["report"]["coverage"]["targeted"] == before["report"]["coverage"]["targeted"] + 1
    # and it was found by asking for her records, not by reading everyone's
    assert set(store.asked) == {("calls", ("visitor_id",)), ("cases", ("visitor_id",)), ("residents", ("visitor_id",))}


def test_one_visitors_records_never_enter_the_cache(counted, tz, now):
    store, round_id = counted
    anonymous = views_with(store, round_id, None)
    add_visitor_call(store, round_id, ALICE, now, tz, feeling="severe_symptoms")
    cache = RoundCache(60, Ticking())
    hers = views_with(store, round_id, cache, ALICE)  # the visitor with a call reads first
    assert hers != anonymous and hers["report"]["includes_yours"] is True
    for viewer in (BOB, None):
        assert views_with(store, round_id, cache, viewer) == anonymous
    assert "v-1" not in repr(cache._entries) and ALICE not in repr(cache._entries)
    assert views_with(store, round_id, cache, ALICE) == hers


def test_a_viewer_without_an_id_makes_no_query_by_visitor(counted):
    store, round_id = counted
    for cache in (None, RoundCache(0), RoundCache(60, Ticking())):
        store.forget()
        views_with(store, round_id, cache, None)
        views_with(store, round_id, cache, "")
        assert all("visitor_id" not in fields for _, fields in store.asked)


def test_two_parts_load_at_the_same_time_and_one_part_loads_once():
    cache = RoundCache(60, Ticking())
    both_inside = threading.Barrier(2, timeout=5)
    loads = []

    def slow(part):
        def load():
            loads.append(part)
            both_inside.wait()  # passes only if the other part's load is running too
            return part
        return load

    results = {}
    readers = [threading.Thread(target=lambda p=p: results.setdefault(p, cache.get("r1", p, slow(p)))) for p in ("a", "b")]
    for reader in readers:
        reader.start()
    for reader in readers:
        reader.join(5)
    assert results == {"a": "a", "b": "b"}

    started, release = threading.Event(), threading.Event()
    calls = []

    def held():
        calls.append(1)
        started.set()
        release.wait(5)
        return "c"

    first = threading.Thread(target=lambda: results.setdefault("c1", cache.get("r1", "c", held)))
    first.start()
    started.wait(5)
    second = threading.Thread(target=lambda: results.setdefault("c2", cache.get("r1", "c", held)))
    second.start()
    release.set()
    first.join(5)
    second.join(5)
    assert results["c1"] == results["c2"] == "c" and calls == [1]  # the second reader waited for the first load


def test_with_the_cache_off_every_read_goes_to_the_store_and_costs_what_it_did(counted):
    store, round_id = counted
    assert Settings(_env_file=None).view_cache_seconds == 0
    off = RoundCache(0)
    for view, most in (
        (lambda cache: round_overview(store, round_id, cache=cache), 6),
        (lambda cache: ward_cases(store, "w1", round_id, cache=cache), 5),
        (lambda cache: build_report(store, round_id, cache=cache), 7),
    ):
        for cache in (None, off, off):
            store.forget()
            view(cache)
            assert 0 < store.reads <= most
    assert off._entries == {}


def test_a_visitor_with_no_call_costs_one_more_read_and_one_with_a_call_three(counted, tz, now):
    store, round_id = counted
    cache = RoundCache(60, Ticking())
    round_overview(store, round_id, cache=cache)
    store.forget()
    round_overview(store, round_id, BOB, cache=cache)
    assert store.asked == [("calls", ("visitor_id",))]
    add_visitor_call(store, round_id, BOB, now, tz, feeling="ok")
    store.forget()
    round_overview(store, round_id, BOB, cache=cache)
    assert store.reads == 3


def test_with_a_register_cache_a_shared_load_does_not_read_visitors_residents(counted, tz, now):
    store, round_id = counted
    add_visitor_call(store, round_id, ALICE, now, tz, feeling="mild_symptoms")
    registers = RoundCache(86400, Ticking())
    load_register(store, "d1", registers)  # what the region screens keep for a day
    plain = {viewer: views_with(store, round_id, None, viewer) for viewer in (None, ALICE)}
    store.forget()
    for viewer in (None, ALICE):
        kept = {
            "overview": round_overview(store, round_id, viewer, registers=registers),
            "cases": ward_cases(store, "w1", round_id, viewer, registers=registers),
            "report": build_report(store, round_id, viewer, registers=registers),
        }
        assert kept == plain[viewer]
    assert ("residents", ("district_id",)) not in store.asked
    assert "v-1" not in load_round(store, round_id, registers=registers).residents


def test_the_cache_drops_an_expired_round_and_keeps_nothing_of_a_failed_load():
    clock = Ticking()
    cache = RoundCache(2, clock)
    loads = []

    def load(value):
        return lambda: loads.append(value) or value

    def gone():
        raise KeyError("rounds/round-3")

    assert cache.get("round-1", "round", load("a")) == "a"
    assert cache.get("round-1", "alerts", load("b")) == "b"
    assert cache.get("round-1", "round", load("never")) == "a"
    assert cache.get("round-2", "round", load("c")) == "c"  # the demo was rebuilt; the old round is still fresh
    assert set(cache._entries) == {("round-1", "round"), ("round-1", "alerts"), ("round-2", "round")}
    clock.now += 2
    assert cache.get("round-1", "round", load("d")) == "d"  # expired, so read again
    assert set(cache._entries) == {("round-1", "round")}
    with pytest.raises(KeyError):
        cache.get("round-3", "round", gone)
    assert set(cache._entries) == {("round-1", "round")}
    assert loads == ["a", "b", "c", "d"]


def test_readers_who_arrive_together_wait_for_one_load():
    cache = RoundCache(60, Ticking())
    loads, started, release = [], threading.Event(), threading.Event()

    def slow_load():
        loads.append(1)
        started.set()
        assert release.wait(5)
        return "the round"

    results = []
    readers = [threading.Thread(target=lambda: results.append(cache.get("r", "round", slow_load))) for _ in range(8)]
    for reader in readers:
        reader.start()
    assert started.wait(5)
    release.set()
    for reader in readers:
        reader.join(5)
    assert results == ["the round"] * 8 and len(loads) == 1


def test_ward_directory_lists_workers_and_facilities_without_phone_numbers(store, scenario):
    store.set(
        "facilities", "cool-1",
        {"id": "cool-1", "name": "Ward 1 cooling centre", "type": "cooling_centre", "ward_id": "w1", "lat": 21.1, "lng": 79.1},
    )
    assert ward_directory(store, scenario) == [
        {
            "id": "w1", "name": "Ward 1", "lat": 21.1, "lng": 79.1, "worker_name": "Asha One",
            "facilities": [
                {"id": "cool-1", "name": "Ward 1 cooling centre", "type": "cooling_centre", "lat": 21.1, "lng": 79.1}
            ],
        }
    ]


def test_the_report_counts_what_workers_and_the_officer_did(store, scenario):
    from app.case_actions import acknowledge, escalate

    fresh = build_report(store, scenario)["response"]
    assert (fresh["escalated"], fresh["acknowledged"], fresh["raised"], fresh["reassigned"]) == (0, 0, 0, 0)
    case = next(c for c in store.query("cases", round_id=scenario) if c["status"] == "open")
    escalate(store, case["id"], "supplies", "", case["updated_at"])
    acknowledge(store, case["id"], "", case["updated_at"])
    done = build_report(store, scenario)["response"]
    assert (done["escalated"], done["acknowledged"]) == (1, 1)


def test_the_cache_keeps_several_rounds_at_once():
    clock = Ticking()
    cache = RoundCache(60, clock)
    loads = []
    for round_id in ("a", "b", "c", "d", "a", "b"):
        cache.get(round_id, "part", lambda r=round_id: loads.append(r) or r)
    assert loads == ["a", "b", "c", "d"]


def test_an_expired_round_is_dropped_when_another_is_stored():
    clock = Ticking()
    cache = RoundCache(60, clock)
    cache.get("a", "part", lambda: 1)
    clock.now += 61
    cache.get("b", "part", lambda: 2)
    assert list(cache._entries) == [("b", "part")]
