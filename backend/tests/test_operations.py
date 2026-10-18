import json

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.auth import COOKIE_NAME
from app.config import Settings
from app.dispatch import RecordingQueue
from seed.seed import ward_ids
from tests.helpers import seeded_demo

CHURU_WARDS = ward_ids("churu")
CHURU_WORKERS = [w.replace("-ward-", "-worker-") for w in CHURU_WARDS]


@pytest.fixture
def ops(store, now, tz):
    seeded_demo(store, now, tz)
    settings = Settings(_env_file=None, internal_secret="test-secret")
    app = create_app(store, RecordingQueue(), settings, clock=lambda: now)
    client = TestClient(app, base_url="https://testserver")
    assert client.get("/api/ops/session").status_code == 200
    return client


def proposal(client, action="assign_crew"):
    data = client.get("/api/ops/overview?district_id=churu").json()
    case = next(c for c in data["cases"] if c["tier"] == "amber" and c["status"] == "open")
    result = client.post("/api/ops/proposals", json={"action": action, "case_id": case["id"], "worker_id": case["worker_id"]})
    assert result.status_code == 200, result.text
    return result.json(), case


def test_directory_has_sourced_four_states(ops):
    data = ops.get("/api/ops/geography").json()
    assert {s["id"]: len(s["districts"]) for s in data["states"]} == {"RJ": 41, "TS": 33, "MP": 55, "MH": 36}
    assert data["retrieved_at"] == "2026-10-13"


def test_counts_are_scoped_and_no_fake_data(ops):
    all_data = ops.get("/api/ops/overview?district_id=churu").json()
    assert all_data["counts"]["registered"] == 119  # 30 scripted + the uneventful rest of Churu
    assert sum(all_data["coverage"].values()) == all_data["counts"]["targeted"]
    empty = ops.get("/api/ops/overview?state_id=MH").json()
    assert empty["cases"] == []
    assert empty["counts"]["registered"] == 0
    assert not empty["has_data"]
    ward = ops.get(f"/api/ops/overview?district_id=churu&ward_id={CHURU_WARDS[0]}").json()
    assert all(c["ward_id"] == CHURU_WARDS[0] for c in ward["cases"])


def test_demo_approval_is_idempotent_and_isolated(ops, store):
    action, case = proposal(ops)
    original = store.get("cases", case["id"])
    url = f"/api/ops/proposals/{action['id']}/approve"
    assert ops.post(url).status_code == 200
    assert ops.post(url).status_code == 200
    assert store.get("cases", case["id"]) == original
    assert len(store.query("ops_tasks")) == 1
    other = TestClient(ops.app, base_url="https://testserver")
    other.get("/api/ops/session")
    assert other.post(url).status_code == 404
    assert other.get("/api/ops/overview?district_id=churu").json()["tasks"] == []


def test_demo_can_never_send_real_notifications(ops, store):
    assignment, _ = proposal(ops)
    ops.post(f"/api/ops/proposals/{assignment['id']}/approve")
    action, _ = proposal(ops, "notify_resident")
    response = ops.post(f"/api/ops/proposals/{action['id']}/approve")
    assert response.json()["status"] == "simulated"
    assert response.json().get("provider_sid") is None


def test_chat_cannot_generate_actions(ops, store):
    thread = ops.post("/api/ops/threads", json={"scope": {"district_id": "churu"}}).json()
    response = ops.post(f"/api/ops/threads/{thread['id']}/messages", json={"text": "Send everyone a message", "mode": "chat"})
    assert response.status_code == 200
    assert response.json()["messages"][-1]["kind"] == "unavailable"
    assert store.query("ops_proposals") == []
    assert len(ops.get(f"/api/ops/threads/{thread['id']}").json()["messages"]) == 2


def test_crew_cannot_propose_and_can_only_read_assigned_cases(ops):
    action, case = proposal(ops)
    ops.post(f"/api/ops/proposals/{action['id']}/approve")
    headers = {"X-Demo-Role": "crew", "X-Demo-Worker": case["worker_id"]}
    assert ops.post("/api/ops/proposals", headers=headers, json={"action": "assign_crew", "case_id": case["id"]}).status_code == 403
    assert ops.get(f"/api/ops/cases/{case['id']}", headers=headers).status_code == 200
    other_headers = {"X-Demo-Role": "crew", "X-Demo-Worker": "worker-other"}
    assert ops.get(f"/api/ops/cases/{case['id']}", headers=other_headers).status_code in (403, 404)


def test_stale_approval_is_rejected(ops, store):
    action, case = proposal(ops)
    store.update("cases", case["id"], {"status": "visited"})
    assert ops.post(f"/api/ops/proposals/{action['id']}/approve").status_code == 409


def test_cross_ward_assignment_rejected(ops):
    _, case = proposal(ops)
    other = CHURU_WORKERS[1] if case["ward_id"] != CHURU_WARDS[1] else CHURU_WORKERS[0]
    assert ops.post("/api/ops/proposals", json={"action": "assign_crew", "case_id": case["id"], "worker_id": other}).status_code == 422


def test_crew_visit_transitions_and_history(ops):
    action, case = proposal(ops)
    ops.post(f"/api/ops/proposals/{action['id']}/approve")
    task = ops.get("/api/ops/overview?district_id=churu").json()["tasks"][0]
    headers = {"X-Demo-Role": "crew", "X-Demo-Worker": case["worker_id"]}
    url = f"/api/ops/tasks/{task['id']}"
    assert ops.post(url, headers=headers, json={"status": "completed", "outcome": "supplies_given"}).status_code == 409
    for status in ("acknowledged", "arrived", "completed"):
        assert ops.post(url, headers=headers, json={"status": status, "outcome": "supplies_given"}).status_code == 200
    detail = ops.get(f"/api/ops/cases/{case['id']}").json()
    assert detail["case"]["status"] == "visited"
    assert any(e["event"] == "visit_completed" for e in detail["events"])


def test_google_crew_cannot_use_legacy_admin_paths(store, now, tz):
    seeded_demo(store, now, tz)
    settings = Settings(_env_file=None, internal_secret="test-secret", allowed_emails="crew@example.com", google_client_id="client", session_secret="x" * 40, operations_crew_accounts={"crew@example.com": CHURU_WORKERS[0]})
    app = create_app(store, RecordingQueue(), settings, clock=lambda: now)
    from app.auth import Auth
    client = TestClient(app, base_url="https://testserver")
    client.cookies.set(COOKIE_NAME, Auth(settings).sign("crew@example.com"))
    assert client.get("/api/ops/session").json()["role"] == "crew"
    assert client.post("/admin/alerts/replay", json={"district_id": "churu", "hazard": "heat", "severity": "red"}).status_code in (401, 403)
    assert client.get("/api/ops/threads").status_code == 403


def test_agent_output_is_validated_and_does_not_execute(store, now, tz):
    seeded_demo(store, now, tz)
    model = lambda instruction, prompt: json.dumps({"answer": "Review the proposed action.", "case_ids": [], "actions": [{"action": "assign_crew", "case_id": "not-in-scope"}]})
    client = TestClient(create_app(store, RecordingQueue(), Settings(_env_file=None), clock=lambda: now, text_model=model), base_url="https://testserver")
    client.get("/api/ops/session")
    thread = client.post("/api/ops/threads", json={"scope": {"district_id": "churu"}}).json()
    result = client.post(f"/api/ops/threads/{thread['id']}/messages", json={"mode": "agent", "text": "Assign a crew"})
    assert result.status_code == 200
    assert store.query("ops_tasks") == []
    assert store.query("ops_proposals") == []


def test_cross_site_actions_are_refused(ops):
    result = ops.post("/api/ops/threads", json={"scope": {}}, headers={"Origin": "https://untrusted.example"})
    assert result.status_code == 403


def test_thread_history_survives_a_new_app_instance(ops, store, now):
    thread = ops.post("/api/ops/threads", json={"scope": {"district_id": "churu"}}).json()
    ops.post(f"/api/ops/threads/{thread['id']}/messages", json={"text": "Summarize", "mode": "chat"})
    restarted = TestClient(create_app(store, RecordingQueue(), Settings(_env_file=None), clock=lambda: now), base_url="https://testserver")
    restarted.cookies.update(ops.cookies)
    assert len(restarted.get(f"/api/ops/threads/{thread['id']}").json()["messages"]) == 2
    stranger = TestClient(ops.app, base_url="https://testserver")
    stranger.get("/api/ops/session")
    assert stranger.get(f"/api/ops/threads/{thread['id']}").status_code == 404


@pytest.fixture
def live_ops(store, now, tz, monkeypatch):
    seeded_demo(store, now, tz)
    for n, worker_id in enumerate(CHURU_WORKERS, start=1):  # the seed gives workers no number; tests give test ones
        store.update("workers", worker_id, {"phone": f"+91000000009{n}"})
    settings = Settings(_env_file=None, internal_secret="test-secret", twilio_account_sid="ACtest", twilio_auth_token="test-token", twilio_from="+15005550006", public_host="demo.example", dial_allowlist="+910000000001")
    client = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now), base_url="https://testserver", headers={"X-Internal-Secret": "test-secret"})
    service = client.app.state.operations
    sent = []
    monkeypatch.setattr(service, "send_notification", lambda row: sent.append(row) or "SMtest")
    return client, service, sent


def test_real_notifications_need_allowlist_and_explicit_approval(live_ops, store):
    client, service, sent = live_ops
    assignment, case = proposal(client)
    client.post(f"/api/ops/proposals/{assignment['id']}/approve")
    notification, _ = proposal(client, "notify_crew")
    assert sent == []
    url = f"/api/ops/proposals/{notification['id']}/approve"
    assert client.post(url).status_code == 422
    service.settings.dial_allowlist = store.get("workers", case["worker_id"])["phone"]
    assert client.post(url).json()["status"] == "submitted"
    assert client.post(url).json()["status"] == "submitted"
    assert len(sent) == 1
    assert sent[0]["content"] == notification["content"]


def test_resident_notifications_require_consent_and_hours(live_ops, store):
    client, service, sent = live_ops
    assignment, case = proposal(client)
    client.post(f"/api/ops/proposals/{assignment['id']}/approve")
    store.update("residents", case["resident_id"], {"language": "hi", "consent.given": False})
    notification, _ = proposal(client, "notify_resident")
    service.settings.dial_allowlist = store.get("residents", case["resident_id"])["phone"]
    url = f"/api/ops/proposals/{notification['id']}/approve"
    assert client.post(url).status_code == 422
    store.update("residents", case["resident_id"], {"consent.given": True})
    service.settings.call_end_hour = 8
    assert client.post(url).status_code == 409
    service.settings.call_end_hour = 20
    assert client.post(url).json()["status"] == "submitted"
    assert sent[0]["content"].startswith("नमस्ते")


def test_provider_signature_sid_and_duplicate_receipts(live_ops, store):
    from twilio.request_validator import RequestValidator

    client, service, _ = live_ops
    assignment, case = proposal(client)
    client.post(f"/api/ops/proposals/{assignment['id']}/approve")
    service.settings.dial_allowlist = store.get("workers", case["worker_id"])["phone"]
    notification, _ = proposal(client, "notify_crew")
    client.post(f"/api/ops/proposals/{notification['id']}/approve")
    path = f"/twilio/operations/status?proposal_id={notification['id']}"
    fields = {"MessageSid": "SMtest", "MessageStatus": "delivered"}
    assert client.post(path, data=fields).status_code == 403
    signature = RequestValidator("test-token").compute_signature("https://demo.example" + path, fields)
    for _ in range(2):
        assert client.post(path, data=fields, headers={"X-Twilio-Signature": signature}).status_code == 200
    events = store.query("ops_events", proposal_id=notification["id"], event="provider_delivered")
    assert len(events) == 1
    fields["MessageStatus"] = "sent"
    signature = RequestValidator("test-token").compute_signature("https://demo.example" + path, fields)
    client.post(path, data=fields, headers={"X-Twilio-Signature": signature})
    assert store.get("ops_proposals", notification["id"])["status"] == "delivered"
    fields["MessageSid"] = "wrong"
    signature = RequestValidator("test-token").compute_signature("https://demo.example" + path, fields)
    assert client.post(path, data=fields, headers={"X-Twilio-Signature": signature}).status_code == 409


def test_notification_timeout_does_not_resend(live_ops, store, monkeypatch):
    client, service, sent = live_ops
    assignment, case = proposal(client)
    client.post(f"/api/ops/proposals/{assignment['id']}/approve")
    service.settings.dial_allowlist = store.get("workers", case["worker_id"])["phone"]
    notification, _ = proposal(client, "notify_crew")

    def timeout(row):
        sent.append(row)
        raise TimeoutError()

    monkeypatch.setattr(service, "send_notification", timeout)
    url = f"/api/ops/proposals/{notification['id']}/approve"
    assert client.post(url).json()["status"] == "needs_review"
    assert client.post(url).json()["status"] == "needs_review"
    assert len(sent) == 1


def test_model_cannot_act_in_chat_mode(ops, store):
    _, case = proposal(ops)
    before = len(store.query("ops_proposals"))
    ops.app.state.operations.text_model = lambda instruction, prompt: json.dumps({"answer": "A draft.", "case_ids": [case["id"]], "actions": [{"action": "assign_crew", "case_id": case["id"]}]})
    thread = ops.post("/api/ops/threads", json={"scope": {"district_id": "churu"}}).json()
    result = ops.post(f"/api/ops/threads/{thread['id']}/messages", json={"text": "Assign this case", "mode": "chat"})
    assert result.json()["messages"][-1]["kind"] == "model"
    assert len(store.query("ops_proposals")) == before
    assert store.query("ops_tasks") == []


def test_valid_agent_draft_is_saved_without_execution(ops, store):
    _, case = proposal(ops)
    ops.app.state.operations.text_model = lambda instruction, prompt: json.dumps({"answer": "Please review the draft.", "case_ids": [case["id"]], "actions": [{"action": "assign_crew", "case_id": case["id"]}]})
    thread = ops.post("/api/ops/threads", json={"scope": {"district_id": "churu"}}).json()
    result = ops.post(f"/api/ops/threads/{thread['id']}/messages", json={"text": "Assign this case", "mode": "agent"}).json()
    assert len(result["proposals"]) == 1
    assert result["proposals"][0]["status"] == "pending"
    assert store.query("ops_tasks") == []


def test_provider_clients_only_receive_reviewed_text(live_ops, store, monkeypatch):
    from types import SimpleNamespace
    import twilio.rest

    client, service, _ = live_ops
    assignment, case = proposal(client)
    client.post(f"/api/ops/proposals/{assignment['id']}/approve")
    captured = []
    fake = SimpleNamespace(create=lambda **kwargs: captured.append(kwargs) or SimpleNamespace(sid="fake"))
    monkeypatch.setattr(twilio.rest, "Client", lambda *args, **kwargs: SimpleNamespace(messages=fake, calls=fake))
    row, _ = proposal(client, "notify_crew")
    stored = store.get("ops_proposals", row["id"])
    from app.operations import Operations
    assert Operations.send_notification(service, stored) == "fake"
    assert captured[0]["body"] == row["content"]
    assert captured[0]["status_callback"].startswith("https://demo.example/twilio/operations/status?")


def test_early_provider_receipt_is_reconciled(live_ops, store, monkeypatch):
    client, service, _ = live_ops
    assignment, case = proposal(client)
    client.post(f"/api/ops/proposals/{assignment['id']}/approve")
    service.settings.dial_allowlist = store.get("workers", case["worker_id"])["phone"]
    notification, _ = proposal(client, "notify_crew")

    def send(row):
        service.receive_status(row["id"], {"MessageSid": "SMearly", "MessageStatus": "delivered", "To": row["target_phone"]})
        return "SMearly"

    monkeypatch.setattr(service, "send_notification", send)
    assert client.post(f"/api/ops/proposals/{notification['id']}/approve").json()["status"] == "delivered"


def test_case_timeline_is_not_truncated_to_dashboard_limit(ops, store, now):
    _, case = proposal(ops)
    session = store.query("ops_sessions")[0]
    for i in range(110):
        store.set("ops_events", str(i), {"id": str(i), "space": session["id"], "case_id": case["id"], "event": "recorded", "at": now})
    assert len(ops.get(f"/api/ops/cases/{case['id']}").json()["events"]) >= 110
    assert len(ops.get("/api/ops/overview").json()["events"]) == 100


def test_completed_task_does_not_hide_a_new_red_case(ops, store):
    action, case = proposal(ops)
    ops.post(f"/api/ops/proposals/{action['id']}/approve")
    task = store.query("ops_tasks")[0]
    for status in ("acknowledged", "arrived", "completed"):
        assert ops.post(f"/api/ops/tasks/{task['id']}", json={"status": status, "outcome": "supplies_given"}).status_code == 200
    store.update("cases", case["id"], {"tier": "red", "status": "open", "reasons": ["R1"]})
    detail = ops.get(f"/api/ops/cases/{case['id']}").json()
    assert detail["case"]["status"] == "open"
    reopened = ops.post("/api/ops/proposals", json={"action": "assign_crew", "case_id": case["id"]}).json()
    assert ops.post(f"/api/ops/proposals/{reopened['id']}/approve").json()["status"] == "completed"
    assert store.get("ops_tasks", task["id"])["status"] == "assigned"


def test_live_admin_gets_latest_round_not_pinned_demo(live_ops, store, now):
    from datetime import timedelta
    client, _, _ = live_ops
    old = store.query("rounds")[0]
    store.set("rounds", "new-round", {**old, "id": "new-round", "opened_at": now + timedelta(minutes=1)})
    data = client.get("/api/ops/overview?district_id=churu").json()
    assert data["rounds"][0]["id"] == "new-round"
    assert data["counts"]["targeted"] == 0
    assert len(data["available_rounds"]) == 2


def test_sensitive_operations_responses_are_not_cacheable(ops):
    assert ops.get("/api/ops/overview").headers["cache-control"] == "no-store"
    assert ops.get("/api/ops/cases/__reserved__").status_code == 404


def test_named_case_chat_has_timestamped_call_evidence(ops):
    _, case = proposal(ops)
    prompts = []

    def model(instruction, prompt):
        prompts.append(json.loads(prompt))
        return json.dumps({"answer": "Review the recorded answers.", "case_ids": [case["id"]], "actions": []})

    ops.app.state.operations.text_model = model
    thread = ops.post("/api/ops/threads", json={"scope": {"district_id": "churu"}}).json()
    response = ops.post(f"/api/ops/threads/{thread['id']}/messages", json={"text": f"Explain case {case['id']}", "mode": "chat"})
    assert response.status_code == 200
    assert prompts[0]["evidence"][0]["case_id"] == case["id"]
    assert "questions" in prompts[0]["evidence"][0]["call"]
    assert response.json()["messages"][-1]["data_snapshot"]["counts"]


def test_role_view_projection_uses_recorded_fields_without_secrets(ops, store):
    data = ops.get("/api/ops/overview?district_id=churu").json()
    assert len(data["wards"]) == 3
    assert len(data["facilities"]) == 6
    row = data["residents"][0]
    assert row["lat"] == store.get("residents", row["id"])["lat"]
    assert row["district_id"] == "churu"
    assert all("reason_ids" in case for case in data["cases"])
    assert not ({"phone", "stream_token", "browser_ticket", "twilio_sid"} & set(row))
    assert not ({"stream_token", "browser_ticket", "twilio_sid"} & set(row["call"] or {}))


def test_crew_projection_excludes_other_wards_and_residents(ops):
    headers = {"X-Demo-Role": "crew", "X-Demo-Worker": CHURU_WORKERS[0]}
    data = ops.get("/api/ops/overview?district_id=churu", headers=headers).json()
    assert {w["id"] for w in data["wards"]} == {CHURU_WARDS[0]}
    assert all(r["ward_id"] == CHURU_WARDS[0] for r in data["residents"])
    assert all(f["ward_id"] == CHURU_WARDS[0] for f in data["facilities"])
