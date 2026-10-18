"""Ends a call with a given outcome, as if it had happened. The simulate endpoint and the demo
round's script both go through here, so a scripted call is stored exactly like a real one."""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.db import Store
from app.dispatch import FINAL_STATUSES, TaskQueue, handle_call_outcome
from app.models import Answer, Emergency
from app.triage import ALLOWED_VALUES


class CallAlreadyFinished(Exception):
    """The call already has a final status. The message is that status."""


def apply_outcome(
    store: Store,
    queue: TaskQueue,
    call_id: str,
    status: str,
    now: datetime,
    tz: ZoneInfo,
    *,
    answers: dict[str, str] | None = None,
    quotes: dict[str, str] | None = None,
    emergency_reason: str | None = None,
    respondent: str = "unknown",
    start_hour: int = 8,
    end_hour: int = 20,
) -> str:
    """Write what the call learned, then record its outcome. Returns the status stored, which
    can differ from `status`: a reached status with no sign of a person is stored as no_answer.

    Raises ValueError for an unknown question id, KeyError for an unknown call and
    CallAlreadyFinished for a call that has a final status. Nothing is written in those cases."""
    answers, quotes = answers or {}, quotes or {}
    unknown = sorted((set(answers) | set(quotes)) - set(ALLOWED_VALUES))
    if unknown:
        raise ValueError(f"unknown question ids: {unknown}")
    call = store.get("calls", call_id)
    if call is None:
        raise KeyError(f"calls/{call_id}")
    if call["status"] in FINAL_STATUSES:
        raise CallAlreadyFinished(call["status"])
    fields: dict = {"respondent": respondent}
    for question, value in answers.items():
        fields[f"answers.{question}"] = Answer(
            value=value, quote=quotes.get(question, ""), at=now
        ).model_dump()
    if emergency_reason:
        fields["emergency"] = Emergency(flagged=True, reason=emergency_reason, at=now).model_dump()
    store.update("calls", call_id, fields)
    handle_call_outcome(store, queue, call_id, status, now, tz, start_hour, end_hour)
    return store.get("calls", call_id)["status"]
