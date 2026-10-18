import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import replay
from app.api import create_app
from app.cases import mark_visited
from app.config import Settings
from app.db import MemoryStore
from app.demo import open_rounds
from app.dispatch import RecordingQueue
from app.ingest.alerts import record_alert
from app.ingest.replay import make_replay_alert
from app.replay import load_library, plan_replay, release_due
from tests.helpers import add_resident, add_ward

DAY = date(2026, 5, 20)  # the conftest's `now` is 10:00 local on this day
FINE = {
    "feeling": "ok", "fluids_food": "enough", "company": "with_someone", "shelter": "adequate",
    "exposure": "staying_in", "help": "none", "medicine": "has",
}
TIERS = {"green": dict(FINE), "amber": {**FINE, "fluids_food": "low"}, "red": {**FINE, "feeling": "severe_symptoms"}}


def conversation(language, tier, lives_alone=False, conditions=False, outdoor_worker=False):
    emergency = "{name} says her chest hurts" if tier == "red" else None
    return {
        "id": f"{tier}_{language}_{int(lives_alone)}{int(conditions)}{int(outdoor_worker)}",
        "persona_id": f"{tier}_persona", "language": language,
        "profile": {"lives_alone": lives_alone, "outdoor_worker": outdoor_worker, "conditions": conditions, "age_band": "70s"},
        "status": "completed", "respondent": "self", "emergency": emergency,
        "answers": {q: v for q, v in TIERS[tier].items() if q != "medicine" or conditions}, "quotes": {"feeling": "{name} is speaking, I am fine"},
        "tier": tier,
        "transcript": [
            {"role": "agent", "text": "Namaste. Kya main {name} ji se baat kar rahi hoon?"},
            {"role": "resident", "text": "Haan, main {name} hoon."},
            {"role": "agent", "text": "Aapki tabiyat kaisi hai?"},
            {"role": "resident", "text": "Theek hoon."},
        ],
    }


def library(*, free=False, languages=("hi", "mwr", "en")):
    conversations = [
        conversation(language, tier, lives_alone=alone, conditions=conditions)
        for language in languages for tier in ("green", "amber", "red") for alone in (False, True)
        for conditions in (False, True)
    ]
    if free:
        conversations.append({
            "id": "nobody_answered", "persona_id": None, "language": None, "profile": None,
            "status": "no_answer", "respondent": "unknown", "emergency": None, "answers": {}, "quotes": {},
            "tier": "amber", "transcript": [], "failures": [["no_answer", 0], ["no_answer", 20], ["busy", 80]],
        })
    return {"recorded_at": "2026-10-14T10:00:00+05:30", "commit": "abc", "model": "fake", "conversations": conversations}


ONLY_FREE = {"recorded_at": "2026-10-14T10:00:00+05:30", "conversations": [library(free=True)["conversations"][-1]]}


def district(store, tz, day=DAY, residents=40, languages=("hi", "hi", "mwr", "en")):
    """d1 with `residents` residents, a visitor's temporary resident and a resident of another
    district, and today's round pinned. Returns the round id."""
    add_ward(store)
    for n in range(residents):
        add_resident(
            store, f"r{n:02d}", name=f"Name{n:02d}", age=60 + (n * 7) % 30, language=languages[n % len(languages)],
            lives_alone=n % 3 == 0, conditions=["heart"] if n % 4 == 0 else [],
        )
    add_resident(store, "v1", name="Visitor", age=99, visitor_id="visitor-aaaaaaaaaaaa", lives_alone=True)
    add_resident(store, "x1", name="Elsewhere", age=99, district_id="d2", lives_alone=True)
    opened = datetime(day.year, day.month, day.day, 1, 0, tzinfo=timezone.utc)
    round_id = record_alert(store, make_replay_alert("d1", "heat", "red", opened), tz).id
    store.set("demo", "current", {"id": "current", "round_id": round_id, "rounds": {"d1": round_id}, "built_at": opened})
    return round_id


def local(day, hour, minute=0, tz=None):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz).astimezone(timezone.utc)


def at(value):
    return datetime.fromisoformat(value)


def plan_of(store, tz, lib=None, day=DAY, **kwargs):
    round_id = district(store, tz, day, **kwargs)
    return round_id, plan_replay(store, round_id, lib or library(), day, tz)


def test_the_plan_is_the_same_for_the_same_day_and_differs_the_next(tz):
    from app.db import MemoryStore

    _, first = plan_of(MemoryStore(), tz)
    _, again = plan_of(MemoryStore(), tz)
    _, next_day = plan_of(MemoryStore(), tz, day=DAY + timedelta(days=1))
    assert first == again
    shape = lambda plan: [(i["resident_id"], i["conversation_id"], i["call_at"][11:]) for i in plan]
    assert shape(first) != shape(next_day)


def test_the_sample_is_twenty_to_thirty_of_the_districts_own_residents_in_round_order(store, tz):
    round_id, plan = plan_of(store, tz)
    assert 20 <= len(plan) <= 30
    residents = sorted(
        (r for r in store.query("residents", district_id="d1") if not r.get("visitor_id")),
        key=lambda r: (-r["risk_score"], -r["age"], r["id"]),
    )
    assert [i["resident_id"] for i in plan] == [r["id"] for r in residents[: len(plan)]]
    assert store.get("rounds", round_id)["replay"] == plan
    for item in plan:
        assert item["released"] is False and item["visit_released"] is False


def test_the_sample_is_capped_by_the_residents(store, tz):
    _, plan = plan_of(store, tz, residents=12)
    assert len(plan) == 12


def test_every_call_is_after_the_round_opened_and_within_three_hours_in_risk_order(store, tz):
    round_id, plan = plan_of(store, tz)
    opened = store.get("rounds", round_id)["opened_at"]
    start = max(local(DAY, 8, tz=tz), opened + timedelta(minutes=10))
    times = [at(i["call_at"]) for i in plan]
    assert times == sorted(times)
    assert all(t >= opened and start <= t <= start + timedelta(hours=3) for t in times)


def test_a_round_opened_late_starts_its_calls_ten_minutes_after(store, tz):
    round_id = district(store, tz)
    opened = local(DAY, 12, 30, tz)
    store.update("rounds", round_id, {"opened_at": opened})
    times = [at(i["call_at"]) for i in plan_replay(store, round_id, library(), DAY, tz)]
    assert min(times) >= opened + timedelta(minutes=10)
    assert max(times) <= opened + timedelta(minutes=10, hours=3)


def test_a_round_opened_near_the_evening_ends_its_calls_by_eight(store, tz):
    round_id = district(store, tz)
    store.update("rounds", round_id, {"opened_at": local(DAY, 19, 0, tz)})
    times = [at(i["call_at"]) for i in plan_replay(store, round_id, library(), DAY, tz)]
    assert times and min(times) >= local(DAY, 19, 10, tz) and max(times) <= local(DAY, 20, tz=tz)


def test_a_resident_gets_a_conversation_in_their_own_language(store, tz):
    _, plan = plan_of(store, tz)
    by_id = {c["id"]: c for c in library()["conversations"]}
    for item in plan:
        resident = store.get("residents", item["resident_id"])
        assert by_id[item["conversation_id"]]["language"] == resident["language"]
    assert any(store.get("residents", i["resident_id"])["language"] == "mwr" for i in plan)


def test_the_closest_profile_is_chosen_within_the_language(store, tz):
    _, plan = plan_of(store, tz)
    by_id = {c["id"]: c for c in library()["conversations"]}
    for item in plan:
        resident = store.get("residents", item["resident_id"])
        assert by_id[item["conversation_id"]]["profile"]["lives_alone"] == resident["lives_alone"]


def test_a_resident_whose_language_has_no_conversation_still_gets_one(store, tz):
    _, plan = plan_of(store, tz, languages=("gu",))
    assert plan and all(item["conversation_id"] for item in plan)


def test_visits_go_to_about_one_in_five_red_or_amber_conversations(store, tz):
    _, plan = plan_of(store, tz)
    by_id = {c["id"]: c for c in library()["conversations"]}
    visited = [i for i in plan if i["visit"]]
    worrying = [i for i in plan if by_id[i["conversation_id"]]["tier"] in ("red", "amber")]
    assert 1 <= len(visited) <= len(worrying) // 5 + 1
    for item in visited:
        entry = by_id[item["conversation_id"]]
        assert entry["tier"] in ("red", "amber")
        assert timedelta(minutes=60) <= at(item["visit"]["at"]) - at(item["call_at"]) <= timedelta(minutes=150)
        expected = {"hospital"} if entry["emergency"] else {"supplies_given", "advised_ok"}
        assert item["visit"]["outcome"] in expected


def test_an_empty_library_plans_nothing_and_releases_nothing(store, tz, now):
    round_id = district(store, tz)
    assert plan_replay(store, round_id, {"conversations": []}, DAY, tz) == []
    assert "replay" not in store.get("rounds", round_id)
    assert release_due(store, RecordingQueue(), now, tz) == {"calls": 0, "visits": 0}
    assert store.query("calls") == []


def test_a_missing_library_file_reads_as_empty_and_says_so(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(replay, "LIBRARY", tmp_path / "missing.json")
    with caplog.at_level("WARNING", logger="app.replay"):
        assert load_library() == {"conversations": []}
    assert "missing" in caplog.text
    caplog.clear()
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(replay, "LIBRARY", broken)
    with caplog.at_level("WARNING", logger="app.replay"):
        assert load_library() == {"conversations": []}
    assert "could not be read" in caplog.text


def released_calls(store):
    return [c for c in store.query("calls") if c.get("simulated") == "ai_resident"]


def test_calls_are_released_when_due_and_only_once(store, tz, monkeypatch):
    monkeypatch.setattr(replay, "load_library", lambda: library())
    _, plan = plan_of(store, tz)
    queue = RecordingQueue()
    assert release_due(store, queue, local(DAY, 7, 59, tz), tz) == {"calls": 0, "visits": 0}
    assert store.query("calls") == []

    nine = local(DAY, 9, tz=tz)
    due = {i["resident_id"] for i in plan if at(i["call_at"]) <= nine}
    assert 0 < len(due) < len(plan)
    assert release_due(store, queue, nine, tz)["calls"] == len(due)
    assert {c["resident_id"] for c in released_calls(store)} == due
    assert release_due(store, queue, nine, tz) == {"calls": 0, "visits": 0}
    assert len(released_calls(store)) == len(due)
    assert queue.items == []  # nothing replayed is ever dialled
    for call in released_calls(store):
        item = next(i for i in plan if i["resident_id"] == call["resident_id"])
        assert call["id"] == f"{call['round_id']}_{call['resident_id']}_1"
        assert call["channel"] == "ai_resident" and call["conversation_id"] == item["conversation_id"]
        assert call["created_at"] == at(item["call_at"]) and not call.get("visitor_id")


def test_released_calls_open_cases_with_the_conversations_tier(store, tz, monkeypatch):
    monkeypatch.setattr(replay, "load_library", lambda: library())
    round_id, plan = plan_of(store, tz)
    by_id = {c["id"]: c for c in library()["conversations"]}
    release_due(store, RecordingQueue(), local(DAY, 11, 5, tz), tz)
    for item in plan:
        case = store.get("cases", f"{round_id}_{item['resident_id']}")
        assert case["tier"] == by_id[item["conversation_id"]]["tier"]


def test_the_name_token_is_replaced_everywhere(store, tz, monkeypatch):
    monkeypatch.setattr(replay, "load_library", lambda: library())
    release_due_all = lambda: release_due(store, RecordingQueue(), local(DAY, 11, 5, tz), tz)
    plan_of(store, tz)
    release_due_all()
    calls = released_calls(store)
    assert calls
    for call in calls:
        name = store.get("residents", call["resident_id"])["name"]
        text = json.dumps([call["transcript"], call["answers"], call.get("emergency")], default=str)
        assert "{name}" not in text and name in text
        assert call["transcript"][1]["text"] == f"Haan, main {name} hoon."


def test_transcript_lines_start_at_the_call_time(store, tz, monkeypatch):
    monkeypatch.setattr(replay, "load_library", lambda: library())
    _, plan = plan_of(store, tz)
    release_due(store, RecordingQueue(), local(DAY, 11, 5, tz), tz)
    call = released_calls(store)[0]
    item = next(i for i in plan if i["resident_id"] == call["resident_id"])
    times = [line["at"] for line in call["transcript"]]
    assert times[0] == at(item["call_at"]) and times == sorted(times)
    assert times[-1] <= call["ended_at"]


def test_simulated_visits_land_once_on_open_red_or_amber_cases(store, tz, monkeypatch):
    monkeypatch.setattr(replay, "load_library", lambda: library())
    round_id, plan = plan_of(store, tz)
    visited = [i for i in plan if i["visit"]]
    assert len(visited) >= 2
    queue = RecordingQueue()
    # A worker gets to one of them first, before its simulated visit is due: it is never simulated.
    first = visited[0]
    soon = at(first["call_at"]) + timedelta(minutes=5)
    assert release_due(store, queue, soon, tz)["visits"] == 0
    worker_case = f"{round_id}_{first['resident_id']}"
    mark_visited(store, worker_case, "referred", soon)

    late = local(DAY, 14, tz=tz)
    assert release_due(store, queue, late, tz)["visits"] == len(visited) - 1
    assert release_due(store, queue, late, tz) == {"calls": 0, "visits": 0}
    assert store.get("cases", worker_case).get("simulated") is None
    assert store.get("cases", worker_case)["outcome"] == "referred"
    for item in visited[1:]:
        case = store.get("cases", f"{round_id}_{item['resident_id']}")
        assert case["simulated"] == "visit" and case["status"] == "visited"
        assert case["tier"] in ("red", "amber")
        assert case["outcome"] == item["visit"]["outcome"] and case["visited_at"] == at(item["visit"]["at"])
    simulated = [c for c in store.query("cases") if c.get("simulated") == "visit"]
    assert len(simulated) == len(visited) - 1


def test_nothing_is_stamped_after_now(store, tz, monkeypatch):
    monkeypatch.setattr(replay, "load_library", lambda: library())
    round_id, plan = plan_of(store, tz)
    # Thirty seconds into a call: it is released, but ends no later than now.
    now = at(plan[5]["call_at"]) + timedelta(seconds=30)
    release_due(store, RecordingQueue(), now, tz)
    stamps = []
    for call in store.query("calls"):
        stamps += [call.get("created_at"), call.get("started_at"), call.get("ended_at")]
        stamps += [line["at"] for line in call["transcript"]]
        stamps += [a["at"] for a in call["answers"].values()]
        stamps += [(call.get("emergency") or {}).get("at")]
    for case in store.query("cases"):
        stamps += [case.get("updated_at"), case.get("opened_at"), case.get("visited_at")]
    stamps = [s for s in stamps if s is not None]
    assert stamps and max(stamps) <= now


def test_a_conversation_nobody_answered_is_released_after_its_last_attempt(store, tz, monkeypatch):
    monkeypatch.setattr(replay, "load_library", lambda: ONLY_FREE)
    round_id, plan = plan_of(store, tz, lib=ONLY_FREE)
    first = plan[0]
    assert first["conversation_id"] == "nobody_answered" and first["visit"] is None
    queue = RecordingQueue()
    start = at(first["call_at"])
    release_due(store, queue, start + timedelta(minutes=79), tz)
    assert store.query("calls", resident_id=first["resident_id"]) == []

    release_due(store, queue, start + timedelta(minutes=80), tz)
    calls = sorted(store.query("calls", resident_id=first["resident_id"]), key=lambda c: c["attempt"])
    assert [(c["attempt"], c["status"]) for c in calls] == [(1, "no_answer"), (2, "no_answer"), (3, "busy")]
    assert all(c["simulated"] == "ai_resident" and c["conversation_id"] == "nobody_answered" for c in calls)
    assert [c["ended_at"] for c in calls] == [start + timedelta(minutes=m) for m in (0, 20, 80)]
    case = store.get("cases", f"{round_id}_{first['resident_id']}")
    assert case["tier"] == "amber" and "A8" in case["reasons"]
    assert queue.items == []
    assert not [c for c in store.query("calls") if c["status"] == "queued"]


def test_some_residents_are_given_a_call_nobody_answers(store, tz):
    _, plan = plan_of(store, tz, lib=library(free=True))
    assert any(i["conversation_id"] == "nobody_answered" for i in plan)


def test_a_round_called_for_real_first_keeps_its_own_calls(store, tz, monkeypatch):
    from app.dispatch import plan_round

    monkeypatch.setattr(replay, "load_library", lambda: library())
    round_id, plan = plan_of(store, tz)
    plan_round(store, RecordingQueue(), round_id, local(DAY, 8, tz=tz), tz)
    assert release_due(store, RecordingQueue(), local(DAY, 11, 5, tz), tz)["calls"] == 0
    assert released_calls(store) == []
    assert all(c["status"] == "queued" for c in store.query("calls"))


def test_open_rounds_plans_each_round_once(store, tz, now, tmp_path, monkeypatch):
    from seed.seed import build_all, load_seed

    path = tmp_path / "ai_calls.json"
    path.write_text(json.dumps(library()), encoding="utf-8")
    monkeypatch.setattr(replay, "LIBRARY", path)
    load_seed(store, build_all([]))
    pointer = open_rounds(store, "churu", now, tz)
    plans = {rid: store.get("rounds", rid).get("replay") for rid in pointer["rounds"].values()}
    assert plans and all(plans.values())
    path.write_text(json.dumps(library(languages=("en",))), encoding="utf-8")
    open_rounds(store, "churu", now + timedelta(minutes=30), tz)
    assert {rid: store.get("rounds", rid).get("replay") for rid in pointer["rounds"].values()} == plans


def test_open_rounds_without_a_library_plans_nothing(store, tz, now, tmp_path, monkeypatch):
    from seed.seed import build_all, load_seed

    monkeypatch.setattr(replay, "LIBRARY", tmp_path / "missing.json")
    load_seed(store, build_all([]))
    pointer = open_rounds(store, "churu", now, tz)
    assert all(not store.get("rounds", rid).get("replay") for rid in pointer["rounds"].values())
    assert release_due(store, RecordingQueue(), now, tz) == {"calls": 0, "visits": 0}


SECRET = {"X-Internal-Secret": "s3cret"}


def test_the_sweep_and_the_rebuild_release_what_is_due(store, tz, now, tmp_path, monkeypatch):
    from seed.seed import build_all, load_seed

    path = tmp_path / "ai_calls.json"
    path.write_text(json.dumps(library()), encoding="utf-8")
    monkeypatch.setattr(replay, "LIBRARY", path)
    load_seed(store, build_all([]))
    settings = Settings(_env_file=None, internal_secret="s3cret", demo_district_id="churu")
    clock = {"now": now}  # 10:00 on the day of a deploy: the rounds open now, their calls from 10:10
    client = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: clock["now"]))
    pointer = client.post("/internal/demo/rebuild", headers=SECRET).json()
    assert set(pointer) >= {"round_id", "rounds", "replay_planned"}
    assert set(pointer["replay_planned"]) == set(pointer["rounds"])
    assert all(20 <= n <= 30 for n in pointer["replay_planned"].values())
    assert released_calls(store) == []  # nothing is due before the round has been open ten minutes
    clock["now"] = now + timedelta(hours=1, minutes=5)
    morning = client.post("/internal/sweep", headers=SECRET).json()["replay"]["calls"]
    assert morning == len(released_calls(store)) > 0
    assert all(c["created_at"] <= clock["now"] for c in released_calls(store))
    clock["now"] = now + timedelta(hours=2, minutes=30)
    body = client.post("/internal/sweep", headers=SECRET).json()
    assert body["replay"]["calls"] == len(released_calls(store)) - morning > 0
    assert client.post("/internal/sweep", headers=SECRET).json()["replay"] == {"calls": 0, "visits": 0}


class CountingStore(MemoryStore):
    """Counts the queries made of each collection."""

    def __init__(self):
        super().__init__()
        self.queries = Counter()

    def query(self, collection, **filters):
        self.queries[collection] += 1
        return super().query(collection, **filters)


def test_a_round_with_nothing_due_reads_no_residents(tz, monkeypatch):
    store = CountingStore()
    monkeypatch.setattr(replay, "load_library", lambda: library())
    plan_of(store, tz)
    store.queries.clear()
    assert release_due(store, RecordingQueue(), local(DAY, 7, 59, tz), tz) == {"calls": 0, "visits": 0}
    assert store.queries == Counter()
    release_due(store, RecordingQueue(), local(DAY, 9, tz=tz), tz)
    assert store.queries["residents"] == 0  # each due resident is read by id


def test_an_item_that_keeps_failing_is_given_up_after_three_tries(store, tz, monkeypatch):
    monkeypatch.setattr(replay, "load_library", lambda: library())
    round_id, plan = plan_of(store, tz)
    tries = Counter()

    def broken(store, queue, call_id, *args, **kwargs):
        tries[call_id] += 1
        raise RuntimeError("the store is down")

    monkeypatch.setattr(replay, "apply_outcome", broken)
    eight_thirty = local(DAY, 8, 30, tz)
    due = [i for i in plan if at(i["call_at"]) <= eight_thirty]
    assert due
    for _ in range(replay.MAX_TRIES):
        assert release_due(store, RecordingQueue(), eight_thirty, tz) == {"calls": 0, "visits": 0, "errors": len(due)}
    assert release_due(store, RecordingQueue(), eight_thirty, tz) == {"calls": 0, "visits": 0}
    assert set(tries.values()) == {replay.MAX_TRIES} and len(tries) == len(due)
    stored = {i["resident_id"]: i for i in store.get("rounds", round_id)["replay"]}
    assert all(stored[i["resident_id"]]["failed"] for i in due)


def test_a_call_another_run_already_ended_is_not_counted_again(store, tz, monkeypatch):
    monkeypatch.setattr(replay, "load_library", lambda: library())
    round_id, plan = plan_of(store, tz)
    nine = local(DAY, 9, tz=tz)
    stale = store.get("rounds", round_id)  # what a second runner read before the first wrote
    first = release_due(store, RecordingQueue(), nine, tz)["calls"]
    assert first > 0
    store.set("rounds", round_id, stale)  # the second runner's view: nothing released yet
    assert release_due(store, RecordingQueue(), nine, tz) == {"calls": 0, "visits": 0}
    assert len(released_calls(store)) == first


def test_a_recorded_call_nobody_spoke_in_leaves_no_retry_waiting(store, tz, monkeypatch):
    silent = conversation("hi", "green")
    silent.update(id="silent", answers={}, quotes={}, respondent="unknown", emergency=None, tier="amber")
    lib = {"conversations": [silent]}
    monkeypatch.setattr(replay, "load_library", lambda: lib)
    round_id, plan = plan_of(store, tz, lib=lib)
    now = at(plan[0]["call_at"]) + timedelta(minutes=5)
    release_due(store, RecordingQueue(), now, tz)
    calls = store.query("calls", resident_id=plan[0]["resident_id"])
    assert sorted(c["attempt"] for c in calls) == [1, 2, 3]
    assert all(c["status"] == "no_answer" for c in calls)
    for call in calls:
        assert all(call[key] <= now for key in ("created_at", "ended_at", "not_before"))


def test_a_replay_failure_never_stops_the_sweep_or_the_rebuild(store, now, monkeypatch):
    from seed.seed import build_all, load_seed

    def broken(*args):
        raise RuntimeError("the library is broken")

    monkeypatch.setattr("app.api.release_due", broken)
    load_seed(store, build_all([]))
    settings = Settings(_env_file=None, internal_secret="s3cret", demo_district_id="churu")
    client = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now))
    assert client.post("/internal/demo/rebuild", headers=SECRET).status_code == 200
    swept = client.post("/internal/sweep", headers=SECRET)
    assert swept.status_code == 200
    assert swept.json()["replay"] == {"calls": 0, "visits": 0, "errors": 1}


def test_a_case_record_never_carries_the_simulated_mark(store, tz, now):
    from app.schemas import CaseRecord
    from tests.helpers import seeded_region

    round_id = seeded_region(store, now, tz)
    settings = Settings(_env_file=None, internal_secret="s3cret")
    client = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now))
    open_cases = [c for c in store.query("cases", round_id=round_id) if c["status"] == "open" and c["tier"] == "amber"]
    raised, visited = open_cases[0]["id"], open_cases[1]["id"]
    for case_id in (raised, visited):
        store.update("cases", case_id, {"simulated": "visit"})
    for response in (
        client.post(f"/api/cases/{raised}/raise", json={"observation": "fainted"}, headers=SECRET),
        client.post(f"/api/cases/{visited}/visit", json={"outcome": "advised_ok"}, headers=SECRET),
    ):
        assert response.status_code == 200, response.text
        assert "simulated" not in response.json()
        CaseRecord.model_validate(response.json())


def test_the_rebuild_says_nothing_was_planned_without_a_library(store, now, tmp_path, monkeypatch):
    from seed.seed import build_all, load_seed

    monkeypatch.setattr(replay, "LIBRARY", tmp_path / "missing.json")
    load_seed(store, build_all([]))
    settings = Settings(_env_file=None, internal_secret="s3cret", demo_district_id="churu")
    client = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now))
    body = client.post("/internal/demo/rebuild", headers=SECRET).json()
    assert body["replay_planned"] == {d: 0 for d in body["rounds"]}


def test_a_recording_is_given_the_residents_ward_facilities_and_heat(store):
    places = replay.recording_places()  # from the seed's first Churu ward and Churu's scenario
    assert places["ward"] == "Ward 2" and places["peak"] == 47.5
    cool, phc = places["facilities"]["cooling_centre"], places["facilities"]["health_centre"]
    store.set("wards", "w7", {"id": "w7", "name": "Ward 7 – Subhash Nagar", "number": 7, "district_id": "d1"})
    store.set("facilities", "f1", {"id": "f1", "ward_id": "w7", "type": "cooling_centre", "name": "Subhash Nagar cooling hall"})
    store.set("facilities", "f2", {"id": "f2", "ward_id": "w7", "type": "health_centre", "name": "Subhash Nagar PHC"})
    localise = replay.localiser(store, {"ward_id": "w7"}, {"peak_temp_c": 45.5})
    assert localise(f"{cool} is open, or go to {phc}.") == "Subhash Nagar cooling hall is open, or go to Subhash Nagar PHC."
    assert localise("You live in Ward 2, not Ward 23.") == "You live in Ward 7 – Subhash Nagar, not Ward 23."
    assert localise("Today it reaches about 48 degrees (47.5 °C).") == "Today it reaches about 46 degrees (45.5 °C)."
    assert localise("about 48.") == "about 46."
    assert localise("आज 48 डिग्री तक, वार्ड 2 में") == "आज 46 डिग्री तक, वार्ड 7 में"
    assert localise("The alert lasts 48 hours; 148 and 48.25 stay.") == "The alert lasts 48 hours; 148 and 48.25 stay."
    # Churu's own peak, or a round that has none, leaves the heat as recorded.
    assert replay.localiser(store, {"ward_id": "w7"}, {"peak_temp_c": 47.5})("about 48 degrees") == "about 48 degrees"
    assert replay.localiser(store, {"ward_id": "w7"}, {})("about 48 degrees") == "about 48 degrees"


def test_released_calls_carry_the_residents_ward_and_the_rounds_heat(store, tz, monkeypatch):
    places = replay.recording_places()
    lib = library()
    for entry in lib["conversations"]:
        entry["transcript"].append({"role": "agent", "text": f"Aaj lagbhag 48 degrees hoga. {places['facilities']['cooling_centre']} khula hai."})
        entry["quotes"]["shelter"] = "Ward 2 mein cooler hai"
    monkeypatch.setattr(replay, "load_library", lambda: lib)
    round_id = district(store, tz)
    store.update("rounds", round_id, {"peak_temp_c": 46.0})
    store.set("facilities", "c1", {"id": "c1", "ward_id": "w1", "type": "cooling_centre", "name": "Ward 1 cooling centre"})
    plan_replay(store, round_id, lib, DAY, tz)
    release_due(store, RecordingQueue(), local(DAY, 11, 5, tz), tz)
    call = released_calls(store)[0]
    assert call["transcript"][-1]["text"] == "Aaj lagbhag 46 degrees hoga. Ward 1 cooling centre khula hai."
    assert call["answers"]["shelter"]["quote"] == "Ward 1 mein cooler hai"


def test_a_line_mostly_in_devanagari_gets_the_name_in_devanagari(store, tz, monkeypatch):
    lib = library()
    for entry in lib["conversations"]:
        entry["transcript"].append({"role": "agent", "text": "धन्यवाद {name} जी, अपना ध्यान रखिए।"})
    monkeypatch.setattr(replay, "load_library", lambda: lib)
    round_id = district(store, tz)
    for resident in store.query("residents", district_id="d1"):
        store.update("residents", resident["id"], {"name_hi": f"नाम {resident['id']}"})
    plan_replay(store, round_id, lib, DAY, tz)
    release_due(store, RecordingQueue(), local(DAY, 11, 5, tz), tz)
    call = released_calls(store)[0]
    resident = store.get("residents", call["resident_id"])
    assert call["transcript"][1]["text"] == f"Haan, main {resident['name']} hoon."  # a Latin line keeps the Latin name
    assert call["transcript"][-1]["text"] == f"धन्यवाद {resident['name_hi']} जी, अपना ध्यान रखिए।"


def test_a_resident_is_given_a_persona_of_their_gender(monkeypatch):
    import random

    from seed.regions import NAMES

    assert set(NAMES["Rajasthan"][0]) <= set(replay.GENDER_OF_FIRST_NAME)
    genders = replay.persona_genders.__wrapped__()  # the personas file the library was recorded with
    assert genders["healthy_with_family"] == "f" and genders["healthy_alone_younger"] == "m"
    monkeypatch.setattr(replay, "persona_genders", lambda: {"woman": "f", "man": "m"})
    spoken = [{**conversation("hi", "green"), "id": pid, "persona_id": pid} for pid in ("woman", "man")]
    for seed in range(20):
        rng = random.Random(seed)
        assert replay._match({"name": "Kamla Saini", "language": "hi"}, spoken, [], Counter(), rng)["id"] == "woman"
        assert replay._match({"name": "Mohan Jangir", "language": "hi"}, spoken, [], Counter(), rng)["id"] == "man"
    # A name outside the lists, or no persona of the gender, still gets a conversation.
    assert replay._match({"name": "Name00", "language": "hi"}, spoken, [], Counter(), random.Random(1))
    assert replay._match({"name": "Kamla Saini", "language": "hi"}, spoken[1:], [], Counter(), random.Random(1))["id"] == "man"


def test_a_completed_conversation_asks_about_medicine_exactly_when_the_resident_has_conditions(store, tz, caplog):
    import random

    with_medicine = {**conversation("hi", "green", conditions=True), "id": "with_medicine"}
    without = {**conversation("en", "green"), "id": "without"}  # another language: the fit comes first
    emergency = {**conversation("hi", "red"), "id": "emergency"}
    for seed in range(20):
        rng = random.Random(seed)
        assert replay._match({"id": "a", "language": "en", "conditions": ["heart"]}, [with_medicine, without], [], Counter(), rng)["id"] == "with_medicine"
        assert replay._match({"id": "b", "language": "hi", "conditions": []}, [with_medicine, without], [], Counter(), rng)["id"] == "without"
    # An emergency fits anyone; with nothing that fits, the resident is matched as before, and it is noted.
    misfits = []
    assert replay._match({"id": "c", "language": "hi", "conditions": []}, [with_medicine, emergency], [], Counter(), random.Random(1), misfits)["id"] == "emergency"
    assert replay._match({"id": "d", "language": "hi", "conditions": []}, [with_medicine], [], Counter(), random.Random(1), misfits)["id"] == "with_medicine"
    assert misfits == ["d"]
    round_id = district(store, tz)
    with caplog.at_level("WARNING", logger="app.replay"):
        plan_replay(store, round_id, {"conversations": [with_medicine]}, DAY, tz)
    assert caplog.text.count("fits the conditions") == 1  # once for the plan


def test_with_the_real_library_no_completed_replay_misses_or_adds_the_medicine_question(store, tz, now, monkeypatch):
    from pathlib import Path

    from seed.seed import build_all, load_seed

    real = Path(replay.__file__).resolve().parent.parent / "seed" / "fixtures" / "ai_calls.json"
    monkeypatch.setattr(replay, "LIBRARY", real)  # the conftest hides it from every other test
    if not real.is_file():
        pytest.skip("the recorded library is not in this checkout")
    lib = {c["id"]: c for c in load_library()["conversations"]}
    load_seed(store, build_all([]))
    pointer = open_rounds(store, "churu", now, tz)  # plans Churu's day from the real library, read only
    plan = store.get("rounds", pointer["rounds"]["churu"])["replay"]
    assert plan
    for item in plan:
        entry, resident = lib[item["conversation_id"]], store.get("residents", item["resident_id"])
        if entry.get("status") == "completed" and not entry.get("emergency"):
            assert ("medicine" in entry["answers"]) == bool(resident.get("conditions")), item
