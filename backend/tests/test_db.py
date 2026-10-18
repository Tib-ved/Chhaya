import os
import threading
import uuid

import pytest

from app.db import FirestoreStore, MemoryStore

FIRESTORE_PROJECT = os.getenv("CHHAYA_FIRESTORE_TEST_PROJECT") or (
    "chhaya-test" if os.getenv("FIRESTORE_EMULATOR_HOST") else ""
)
firestore_only = pytest.mark.skipif(
    not FIRESTORE_PROJECT, reason="no Firestore emulator or test project configured"
)


@pytest.fixture(params=["memory", pytest.param("firestore", marks=firestore_only)])
def any_store(request, col):
    if request.param == "memory":
        yield MemoryStore()
        return
    store = FirestoreStore(project=FIRESTORE_PROJECT)
    yield store
    for doc_id in ("a", "b", "c"):  # the only document ids these tests create
        store.delete(col, doc_id)


@pytest.fixture
def col():
    return f"t_{uuid.uuid4().hex[:8]}"


def test_get_missing_returns_none(any_store, col):
    assert any_store.get(col, "nope") is None


def test_set_then_get(any_store, col):
    any_store.set(col, "a", {"x": 1})
    assert any_store.get(col, "a") == {"x": 1}


def test_create_refuses_existing(any_store, col):
    assert any_store.create(col, "a", {"x": 1}) is True
    assert any_store.create(col, "a", {"x": 2}) is False
    assert any_store.get(col, "a") == {"x": 1}


def test_update_dotted_path_sets_nested_field(any_store, col):
    any_store.set(col, "a", {"answers": {}, "status": "queued"})
    any_store.update(col, "a", {"answers.feeling": {"value": "ok"}, "status": "completed"})
    assert any_store.get(col, "a") == {"answers": {"feeling": {"value": "ok"}}, "status": "completed"}


def test_update_missing_raises_key_error(any_store, col):
    with pytest.raises(KeyError):
        any_store.update(col, "nope", {"x": 1})


def test_query_filters_on_all_fields(any_store, col):
    any_store.set(col, "a", {"ward": "w1", "tier": "red"})
    any_store.set(col, "b", {"ward": "w1", "tier": "green"})
    any_store.set(col, "c", {"ward": "w2", "tier": "red"})
    assert [d["tier"] for d in any_store.query(col, ward="w1", tier="red")] == ["red"]
    assert len(any_store.query(col)) == 3


def test_returned_documents_are_copies(col):
    store = MemoryStore()
    store.set(col, "a", {"list": [1]})
    store.get(col, "a")["list"].append(2)
    assert store.get(col, "a") == {"list": [1]}


WORKERS, ROUNDS = 6, 200


def test_memory_store_survives_concurrent_writers_and_readers(col):
    store = MemoryStore()
    store.set(col, "shared", {"answers": {}, "n": 0})
    errors: list[BaseException] = []
    ready = threading.Barrier(WORKERS)

    def work(worker: int) -> None:
        try:
            ready.wait(timeout=5)
            for n in range(ROUNDS):
                store.set(col, f"w{worker}_{n}", {"worker": worker, "n": n})
                store.create(col, f"c{worker}_{n}", {"worker": worker, "n": n})
                store.update(col, "shared", {f"answers.q{worker}": {"value": n}})
                store.query(col, worker=worker)
                store.get(col, "shared")
        except BaseException as error:  # recorded and asserted on below
            errors.append(error)

    threads = [threading.Thread(target=work, args=(worker,)) for worker in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(store.query(col)) == WORKERS * ROUNDS * 2 + 1
    assert store.get(col, "shared")["answers"] == {f"q{w}": {"value": ROUNDS - 1} for w in range(WORKERS)}


def test_memory_store_methods_all_take_the_lock(col):
    store = MemoryStore()
    store.set(col, "a", {"x": 1})
    calls = [
        lambda: store.get(col, "a"),
        lambda: store.set(col, "b", {"x": 2}),
        lambda: store.create(col, "c", {"x": 3}),
        lambda: store.update(col, "a", {"x": 4}),
        lambda: store.query(col),
    ]
    for call in calls:
        done = threading.Event()
        with store._lock:
            thread = threading.Thread(target=lambda: (call(), done.set()))
            thread.start()
            assert not done.wait(timeout=0.05)  # blocked while another thread holds the lock
        thread.join(timeout=5)
        assert done.is_set()


def test_delete_removes_and_tolerates_missing(any_store, col):
    any_store.set(col, "a", {"x": 1})
    any_store.delete(col, "a")
    assert any_store.get(col, "a") is None
    any_store.delete(col, "a")  # no error


def test_transition_applies_when_field_matches(any_store, col):
    any_store.set(col, "a", {"status": "queued", "n": 1})
    assert any_store.transition(col, "a", "status", "queued", {"status": "dialing", "n": 2}) is True
    assert any_store.get(col, "a") == {"status": "dialing", "n": 2}


def test_transition_refuses_when_field_differs(any_store, col):
    any_store.set(col, "a", {"status": "dialing"})
    assert any_store.transition(col, "a", "status", "queued", {"status": "failed"}) is False
    assert any_store.get(col, "a") == {"status": "dialing"}


def test_transition_refuses_missing_document(any_store, col):
    assert any_store.transition(col, "nope", "status", "queued", {"status": "dialing"}) is False


def test_only_one_of_many_concurrent_transitions_wins(col):
    store = MemoryStore()
    store.set(col, "a", {"status": "queued"})
    wins = []

    def attempt():
        wins.append(store.transition(col, "a", "status", "queued", {"status": "dialing"}))

    threads = [threading.Thread(target=attempt) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert wins.count(True) == 1


@firestore_only
def test_only_one_of_many_concurrent_transitions_wins_on_firestore(col):
    store = FirestoreStore(project=FIRESTORE_PROJECT)
    results: list = []
    errors: list[BaseException] = []

    def attempt():
        try:
            results.append(store.transition(col, "a", "status", "queued", {"status": "dialing"}))
        except BaseException as error:  # recorded and asserted on below
            errors.append(error)

    try:
        store.set(col, "a", {"status": "queued"})
        threads = [threading.Thread(target=attempt) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == [], [f"{type(e).__name__}: {e}" for e in errors]
        assert results.count(True) == 1
        assert store.get(col, "a") == {"status": "dialing"}
    finally:
        store.delete(col, "a")
