"""The shapes of what the role-view routes return, for the OpenAPI description.

These describe responses; they are not used to build them. The routes still return plain
dicts, so a screen is never filtered or checked by a model here. A test validates real
responses against them, and `extra="forbid"` makes it fail when a route returns a field that
is not written here. Regenerate backend/openapi.json (python -m scripts.export_openapi) after
a change."""
import warnings
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import Hazard, Language, Respondent, Severity, Tier

# The routes' key is `register`, which pydantic warns shadows a BaseModel method. The key is the contract.
warnings.filterwarnings("ignore", message='Field name "register"', category=UserWarning)

CallStatusShown = Literal["not_called", "reached", "no_answer", "trying"]
CaseStatusShown = Literal["open", "visited"]
Outcome = Literal["advised_ok", "supplies_given", "referred", "hospital", "not_home"]
Need = Literal["water_ors", "medicine_check", "cooling_centre"]
AgeBand = Literal["Under 55", "55 to 64", "65 to 74", "75 and over"]
Worry = Literal["amber", "red"]
EscalationReason = Literal["transport", "supplies", "missing", "other"]
Observation = Literal["confused", "fainted", "dry_skin", "vomiting", "chest_breathing", "other"]


class Shape(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Detail(Shape):
    """An error from FastAPI: a missing case, a caller who may not act, or a refused visit."""

    detail: str = Field(description="What went wrong, in words a person can read.")


class Refusal(Detail):
    """An action that cannot be done to this case in its present state."""

    reason: Literal[
        "not_open", "already_flagged", "no_flag", "not_amber", "green_case", "bad_value", "unknown_worker", "same_worker",
    ] = Field(description="A fixed word for the refusal, to branch on. The route's description lists which ones it can return.")


class InvalidRequest(BaseModel):
    """FastAPI's answer (422) to a request body that is malformed, missing a field or too long."""

    model_config = ConfigDict(extra="allow")  # FastAPI adds more keys to each issue than are listed here

    detail: list["InvalidField"] = Field(description="One entry for each field that was wrong.")


class InvalidField(BaseModel):
    model_config = ConfigDict(extra="allow")

    loc: list[str | int] = Field(description="Where the problem is, such as ['body', 'reason'].")
    msg: str = Field(description="What is wrong, in English.")
    type: str = Field(description="A fixed word for the kind of problem.")


class RoundWarning(Shape):
    """The heat or cold warning behind a district's round. Null wherever the district has no round."""

    hazard: Hazard
    severity: Severity | None = Field(description="Null if the warning's record has gone.")
    source: Literal["imd", "sachet", "weather", "replay", "declared", "scenario", "forecast_state_rule"] | None = Field(description="Who issued the warning. Null if its record has gone.")
    scenario: bool = Field(description="True when the warning was raised by the heat scenario through the trigger rules.")
    reason: str | None = Field(description="One plain sentence on why it was raised, such as the forecast peak against the threshold, or the district against its state for a forecast_state_rule warning. Null for other sources.")
    threshold_c: float | None = Field(description="The heat threshold the forecast met, in Celsius. Null unless it is a scenario warning.")
    raised_at: datetime | None = Field(description="When the warning was received. Null if its record has gone.")
    peak_temp_c: float | None = Field(description="The day's highest forecast temperature, in Celsius.")
    peak_hour_local: int | None = Field(description="The local hour (0 to 23) of that peak.")
    opened_at: datetime | None = Field(description="When the warning arrived and the round opened.")


class RoundFigures(Shape):
    """The round's counts. Every one counts residents or cases in the part shown (district, ward or one worker)."""

    called: int = Field(description="Residents with at least one call attempt.")
    reached: int = Field(description="Residents who were spoken to (a call completed or dropped mid-way).")
    no_answer: int = Field(description="Residents never reached, after the last attempt allowed.")
    trying: int = Field(description="Residents never reached yet but still being tried.")
    red: int = Field(description="Residents whose case is red.")
    amber: int = Field(description="Residents whose case is amber.")
    green: int = Field(description="Residents whose case is green.")
    to_visit: int = Field(description="Red and amber cases still waiting for a visit.")
    visited: int = Field(description="Red and amber cases already visited.")
    red_open: int = Field(description="Red cases still waiting for a visit.")
    median_minutes_to_visit: float | None = Field(description="Median minutes from a red or amber case opening to its visit. Null until one has been visited.")


class AgeBandCount(Shape):
    band: AgeBand
    count: int


class ProfileGroup(Shape):
    id: str = Field(description="'high_risk', 'lives_alone', or a health condition such as 'diabetes'.")
    label: str = Field(description="The group's name in English, ready to show.")
    count: int


class RegisterProfile(Shape):
    """Who is on the register: the people who would be called, whether or not there is a round."""

    residents: int
    age_bands: list[AgeBandCount] = Field(description="Always the four bands, youngest first.")
    groups: list[ProfileGroup] = Field(description="High risk, lives alone, then one group per health condition found.")


class Problem(Shape):
    """A rule that put residents on the list, with how many it applies to."""

    rule: str = Field(description="The rule's id, such as 'A1'.")
    label: str = Field(description="The rule in words.")
    count: int


class Progress(Shape):
    """A point every ten minutes from the round's opening to its clock; the four lists have equal length."""

    minutes: list[int] = Field(description="Minutes since the round opened.")
    reached: list[int] = Field(description="Residents spoken to by then, running total.")
    opened: list[int] = Field(description="Cases opened by then, running total.")
    visited: list[int] = Field(description="Cases visited by then, running total.")


class ForecastHour(Shape):
    hour: int = Field(description="Local hour of the day, 0 to 23.")
    temp_c: float


class Place(Shape):
    """A health centre."""

    name: str
    lat: float
    lng: float


class EscalationNote(Shape):
    """A worker's message to the officer about a case."""

    reason: EscalationReason
    note: str = Field(description="Free text from the worker, cut to 200 characters.")
    at: datetime = Field(description="When the worker flagged it.")
    acknowledged_at: datetime | None = Field(description="When the officer acknowledged it. Null while it waits.")
    reply: str = Field(description="The officer's reply. Empty until acknowledged.")


class EscalationShown(EscalationNote):
    reason_label: str = Field(description="The reason in words, ready to show.")


class RaisedNote(Shape):
    """A worker raised an amber case to red after seeing the person."""

    observation: Observation
    at: datetime


class RaisedShown(RaisedNote):
    observation_label: str = Field(description="What the worker saw, in words, ready to show.")


class ResidentRow(Shape):
    """One resident as every list shows them: the register entry joined to this round's call and case."""

    id: str
    name: str
    name_hi: str = Field(description="The name in Devanagari. Empty where there is none.")
    age: int
    ward_id: str | None
    ward_name: str | None
    worker_id: str | None = Field(description="The worker who holds the case, or if there is none, the ward's worker.")
    risk_score: int = Field(description="The register's score from age, living alone, outdoor work and conditions. Higher is riskier.")
    risk_factors: list[str] = Field(description="Why the score is what it is, in short English phrases.")
    lives_alone: bool
    outdoor_worker: bool
    conditions: list[str] = Field(description="Health conditions: heart, diabetes, kidney, respiratory, pregnancy, disability.")
    age_band: AgeBand
    high_risk: bool = Field(description="True when risk_score is 5 or more.")
    lat: float | None
    lng: float | None
    call_status: CallStatusShown = Field(description="not_called: no attempt yet. reached: spoken to. no_answer: never reached and out of attempts. trying: never reached yet but more attempts to come.")
    attempts: int = Field(description="Call attempts so far; 0 if none.")
    tier: Tier | None = Field(description="The case's tier. Null where there is no case: not called, or not reached yet.")
    reasons: list[str] = Field(description="Ids of the rules behind the tier, without the green rule. Empty where there is no case.")
    reason_labels: list[str] = Field(description="The same rules in words, in the same order.")
    needs: list[Need] = Field(description="What a worker should bring, from what the resident said.")
    case_id: str | None = Field(description="Null where there is no case.")
    case_status: CaseStatusShown | None = Field(description="Null where there is no case.")
    opened_at: datetime | None = Field(description="When the case opened.")
    visited_at: datetime | None
    outcome: Outcome | None = Field(description="How the visit went. Null until visited.")
    outcome_label: str | None = Field(description="The outcome in words.")
    waiting_minutes: int | None = Field(description="Minutes the case has waited. Null unless the case is open and needs a visit (red or amber).")
    escalated: bool = Field(description="True only while a worker's flag has not been acknowledged by the officer.")
    raised: bool = Field(description="True if a worker raised this case from amber to red.")
    simulated: Literal["ai_resident"] | None = Field(description="'ai_resident' when the call was a recorded conversation with an AI-played resident. Null otherwise.")
    simulated_visit: bool = Field(description="True when the visit on the case was simulated.")
    yours: bool = Field(description="True for the record made by the caller's own test call (sent with X-Visitor). Always false for the team and for seeded residents.")


class Thresholds(Shape):
    orange: float = Field(description="Forecast maximum in Celsius at or above which the heat alert is orange.")
    red: float = Field(description="Forecast maximum in Celsius at or above which the heat alert is red.")


class CallPlanEntry(Shape):
    """A resident the day's round will call early, before the alert has opened it."""

    id: str
    name: str
    name_hi: str
    age: int
    ward_name: str | None
    risk_score: int
    reasons: list[str] = Field(description="Why they are called early: the register's risk factors, in short English phrases.")


class DistrictBrief(Shape):
    id: str
    name: str
    state: str | None


class DistrictCard(Shape):
    """A district on the region screen."""

    id: str
    name: str
    state: str | None
    wards: int
    residents: int = Field(description="People on the register, plus the caller's own test resident where it is in this district.")
    high_risk: int
    living_alone: int
    aged_75_plus: int
    warning: RoundWarning | None = Field(description="Null where the district has no warning, so no round.")
    round: RoundFigures | None = Field(description="Null where the district has no round.")


class WardCard(Shape):
    """A ward on its district's screen."""

    id: str
    name: str
    worker_id: str | None
    worker_name: str | None
    residents: int
    high_risk: int
    round: RoundFigures | None = Field(description="Null where the district has no round.")


class RegionView(Shape):
    districts: list[DistrictCard] = Field(description="Districts with a warning first, then the rest, each by name.")


class DistrictView(Shape):
    district: DistrictBrief
    warning: RoundWarning | None = Field(description="Null where the district has no warning.")
    as_of: datetime | None = Field(description="The moment the round's figures are timed to. Null where there is no round.")
    register: RegisterProfile = Field(description="Who is on the register here, whether or not there is a round.")
    round: RoundFigures | None = Field(description="Null where the district has no round.")
    wards: list[WardCard]
    problems: list[Problem] = Field(description="Rules behind the current cases, the most common first. Empty without a round.")
    progress: Progress | None = Field(description="Null where there is no round, or its span is too long to chart.")
    forecast: list[ForecastHour] | None = Field(description="The day's hourly forecast the warning was raised on. Without one: in the demo dataset, the heat scenario's curve before the round exists; live, the newest stored Google Weather hours up to today. Null where there is none.")
    forecast_source: Literal["scenario", "round", "weather"] | None = Field(description="Where `forecast` comes from: \"round\" for the curve the round recorded, \"scenario\" for the demo heat scenario's curve before the round exists, \"weather\" for the stored live weather. Null where there is no forecast.")
    real_forecast: list[ForecastHour] | None = Field(description="The real weather forecast for the day, where the round holds one. Null otherwise.")
    thresholds: Thresholds | None = Field(description="The demo heat scenario's thresholds, for drawing against the forecast. Null live, where the state rule raises alerts.")
    call_plan: list[CallPlanEntry] | None = Field(description="Before the round exists, the ten residents it will call first, in order. Null once there is a round, or for a district with no scenario.")
    attention: list[ResidentRow] = Field(description="Cases that need the officer: unacknowledged flags first, then red cases not yet visited, longest wait first.")
    residents: list[ResidentRow] = Field(description="Everyone on the register, the highest tier first.")


class WardDetail(Shape):
    id: str
    name: str
    district_id: str
    district_name: str
    worker_id: str | None
    worker_name: str | None
    health_centre: Place | None


class WardView(Shape):
    ward: WardDetail
    warning: RoundWarning | None = Field(description="Null where the district has no warning.")
    register: RegisterProfile = Field(description="Who is on the register here, whether or not there is a round.")
    round: RoundFigures | None = Field(description="Null where the district has no round.")
    problems: list[Problem]
    residents: list[ResidentRow]


class ResidentProfile(ResidentRow):
    """The resident's row with who and where they are."""

    language: Language | None
    district_id: str
    district_name: str
    worker_name: str | None
    emergency_contact_name: str | None = Field(description="Only the name. The contact's number is never sent.")


class CallSummary(Shape):
    status: CallStatusShown
    attempts: int
    first_at: datetime | None = Field(description="When the first attempt started or ended.")
    answered_by: Respondent | None = Field(description="Who answered, from the conversation the answers come from. Null if nobody was spoken to.")
    language_used: str | None
    recorded_at: datetime | None = Field(description="For a call with an AI-played resident, when the conversation was recorded. Null otherwise.")


class AnswerShown(Shape):
    """One question the call asked and the answer recorded. Unanswered questions are left out."""

    id: str
    label: str = Field(description="The question's name, such as 'Water'.")
    value: str
    value_label: str = Field(description="The answer in words.")
    quote: str | None = Field(description="What the resident said, where it was kept.")
    worry: Worry | None = Field(description="How worrying this answer is on its own, for colouring. Not how the tier was decided.")


class RuleShown(Shape):
    id: str
    label: str
    tier: Tier = Field(description="The tier this rule gives.")
    question_id: str | None = Field(description="The question whose answer matched, where it was one answer.")
    detail: str = Field(description="The matching answer in words. Empty where no single answer applies.")


class TranscriptLine(Shape):
    role: Literal["agent", "resident", "system"] = Field(
        description=(
            "'system' is a note, not something said: where a long transcript was cut, or "
            "'[prompted after a silence]' or '[prompted to close the call]' where the agent was prompted."
        )
    )
    text: str
    at: datetime | None


class CaseDetail(Shape):
    id: str
    tier: Tier
    status: CaseStatusShown
    opened_at: datetime | None
    visited_at: datetime | None
    outcome: Outcome | None
    outcome_label: str | None
    visit_note: str = Field(description="The worker's note on the visit. Empty if none.")
    simulated_visit: bool = Field(description="True when the visit was simulated.")
    waiting_minutes: int | None = Field(description="Null unless the case is open and needs a visit.")
    escalation: EscalationShown | None = Field(description="The worker's flag. Null if none was made.")
    raised: RaisedShown | None = Field(description="Set if a worker raised the case to red.")
    worker_id: str | None = Field(description="Who holds the case now.")
    ward_worker_id: str | None = Field(description="The ward's own worker, to show a case that has been moved.")
    reassigned_from: str | None = Field(description="The worker the officer moved it from. Null if never moved.")
    can_act: bool = Field(description="Whether the caller may visit, flag, reassign or raise this case: it is open, needs a visit, and is the caller's own test case or the caller is the team.")
    can_acknowledge: bool = Field(description="Whether there is an unacknowledged flag the caller may acknowledge.")


class WorkerOption(Shape):
    """A worker the case could be reassigned to."""

    id: str
    name: str
    name_hi: str
    ward_name: str | None
    open: int = Field(description="Open cases needing a visit that this worker holds.")


class ResidentView(Shape):
    resident: ResidentProfile
    warning: RoundWarning | None = Field(description="Null where the district has no warning.")
    call: CallSummary | None = Field(description="Null where the resident has had no call.")
    questions: list[AnswerShown] = Field(description="The answers the tier was decided on. Empty where nobody was spoken to.")
    rules: list[RuleShown] = Field(description="Why the case has its tier. Empty where there is no case.")
    transcript: list[TranscriptLine] = Field(description="Of the conversation the answers come from. Empty where there is none.")
    note: str = Field(description="A short written summary of the case for the worker. Empty if none yet.")
    case: CaseDetail | None = Field(description="Null where there is no case.")
    health_centre: Place | None
    workers: list[WorkerOption] = Field(description="Every worker in the district, for reassigning.")


class WorkerSummary(Shape):
    id: str
    name: str
    name_hi: str
    ward_id: str | None
    ward_name: str | None
    open: int = Field(description="Open cases needing a visit.")
    done: int = Field(description="Visited cases that needed a visit.")


class WorkersView(Shape):
    district_id: str = Field(description="The district of the pinned demo round (or the demo district).")
    workers: list[WorkerSummary]


class WorkerDetail(Shape):
    id: str
    name: str
    name_hi: str
    ward_id: str
    ward_name: str
    district_id: str
    district_name: str


class WorkerCounts(Shape):
    to_visit: int
    done: int
    red_open: int
    longest_wait_minutes: int | None = Field(description="Null when nothing is waiting.")


class CarryItem(Shape):
    id: Need
    label: str
    count: int = Field(description="Open cases of this worker that need it.")


class WorkerView(Shape):
    worker: WorkerDetail
    warning: RoundWarning | None = Field(description="Null where the district has no warning.")
    counts: WorkerCounts
    carry: list[CarryItem] = Field(description="What to bring. Only what at least one open case needs.")
    health_centre: Place | None
    residents: list[ResidentRow] = Field(description="Those whose case is hers, and those of her ward with no case.")


class CaseRecord(Shape):
    """The case as stored after an action. The visitor's id is left out."""

    id: str
    round_id: str
    resident_id: str
    ward_id: str
    worker_id: str | None
    tier: Tier
    reasons: list[str] = Field(description="Ids of the rules behind the tier. A raised case starts with 'R5'.")
    note: str
    note_tries: int
    status: Literal["open", "visited", "resolved"]
    visited_at: datetime | None
    outcome: Outcome | None
    updated_at: datetime
    opened_at: datetime | None
    escalation: EscalationNote | None
    raised: RaisedNote | None
    reassigned_from: str | None
    visit_note: str


class WeatherArea(Shape):
    kind: Literal["state", "district", "ward"]
    id: str = Field(description="The ward or district id, or the state's name.")
    name: str
    state: str | None


class WeatherHour(Shape):
    hour: int = Field(description="The local hour, 0 to 23.")
    temp_c: float | None
    feels_like_c: float | None
    heat_index_c: float | None
    humidity_pct: float | None
    rain_pct: float | None
    condition_type: str | None
    condition_text: str | None
    icon_uri: str | None = Field(description="The base address of Google's icon; add '.svg' or '.png'.")
    is_day: bool | None


class WeatherDay(Shape):
    date: str = Field(description="The local date, YYYY-MM-DD.")
    max_c: float | None
    min_c: float | None
    condition_type: str | None
    condition_text: str | None
    icon_uri: str | None
    rain_pct: float | None


class WeatherNow(Shape):
    hour: int = Field(description="The hour of the forecast nearest the current local hour.")
    temp_c: float | None
    feels_like_c: float | None
    condition_text: str | None
    icon_uri: str | None
    is_day: bool | None


class WeatherToday(Shape):
    max_c: float | None
    min_c: float | None
    humidity_pct: float | None = Field(description="The mean of the day's hours.")
    rain_pct: float | None = Field(description="The highest of the day's hours.")


class WeatherStateDistrict(Shape):
    id: str
    name: str
    peak_c: float | None = Field(description="Today's highest hourly temperature. Null where there is no forecast today.")
    peak_hour: int | None
    alert: Severity | None = Field(description="The level of today's heat alert in the district, if any.")


class WeatherState(Shape):
    name: str
    mean_peak_c: float | None = Field(description="The mean of the peaks of the districts with a forecast today.")
    max_peak_c: float | None
    districts: list[WeatherStateDistrict] = Field(description="Hottest first; those with no forecast last.")


class WeatherAlert(Shape):
    severity: Severity | None
    reason_text: str | None = Field(description="One plain sentence on why it was raised.")
    raw: dict = Field(description="What the alert recorded, as stored.")


class WeatherView(Shape):
    """The stored weather of a state, district or ward: the live fetch's snapshots, or the demo scenario."""

    area: WeatherArea
    source: Literal["google_weather", "demo_scenario"]
    fetched_at: datetime | None = Field(description="When the forecast shown was fetched: today's, or before today's is in, the last one there was. Null if there has never been one.")
    now: WeatherNow | None
    today: WeatherToday | None
    hours: list[WeatherHour]
    days: list[WeatherDay]
    state: WeatherState | None
    alert: WeatherAlert | None
    unavailable_since: datetime | None = Field(description="When the morning fetch was due (06:00 local), once it is 30 minutes overdue and the live dataset still has no forecast for the area today. The last forecast there was is still shown. Always null in the demo.")
