from fastapi.testclient import TestClient

from app.config import Settings
from app.datasets import DatasetStore
from app.db import MemoryStore
from app.dispatch import RecordingQueue
from app.router import build_apps


def test_the_two_datasets_never_see_each_other():
    base = MemoryStore()
    settings = Settings(_env_file=None, internal_secret="s3cret")
    DatasetStore(base, "demo").set("rounds", "churu_heat_20261015", {"id": "churu_heat_20261015", "district_id": "churu", "hazard": "heat", "status": "open", "alert_id": "a"})
    c = TestClient(build_apps(base, RecordingQueue(), settings))
    team = {"X-Internal-Secret": "s3cret"}
    with c:
        demo = c.get("/api/rounds?mode=demo", headers=team).json()
        live = c.get("/api/rounds", headers=team).json()
    assert any("churu_heat_20261015" in str(r) for r in (demo if isinstance(demo, list) else demo.get("rounds", [])))
    assert "churu_heat_20261015" not in str(live)


def test_live_data_is_invisible_in_demo():
    base = MemoryStore()
    settings = Settings(_env_file=None, internal_secret="s3cret")
    DatasetStore(base, "live").set("rounds", "sikar_heat_20261015", {"id": "sikar_heat_20261015", "district_id": "sikar", "hazard": "heat", "status": "open", "alert_id": "forecast_sikar_heat_20261015"})
    DatasetStore(base, "live").set("weather", "sikar_20261015_0600", {"id": "sikar_20261015_0600", "area_id": "sikar", "local_day": "2026-10-15"})
    c = TestClient(build_apps(base, RecordingQueue(), settings))
    team = {"X-Internal-Secret": "s3cret"}
    with c:
        live = c.get("/api/rounds", headers=team).json()
        demo = c.get("/api/rounds?mode=demo", headers=team).json()
    assert "sikar_heat_20261015" in str(live)
    assert "sikar_heat_20261015" not in str(demo)
    assert DatasetStore(base, "demo").query("weather") == [] and DatasetStore(base, "demo").get("rounds", "sikar_heat_20261015") is None
