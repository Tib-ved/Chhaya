import json
from datetime import timedelta

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

import app.api as api_module
from app.api import create_app
from app.config import Settings
from app.dispatch import RecordingQueue
from app.feed import load_feed
from app.recorded import audio_file, build_timeline, load_recording
from seed.seed import ward_ids
from tests.helpers import add_visitor_call, seeded_demo


@pytest.fixture
def finished_call(store, tz, now):
    round_id = seeded_demo(store, now, tz)
    call_id = add_visitor_call(
        store, round_id, "visitor-aaaaaaaaaaaa", now, tz, resident_id="v-7", ward_id=ward_ids("churu")[0],
        feeling="mild_symptoms", fluids_food="low",
    )
    start = now - timedelta(seconds=100)
    store.update("calls", call_id, {
        "started_at": start,
        "ended_at": start + timedelta(seconds=90),
        "end_outcome": "completed",
        "respondent": "self",
        "language_used": "hi",
        "answers.feeling.at": start + timedelta(seconds=20),
        "answers.fluids_food.at": start + timedelta(seconds=35),
        "transcript": [
            {"role": "agent", "text": "Namaste", "at": start + timedelta(seconds=1)},
            {"role": "resident", "text": "haan", "at": start + timedelta(seconds=6)},
        ],
        "browser_ticket": "must-not-leak", "stream_token": "must-not-leak",
    })
    store.update("residents", "v-7", {"card_id": "alone_dizzy"})
    return store.get("calls", call_id)


def test_the_timeline_replays_the_call_in_order(store, finished_call):
    timeline = build_timeline(finished_call, load_feed(store, finished_call))
    assert (timeline["card_id"], timeline["language"], timeline["duration_s"]) == ("alone_dizzy", "hi", 90)
    # Who answered has no time of its own in the store, so it is placed at the start.
    assert [e for e in timeline["events"] if e["type"] == "respondent"] == [{"t": 0.0, "type": "respondent", "value": "self"}]
    events = [e for e in timeline["events"] if e["type"] != "respondent"]
    assert [(event["t"], event["type"]) for event in events] == [
        (1.0, "transcript"), (6.0, "transcript"), (20.0, "answer"), (35.0, "answer"), (90.0, "ended"),
    ]
    assert events[0] == {"t": 1.0, "type": "transcript", "role": "agent", "text": "Namaste"}
    assert events[2] == {
        "t": 20.0, "type": "answer", "id": "feeling", "value": "mild_symptoms",
        "value_label": "Mild symptoms", "quote": None,
    }
    assert events[-1] == {"t": 90.0, "type": "ended", "status": "completed", "end_outcome": "completed"}
    assert timeline["events"][-1]["type"] == "ended"  # always last, whatever the times
    assert timeline["result"]["case"]["tier"] == "amber"
    assert "must-not-leak" not in json.dumps(jsonable_encoder(timeline))


def test_no_recording_means_null_and_no_audio(tmp_path):
    assert load_recording(tmp_path) is None
    assert audio_file(tmp_path) is None
    (tmp_path / "recorded_call.json").write_text("not json", encoding="utf-8")
    assert load_recording(tmp_path) is None


def test_the_endpoint_serves_the_file_and_its_audio(store, now, tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "RECORDED_DIR", tmp_path)
    client = TestClient(create_app(store, RecordingQueue(), Settings(_env_file=None, internal_secret="s3cret"), clock=lambda: now))
    assert client.get("/api/demo/recorded-call").json() == {"recording": None}
    assert client.get("/api/demo/recorded-call/audio").status_code == 404

    (tmp_path / "recorded_call.json").write_text(json.dumps({"card_id": "alone_dizzy", "events": []}), encoding="utf-8")
    assert client.get("/api/demo/recorded-call").json() == {
        "recording": {"card_id": "alone_dizzy", "events": [], "audio_url": None}
    }
    (tmp_path / "recorded_call.mp3").write_bytes(b"ID3-not-really-audio")
    assert client.get("/api/demo/recorded-call").json()["recording"]["audio_url"] == "/api/demo/recorded-call/audio"
    audio = client.get("/api/demo/recorded-call/audio")
    assert audio.status_code == 200 and audio.content == b"ID3-not-really-audio"
