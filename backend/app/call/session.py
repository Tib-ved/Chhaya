"""One call, from the moment it is answered to its final status."""
import asyncio
import logging

from app.call.deps import CallDeps
from app.call.guidance import always_asked, build_instruction, opening_prompt, question_order
from app.call.live import live_settings_snapshot, run_call
from app.call.tools import CallTools
from app.dispatch import FINAL_STATUSES, handle_call_outcome, mark_call_started

logger = logging.getLogger(__name__)
FACILITY_PREFERENCE = {
    "heat": ("cooling_centre", "health_centre"),
    "cold": ("night_shelter", "health_centre"),
}


def _nearest_facility(store, resident: dict, hazard: str) -> str | None:
    in_ward = store.query("facilities", ward_id=resident["ward_id"])
    for wanted in FACILITY_PREFERENCE.get(hazard, ()):
        for facility in sorted(in_ward, key=lambda f: f["id"]):
            if facility.get("type") == wanted:
                return facility["name"]
    return None


class CallRefused(Exception):
    """The call cannot be conducted (unknown, already finished, or its records are missing).

    Raised before anything is changed."""


def _prepare(deps: CallDeps, call_id: str):
    store = deps.store
    call = store.get("calls", call_id)
    if call is None:
        raise CallRefused(f"calls/{call_id} does not exist")
    if call["status"] in FINAL_STATUSES:
        raise CallRefused(f"call {call_id} already finished as {call['status']}")
    resident = store.get("residents", call["resident_id"])
    if resident is None:
        raise CallRefused(f"residents/{call['resident_id']} does not exist")
    current_round = store.get("rounds", call["round_id"])
    if current_round is None:
        raise CallRefused(f"rounds/{call['round_id']} does not exist")
    # The follow-ups everyone is asked: the script says them and the tool handler enforces them.
    has_conditions = bool(resident.get("conditions"))
    script = {
        "always_asked": always_asked(current_round["hazard"], has_conditions, resident["language"]),
        "order": question_order(current_round["hazard"], has_conditions),
    }
    instruction = build_instruction(
        resident,
        current_round["hazard"],
        current_round.get("peak_temp_c"),
        _nearest_facility(store, resident, current_round["hazard"]),
        speaks_as=deps.settings.live_speaks_as,
        channel=call.get("channel", "phone"),
    )
    # Last, so that nothing above can leave the call marked started. The settings go in the
    # same write, so that two test calls made with different settings can be told apart later.
    mark_call_started(store, call_id, "in_progress", deps.clock(), live_settings_snapshot(deps.settings))
    return resident, instruction, script


def _finalise(deps: CallDeps, call_id: str, tools: CallTools, reason: str, language: str) -> str:
    """Store the outcome. The order matters: the outcome comes before the cosmetic write."""
    status = tools.final_status(reason)
    # Load-bearing order: flush() writes the facts whose store write failed during the call, and
    # the outcome below is decided from, and the case computed from, what the call document
    # holds. Recording the outcome first could lose an emergency or end the case amber.
    tools.flush()  # never raises
    for attempt in (1, 2):
        try:
            handle_call_outcome(
                deps.store, deps.queue, call_id, status, deps.clock(), deps.tz,
                deps.settings.call_start_hour, deps.settings.call_end_hour,
            )
            break
        except Exception:
            logger.exception("could not record the outcome of call %s (attempt %d)", call_id, attempt)
            if attempt == 2:
                raise
    try:
        deps.store.update("calls", call_id, {"language_used": language})
    except Exception:
        logger.exception("could not record the language of call %s", call_id)
    # The stored status can differ from what the tools computed (a connected call that reached
    # nobody is stored as no_answer), so report what the call document says.
    try:
        stored = deps.store.get("calls", call_id)
        if stored and stored.get("status") in FINAL_STATUSES:
            return stored["status"]
    except Exception:
        logger.exception("could not read back the status of call %s", call_id)
    return status


async def _settle(future: asyncio.Future) -> asyncio.CancelledError | None:
    """Wait until a worker-thread future has finished, whatever happens to this task.

    The future is shielded and always awaited, so it never outlives the caller and its exception
    is always retrieved. Returns the CancelledError this task received meanwhile, if any."""
    cancelled = None
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError as error:
            # Remember it even if the worker has just finished; only the worker's own
            # cancellation is not a cancellation of this task.
            if not future.cancelled():
                cancelled = cancelled or error
        except Exception:
            pass  # the future's own failure: the caller reads it with future.result()
    return cancelled


async def _keep_transcript_saved(tools: CallTools, every: float, stop: asyncio.Event) -> None:
    """While the call runs, store the transcript every `every` seconds if it has changed, and
    with it the timeline of the call so far.

    Ends when `stop` is set. Never raises: if anything here fails, that is logged once and the
    saving stops, and the call goes on. The last lines are written by tools.flush(), which runs
    only after this has finished, so an older copy can never land on top of the final one."""
    if every <= 0:
        return
    while True:
        try:
            try:
                await asyncio.wait_for(stop.wait(), timeout=every)
                return
            except asyncio.TimeoutError:
                pass
            entries = tools.transcript_if_changed()
            if entries is not None:
                await asyncio.to_thread(tools.write_transcript, entries, tools.timeline())
        except Exception:
            # Nothing from the transcript goes in the message. The whole transcript is still
            # written when the call ends.
            logger.exception("the running transcript is no longer being saved during this call")
            return


# Someone in distress may be unable to speak. A keypad press on the phone, or the help button of
# a browser call, makes the call an emergency: flagged as the agent's flag_emergency would flag
# it, the agent told in this note, and the call ended for it if the agent has not ended it
# within HELP_END_SECONDS. Once per call, however many presses follow.
HELP_REASON = "pressed a button, could not speak"
HELP_NOTE = "[the person pressed a button on the phone]"
HELP_END_SECONDS = 20.0


async def _escalate_when_pressed(
    call_id: str, pressed: asyncio.Event, tools: CallTools, notes: asyncio.Queue, audio_in: asyncio.Queue
) -> None:
    """Waits for the press, then escalates. Each write is awaited to its end even if this task
    is cancelled meanwhile, so that the outcome is never recorded before the emergency."""
    from app.call.bridge_common import push_hangup  # bridge_common imports this module

    await pressed.wait()
    tools.add_event("help_pressed")
    flagged = asyncio.ensure_future(asyncio.to_thread(tools.handle, "flag_emergency", {"reason": HELP_REASON}))
    cancelled = await _settle(flagged)
    tools.add_event("tool", name="flag_emergency", by="help_pressed")
    if cancelled is not None:
        raise cancelled
    logger.warning("call %s: the help button was pressed; flagged as an emergency", call_id)
    notes.put_nowait(HELP_NOTE)
    await asyncio.sleep(HELP_END_SECONDS)
    if tools.ended:
        return
    logger.warning("call %s: the agent did not end the call after the help button; ended as an emergency", call_id)
    ending = asyncio.ensure_future(asyncio.to_thread(tools.end_for_the_agent, "emergency"))
    cancelled = await _settle(ending)
    tools.add_event("tool", name="end_call", by="help_pressed")
    if cancelled is not None:
        raise cancelled
    push_hangup(audio_in)  # the relays stop; the call ends as ended by the agent, since tools.ended is set


def _time_limit(settings, resident: dict) -> int:
    """A visitor's test call on their own phone is shorter: its cost is reserved up front."""
    if resident.get("visitor_id") and resident.get("phone"):
        return settings.visitor_phone_max_seconds
    return settings.max_call_seconds


async def conduct_call(
    deps: CallDeps, call_id: str, audio_in: asyncio.Queue, send_audio, clear_audio,
    help_pressed: asyncio.Event | None = None,
) -> str:
    """Run one answered call and store its final status.

    Raises CallRefused, before changing anything, for a call that cannot be conducted. Any other
    exception while preparing also leaves the call untouched. Once the call is marked started,
    the single `finally` below records its outcome on every exit: return, error or cancellation.
    """
    answered_at = asyncio.get_running_loop().time()  # the pickup is timed from here
    prepared = asyncio.ensure_future(asyncio.to_thread(_prepare, deps, call_id))
    cancelled = await _settle(prepared)
    if prepared.exception() is not None:  # nothing was marked started
        raise cancelled or prepared.exception()
    resident, instruction, script = prepared.result()
    # ---- the call is marked started: from here the finally block always records the outcome
    tools = CallTools(deps.store, call_id, deps.clock, **script)
    reason = "open_failed"  # what a cancellation or failure before the session opens means
    stop_saving = asyncio.Event()
    saver = asyncio.ensure_future(
        _keep_transcript_saved(tools, deps.settings.transcript_save_seconds, stop_saving)
    )
    saver.add_done_callback(lambda task: task.cancelled() or task.exception())  # never left unread
    notes: asyncio.Queue = asyncio.Queue()  # passed to the agent as they come
    escalation = None
    if help_pressed is not None:
        escalation = asyncio.ensure_future(_escalate_when_pressed(call_id, help_pressed, tools, notes, audio_in))
        escalation.add_done_callback(lambda task: task.cancelled() or task.exception())
    try:
        if cancelled:  # cancelled while the preparation was finishing
            raise cancelled
        async with deps.open_session(deps.settings, instruction) as session:
            reason = "error"  # from here a failure is a dropped conversation, not a failed dial
            reason = await run_call(
                session, tools, audio_in, send_audio, clear_audio,
                opening_prompt(resident), _time_limit(deps.settings, resident),
                quiet_seconds=deps.settings.live_quiet_seconds,
                quiet_limit=deps.settings.live_quiet_limit,
                greeting_guard_seconds=deps.settings.live_greeting_guard_seconds,
                closing_silence_seconds=deps.settings.live_closing_silence_seconds,
                mute_repeats=deps.settings.live_mute_repeats,
                notes=notes,
                answered_at=answered_at,
            )
    except asyncio.CancelledError as error:
        cancelled = cancelled or error
        if reason != "open_failed":
            reason = "caller_hung_up"
        raise
    except Exception:
        logger.exception("call %s ended with an error (%s)", call_id, reason)
    finally:
        # An escalation that is writing is let finish, so that its emergency is recorded first.
        escalation_cancel = None
        if escalation is not None:
            escalation.cancel()
            escalation_cancel = await _settle(escalation)
        # First let a save that is in flight finish, so that it cannot overwrite the final
        # transcript. Then record the outcome, as before.
        stop_saving.set()
        saver_cancel = await _settle(saver) or escalation_cancel
        outcome = asyncio.ensure_future(
            asyncio.to_thread(_finalise, deps, call_id, tools, reason, resident["language"])
        )
        late_cancel = await _settle(outcome) or saver_cancel
        status = outcome.result()  # raises if the outcome could not be stored (already logged)
    if late_cancel is not None:
        raise late_cancel
    return status
