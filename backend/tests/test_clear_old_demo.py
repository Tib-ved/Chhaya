from datetime import datetime, timezone

from app.db import MemoryStore
from scripts.clear_old_demo import clear_old_demo

NOW = datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)


def _alert(alert_id, source):
    return {"id": alert_id, "source": source, "district_id": "churu", "severity": "red", "received_at": NOW}


def _seed(store):
    store.set("alerts", "a-demo", _alert("a-demo", "scenario"))
    store.set("alerts", "a-live", _alert("a-live", "forecast_state_rule"))
    store.set("rounds", "r-demo", {"id": "r-demo", "alert_id": "a-demo"})
    store.set("rounds", "r-live", {"id": "r-live", "alert_id": "a-live"})
    store.set("cases", "c-demo", {"id": "c-demo", "round_id": "r-demo"})
    store.set("cases", "c-live", {"id": "c-live", "round_id": "r-live"})
    store.set("calls", "k-demo", {"id": "k-demo", "round_id": "r-demo"})
    store.set("calls", "k-live", {"id": "k-live", "round_id": "r-live"})
    store.set("demo", "current", {"id": "current", "round_id": "r-demo", "rounds": {"churu": "r-demo"}})


def test_only_live_records_remain():
    store = MemoryStore()
    _seed(store)
    counts = clear_old_demo(store, apply=True)
    assert counts == {"alerts": 1, "rounds": 1, "cases": 1, "calls": 1, "demo": 1}
    assert [a["id"] for a in store.query("alerts")] == ["a-live"]
    assert [r["id"] for r in store.query("rounds")] == ["r-live"]
    assert [c["id"] for c in store.query("cases")] == ["c-live"]
    assert [c["id"] for c in store.query("calls")] == ["k-live"]
    assert store.get("demo", "current") is None


def test_without_apply_nothing_changes():
    store = MemoryStore()
    _seed(store)
    assert clear_old_demo(store, apply=False)["rounds"] == 1
    assert len(store.query("alerts")) == 2 and store.get("demo", "current")


def test_pointer_to_a_live_round_is_kept():
    store = MemoryStore()
    _seed(store)
    store.set("demo", "current", {"id": "current", "round_id": "r-live", "rounds": {"churu": "r-live"}})
    assert clear_old_demo(store, apply=True)["demo"] == 0
    assert store.get("demo", "current")
