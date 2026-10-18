import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.call.fake_live import open_fake_session
from app.config import Settings
from app.dispatch import RecordingQueue
from app.schemas import (
    CaseRecord, Detail, DistrictView, RegionView, Refusal, ResidentView, WardView, WorkersView, WorkerView,
)
from scripts.export_openapi import build_openapi
from tests.helpers import seeded_region
from tests.test_role_api import ALICE, BIKANER_RESIDENT, BIKANER_WORKER, WARD_2, WORKER_2, WORKER_58, take_test_call

BIKANER_WARD = BIKANER_WORKER.replace("-worker-", "-ward-")
COMMITTED = Path(__file__).resolve().parents[1] / "openapi.json"
ROLE_ROUTES = {
    ("get", "/api/region"), ("get", "/api/districts/{district_id}"), ("get", "/api/wards/{ward_id}/detail"),
    ("get", "/api/residents/{resident_id}"), ("get", "/api/workers"), ("get", "/api/workers/{worker_id}/residents"),
    ("post", "/api/cases/{case_id}/visit"), ("post", "/api/cases/{case_id}/escalate"),
    ("post", "/api/cases/{case_id}/acknowledge"), ("post", "/api/cases/{case_id}/reassign"),
    ("post", "/api/cases/{case_id}/raise"),
}


@pytest.fixture
def rig(store, now, tz):
    seeded_region(store, now, tz)
    settings = Settings(
        _env_file=None, internal_secret="s3cret", max_call_seconds=20,
        fake_live=True, fake_live_step_seconds=0, transcript_save_seconds=0,
    )
    return TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now, open_session=open_fake_session))


def test_every_get_response_fits_its_schema(rig):
    own = take_test_call(rig, ALICE)["resident_id"]
    for who in ({}, ALICE):
        for path, model in (
            ("/api/region", RegionView),
            ("/api/districts/churu", DistrictView),
            ("/api/districts/bikaner", DistrictView),
            (f"/api/wards/{WARD_2}/detail", WardView),
            (f"/api/wards/{BIKANER_WARD}/detail", WardView),
            ("/api/workers", WorkersView),
            (f"/api/workers/{WORKER_2}/residents", WorkerView),
            (f"/api/workers/{BIKANER_WORKER}/residents", WorkerView),
            ("/api/residents/r08", ResidentView),   # reached
            ("/api/residents/r12", ResidentView),   # never reached
            ("/api/residents/r13", ResidentView),   # still being tried
            (f"/api/residents/{BIKANER_RESIDENT}", ResidentView),  # a district without a warning
        ):
            response = rig.get(path, headers=who)
            assert response.status_code == 200, (path, who)
            model.model_validate(response.json())
    mine = rig.get(f"/api/residents/{own}", headers=ALICE)
    assert mine.status_code == 200
    assert ResidentView.model_validate(mine.json()).resident.yours is True


def test_every_post_response_fits_its_schema(rig):
    case_id = take_test_call(rig, ALICE)["case"]["id"]

    def post(action, body):
        return rig.post(f"/api/cases/{case_id}/{action}", json=body, headers=ALICE)

    def refused(response, status, model=Refusal):
        assert response.status_code == status, response.text
        model.model_validate(response.json())

    done = post("escalate", {"reason": "supplies", "note": "out of ORS"})
    assert done.status_code == 200
    CaseRecord.model_validate(done.json())
    refused(post("escalate", {"reason": "supplies"}), 409)

    done = post("acknowledge", {"reply": "on my way"})
    assert done.status_code == 200
    CaseRecord.model_validate(done.json())
    refused(post("acknowledge", {}), 409)

    done = post("reassign", {"worker_id": WORKER_58})
    assert done.status_code == 200
    CaseRecord.model_validate(done.json())
    refused(post("reassign", {"worker_id": WORKER_58}), 422)

    done = post("raise", {"observation": "fainted"})
    assert done.status_code == 200
    CaseRecord.model_validate(done.json())
    refused(post("raise", {"observation": "fainted"}), 409)

    done = post("visit", {"outcome": "hospital", "note": "taken by 108"})
    assert done.status_code == 200
    CaseRecord.model_validate(done.json())
    refused(post("visit", {"outcome": "hospital"}), 409, Detail)  # a visit refusal has no reason
    refused(rig.post(f"/api/cases/{case_id}/visit", json={"outcome": "hospital"}), 403, Detail)
    refused(post("escalate", {"reason": "other"}), 409)


def test_the_schema_describes_all_eleven_routes():
    paths = build_openapi()["paths"]
    for method, path in ROLE_ROUTES:
        operation = paths[path][method]
        ok = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert ok.get("$ref"), (method, path)
        assert operation["tags"] == ["Role views"] and operation["summary"] and operation["description"]
        assert any(p["name"] == "x-visitor" and p["in"] == "header" for p in operation["parameters"]), path
    for path in (p for m, p in ROLE_ROUTES if m == "post"):
        for status in ("403", "404", "409", "422"):
            schema = paths[path]["post"]["responses"][status]["content"]["application/json"]["schema"]
            assert "$ref" in json.dumps(schema), (path, status)  # a union is a list of references


def test_the_committed_file_is_the_current_description():
    assert COMMITTED.is_file(), "run `python -m scripts.export_openapi` from backend/"
    assert json.loads(COMMITTED.read_text(encoding="utf-8")) == build_openapi(), (
        "backend/openapi.json is out of date: run `python -m scripts.export_openapi` from backend/ and commit it"
    )
