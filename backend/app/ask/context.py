"""What Ask Chhaya's tools read: the app's store and clock, set by create_app.

A request's own store and clock (`use`, set per answer) win over the app-wide ones, so the live
and demo apps in one process each answer from their own dataset.

A conversation belongs to one dataset, kept as `mode` in its session state; the routes refuse a
turn sent to the other app, and hand the conversation's mode to `use`, so `mode()` is what the tools
serve.

Under `adk eval` there is no app: with ASK_FIXTURE=1 the tools read the fixed evaluation dataset.
"""
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Callable

from app.db import Store

_store: list[Store] = []
_clock: list[Callable[[], datetime]] = []
_current: ContextVar[tuple | None] = ContextVar("ask_context", default=None)


MODES = ("live", "demo")


def mode_of_state(state: dict | None) -> str:
    """The dataset a conversation belongs to, from its session state. One without (made before
    conversations stored it) is live."""
    mode = (state or {}).get("mode")
    return mode if mode in MODES else "live"


def dataset_of(store: Store) -> str:
    """The dataset a store serves (app.datasets.DatasetStore); a plain store is live."""
    return getattr(store, "dataset", "live")


@contextmanager
def use(store: Store, clock: Callable[[], datetime], mode: str | None = None):
    """This request's store and clock: each app (live, demo) answers from its own dataset. `mode` is
    the conversation's, from its session state; the routes make sure it is the store's."""
    token = _current.set((store, clock, mode or dataset_of(store)))
    try:
        yield
    finally:
        try:
            _current.reset(token)
        except ValueError:  # a streamed answer closed from another context: nothing of ours to undo there
            pass


def set_store(store: Store) -> None:
    _store[:] = [store]


def set_clock(clock: Callable[[], datetime]) -> None:
    _clock[:] = [clock]


def get_store() -> Store:
    if (current := _current.get()) is not None:
        return current[0]
    if not _store:
        if os.environ.get("ASK_FIXTURE") == "1":
            from evals.ask_chhaya.fixture import FIXTURE_NOW, build_store

            set_store(build_store())
            set_clock(lambda: FIXTURE_NOW)
        else:
            raise RuntimeError("Ask Chhaya has no store: create_app sets one, or ASK_FIXTURE=1 for evaluation")
    return _store[0]


def now() -> datetime:
    if (current := _current.get()) is not None:
        return current[1]()
    get_store()  # the fixture sets its clock with its store
    return _clock[0]() if _clock else datetime.now(timezone.utc)


def mode() -> str:
    """The dataset the tools serve this turn: the conversation's mode, else the store's."""
    if (current := _current.get()) is not None:
        return current[2]
    return dataset_of(get_store())
