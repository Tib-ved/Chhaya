from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from app.config import Settings
from app.db import Store
from app.dispatch import TaskQueue


@dataclass
class CallDeps:
    """Everything a call needs from outside. Tests swap the last three for fakes."""

    store: Store
    queue: TaskQueue
    settings: Settings
    clock: Callable[[], datetime]
    tz: ZoneInfo
    open_session: Callable  # (settings, instruction) -> async context manager yielding a session
    place_call: Callable | None = None  # (to, twiml, status_callback) -> provider call id
    validate_twilio: Callable | None = None  # (url, params, signature) -> bool
    after_call: Callable | None = None  # (call_id) -> None; runs once a call has ended, e.g. the case note
