from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Hazard = Literal["heat", "cold"]
Severity = Literal["yellow", "orange", "red"]
Tier = Literal["red", "amber", "green"]
Language = Literal["hi", "en", "mr", "gu", "mwr"]
Condition = Literal["heart", "diabetes", "kidney", "respiratory", "pregnancy", "disability"]
CallStatus = Literal[
    "queued", "dialing", "in_progress", "completed", "no_answer", "busy", "failed", "dropped"
]
FinalCallStatus = Literal["completed", "dropped", "no_answer", "busy", "failed"]
Respondent = Literal["self", "family", "other", "unknown"]

FAILED_STATUSES = {"no_answer", "busy", "failed"}
REACHED_STATUSES = {"completed", "dropped"}
WAITING_STATUSES = {"queued", "dialing"}
CONVERSATION_STATUSES = {"in_progress", "completed", "dropped"}
MAX_ATTEMPTS = 3
VISITOR_USAGE = "demo_usage"  # one record per call a visitor started; the limits are counted from it


class Consent(BaseModel):
    given: bool
    at: datetime | None = None
    method: str = ""


class Contact(BaseModel):
    name: str
    phone: str


class Resident(BaseModel):
    id: str
    name: str
    name_hi: str = ""  # the name in Devanagari, for the worker's Hindi screens
    phone: str
    age: int
    language: Language
    district_id: str
    ward_id: str
    lat: float
    lng: float
    lives_alone: bool = False
    outdoor_worker: bool = False
    conditions: list[Condition] = []
    emergency_contact: Contact | None = None
    consent: Consent
    risk_score: int = 0
    visitor_id: str | None = None  # set on the temporary resident behind a visitor's own call
    card_id: str | None = None  # which character card that resident was copied from


class Alert(BaseModel):
    id: str
    source: Literal["imd", "sachet", "weather", "replay", "declared", "scenario", "forecast_state_rule"]
    hazard: Hazard
    severity: Severity
    district_id: str
    valid_from: datetime
    valid_to: datetime
    raw: dict = {}
    received_at: datetime


class Round(BaseModel):
    id: str
    alert_id: str
    district_id: str
    hazard: Hazard
    status: Literal["open", "calling", "complete"] = "open"
    opened_at: datetime | None = None  # when the alert that opened the round arrived
    started_at: datetime | None = None
    completed_at: datetime | None = None
    peak_temp_c: float | None = None
    peak_hour_local: int | None = None
    as_of: datetime | None = None  # a demo round's fixed clock: its seeded waits are timed against it
    forecast_hours: list[dict] = []  # [{hour, temp_c}] for the day, where the round holds one


class Answer(BaseModel):
    value: str  # deliberately not an enum: triage must cope with bad values
    quote: str = ""
    at: datetime


class Emergency(BaseModel):
    flagged: bool
    reason: str = ""
    at: datetime


class Call(BaseModel):
    id: str
    round_id: str
    resident_id: str
    attempt: int = 1
    channel: Literal["phone", "browser", "ai_resident"] = "phone"
    status: CallStatus = "queued"
    twilio_sid: str | None = None
    stream_token: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_s: int | None = None
    respondent: Respondent = "unknown"
    language_used: str | None = None
    answers: dict[str, Answer] = {}
    emergency: Emergency | None = None
    transcript: list[dict] = []
    end_outcome: str | None = None
    live_settings: dict = {}  # the voice settings the call ran with, to compare test calls; never served
    timeline: list[dict] = []  # what happened on the line and when, never what was said; never served
    browser_ticket: str | None = None
    browser_ticket_expires: datetime | None = None
    visitor_id: str | None = None  # set on a visitor's own call; hidden from everyone else
    created_at: datetime | None = None
    not_before: datetime | None = None  # when the call is due to be dialled; the sweep re-queues overdue ones
    dialing_at: datetime | None = None  # when it was claimed for dialling; the sweep finishes stale claims
    simulated: Literal["ai_resident"] | None = None  # a recorded AI-resident conversation, replayed (app.replay)
    conversation_id: str | None = None  # which conversation of the library it replays


class Case(BaseModel):
    id: str
    round_id: str
    resident_id: str
    ward_id: str
    worker_id: str | None = None
    tier: Tier
    reasons: list[str]
    note: str = ""
    note_tries: int = 0  # how many times the text model has been asked for the note
    status: Literal["open", "visited", "resolved"] = "open"
    visited_at: datetime | None = None
    outcome: str | None = None
    updated_at: datetime
    opened_at: datetime | None = None  # when the case was first stored; None on older cases
    visitor_id: str | None = None
    escalation: dict | None = None  # {reason, note, at, acknowledged_at, reply}: flagged to the officer
    raised: dict | None = None  # {observation, at}: a worker raised an amber case to red
    reassigned_from: str | None = None  # the worker the officer moved the case from
    visit_note: str = ""
