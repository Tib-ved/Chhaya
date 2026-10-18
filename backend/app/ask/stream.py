"""ADK events in, the browser's server-sent events out.

In SSE mode ADK sends each piece of text as a partial event and then the whole response again as a final
one, and each tool call twice the same way. Text is gathered from the partials and passed on at the final,
unless that response ends in a tool call; calls are passed on only from the finals.
Only the root agent's events reach here: a specialist (an AgentTool) runs out of sight.
"""
import json
from typing import AsyncIterator

from google.adk.events import Event

from app.ask.guard import REFUSAL
from app.ask.visuals import validate_visual

ROOT = "chhaya"
VISUALS = {"show_chart": "chart", "show_table": "table"}
LABELS = {
    "list_areas": "Looking up areas", "get_area_overview": "Reading the figures for {area}",
    "list_alerts": "Checking heat alerts", "list_rounds": "Checking call rounds", "list_cases": "Reading cases in {area}",
    "get_case": "Opening a case", "top_risk_residents": "Ranking residents by risk in {area}",
    "find_resident": "Finding the resident", "get_resident": "Reading the resident's record",
    "list_calls": "Reading calls", "get_call": "Opening a call", "get_ward_staff": "Looking up ward staff",
    "analyst": "Analysing the numbers", "call_summarizer": "Summarising the call",
}


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def status_label(name: str, args: dict) -> str:
    area = (args or {}).get("area") or "all areas"
    return LABELS.get(name, "Working").format(area=area) + "…"


def _collect_ids(value, case_ids: list, call_ids: list) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "case_id" and isinstance(item, str) and item not in case_ids:
                case_ids.append(item)
            elif key == "call_id" and isinstance(item, str) and item not in call_ids:
                call_ids.append(item)
            else:
                _collect_ids(item, case_ids, call_ids)
    elif isinstance(value, list):
        for item in value:
            _collect_ids(item, case_ids, call_ids)


class Turn:
    """What one turn produced, for the end-of-turn events and the stored message."""

    def __init__(self) -> None:
        self.text = ""
        self.case_ids: list[str] = []
        self.call_ids: list[str] = []
        self.visuals: list[dict] = []

    @property
    def refused(self) -> bool:
        return self.text.strip() == REFUSAL


async def to_sse(events: AsyncIterator[Event], turn: Turn) -> AsyncIterator[str]:
    # A response's text is held until its final event shows whether it ends in a tool call: words
    # written before a tool call are the model thinking aloud ("Ward 58 likely churu-ward-58."), not
    # the answer, and are dropped. So the answer arrives one response at a time, not word by word.
    held = ""
    try:
        async for event in events:
            if event.error_code:
                raise RuntimeError(f"model: {event.error_code} {event.error_message or ''}".strip())
            if event.author != ROOT or not event.content:
                continue
            parts = event.content.parts or []
            if event.partial:
                held += "".join(part.text for part in parts if part.text and not part.thought)
                continue
            text = held or "".join(part.text for part in parts if part.text and not part.thought)
            held = ""
            calls = any(part.function_call for part in parts)
            async for chunk in _final(parts, turn):
                yield chunk
            if text and not calls:
                turn.text += text
                yield sse("delta", {"text": text})
    except Exception:
        if held:  # a failure mid-answer keeps what had been written
            turn.text += held
            yield sse("delta", {"text": held})
        raise


async def _final(parts, turn: Turn) -> AsyncIterator[str]:
    """The tool calls and results of a response's final event."""
    for part in parts:
        if part.function_call:
            call = part.function_call
            args = dict(call.args or {})
            if call.name in VISUALS:
                spec = validate_visual(call.name, args)
                if spec is not None:
                    visual = {"id": call.id or f"{VISUALS[call.name]}-{len(turn.visuals) + 1}", "spec": spec}
                    turn.visuals.append({"kind": VISUALS[call.name], **visual})
                    yield sse(VISUALS[call.name], visual)
            else:
                yield sse("status", {"label": status_label(call.name, args)})
        elif part.function_response:
            _collect_ids(part.function_response.response, turn.case_ids, turn.call_ids)
