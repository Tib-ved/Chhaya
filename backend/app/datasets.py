"""Two datasets in one store: live (plain collection names) and demo (the same names with `demo_`).

Only per-round data is split; the register (districts, wards, residents, workers, facilities) and
the app's own records (sessions, usage ledgers) are shared. Every request is served by an app built
on one of these wrappers, so the two datasets can never be read together (spec L8, L9)."""
from typing import Literal

from app.db import Store

DATASETS = ("live", "demo")
Dataset = Literal["live", "demo"]
# Everything that belongs to a day's rounds. "demo" is the current-day pointer (app.demo.DEMO_COLLECTION).
PER_ROUND = frozenset({
    "alerts", "rounds", "calls", "cases", "demo", "weather",
    "ops_tasks", "ops_events", "ops_proposals", "ops_receipts", "ops_threads", "ops_messages",
})


def collection_for(dataset: str, name: str) -> str:
    if dataset not in DATASETS:
        raise ValueError(f"unknown dataset {dataset!r}")
    return f"demo_{name}" if dataset == "demo" and name in PER_ROUND else name


class DatasetStore:
    def __init__(self, store: Store, dataset: str) -> None:
        collection_for(dataset, "rounds")  # refuses an unknown dataset
        self.base, self.dataset = store, dataset

    def _c(self, name: str) -> str:
        return collection_for(self.dataset, name)

    def get(self, collection, doc_id):
        return self.base.get(self._c(collection), doc_id)

    def set(self, collection, doc_id, data):
        return self.base.set(self._c(collection), doc_id, data)

    def create(self, collection, doc_id, data):
        return self.base.create(self._c(collection), doc_id, data)

    def update(self, collection, doc_id, fields):
        return self.base.update(self._c(collection), doc_id, fields)

    def query(self, collection, **equals):
        return self.base.query(self._c(collection), **equals)

    def delete(self, collection, doc_id):
        return self.base.delete(self._c(collection), doc_id)

    def transition(self, collection, doc_id, field, expected, fields):
        return self.base.transition(self._c(collection), doc_id, field, expected, fields)
