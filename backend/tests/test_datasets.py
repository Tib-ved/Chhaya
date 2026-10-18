import pytest

from app.datasets import PER_ROUND, DatasetStore, collection_for
from app.db import MemoryStore


def test_demo_renames_per_round_collections_and_shares_the_register():
    base = MemoryStore()
    demo, live = DatasetStore(base, "demo"), DatasetStore(base, "live")
    demo.set("rounds", "churu_heat_20261015", {"id": "churu_heat_20261015", "v": "demo"})
    live.set("rounds", "churu_heat_20261015", {"id": "churu_heat_20261015", "v": "live"})
    assert demo.get("rounds", "churu_heat_20261015")["v"] == "demo"
    assert live.get("rounds", "churu_heat_20261015")["v"] == "live"
    assert base.get("demo_rounds", "churu_heat_20261015")["v"] == "demo"
    demo.set("residents", "r1", {"id": "r1"})
    assert live.get("residents", "r1") == {"id": "r1"}  # the register is shared


@pytest.mark.parametrize("name", sorted(PER_ROUND))
def test_every_store_method_goes_to_the_mapped_collection(name):
    base = MemoryStore()
    demo = DatasetStore(base, "demo")
    demo.set(name, "a", {"id": "a", "n": 1, "s": "x"})
    assert demo.create(name, "b", {"id": "b", "n": 2, "s": "x"}) is True
    demo.update(name, "a", {"n": 3})
    assert demo.transition(name, "a", "n", 3, {"n": 4}) is True
    assert sorted(d["id"] for d in demo.query(name, s="x")) == ["a", "b"]
    demo.delete(name, "b")
    assert base.get(collection_for("demo", name), "a")["n"] == 4
    assert base.get(name, "a") is None


def test_live_names_are_unchanged_and_unknown_datasets_are_refused():
    assert collection_for("live", "rounds") == "rounds"
    assert collection_for("demo", "residents") == "residents"
    with pytest.raises(ValueError):
        DatasetStore(MemoryStore(), "staging")
