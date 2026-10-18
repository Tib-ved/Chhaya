"""Opening a phone call's voice session while the phone rings, so that it is ready when answered."""
import asyncio
import logging

from app.call.deps import CallDeps
from app.call.guidance import build_instruction
from app.call.session import _nearest_facility

logger = logging.getLogger(__name__)


def call_instruction(deps: CallDeps, call_id: str) -> tuple[str, str]:
    """The instruction conduct_call will open this call's session with, and the call's language.
    Built as session._prepare builds it: if the two ever differ, the pre-opened session is simply
    not taken, and the call opens its own as before."""
    store = deps.store
    call = store.get("calls", call_id)
    resident = store.get("residents", call["resident_id"])
    current_round = store.get("rounds", call["round_id"])
    instruction = build_instruction(
        resident,
        current_round["hazard"],
        current_round.get("peak_temp_c"),
        _nearest_facility(store, resident, current_round["hazard"]),
        speaks_as=deps.settings.live_speaks_as,
    )
    return instruction, resident["language"]


def pre_open_call(deps: CallDeps, call_id: str) -> None:
    """Start opening the voice session of a phone call that is being dialled. Called from the
    worker thread of a dial endpoint, or from the event loop. Never raises: a call whose session
    was not pre-opened opens its own when it is answered."""
    pre_open = getattr(deps.open_session, "pre_open", None)  # the real voice model only
    if pre_open is None or deps.settings.live_pre_open_seconds <= 0:
        return
    try:
        instruction, language = call_instruction(deps, call_id)
        try:
            asyncio.get_running_loop()
        except RuntimeError:  # a worker thread: hand it to the event loop
            import anyio.from_thread

            anyio.from_thread.run_sync(pre_open, deps.settings, instruction, language)
        else:
            pre_open(deps.settings, instruction, language)
    except Exception as error:
        logger.warning("could not pre-open the voice session of call %s (%s)", call_id, type(error).__name__)
