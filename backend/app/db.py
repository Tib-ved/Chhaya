import copy
import threading
from typing import Any, Protocol

from app.config import Settings


class Store(Protocol):
    def get(self, collection: str, doc_id: str) -> dict | None: ...
    def set(self, collection: str, doc_id: str, data: dict) -> None: ...
    def create(self, collection: str, doc_id: str, data: dict) -> bool: ...
    def update(self, collection: str, doc_id: str, fields: dict) -> None: ...
    def query(self, collection: str, **equals: Any) -> list[dict]: ...
    def delete(self, collection: str, doc_id: str) -> None: ...
    def transition(
        self, collection: str, doc_id: str, field: str, expected: Any, fields: dict
    ) -> bool:
        """Apply fields only if the document exists and doc[field] == expected. Atomic.

        Returns True only when this call applied the fields. May raise if the underlying
        store fails. After a transient store error a False can mean an earlier attempt
        already applied the change, so a caller that gets False must not assume nothing changed.
        """


class MemoryStore:
    """In-memory store. One lock guards every method: FastAPI runs handlers on a thread pool."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict]] = {}
        self._lock = threading.RLock()  # re-entrant: create calls set

    def get(self, collection: str, doc_id: str) -> dict | None:
        with self._lock:
            doc = self._data.get(collection, {}).get(doc_id)
            return copy.deepcopy(doc) if doc is not None else None

    def set(self, collection: str, doc_id: str, data: dict) -> None:
        with self._lock:
            self._data.setdefault(collection, {})[doc_id] = copy.deepcopy(data)

    def create(self, collection: str, doc_id: str, data: dict) -> bool:
        with self._lock:
            if doc_id in self._data.get(collection, {}):
                return False
            self.set(collection, doc_id, data)
            return True

    def update(self, collection: str, doc_id: str, fields: dict) -> None:
        with self._lock:
            doc = self._data.get(collection, {}).get(doc_id)
            if doc is None:
                raise KeyError(f"{collection}/{doc_id}")
            for path, value in fields.items():
                *parents, leaf = path.split(".")
                target = doc
                for key in parents:
                    target = target.setdefault(key, {})
                target[leaf] = copy.deepcopy(value)

    def query(self, collection: str, **equals: Any) -> list[dict]:
        with self._lock:
            return [
                copy.deepcopy(doc)
                for doc in self._data.get(collection, {}).values()
                if all(doc.get(k) == v for k, v in equals.items())
            ]

    def delete(self, collection: str, doc_id: str) -> None:
        with self._lock:
            self._data.get(collection, {}).pop(doc_id, None)

    def transition(
        self, collection: str, doc_id: str, field: str, expected: Any, fields: dict
    ) -> bool:
        with self._lock:
            doc = self._data.get(collection, {}).get(doc_id)
            if doc is None or doc.get(field) != expected:
                return False
            self.update(collection, doc_id, fields)
            return True


class FirestoreStore:
    def __init__(self, project: str) -> None:
        from google.cloud import firestore

        self._db = firestore.Client(project=project or None)

    def _doc(self, collection: str, doc_id: str):
        return self._db.collection(collection).document(doc_id)

    def get(self, collection: str, doc_id: str) -> dict | None:
        snapshot = self._doc(collection, doc_id).get()
        return snapshot.to_dict() if snapshot.exists else None

    def set(self, collection: str, doc_id: str, data: dict) -> None:
        self._doc(collection, doc_id).set(data)

    def create(self, collection: str, doc_id: str, data: dict) -> bool:
        from google.api_core.exceptions import Conflict

        try:
            self._doc(collection, doc_id).create(data)
            return True
        except Conflict:
            return False

    def update(self, collection: str, doc_id: str, fields: dict) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self._doc(collection, doc_id).update(fields)
        except NotFound:
            raise KeyError(f"{collection}/{doc_id}") from None

    def query(self, collection: str, **equals: Any) -> list[dict]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        query = self._db.collection(collection)
        for field, value in equals.items():
            query = query.where(filter=FieldFilter(field, "==", value))
        return [snapshot.to_dict() for snapshot in query.stream()]

    def delete(self, collection: str, doc_id: str) -> None:
        self._doc(collection, doc_id).delete()

    def transition(
        self, collection: str, doc_id: str, field: str, expected: Any, fields: dict
    ) -> bool:
        """True only if this call applied the fields; may raise on store failure. After a
        transient error a retried call can return False because an earlier attempt succeeded."""
        from google.cloud import firestore

        reference = self._doc(collection, doc_id)

        @firestore.transactional
        def apply(transaction) -> bool:
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists or (snapshot.to_dict() or {}).get(field) != expected:
                return False
            transaction.update(reference, fields)
            return True

        return apply(self._db.transaction())


def get_store(settings: Settings) -> Store:
    if settings.store == "firestore":
        return FirestoreStore(settings.gcp_project)
    return MemoryStore()
