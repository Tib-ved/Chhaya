import json

import pytest

from app.case_actions import raise_to_red
from app.resident_view import resident_view
from seed.seed import ward_ids
from tests.helpers import add_visitor_call, seeded_region

ALICE, BOB = "3f2b8c1e-7a4d-4e2a-9b1c-5d6e7f8a9b0c", "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
WARD_2 = ward_ids("churu")[0]
BIKANER_RESIDENT = "bikaner-1-01"  # the first resident of Bikaner's first ward


def _keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _keys(item)


@pytest.fixture
def region(store, now, tz):
    store.round_id = seeded_region(store, now, tz)
    return store


def test_a_reached_resident_shows_why_and_what_was_said(region, now):
    view = resident_view(region, "r08", None, False, now)  # water running short
    assert view["resident"]["id"] == "r08" and view["resident"]["district_name"] == "Churu"
    assert view["resident"]["worker_name"] == "Meena Bishnoi"
    assert view["warning"]["severity"] == "red"
    assert view["call"]["status"] == "reached" and view["call"]["answered_by"] == "self"
    water = next(q for q in view["questions"] if q["id"] == "fluids_food")
    assert (water["value"], water["value_label"], water["worry"]) == ("low", "Low", "amber")
    assert water["quote"] == "paani thoda hi bacha hai"
    assert next(q for q in view["questions"] if q["id"] == "feeling")["worry"] is None
    rule = next(r for r in view["rules"] if r["id"] == "A2")
    assert rule == {"id": "A2", "label": "Low on water or food", "tier": "amber", "question_id": "fluids_food", "detail": "Water: Low"}
    if view["resident"]["language"] in ("hi", "mwr", "en"):  # the languages a scripted call is written in
        assert view["transcript"][0]["role"] == "agent" and len(view["transcript"]) > 4
    else:
        assert view["transcript"] == []
    assert view["note"].startswith("Water at home is running short")
    assert view["case"]["tier"] == "amber" and view["case"]["status"] == "open" and view["case"]["can_act"] is False
    assert view["health_centre"]["name"] == "Ward 30 health centre"
    assert {w["name"] for w in view["workers"]} == {"Sunita Kanwar", "Meena Bishnoi", "Kavita Saini"}
    assert all(isinstance(w["open"], int) for w in view["workers"])


def test_the_team_may_act_and_a_visitor_only_on_their_own(region, now, tz):
    assert resident_view(region, "r08", None, True, now)["case"]["can_act"] is True
    add_visitor_call(region, region.round_id, ALICE, now, tz, resident_id="v-alice", ward_id=WARD_2, district_id="churu", feeling="mild_symptoms")
    hers = resident_view(region, "v-alice", ALICE, False, now)
    assert hers["resident"]["yours"] is True and hers["case"]["can_act"] is True
    with pytest.raises(KeyError):
        resident_view(region, "v-alice", BOB, False, now)
    with pytest.raises(KeyError):
        resident_view(region, "v-alice", None, True, now)  # not even the team reads a visitor's own
    with pytest.raises(KeyError):
        resident_view(region, "nobody", None, False, now)


def test_a_flag_and_a_raise_are_shown_in_words(region, now):
    flagged = resident_view(region, "r18", None, False, now)["case"]["escalation"]
    assert flagged["reason"] == "missing" and flagged["reason_label"] == "Cannot find the person"
    assert flagged["acknowledged_at"] is None
    raise_to_red(region, f"{region.round_id}_r08", "fainted", now)
    view = resident_view(region, "r08", None, False, now)
    assert view["case"]["tier"] == "red"
    assert view["case"]["raised"]["observation_label"] == "Fainted or collapsed"
    assert view["rules"][0] == {
        "id": "R5", "label": "Raised to red by the health worker", "tier": "red",
        "question_id": None, "detail": "Fainted or collapsed",
    }


def test_someone_never_reached_has_no_conversation(region, now):
    view = resident_view(region, "r12", None, False, now)  # three calls unanswered
    assert view["call"] == {"status": "no_answer", "attempts": 3, "first_at": view["call"]["first_at"], "answered_by": None, "language_used": None, "recorded_at": None}
    assert view["questions"] == [] and view["transcript"] == []
    assert [r["id"] for r in view["rules"]] == ["A8"]
    still_trying = resident_view(region, "r13", None, False, now)
    assert still_trying["call"]["status"] == "trying" and still_trying["case"] is None and still_trying["rules"] == []


def test_a_resident_of_a_district_without_a_warning(region, now):
    view = resident_view(region, BIKANER_RESIDENT, None, False, now)
    assert view["warning"] is None and view["call"] is None and view["case"] is None
    assert view["questions"] == [] and view["rules"] == [] and view["transcript"] == [] and view["note"] == ""
    assert view["resident"]["risk_factors"] is not None and view["resident"]["district_name"] == "Bikaner"


def test_nothing_private_leaves(region, now, tz):
    add_visitor_call(region, region.round_id, ALICE, now, tz, resident_id="v-alice", ward_id=WARD_2, district_id="churu", feeling="mild_symptoms")
    for resident_id, who in (("r08", None), ("v-alice", ALICE), (BIKANER_RESIDENT, None)):
        view = resident_view(region, resident_id, who, False, now)
        assert "+91" not in json.dumps(view, default=str)
        # the words a transcript speaks may say "phone"; what must not leave is a field of that name
        assert not [k for k in _keys(view) if any(bad in k for bad in ("phone", "visitor_id", "ticket", "token"))]


def test_the_answers_shown_are_the_ones_the_tier_was_decided_on(region, now):
    from app.cases import upsert_case
    from app.dispatch import call_id_for

    # r01 answered everything well; make attempt 1 a dropped call that said water was low,
    # and add a second attempt that completed with "enough".
    first = region.get("calls", call_id_for(region.round_id, "r01", 1))
    region.update("calls", first["id"], {
        "status": "dropped", "answers.fluids_food": {"value": "low", "quote": "paani kam hai", "at": now},
    })
    second = {**first, "id": call_id_for(region.round_id, "r01", 2), "attempt": 2, "status": "completed",
              "ended_at": now, "answers": {"fluids_food": {"value": "enough", "quote": "", "at": now}}}
    region.set("calls", second["id"], second)
    upsert_case(region, region.round_id, "r01", now)

    view = resident_view(region, "r01", None, False, now)
    water = next(q for q in view["questions"] if q["id"] == "fluids_food")
    assert (water["value"], water["quote"], water["worry"]) == ("low", "paani kam hai", "amber")
    rule = next(r for r in view["rules"] if r["id"] == "A2")
    assert rule["detail"] == "Water: Low" and rule["question_id"] == "fluids_food"


def test_a_call_still_in_progress_shows_no_conversation(region, now):
    from app.dispatch import call_id_for
    from app.models import Call
    from tests.helpers import add_resident

    add_resident(region, "v-live", name="Visitor Character", phone="", ward_id=WARD_2, district_id="churu", visitor_id=ALICE)
    call = Call(
        id=call_id_for(region.round_id, "v-live", 1), round_id=region.round_id, resident_id="v-live",
        channel="browser", visitor_id=ALICE, status="in_progress", created_at=now,
    )
    region.set("calls", call.id, call.model_dump())
    view = resident_view(region, "v-live", ALICE, False, now)
    assert view["call"]["status"] == "trying" and view["case"] is None
    assert view["questions"] == [] and view["transcript"] == []


def test_an_ai_resident_call_is_marked_and_dated_from_the_library(region, now, tmp_path, monkeypatch):
    from app import resident_view as module

    library = tmp_path / "ai_calls.json"
    library.write_text(json.dumps({"recorded_at": "2026-10-13T10:00:00+00:00", "conversations": [
        {"id": "c1", "recorded_at": "2026-10-12T09:30:00+00:00"}, {"id": "c2"}]}), encoding="utf-8")
    monkeypatch.setattr(module, "LIBRARY", library)
    plain = resident_view(region, "r08", None, False, now)
    assert plain["resident"]["simulated"] is None and plain["call"]["recorded_at"] is None
    call = next(c for c in region.query("calls") if c["round_id"] == region.round_id and c["resident_id"] == "r08")
    region.update("calls", call["id"], {"simulated": "ai_resident", "conversation_id": "c1"})
    view = resident_view(region, "r08", None, False, now)
    assert view["resident"]["simulated"] == "ai_resident"
    assert view["call"]["recorded_at"] == "2026-10-12T09:30:00+00:00"
    region.update("calls", call["id"], {"conversation_id": "c2"})  # falls back to the library's own date
    assert resident_view(region, "r08", None, False, now)["call"]["recorded_at"] == "2026-10-13T10:00:00+00:00"
    region.update("calls", call["id"], {"conversation_id": "gone", "recorded_at": "2026-10-11T00:00:00+00:00"})
    assert resident_view(region, "r08", None, False, now)["call"]["recorded_at"] == "2026-10-11T00:00:00+00:00"
    monkeypatch.setattr(module, "LIBRARY", tmp_path / "missing.json")
    region.update("calls", call["id"], {"recorded_at": None})
    assert resident_view(region, "r08", None, False, now)["call"]["recorded_at"] is None


def test_a_simulated_visit_is_marked_on_the_case(region, now):
    case = next(c for c in region.query("cases") if c.get("round_id") == region.round_id and c["resident_id"] == "r08")
    assert resident_view(region, "r08", None, False, now)["case"]["simulated_visit"] is False
    region.update("cases", case["id"], {"simulated": "visit"})
    view = resident_view(region, "r08", None, False, now)
    assert view["case"]["simulated_visit"] is True and view["resident"]["simulated_visit"] is True
