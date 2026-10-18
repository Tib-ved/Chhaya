import json
import re

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.api as api_module
from app.api import create_app
from app.config import Settings, check_settings
from app.dispatch import RecordingQueue
from app.ingest.replay import make_replay_alert
from app.models import Alert
from seed.seed import ward_ids
from tests.helpers import add_resident, add_ward, add_visitor_call, pin_round, seeded_demo

SECRET = {"X-Internal-Secret": "s3cret"}
CHURU_WARDS = ward_ids("churu")


@pytest.fixture
def client(store, now):
    add_ward(store)
    add_resident(store, "r1", name="Sita", age=82, lives_alone=True)
    add_resident(store, "r2", name="Tara", age=66)
    app = create_app(store, RecordingQueue(), Settings(_env_file=None, internal_secret="s3cret"), clock=lambda: now)
    client = TestClient(app)
    client.pin = lambda round_id: pin_round(store, round_id, now)  # what the demo rebuild does
    return client


def start_round(client):
    replay = client.post(
        "/admin/alerts/replay",
        json={"district_id": "d1", "hazard": "heat", "severity": "red"},
        headers=SECRET,
    )
    assert replay.status_code == 200
    round_id = replay.json()["round_id"]
    dispatch = client.post(f"/internal/rounds/{round_id}/dispatch", headers=SECRET)
    assert dispatch.status_code == 200
    client.pin(round_id)
    return round_id, dispatch.json()["queued"]


def test_health(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_admin_and_internal_need_the_secret(client):
    body = {"district_id": "d1", "hazard": "heat", "severity": "red"}
    assert client.post("/admin/alerts/replay", json=body).status_code == 401
    assert client.post("/internal/rounds/x/dispatch", headers={"X-Internal-Secret": "wrong"}).status_code == 401
    assert client.post("/admin/calls/x/simulate", json={"status": "completed"}).status_code == 401


def test_unauthorised_request_changes_nothing(client, store):
    body = {"district_id": "d1", "hazard": "heat", "severity": "red"}
    assert client.post("/admin/alerts/replay", json=body).status_code == 401
    assert store.query("alerts") == []
    assert store.query("rounds") == []


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Secret": ""}])
def test_empty_configured_secret_authorises_nobody(store, now, headers):
    add_ward(store)
    app = create_app(store, RecordingQueue(), Settings(_env_file=None, internal_secret=""), clock=lambda: now)
    body = {"district_id": "d1", "hazard": "heat", "severity": "red"}
    response = TestClient(app).post("/admin/alerts/replay", json=body, headers=headers)
    assert response.status_code == 401
    assert store.query("rounds") == []


@pytest.mark.parametrize("secret", ["", "dev-secret"])
def test_check_settings_rejects_weak_secret_outside_memory(secret):
    with pytest.raises(RuntimeError, match="INTERNAL_SECRET"):
        check_settings(Settings(_env_file=None, store="firestore", internal_secret=secret), env={})


@pytest.mark.parametrize(
    "settings",
    [
        Settings(_env_file=None, store="memory", internal_secret="dev-secret"),
        Settings(_env_file=None, store="firestore", internal_secret="a-real-strong-value"),
    ],
)
def test_check_settings_accepts_valid_combinations(settings):
    check_settings(settings, env={})


def test_replay_unknown_district_is_404(client):
    response = client.post(
        "/admin/alerts/replay",
        json={"district_id": "nowhere", "hazard": "heat", "severity": "red"},
        headers=SECRET,
    )
    assert response.status_code == 404


def test_replay_yellow_opens_no_round(client):
    response = client.post(
        "/admin/alerts/replay",
        json={"district_id": "d1", "hazard": "heat", "severity": "yellow"},
        headers=SECRET,
    )
    assert response.json()["round_id"] is None


def test_current_round_empty(client):
    assert client.get("/api/rounds/current").json() == {"round": None}


def test_full_loop(client):
    round_id, queued = start_round(client)
    assert queued == [f"{round_id}_r1_1", f"{round_id}_r2_1"]

    overview = client.get("/api/rounds/current").json()
    assert overview["round"]["id"] == round_id
    assert overview["counts"]["queued"] == 2

    simulate = client.post(
        f"/admin/calls/{round_id}_r1_1/simulate",
        json={
            "status": "completed",
            "respondent": "self",
            "answers": {"feeling": "severe_symptoms", "company": "alone"},
            "quotes": {"feeling": "chakkar aa raha hai"},
        },
        headers=SECRET,
    )
    assert simulate.status_code == 200
    assert simulate.json() == {"ok": True, "status": "completed"}
    second = client.post(
        f"/admin/calls/{round_id}_r2_1/simulate",
        json={
            "status": "completed",
            "respondent": "self",
            "answers": {
                "feeling": "ok",
                "fluids_food": "enough",
                "company": "with_someone",
                "shelter": "adequate",
                "exposure": "staying_in",
                "help": "none",
            },
        },
        headers=SECRET,
    )
    assert second.status_code == 200

    cases = client.get("/api/wards/w1/cases").json()
    assert cases["round_id"] == round_id
    assert [c["tier"] for c in cases["cases"]] == ["red", "green"]
    assert cases["cases"][0]["quote"] == "chakkar aa raha hai"
    assert cases["cases"][0]["resident"]["name"] == "Sita"

    refused = client.post(f"/api/cases/{round_id}_r1/visit", json={"outcome": "referred"})
    assert refused.status_code == 403  # a visitor may mark only the case from their own call
    visit = client.post(f"/api/cases/{round_id}_r1/visit", json={"outcome": "referred"}, headers=SECRET)
    assert visit.json()["status"] == "visited"

    report = client.get(f"/api/rounds/{round_id}/report").json()
    assert report["coverage"] == {
        "targeted": 2, "reached": 2, "unreachable": 0, "pending": 0, "not_callable": 0,
    }
    assert report["response"]["visited"] == 1
    assert report["open_red_cases"] == []


def test_simulated_emergency_is_red(client):
    round_id, _ = start_round(client)
    response = client.post(
        f"/admin/calls/{round_id}_r2_1/simulate",
        json={"status": "completed", "emergency_reason": "collapsed", "answers": {"feeling": "ok"}},
        headers=SECRET,
    )
    assert response.status_code == 200
    cases = client.get("/api/wards/w1/cases").json()["cases"]
    assert cases[0]["tier"] == "red"
    assert "R1" in cases[0]["reasons"]


def test_unknown_ids_are_404(client):
    assert client.post("/internal/rounds/nope/dispatch", headers=SECRET).status_code == 404
    assert client.post("/admin/calls/nope/simulate", json={"status": "completed"}, headers=SECRET).status_code == 404
    assert client.post("/api/cases/nope/visit", json={"outcome": "x"}).status_code == 404
    assert client.get("/api/rounds/nope/report", headers=SECRET).status_code == 404
    assert client.get("/api/wards/w1/cases?round_id=nope", headers=SECRET).status_code == 404
    assert client.get("/api/rounds/nope/overview", headers=SECRET).status_code == 404


def test_simulate_rejects_unknown_status(client):
    round_id, _ = start_round(client)
    response = client.post(
        f"/admin/calls/{round_id}_r1_1/simulate", json={"status": "exploded"}, headers=SECRET
    )
    assert response.status_code == 422


def test_simulate_on_finalised_call_is_409_and_writes_nothing(client, store):
    round_id, _ = start_round(client)
    call_id = f"{round_id}_r1_1"
    url = f"/admin/calls/{call_id}/simulate"
    first = client.post(
        url, json={"status": "completed", "answers": {"feeling": "ok"}}, headers=SECRET
    )
    assert first.status_code == 200
    call_before = store.get("calls", call_id)
    case_before = store.get("cases", f"{round_id}_r1")
    second = client.post(
        url,
        json={"status": "completed", "answers": {"feeling": "severe_symptoms"}},
        headers=SECRET,
    )
    assert second.status_code == 409
    assert store.get("calls", call_id) == call_before
    assert store.get("cases", f"{round_id}_r1") == case_before


def test_simulate_rejects_unknown_question_keys(client, store):
    round_id, _ = start_round(client)
    call_id = f"{round_id}_r1_1"
    before = store.get("calls", call_id)
    url = f"/admin/calls/{call_id}/simulate"
    bad_answers = client.post(
        url,
        json={"status": "completed", "answers": {"feeling.value": "ok"}},
        headers=SECRET,
    )
    bad_quotes = client.post(
        url,
        json={"status": "completed", "answers": {"feeling": "ok"}, "quotes": {"nonsense": "x"}},
        headers=SECRET,
    )
    assert bad_answers.status_code == 422
    assert bad_quotes.status_code == 422
    assert store.get("calls", call_id) == before


def test_api_responses_contain_no_full_phone_numbers(client):
    round_id, _ = start_round(client)
    client.post(
        f"/admin/calls/{round_id}_r1_1/simulate",
        json={"status": "completed", "answers": {"feeling": "severe_symptoms"}},
        headers=SECRET,
    )
    for path in ("/api/wards/w1/cases", "/api/rounds/current", f"/api/rounds/{round_id}/report"):
        text = json.dumps(client.get(path).json())
        assert not re.search(r"\+\d{8,15}", text), path
    resident = client.get("/api/wards/w1/cases", headers=SECRET).json()["cases"][0]["resident"]
    assert resident["phone"] == "+91********01"
    for headers in ({}, {"X-Visitor": "visitor-aaaaaaaaaaaa"}):
        resident = client.get("/api/wards/w1/cases", headers=headers).json()["cases"][0]["resident"]
        assert resident["phone"] is None


def test_simulate_rejects_a_non_final_status(client, store):
    round_id, _ = start_round(client)
    call_id = f"{round_id}_r1_1"
    before = store.get("calls", call_id)
    for status in ("in_progress", "dialing", "queued", "no-answer"):
        response = client.post(
            f"/admin/calls/{call_id}/simulate", json={"status": status}, headers=SECRET
        )
        assert response.status_code == 422, status
    assert store.get("calls", call_id) == before


@pytest.mark.parametrize("store_name", ["memory", "firestore"])
@pytest.mark.parametrize("secret", ["", "dev-secret"])
def test_check_settings_rejects_weak_secret_on_cloud_run(store_name, secret):
    settings = Settings(_env_file=None, store=store_name, internal_secret=secret)
    with pytest.raises(RuntimeError, match="INTERNAL_SECRET"):
        check_settings(settings, env={"K_SERVICE": "chhaya-backend"})


def test_check_settings_accepts_strong_secret_on_cloud_run():
    # Cloud Run now also needs sign-in or an explicit choice to run without it.
    settings = Settings(
        _env_file=None, store="memory", internal_secret="a-real-strong-value", allow_open_access=True
    )
    check_settings(settings, env={"K_SERVICE": "chhaya-backend"})


def test_check_settings_reads_the_real_environment_by_default(monkeypatch):
    settings = Settings(_env_file=None, store="memory", internal_secret="dev-secret")
    monkeypatch.delenv("K_SERVICE", raising=False)
    check_settings(settings)
    monkeypatch.setenv("K_SERVICE", "chhaya-backend")
    with pytest.raises(RuntimeError, match="INTERNAL_SECRET"):
        check_settings(settings)


def test_misspelt_store_fails_at_start_up():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, store="firestroe")


def test_alert_source_accepts_weather_and_rejects_others(now):
    fields = make_replay_alert("d1", "heat", "red", now).model_dump()
    assert Alert(**{**fields, "source": "weather"}).source == "weather"
    with pytest.raises(ValidationError):
        Alert(**{**fields, "source": "rumour"})


def test_simulate_reports_the_status_actually_stored(client):
    round_id, _ = start_round(client)
    response = client.post(
        f"/admin/calls/{round_id}_r1_1/simulate", json={"status": "completed"}, headers=SECRET
    )
    assert response.json() == {"ok": True, "status": "no_answer"}


@pytest.fixture
def demo_client(store, now, tz):
    round_id = seeded_demo(store, now, tz)
    app = create_app(store, RecordingQueue(), Settings(_env_file=None, internal_secret="s3cret"), clock=lambda: now)
    client = TestClient(app)
    client.round_id = round_id
    client.store, client.now, client.tz = store, now, tz
    return client


def test_demo_describes_the_pinned_round_the_cards_and_the_languages(demo_client):
    body = demo_client.get("/api/demo").json()
    assert body["round_id"] == demo_client.round_id
    assert body["built_at"] is not None
    assert [card["id"] for card in body["cards"]] == ["alone_dizzy", "must_work", "family_answers", "emergency"]
    assert [language["code"] for language in body["languages"]] == ["hi", "en", "mr", "gu", "mwr"]


def test_with_nothing_pinned_the_visitor_endpoints_say_so_quietly(client):
    assert client.get("/api/demo").json()["round_id"] is None
    assert client.get("/api/rounds/current").json() == {"round": None}
    assert client.get("/api/wards").json() == {"round_id": None, "wards": []}
    assert client.get("/api/wards/w1/cases").json() == {"round_id": None, "cases": []}


def test_a_team_replay_does_not_replace_what_visitors_see(demo_client):
    replay = demo_client.post(
        "/admin/alerts/replay", json={"district_id": "churu", "hazard": "heat", "severity": "red"}, headers=SECRET
    ).json()
    assert replay["round_id"] != demo_client.round_id
    demo_client.post(f"/internal/rounds/{replay['round_id']}/dispatch", headers=SECRET)
    assert demo_client.get("/api/rounds/current").json()["round"]["id"] == demo_client.round_id
    assert demo_client.get("/api/demo").json()["round_id"] == demo_client.round_id


def test_only_the_team_may_look_at_another_round(demo_client):
    other = demo_client.post(
        "/admin/alerts/replay", json={"district_id": "churu", "hazard": "cold", "severity": "red"}, headers=SECRET
    ).json()["round_id"]
    for path in (f"/api/rounds/{other}/report", f"/api/wards/{CHURU_WARDS[0]}/cases?round_id={other}", f"/api/wards?round_id={other}"):
        assert demo_client.get(path).status_code == 403, path
        assert demo_client.get(path, headers=SECRET).status_code == 200, path
    for path in ("/api/rounds", f"/api/rounds/{other}/overview"):
        assert demo_client.get(path).status_code == 401, path
        assert demo_client.get(path, headers=SECRET).status_code == 200, path
    listed = demo_client.get("/api/rounds", headers=SECRET).json()
    assert listed["pinned"] == demo_client.round_id
    assert {r["id"] for r in listed["rounds"]} == {other, demo_client.round_id}


def test_the_pinned_round_is_open_to_anyone(demo_client):
    round_id = demo_client.round_id
    assert demo_client.get(f"/api/rounds/{round_id}/report").json()["coverage"]["targeted"] == 30
    assert demo_client.get(f"/api/wards/{CHURU_WARDS[0]}/cases?round_id={round_id}").status_code == 200
    wards = demo_client.get("/api/wards").json()["wards"]
    assert [w["id"] for w in wards] == CHURU_WARDS
    assert [f["type"] for f in wards[0]["facilities"]] == ["cooling_centre", "health_centre"]
    assert "phone" not in json.dumps(wards)


def test_a_seeded_case_shows_no_button_to_a_visitor_and_one_to_the_team(demo_client):
    cases = demo_client.get(f"/api/wards/{CHURU_WARDS[0]}/cases").json()["cases"]
    assert not any(c["can_mark"] for c in cases)
    for_team = demo_client.get(f"/api/wards/{CHURU_WARDS[0]}/cases", headers=SECRET).json()["cases"]
    assert for_team[0]["tier"] == "red" and for_team[0]["can_mark"] is True


def test_visit_refusals_have_their_own_status_codes(demo_client):
    cases = demo_client.get(f"/api/wards/{CHURU_WARDS[1]}/cases", headers=SECRET).json()["cases"]
    visited = next(c for c in cases if c["status"] == "visited")
    open_amber = next(c for c in cases if c["status"] == "open" and c["tier"] == "amber")
    green = next(c for c in cases if c["tier"] == "green")

    def visit(case, outcome, headers=SECRET):
        return demo_client.post(f"/api/cases/{case['id']}/visit", json={"outcome": outcome}, headers=headers)

    assert visit(open_amber, "referred", headers={}).status_code == 403
    assert visit(visited, "referred").status_code == 409
    assert visit(green, "advised_ok").status_code == 422
    assert visit(open_amber, "had a chat").status_code == 422
    done = visit(open_amber, "not_home")
    assert done.status_code == 200
    assert (done.json()["status"], done.json()["outcome"]) == ("visited", "not_home")
    assert "visitor_id" not in done.json()


def test_the_days_rounds_can_be_opened_by_a_machine_or_by_the_team(demo_client):
    calls_before = demo_client.store.query("calls", round_id=demo_client.round_id)
    assert demo_client.post("/internal/demo/rebuild").status_code == 401
    assert demo_client.post("/admin/demo/rebuild").status_code == 401
    for path in ("/internal/demo/rebuild", "/admin/demo/rebuild"):  # the second opens nothing new
        rebuilt = demo_client.post(path, headers=SECRET)
        assert rebuilt.status_code == 200
        assert rebuilt.json()["round_id"] == "churu_heat_20260520"  # today's real round, 10:00 in Kolkata
        assert rebuilt.json()["rounds"] == {
            district: f"{district}_heat_20260520" for district in ("bikaner", "churu", "jhunjhunu", "sikar")
        }
    assert demo_client.get("/api/demo").json()["round_id"] == "churu_heat_20260520"
    assert demo_client.store.get("rounds", demo_client.round_id) is not None  # nothing is deleted
    assert demo_client.store.query("calls", round_id=demo_client.round_id) == calls_before


def test_rebuilding_before_the_district_is_seeded_is_a_409(client):
    assert client.post("/internal/demo/rebuild", headers=SECRET).status_code == 409


def test_evaluation_results_are_served_when_the_file_exists(demo_client, tmp_path, monkeypatch):
    results = tmp_path / "eval_latest.json"
    monkeypatch.setattr(api_module, "EVAL_RESULTS", results)
    assert demo_client.get("/api/eval/latest").json() == {"run": None}
    results.write_text("not json", encoding="utf-8")
    assert demo_client.get("/api/eval/latest").json() == {"run": None}
    results.write_text(json.dumps({"ran_at": "2026-10-16T09:00:00Z", "red_below_red": 0}), encoding="utf-8")
    assert demo_client.get("/api/eval/latest").json() == {"run": {"ran_at": "2026-10-16T09:00:00Z", "red_below_red": 0}}


def test_a_visitor_marks_only_the_case_from_their_own_call(demo_client):
    a, b = "visitor-aaaaaaaaaaaa", "visitor-bbbbbbbbbbbb"
    add_visitor_call(
        demo_client.store, demo_client.round_id, a, demo_client.now, demo_client.tz,
        resident_id="v-a", ward_id=CHURU_WARDS[0], feeling="mild_symptoms",
    )
    case_id = f"{demo_client.round_id}_v-a"
    before = demo_client.store.get("cases", case_id)
    assert before is not None and before["visitor_id"] == a

    def visit(headers):
        return demo_client.post(f"/api/cases/{case_id}/visit", json={"outcome": "referred"}, headers=headers)

    assert visit({"X-Visitor": b}).status_code == 403
    assert visit({}).status_code == 403
    assert demo_client.store.get("cases", case_id) == before
    mine = visit({"X-Visitor": a})
    assert mine.status_code == 200
    assert mine.json()["status"] == "visited"
    assert "visitor_id" not in mine.json()
    assert visit({"X-Visitor": a}).status_code == 409


def test_a_rebuild_error_other_than_an_unseeded_district_is_not_a_409(demo_client, monkeypatch):
    def broken(*args, **kwargs):
        raise KeyError("wards/ward-x")

    monkeypatch.setattr(api_module, "open_rounds", broken)
    with pytest.raises(KeyError):
        demo_client.post("/internal/demo/rebuild", headers=SECRET)


def test_a_demo_round_is_never_dispatched(store, now, tz):
    round_id = seeded_demo(store, now, tz)
    queue = RecordingQueue()
    app = create_app(store, queue, Settings(_env_file=None, internal_secret="s3cret"), clock=lambda: now)
    calls_before = len(store.query("calls"))
    response = TestClient(app).post(f"/internal/rounds/{round_id}/dispatch", headers=SECRET)
    assert response.status_code == 409
    assert response.json()["detail"] == "a demo round is scripted, not dispatched"
    assert queue.items == []
    assert len(store.query("calls")) == calls_before


def test_the_api_pages_are_off_when_the_service_is_open_on_cloud_run(store, now, monkeypatch):
    settings = Settings(_env_file=None, internal_secret="a-long-random-value-not-in-any-repository", allow_open_access=True)
    local = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now))
    assert local.get("/openapi.json").status_code == 200
    monkeypatch.setenv("K_SERVICE", "chhaya")
    deployed = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now))
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert deployed.get(path).status_code == 404, path
    assert deployed.get("/health").json() == {"ok": True}


def test_the_worker_pool_is_as_wide_as_the_setting(store, now):
    import asyncio

    settings = Settings(_env_file=None, internal_secret="s3cret", worker_threads=7)
    app = create_app(store, RecordingQueue(), settings, clock=lambda: now)

    @app.get("/test-only/pool")
    async def pool() -> dict:
        return {"width": asyncio.get_running_loop()._default_executor._max_workers}

    with TestClient(app) as client:  # the context manager runs start-up
        assert client.get("/test-only/pool").json() == {"width": 7}


def test_a_stale_pointer_is_logged_not_just_hidden(client, store, now, caplog):
    pin_round(store, "demo_gone", now)
    assert client.get("/api/rounds/current").json() == {"round": None}
    assert "demo_gone" in caplog.text


def test_a_call_in_a_demo_round_cannot_be_simulated(demo_client, store):
    round_id = store.get("demo", "current")["round_id"]
    assert round_id.startswith("demo_")
    waiting = next(c for c in store.query("calls", round_id=round_id) if c["status"] == "queued")
    before = (store.query("calls"), store.query("cases"))
    refused = demo_client.post(
        f"/admin/calls/{waiting['id']}/simulate", json={"status": "no_answer"}, headers=SECRET
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == "a demo round is scripted"
    assert (store.query("calls"), store.query("cases")) == before  # no retry, on any queue


def test_work_already_handed_to_the_pool_is_not_dropped_at_shutdown(store, now):
    import asyncio
    import threading

    settings = Settings(_env_file=None, internal_secret="s3cret", worker_threads=1)
    app = create_app(store, RecordingQueue(), settings, clock=lambda: now)
    hold, written = threading.Event(), []

    @app.post("/test-only/work")
    async def work() -> dict:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, hold.wait, 0.3)  # keeps the one worker busy past the shutdown
        loop.run_in_executor(None, written.append, "outcome")  # waits its turn in the pool
        return {"ok": True}

    with TestClient(app) as client:
        assert client.post("/test-only/work").json() == {"ok": True}
        assert written == []  # still waiting when the service is told to stop
    for _ in range(250):
        if written:
            break
        threading.Event().wait(0.02)
    assert written == ["outcome"]
