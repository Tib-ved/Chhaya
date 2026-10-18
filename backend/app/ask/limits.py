"""How much Ask Chhaya may be asked: a rate per minute, a day's allowance per visitor, a day's cap for
everyone, and how many answers one owner may have streaming at once."""
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Callable

from app.db import Store

PER_MINUTE = 12
VISITOR_DAILY = 100
STREAMS = {True: 3, False: 1}  # team, visitor
USAGE = "ask_usage"


class Limits:
    def __init__(self, store: Store, clock: Callable[[], datetime], daily_cap: int, monotonic=time.monotonic) -> None:
        self._store, self._clock, self._cap, self._monotonic = store, clock, daily_cap, monotonic
        self._recent: dict[str, deque] = defaultdict(deque)
        self._streams: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def _bump(self, doc_id: str) -> int:
        doc = self._store.get(USAGE, doc_id) or {"count": 0}
        count = int(doc.get("count", 0)) + 1
        self._store.set(USAGE, doc_id, {"count": count, "day": doc_id[:10]})
        return count

    def check_and_count(self, owner: str, team: bool) -> str | None:
        """None when the message may go ahead (and it is counted), else why not: rate, daily or resting."""
        now = self._monotonic()
        with self._lock:
            recent = self._recent[owner]
            while recent and now - recent[0] > 60:
                recent.popleft()
            if len(recent) >= PER_MINUTE:
                return "rate"
            day = self._clock().date().isoformat()
            total = (self._store.get(USAGE, day) or {}).get("count", 0)
            if total >= self._cap:
                return "resting"
            if not team and (self._store.get(USAGE, f"{day}_{owner}") or {}).get("count", 0) >= VISITOR_DAILY:
                return "daily"
            recent.append(now)
            self._bump(day)
            self._bump(f"{day}_{owner}")
            return None

    def open_stream(self, owner: str, team: bool) -> bool:
        with self._lock:
            if self._streams[owner] >= STREAMS[team]:
                return False
            self._streams[owner] += 1
            return True

    def close_stream(self, owner: str) -> None:
        with self._lock:
            self._streams[owner] = max(0, self._streams[owner] - 1)
