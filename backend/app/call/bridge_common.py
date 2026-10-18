"""Mechanics shared by the browser bridge and the Twilio bridge."""
import asyncio
import logging

from app.call.session import _settle

logger = logging.getLogger(__name__)

AUDIO_QUEUE_FRAMES = 250  # about five seconds of 20 ms frames


def new_audio_queue() -> asyncio.Queue:
    return asyncio.Queue(maxsize=AUDIO_QUEUE_FRAMES)


def push_frame(audio_in: asyncio.Queue, frame: bytes) -> None:
    """Queue one audio frame. When the queue is full the oldest frame is dropped."""
    while audio_in.full():
        audio_in.get_nowait()
    audio_in.put_nowait(frame)


def push_hangup(audio_in: asyncio.Queue) -> None:
    """Queue the 'caller has gone' sentinel. Always fits: a full queue loses its oldest frames."""
    while audio_in.full():
        audio_in.get_nowait()
    audio_in.put_nowait(None)


async def stop_reader(reader: asyncio.Task | None) -> asyncio.CancelledError | None:
    """Cancel the reader task and wait until it has stopped, whatever happens to this task.

    Returns the cancellation received meanwhile, for the caller to re-raise."""
    if reader is None:
        return None
    reader.cancel()
    return await _settle(reader)


AFTER_CALL_SECONDS = 10.0  # the longest the ended call's socket is kept for the after-call step


async def run_after_call(deps, call_id: str) -> None:
    """Run the after-call step (the case note) in a worker thread, for a bounded time.
    Never raises, except for the cancellation of this task: the call has already been stored."""
    if deps.after_call is None:
        return
    try:
        await asyncio.wait_for(asyncio.to_thread(deps.after_call, call_id), AFTER_CALL_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("the after-call step of call %s is taking long; left to finish by itself", call_id)
    except Exception as error:
        logger.error("the after-call step of call %s failed (%s)", call_id, type(error).__name__)
