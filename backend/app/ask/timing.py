"""How long each step of an Ask Chhaya turn takes: the topic check, every model call and every tool.

One line per step goes to the log (`app.ask.timing`, INFO) and, when ASK_TIMING_FILE names a file, is
appended there too, so an evaluation run can read them back. The callbacks only watch: they return None.
"""
import logging
import os
import threading
import time

log = logging.getLogger("app.ask.timing")

_starts: dict[tuple[str, str], float] = {}
_lock = threading.Lock()
STALE_SECONDS = 300  # a start with no end (an error, a cancelled turn) is forgotten after this long


def clock() -> float:
    return time.perf_counter()


def note(step: str, name: str, seconds: float, invocation: str) -> None:
    line = f"ask timing step={step} name={name} seconds={seconds:.3f} invocation={invocation}"
    log.info(line)
    path = os.environ.get("ASK_TIMING_FILE", "").strip()
    if path:
        try:
            with _lock, open(path, "a", encoding="utf-8") as out:
                out.write(line + "\n")
        except OSError as error:
            log.warning("ask timing file not written: %s", type(error).__name__)


def _start(key: tuple[str, str]) -> None:
    with _lock:
        now = clock()
        for old in [k for k, t in _starts.items() if now - t > STALE_SECONDS]:
            del _starts[old]
        _starts[key] = now


def _stop(step: str, name: str, key: tuple[str, str]) -> None:
    with _lock:
        started = _starts.pop(key, None)
    if started is not None:
        note(step, name, clock() - started, key[0])


def before_model(callback_context, llm_request):
    _start((callback_context.invocation_id, callback_context.agent_name))
    return None


def after_model(callback_context, llm_response):
    if getattr(llm_response, "partial", False):  # streamed chunks: only the final one ends the call
        return None
    _stop("model", callback_context.agent_name, (callback_context.invocation_id, callback_context.agent_name))
    return None


def before_tool(tool, args, tool_context):
    _start((tool_context.invocation_id, tool_context.function_call_id))
    return None


def after_tool(tool, args, tool_context, tool_response):
    _stop("tool", tool.name, (tool_context.invocation_id, tool_context.function_call_id))
    return None
