import asyncio
import hmac
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.auth import Auth, SignInMiddleware, register_auth_routes, verify_with_google
from app.call.browser_bridge import register_browser_routes
from app.call.deps import CallDeps
from app.call.live import open_live_session, warm_up
from app.call.twilio_bridge import place_visitor_phone_call, register_twilio_routes, telephony_ready
from app.case_note import backfill_notes, note_for_call
from app.case_actions import ActionRefused, acknowledge, escalate, raise_to_red, reassign
from app.cases import VisitRefused, mark_visited
from app.config import Settings
from app.db import Store
from app.demo import DEMO_PREFIX, open_rounds, pinned, pinned_round_id
from app.demo_cards import LANGUAGES, public_cards
from app.dispatch import TaskQueue, plan_round
from app.ingest.alerts import record_alert
from app.ingest.replay import make_replay_alert
from app.feed import load_feed
from app.models import FinalCallStatus, Hazard, Language, Respondent, Severity
from app.recorded import RECORDED_DIR, audio_file, load_recording
from app.region import district_view, region_view, ward_view
from app.weather_view import weather_view
from app.report import RoundCache, build_report, round_overview, ward_cases, ward_directory
from app.replay import release_due
from app.secret_manager import read_secret
from app.resident_view import resident_view
from app.schemas import (
    CaseRecord, Detail, DistrictView, InvalidRequest, RegionView, Refusal, ResidentView, WardView, WeatherView, WorkersView,
    WorkerView,
)
from app.simulate import CallAlreadyFinished, apply_outcome
from app.summary import refresh_summaries, write_summary
from app.sweep import sweep
from app.textgen import TextModel
from app.ingest.live_weather import run_live_fetch
from app.weather import fetch_days as fetch_days_from_weather_api
from app.weather import fetch_forecast as fetch_from_weather_api
from app.worker_view import worker_view, workers_view
from app.visitor import (
    LimitReached, NoDemoRound, clean_visitor_id, create_visitor_call, latest_call,
)
from app.visitor_phone import E164, allowed_codes, country_code
from app.web import DEFAULT_WEB_DIR, register_web
from app.operations import register_operations
from app.ask.routes import register_ask
from app.operations_auth import assigned_role

logger = logging.getLogger(__name__)
# Kept on a stored case but never in a case record: whose call it was, and the replay's mark on
# a simulated visit (the role views carry that as simulated_visit).
UNSERVED_CASE_KEYS = {"visitor_id", "simulated"}
# Written by the evaluation harness; absent until it has been run on this build.
EVAL_RESULTS = Path(__file__).parent / "static" / "eval_latest.json"
# What an id from a URL must look like before the store is asked for it: every id this
# service makes does. The store reserves names such as "__name__" and fails on them.
PATH_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")
RESERVED_ID = re.compile(r"__.*__")


def storable_id(value: str) -> bool:
    """Whether a call, case or ward id from a URL may be looked up in the store."""
    return PATH_ID.fullmatch(value) is not None and RESERVED_ID.fullmatch(value) is None


class ReplayBody(BaseModel):
    district_id: str
    hazard: Hazard
    severity: Severity


class SimulateBody(BaseModel):
    status: FinalCallStatus  # simulate only ends a call
    answers: dict[str, str] = {}
    quotes: dict[str, str] = {}
    emergency_reason: str | None = None
    respondent: Respondent = "unknown"


class VisitBody(BaseModel):
    outcome: str = Field(max_length=64)
    note: str = Field("", max_length=1000)  # clean_note cuts what is stored; this refuses a huge body unread


class EscalateBody(BaseModel):
    reason: str = Field(max_length=64)
    note: str = Field("", max_length=1000)


class AcknowledgeBody(BaseModel):
    reply: str = Field("", max_length=1000)


class ReassignBody(BaseModel):
    worker_id: str = Field(max_length=64)


class RaiseBody(BaseModel):
    observation: str = Field(max_length=64)


class VisitorCallBody(BaseModel):
    card_id: str
    language: Language


class VisitorPhoneCallBody(BaseModel):
    card_id: str
    language: Language
    phone: str  # no max_length: the route refuses anything not E.164 as phone_invalid, never echoing it

# What the role-view routes document in the OpenAPI description. Documentation only: the routes
# use `responses=`, never `response_model=`, so nothing here validates or filters a response.
ROLE_VIEWS = ["Role views"]
READ_VISITOR = (
    "Anyone may call it. Send the X-Visitor header to also see the record made by your own test call; "
    "without it, or with another visitor's id, that record is not in the answer."
)
MISSING = {"model": Detail, "description": "No such record, or it belongs to another visitor's test call."}
NOT_YOURS = {"model": Detail, "description": "The case is not from the caller's own test call, and the caller is not the team."}
NO_CASE = {"model": Detail, "description": "No such case."}
INVALID = {"model": Refusal | InvalidRequest, "description": "Refused (a body with the reason), or the request body is malformed or too long."}
ACTING = (
    " Allowed for the team, and for a visitor on the case from their own test call (send X-Visitor); "
    "anyone else gets 403. The answer is the stored case; it never includes the visitor's id."
)


def _post_responses(refused: dict, invalid: dict) -> dict:
    return {200: {"model": CaseRecord}, 403: NOT_YOURS, 404: NO_CASE, 409: refused, 422: invalid}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_app(
    store: Store,
    queue: TaskQueue,
    settings: Settings,
    clock: Callable[[], datetime] = _utc_now,
    open_session: Callable | None = None,
    place_call: Callable | None = None,
    validate_twilio: Callable | None = None,
    verify_google_token: Callable[[str, str], dict] | None = None,
    text_model: TextModel | None = None,
    fetch_forecast: Callable[[float, float], dict] | None = None,
    fetch_hours: Callable | None = None,
    fetch_days: Callable | None = None,
    ask_runner: Any = None,
    ask_titler: Callable | None = None,
) -> FastAPI:
    auth = Auth(settings, store=store)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Every store call inside a coroutine runs in this pool; several calls at once, each
        # writing answers while its transcript is saved, need more than the default handful.
        pool = ThreadPoolExecutor(max_workers=settings.worker_threads, thread_name_prefix="chhaya")
        asyncio.get_running_loop().set_default_executor(pool)
        warming = None
        if open_session is None and settings.gcp_project.strip() and settings.live_warm_up:
            # In the background: start-up does not wait, and the first call no longer pays the cold start.
            warming = asyncio.create_task(warm_up(settings))
        try:
            yield
        finally:
            if warming is not None and not warming.done():
                warming.cancel()
            # Not cancelling what is queued: a call's outcome write in flight must not be dropped.
            pool.shutdown(wait=False)

    # On an open deployment the generated API pages would be a map for strangers.
    open_to_all = auth.on_cloud_run and settings.allow_open_access
    hidden = {"docs_url": None, "redoc_url": None, "openapi_url": None} if open_to_all else {}
    app = FastAPI(
        title="Chhaya",
        version="0.1.0",
        description="Wellness calls to vulnerable residents during a heatwave, and the screens that follow them up.",
        lifespan=lifespan,
        **hidden,
    )

    @app.exception_handler(RequestValidationError)
    async def refuse_malformed_body(request: Request, error: RequestValidationError):
        if request.url.path == "/api/visitor/phone-calls":  # the default answer would echo the number back
            return JSONResponse({"detail": "The request is not valid.", "reason": "bad_request"}, status_code=422)
        return await request_validation_exception_handler(request, error)
    # Outermost: with sign-in on, nothing below is reached without a session or the secret,
    # except the open paths listed in app.auth.
    app.add_middleware(SignInMiddleware, auth=auth)
    register_auth_routes(app, auth, verify_google_token or verify_with_google)
    tz = ZoneInfo(settings.local_tz)
    hours = {"start_hour": settings.call_start_hour, "end_hour": settings.call_end_hour}

    def require_secret(x_internal_secret: str = Header(default="")) -> None:
        expected = settings.internal_secret
        if not expected or not hmac.compare_digest(
            x_internal_secret.encode(), expected.encode()
        ):
            raise HTTPException(status_code=401, detail="missing or wrong secret")

    def is_operator(
        request: Request,
        x_internal_secret: str = Header(default="", description="The team's shared secret. Without it, or a team sign-in, the caller is a reader."),
    ) -> bool:
        """The team: the internal secret, or (with sign-in on) a signed-in allowlisted account."""
        email = auth.email_from_cookies(request.cookies)
        if settings.operations_admin_emails or settings.operations_crew_accounts:
            return auth.secret_matches(x_internal_secret) or assigned_role(settings, email) == ("admin", None)
        return auth.secret_matches(x_internal_secret) or email is not None

    def require_operator(allowed: bool = Depends(is_operator)) -> None:
        if not allowed:
            raise HTTPException(status_code=401, detail="missing or wrong secret")

    def visitor(
        x_visitor: str = Header(default="", description="A random id (a UUID) the browser made for itself. It shows the caller their own test call and lets them act on its case. Optional."),
    ) -> str | None:
        """The id a visitor's browser made for itself, or None. It is not a login."""
        return clean_visitor_id(x_visitor)

    def round_for(requested: str | None, team: bool) -> str | None:
        """The round a request is about: the pinned demo round, or for the team any round."""
        current = pinned_round_id(store)
        if requested is None or requested == current:
            return current
        if not team:
            raise HTTPException(status_code=403, detail="only the demo round is open")
        return requested

    # What every visitor is shown alike, kept briefly so that open reads have a cost ceiling.
    # Only visitors' reads use it: the team always reads the store.
    views = RoundCache(settings.view_cache_seconds)
    _NO_VIEWS = RoundCache(0)
    registers = RoundCache(settings.register_cache_seconds)  # districts' registers, which change only on a reseed

    secured = [Depends(require_secret)]  # /internal: machines only
    operator = [Depends(require_operator)]  # /admin

    call_deps = CallDeps(
        store=store,
        queue=queue,
        settings=settings,
        clock=clock,
        tz=tz,
        open_session=open_session or open_live_session,
        place_call=place_call,
        validate_twilio=validate_twilio,
        after_call=(lambda call_id: note_for_call(store, text_model, call_id)) if text_model else None,
    )
    register_browser_routes(app, call_deps, operator)
    register_twilio_routes(app, call_deps, secured)

    def not_found(error: KeyError) -> HTTPException:
        return HTTPException(status_code=404, detail=f"not found: {error.args[0]}")

    @app.get("/health")  # Cloud Run's front end keeps /healthz for itself
    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.post("/admin/alerts/replay", dependencies=operator)
    def replay(body: ReplayBody) -> dict:
        if store.get("districts", body.district_id) is None:
            raise HTTPException(status_code=404, detail="unknown district")
        alert = make_replay_alert(body.district_id, body.hazard, body.severity, clock())
        opened = record_alert(store, alert, tz)
        return {"alert_id": alert.id, "round_id": opened.id if opened else None}

    @app.post("/internal/rounds/{round_id}/dispatch", dependencies=secured)
    def dispatch(round_id: str) -> dict:
        if round_id.startswith(DEMO_PREFIX):
            raise HTTPException(status_code=409, detail="a demo round is scripted, not dispatched")
        try:
            return {"queued": plan_round(store, queue, round_id, clock(), tz, **hours)}
        except KeyError as error:
            raise not_found(error)

    def release_replay(now: datetime) -> dict:
        """Release the recorded calls and simulated visits now due. A failure here is logged and
        counted; it never stops the sweep or the rebuild."""
        try:
            return release_due(store, queue, now, tz)
        except Exception:
            logger.exception("the replay could not be released")
            return {"calls": 0, "visits": 0, "errors": 1}

    @app.post("/internal/sweep", dependencies=secured)
    def run_sweep() -> dict:
        now = clock()
        round_ids: list[str] = []
        if text_model is not None:
            # Chosen before the sweep: it marks a round complete on the pass after its last
            # call, and that round still needs its last notes and its final summary. Real
            # rounds first, so that a busy demo cannot use up the pass's model calls.
            current = pinned_round_id(store)
            live = sorted(
                r["id"] for r in store.query("rounds", status="calling")
                if r["id"] != current and not r["id"].startswith(DEMO_PREFIX)
            )
            round_ids = live + ([current] if current else [])
        swept = sweep(store, queue, settings, now, tz, can_dial=telephony_ready(call_deps))
        result = {"calls": swept, "notes": 0, "summaries": 0}
        result["replay"] = release_replay(now)
        if text_model is not None:
            # What the after-call step did not get to, and summaries whose figures have moved on.
            result["notes"] = backfill_notes(store, text_model, round_ids)
            result["summaries"] = refresh_summaries(store, text_model, round_ids, now, registers)
        return result

    @app.post("/internal/ingest", dependencies=secured)
    def ingest() -> dict:
        # The old 15-minute threshold rule. Gone, so that a scheduler job left behind cannot run it on live data.
        raise HTTPException(status_code=410, detail="gone: the weather is fetched by /internal/weather/fetch (06:00 and 14:00)")

    @app.post("/internal/weather/fetch", dependencies=secured)
    def weather_fetch() -> dict:
        key = settings.weather_api_key.strip() or read_secret(settings.gcp_project, settings.weather_api_key_secret)
        if not key:
            raise HTTPException(status_code=503, detail="the weather forecast is not configured")
        hours = fetch_hours or (lambda lat, lng: fetch_from_weather_api(key, lat, lng))
        days = fetch_days or (lambda lat, lng: fetch_days_from_weather_api(key, lat, lng))
        summary = run_live_fetch(store, settings, clock(), tz, hours, days)
        if summary["wards_ok"] == 0:
            # A 5xx makes Cloud Scheduler retry (spec §7). What the run did is in the log.
            raise HTTPException(status_code=502, detail=f"fetched 0 of {summary['wards_ok'] + summary['wards_failed']} wards")
        return summary

    @app.post("/admin/calls/{call_id}/simulate", dependencies=operator)
    def simulate(call_id: str, body: SimulateBody) -> dict:
        if not storable_id(call_id):
            raise HTTPException(status_code=404, detail="no such call")
        if ((store.get("calls", call_id) or {}).get("round_id") or "").startswith(DEMO_PREFIX):
            # A failed outcome here would put a retry on the real queue.
            raise HTTPException(status_code=409, detail="a demo round is scripted")
        try:
            stored = apply_outcome(
                store, queue, call_id, body.status, clock(), tz,
                answers=body.answers, quotes=body.quotes,
                emergency_reason=body.emergency_reason, respondent=body.respondent, **hours,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
        except CallAlreadyFinished as error:
            raise HTTPException(status_code=409, detail=f"call already finished as {error}")
        except KeyError as error:
            raise not_found(error)
        return {"ok": True, "status": stored}

    @app.get("/api/demo")
    def demo() -> dict:
        current = pinned(store) or {}
        return {
            "round_id": current.get("round_id"),
            "built_at": current.get("built_at"),
            "cards": public_cards(),
            "languages": LANGUAGES,
        }

    @app.get("/api/demo/recorded-call")
    def recorded_call() -> dict:
        recording = load_recording(RECORDED_DIR)
        if recording is None:
            return {"recording": None}
        has_audio = audio_file(RECORDED_DIR) is not None
        return {"recording": {**recording, "audio_url": "/api/demo/recorded-call/audio" if has_audio else None}}

    @app.get("/api/demo/recorded-call/audio")
    def recorded_call_audio() -> FileResponse:
        audio = audio_file(RECORDED_DIR)
        if audio is None:
            raise HTTPException(status_code=404, detail="no recording")
        return FileResponse(audio)

    @app.get("/api/rounds/current")
    def rounds_current(
        team: bool = Depends(is_operator), visitor_id: str | None = Depends(visitor)
    ) -> dict:
        round_id = pinned_round_id(store)
        if round_id is None:
            return {"round": None}
        try:
            return round_overview(store, round_id, visitor_id, cache=None if team else views, registers=registers)
        except KeyError as error:
            # The pointer names a round that has gone. Expected for a moment during a rebuild;
            # logged, so that a pointer left wrong is noticed.
            logger.warning("the demo pointer names %s, which could not be read (%s)", round_id, error.args[0])
            return {"round": None}

    @app.get("/api/rounds", dependencies=operator)
    def rounds() -> dict:
        far_past = datetime.min.replace(tzinfo=timezone.utc)
        newest_first = sorted(
            store.query("rounds"),
            key=lambda r: (r.get("opened_at") or r.get("started_at") or far_past, r["id"]),
            reverse=True,
        )
        return {"pinned": pinned_round_id(store), "rounds": newest_first}

    @app.get("/api/rounds/{round_id}/overview", dependencies=operator)
    def overview(round_id: str) -> dict:
        try:
            return round_overview(store, round_id, registers=registers)
        except KeyError as error:
            raise not_found(error)

    @app.post("/admin/rounds/{round_id}/summary", dependencies=operator)
    def summarise(round_id: str) -> dict:
        if text_model is None:
            raise HTTPException(status_code=503, detail="no text model is configured")
        if not storable_id(round_id):
            raise HTTPException(status_code=404, detail="no such round")
        try:
            return {"summary": write_summary(store, text_model, round_id, clock(), registers)}
        except KeyError as error:
            raise not_found(error)

    @app.get("/api/rounds/{round_id}/report")
    def report(
        round_id: str, team: bool = Depends(is_operator), visitor_id: str | None = Depends(visitor)
    ) -> dict:
        try:
            return build_report(
                store, round_for(round_id, team), visitor_id, cache=None if team else views, registers=registers
            )
        except KeyError as error:
            raise not_found(error)

    @app.get("/api/wards")
    def wards(round_id: str | None = None, team: bool = Depends(is_operator)) -> dict:
        chosen = round_for(round_id, team)
        if chosen is None:
            return {"round_id": None, "wards": []}
        try:
            return {"round_id": chosen, "wards": ward_directory(store, chosen)}
        except KeyError as error:
            raise not_found(error)

    @app.get("/api/wards/{ward_id}/cases")
    def cases_for_ward(
        ward_id: str,
        round_id: str | None = None,
        team: bool = Depends(is_operator),
        visitor_id: str | None = Depends(visitor),
    ) -> dict:
        if not storable_id(ward_id):
            raise HTTPException(status_code=404, detail="no such ward")
        chosen = round_for(round_id, team)
        if chosen is None:
            return {"round_id": None, "cases": []}
        try:
            listed = ward_cases(
                store, ward_id, chosen, visitor_id, operator=team, cache=None if team else views, registers=registers
            )
            return {"round_id": chosen, "cases": listed}
        except KeyError as error:
            raise not_found(error)

    def case_for(case_id: str, team: bool, visitor_id: str | None) -> dict:
        """The case, if it exists and this caller may act on it: the case's own visitor, or the team."""
        case = store.get("cases", case_id) if storable_id(case_id) else None
        if case is None:
            raise HTTPException(status_code=404, detail="no such case")
        yours = visitor_id is not None and case.get("visitor_id") == visitor_id
        if not (team or yours):
            raise HTTPException(status_code=403, detail="you can act only on the case from your own call")
        return case

    @app.post(
        "/api/cases/{case_id}/visit", tags=ROLE_VIEWS, summary="Record a visit",
        description=(
            "A health worker records that the case was visited, with how it went (one of advised_ok, supplies_given, referred, "
            "hospital, not_home) and an optional note." + ACTING + " A refused visit answers only {detail}, with no reason: "
            "409 when the case was visited already, 422 for an unknown outcome or a green case."
        ),
        responses={
            200: {"model": CaseRecord}, 403: NOT_YOURS, 404: NO_CASE,
            409: {"model": Detail, "description": "The case has already been visited."},
            422: {"model": Detail | InvalidRequest, "description": "An unknown outcome, a green case, or a malformed body."},
        },
    )
    def visit(
        case_id: str,
        body: VisitBody,
        team: bool = Depends(is_operator),
        visitor_id: str | None = Depends(visitor),
    ) -> dict:
        case_for(case_id, team, visitor_id)
        try:
            visited = mark_visited(store, case_id, body.outcome, clock(), body.note)
        except VisitRefused as refusal:
            status = 409 if refusal.reason == "already_visited" else 422
            raise HTTPException(status_code=status, detail=str(refusal))
        except KeyError as error:
            raise not_found(error)
        return {key: value for key, value in visited.items() if key not in UNSERVED_CASE_KEYS}

    REFUSAL_STATUS = {"not_open": 409, "already_flagged": 409, "no_flag": 409, "not_amber": 409}  # the rest are 422

    def act_on_case(case_id: str, team: bool, visitor_id: str | None, action: Callable[[], dict]):
        """Run an action on a case for someone allowed to: the case's own visitor, or the team."""
        case_for(case_id, team, visitor_id)
        try:
            changed = action()
        except ActionRefused as refusal:
            return JSONResponse(
                {"detail": str(refusal), "reason": refusal.reason}, status_code=REFUSAL_STATUS.get(refusal.reason, 422)
            )
        except KeyError as error:
            raise not_found(error)
        return {key: value for key, value in changed.items() if key not in UNSERVED_CASE_KEYS}

    @app.post(
        "/api/cases/{case_id}/escalate", tags=ROLE_VIEWS, summary="Flag a case to the officer",
        description=(
            "A health worker tells the officer about an open case (reason: transport, supplies, missing or other). The tier "
            "does not change." + ACTING + " Refusals carry a reason: 409 not_open or already_flagged; 422 green_case or bad_value."
        ),
        responses=_post_responses({"model": Refusal, "description": "not_open or already_flagged."}, INVALID),
    )
    def escalate_case(case_id: str, body: EscalateBody, team: bool = Depends(is_operator), visitor_id: str | None = Depends(visitor)):
        return act_on_case(case_id, team, visitor_id, lambda: escalate(store, case_id, body.reason, body.note, clock()))

    @app.post(
        "/api/cases/{case_id}/acknowledge", tags=ROLE_VIEWS, summary="Acknowledge a flag",
        description=(
            "The officer has seen a worker's flag on the case and may leave a reply. Works whatever the case's status." + ACTING
            + " Refusal: 409 no_flag, when nothing is waiting to be acknowledged."
        ),
        responses=_post_responses({"model": Refusal, "description": "no_flag."}, INVALID),
    )
    def acknowledge_case(case_id: str, body: AcknowledgeBody, team: bool = Depends(is_operator), visitor_id: str | None = Depends(visitor)):
        return act_on_case(case_id, team, visitor_id, lambda: acknowledge(store, case_id, body.reply, clock()))

    @app.post(
        "/api/cases/{case_id}/reassign", tags=ROLE_VIEWS, summary="Give a case to another worker",
        description=(
            "The officer gives an open case to another worker of the same district." + ACTING
            + " Refusals carry a reason: 409 not_open; 422 green_case, same_worker or unknown_worker."
        ),
        responses=_post_responses({"model": Refusal, "description": "not_open."}, INVALID),
    )
    def reassign_case(case_id: str, body: ReassignBody, team: bool = Depends(is_operator), visitor_id: str | None = Depends(visitor)):
        def move() -> dict:
            if not storable_id(body.worker_id):  # answered like any unknown worker, not left to the store
                raise ActionRefused("unknown_worker", "no such worker in this district")
            return reassign(store, case_id, body.worker_id, clock())

        return act_on_case(case_id, team, visitor_id, move)

    @app.post(
        "/api/cases/{case_id}/raise", tags=ROLE_VIEWS, summary="Raise a case to red",
        description=(
            "A worker who has seen the person raises an open amber case to red, saying what was seen (confused, fainted, "
            "dry_skin, vomiting, chest_breathing or other). No action ever lowers a tier." + ACTING
            + " Refusals carry a reason: 409 not_open or not_amber; 422 bad_value."
        ),
        responses=_post_responses({"model": Refusal, "description": "not_open or not_amber."}, INVALID),
    )
    def raise_case(case_id: str, body: RaiseBody, team: bool = Depends(is_operator), visitor_id: str | None = Depends(visitor)):
        return act_on_case(case_id, team, visitor_id, lambda: raise_to_red(store, case_id, body.observation, clock()))

    def shown(doc_id: str, build: Callable[[RoundCache | None], dict], team: bool) -> dict:
        """A read for the role screens. The team always reads the store; visitors share the caches."""
        if not storable_id(doc_id):
            raise HTTPException(status_code=404, detail="not found")
        try:
            return build(None if team else views)
        except KeyError as error:
            raise not_found(error)

    @app.get(
        "/api/region", tags=ROLE_VIEWS, summary="All districts",
        description="One card per district: its size, its warning and, where it has a round, the round's figures. " + READ_VISITOR,
        responses={200: {"model": RegionView}},
    )
    def region(team: bool = Depends(is_operator), visitor_id: str | None = Depends(visitor)) -> dict:
        return region_view(store, visitor_id, clock(), None if team else views, registers)

    @app.get(
        "/api/districts/{district_id}", tags=ROLE_VIEWS, summary="One district",
        description="A district's register, warning, round figures, wards, residents and the cases that need the officer. " + READ_VISITOR,
        responses={200: {"model": DistrictView}, 404: MISSING},
    )
    def district(district_id: str, team: bool = Depends(is_operator), visitor_id: str | None = Depends(visitor)) -> dict:
        return shown(district_id, lambda cache: district_view(store, district_id, visitor_id, clock(), cache, registers), team)

    @app.get(
        "/api/wards/{ward_id}/detail", tags=ROLE_VIEWS, summary="One ward",
        description="A ward's worker, health centre, register, round figures and residents. " + READ_VISITOR,
        responses={200: {"model": WardView}, 404: MISSING},
    )
    def ward_detail(ward_id: str, team: bool = Depends(is_operator), visitor_id: str | None = Depends(visitor)) -> dict:
        return shown(ward_id, lambda cache: ward_view(store, ward_id, visitor_id, clock(), cache, registers), team)

    @app.get(
        "/api/residents/{resident_id}", tags=ROLE_VIEWS, summary="One resident",
        description=(
            "A resident in full: the call, the answers, why the rules gave the tier, the transcript and the case. "
            "A test-call resident is shown only to the visitor who made it (send X-Visitor); anyone else gets 404."
        ),
        responses={200: {"model": ResidentView}, 404: MISSING},
    )
    def resident(resident_id: str, team: bool = Depends(is_operator), visitor_id: str | None = Depends(visitor)) -> dict:
        return shown(resident_id, lambda cache: resident_view(store, resident_id, visitor_id, team, clock(), cache, registers), team)

    @app.get(
        "/api/weather", tags=ROLE_VIEWS, summary="The weather of an area",
        description=(
            "The stored weather of a ward, else a district, else a state (by name); with none named, the demo "
            "district's state. Today's hours, the coming days, the state's districts by peak and the area's heat "
            "alert with its reason. Reads only what the twice-daily fetch (or the demo scenario) stored: it never "
            "calls the Weather API. Anyone may call it."
        ),
        responses={200: {"model": WeatherView}, 404: MISSING},
    )
    def weather(
        state: str | None = None, district_id: str | None = None, ward_id: str | None = None,
        team: bool = Depends(is_operator),
    ) -> dict:
        if not (state or district_id or ward_id):
            district_id = settings.demo_district_id
            if store.get("districts", district_id) is None:
                raise HTTPException(status_code=404, detail="not found")
            state, district_id = store.get("districts", district_id).get("state"), None
        for value in (district_id, ward_id):
            if value and not storable_id(value):
                raise HTTPException(status_code=404, detail="not found")
        if state and (len(state) > 100 or "/" in state):
            raise HTTPException(status_code=404, detail="not found")
        area = f"{state or ''}|{district_id or ''}|{ward_id or ''}"
        try:
            return (views if not team else _NO_VIEWS).get(
                "weather", area, lambda: weather_view(store, state, district_id, ward_id, clock(), tz)
            )
        except KeyError as error:
            raise not_found(error)

    @app.get(
        "/api/workers", tags=ROLE_VIEWS, summary="The workers",
        description="The health workers of the demo district with their open and done visits. " + READ_VISITOR,
        responses={200: {"model": WorkersView}},
    )
    def workers(team: bool = Depends(is_operator), visitor_id: str | None = Depends(visitor)) -> dict:
        current = store.get("rounds", pinned_round_id(store) or "") if pinned_round_id(store) else None
        district_id = (current or {}).get("district_id") or settings.demo_district_id
        return shown(district_id, lambda cache: workers_view(store, district_id, visitor_id, clock(), cache, registers), team)

    @app.get(
        "/api/workers/{worker_id}/residents", tags=ROLE_VIEWS, summary="One worker's residents",
        description="A worker's counts, what to carry, and the residents whose case is hers or who are in her ward with no case. " + READ_VISITOR,
        responses={200: {"model": WorkerView}, 404: MISSING},
    )
    def worker_residents(worker_id: str, team: bool = Depends(is_operator), visitor_id: str | None = Depends(visitor)) -> dict:
        return shown(worker_id, lambda cache: worker_view(store, worker_id, visitor_id, clock(), cache, registers), team)

    @app.get("/api/eval/latest")
    def eval_latest() -> dict:
        try:
            return {"run": json.loads(EVAL_RESULTS.read_text(encoding="utf-8"))}
        except FileNotFoundError:
            return {"run": None}
        except (OSError, ValueError):
            logger.exception("the evaluation results file could not be read")
            return {"run": None}

    def open_today() -> dict:
        now = clock()
        try:
            pointer = open_rounds(store, settings.demo_district_id, now, tz, settings)  # this app's thresholds
        except KeyError as error:
            if error.args[0] != f"districts/{settings.demo_district_id}":
                raise  # a missing record somewhere else is a bug, not an unseeded district
            raise HTTPException(status_code=409, detail="the visitors' district has not been seeded")
        release_replay(now)  # a run later in the morning catches up at once
        # How many recorded calls each district's round has planned: 0 shows a missing library.
        planned = {
            district_id: len((store.get("rounds", round_id) or {}).get("replay") or [])
            for district_id, round_id in (pointer.get("rounds") or {}).items()
        }
        return {**pointer, "replay_planned": planned}

    app.post("/internal/demo/rebuild", dependencies=secured)(open_today)  # for the morning job
    app.post("/admin/demo/rebuild", dependencies=operator)(open_today)  # for the team

    LIMIT_WORDS = {
        "visitor_hourly": "You have taken the calls allowed in one hour.",
        "address_hourly": "Too many calls have been started from this network in the past hour.",
        "address_failed_starts": "Calls from this network are not starting at the moment. Try again later.",
        "lines_busy": "All lines are busy. Try again in a minute.",
        "daily_cap": "Today's calls have all been used. Come back tomorrow.",
        "phone_number_daily": "This number has had its test calls for today. Try the call in your browser.",
        "address_phone_daily": "Too many phone calls have been asked for from this network today. Try the call in your browser.",
        "phone_budget": "Today's phone calls have all been used. Try the call in your browser.",
    }

    def limit_response(limit: LimitReached) -> JSONResponse:
        logger.warning("visitor call refused: %s", limit.reason)
        headers = {"Retry-After": str(limit.retry_after_s)} if limit.retry_after_s else {}
        return JSONResponse(
            {"detail": LIMIT_WORDS[limit.reason], "reason": limit.reason, "retry_after_s": limit.retry_after_s},
            status_code=limit.status,
            headers=headers,
        )

    @app.post("/api/visitor/calls")
    def start_visitor_call(
        body: VisitorCallBody, request: Request, visitor_id: str | None = Depends(visitor)
    ):
        if visitor_id is None:
            raise HTTPException(status_code=400, detail="the X-Visitor header is missing or malformed")
        address = auth.client_address(request)
        try:
            return create_visitor_call(
                store, settings, body.card_id, body.language, visitor_id, address, clock(), tz
            )
        except KeyError as error:
            if error.args[0] != f"cards/{body.card_id}":
                raise  # a missing record somewhere else is a bug, not a wrong card
            raise HTTPException(status_code=404, detail="unknown card")
        except NoDemoRound:
            raise HTTPException(status_code=409, detail="No call round is open yet; try again after 06:00 IST.")
        except LimitReached as limit:
            return limit_response(limit)

    @app.post("/api/visitor/phone-calls")
    def start_visitor_phone_call(
        body: VisitorPhoneCallBody, request: Request, visitor_id: str | None = Depends(visitor)
    ):
        if visitor_id is None:
            raise HTTPException(status_code=400, detail="the X-Visitor header is missing or malformed")
        if not E164.fullmatch(body.phone):
            return JSONResponse({"detail": "Enter the number with its country code.", "reason": "phone_invalid"}, status_code=422)
        code = country_code(body.phone, allowed_codes(settings))
        if code is None or code not in settings.visitor_phone_rates_usd:
            return JSONResponse(
                {"detail": "Test calls can ring numbers in Asia-Pacific countries only. Try the call in your browser.",
                 "reason": "country_not_allowed"},
                status_code=422,
            )
        if not telephony_ready(call_deps):
            return JSONResponse(
                {"detail": "Phone calls are not available at the moment. Try the call in your browser.",
                 "reason": "phone_unavailable"},
                status_code=503,
            )
        address = auth.client_address(request)
        try:
            made = create_visitor_call(
                store, settings, body.card_id, body.language, visitor_id, address, clock(), tz, phone=body.phone
            )
        except KeyError as error:
            if error.args[0] != f"cards/{body.card_id}":
                raise
            raise HTTPException(status_code=404, detail="unknown card")
        except NoDemoRound:
            raise HTTPException(status_code=409, detail="No call round is open yet; try again after 06:00 IST.")
        except LimitReached as limit:
            return limit_response(limit)
        if not place_visitor_phone_call(call_deps, made["call_id"]):
            return JSONResponse(
                {"detail": "We couldn't reach your phone. Check the number and try again, or use the browser call.",
                 "reason": "provider_error"},
                status_code=502,
            )
        return {"call_id": made["call_id"]}

    def feed_of(call: dict | None) -> dict | None:
        """The feed of a call, or None if the call, its resident or its round has gone."""
        if call is None:
            return None
        try:
            return load_feed(store, call)
        except KeyError as error:
            logger.warning("call %s has no feed: %s is missing", call["id"], error.args[0])
            return None

    # Registered before the {call_id} route, or "latest" would be read as a call id.
    @app.get("/api/visitor/calls/latest")
    def visitor_latest_call(visitor_id: str | None = Depends(visitor)) -> dict:
        return {"call": feed_of(latest_call(store, visitor_id)) if visitor_id else None}

    @app.get("/api/visitor/calls/{call_id}")
    def visitor_call(call_id: str, visitor_id: str | None = Depends(visitor)) -> dict:
        call = store.get("calls", call_id) if visitor_id and storable_id(call_id) else None
        # the same answer for a call that does not exist and for one that is someone else's
        feed = feed_of(call) if call and call.get("visitor_id") == visitor_id else None
        if feed is None:
            raise HTTPException(status_code=404, detail="no such call")
        return feed

    register_operations(app, store, settings, auth, clock, text_model)
    register_ask(app, store, settings, auth, clock, runner=ask_runner, titler=ask_titler)

    # Last, so that it only ever answers what no route above did.
    register_web(app, Path(settings.web_dir) if settings.web_dir else DEFAULT_WEB_DIR)
    return app
