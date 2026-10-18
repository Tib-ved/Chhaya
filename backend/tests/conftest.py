from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.db import MemoryStore


@pytest.fixture
def tz():
    return ZoneInfo("Asia/Kolkata")


@pytest.fixture
def now():
    # 10:00 in Asia/Kolkata
    return datetime(2026, 5, 20, 4, 30, tzinfo=timezone.utc)


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture(autouse=True)
def no_recorded_library(tmp_path_factory, monkeypatch):
    """Rounds opened in tests replay nothing unless a test gives them a library: the recorded
    one in seed/fixtures changes whenever it is recorded again."""
    monkeypatch.setattr("app.replay.LIBRARY", tmp_path_factory.getbasetemp() / "no_ai_calls.json")
