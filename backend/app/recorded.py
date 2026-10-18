"""The recorded call, for a visitor with no microphone: one of the team's own browser calls as
a synthetic character, kept as a timeline that a screen can replay beside its audio.

It is a file, not a call: playing it creates nothing and counts against no limit."""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
RECORDED_DIR = Path(__file__).parent / "static"
TIMELINE_NAME = "recorded_call.json"
AUDIO_NAMES = ("recorded_call.webm", "recorded_call.mp3", "recorded_call.m4a", "recorded_call.wav")


def build_timeline(call: dict, feed: dict) -> dict:
    """What happened in a finished call, as events in seconds from its start, plus its final feed."""
    transcript = call.get("transcript") or []
    start = call.get("started_at") or (transcript[0].get("at") if transcript else None)

    def seconds(moment) -> float:
        if start is None or moment is None:
            return 0.0
        return round(max(0.0, (moment - start).total_seconds()), 1)

    events = [
        {"t": seconds(entry.get("at")), "type": "transcript", "role": entry.get("role"), "text": entry.get("text")}
        for entry in transcript
    ]
    events += [
        {
            "t": seconds(question["at"]), "type": "answer", "id": question["id"],
            "value": question["value"], "value_label": question["value_label"], "quote": question["quote"],
        }
        for question in feed["questions"]
        if question["value"] is not None
    ]
    if feed.get("emergency"):
        events.append({"t": seconds(feed["emergency"].get("at")), "type": "emergency", "reason": feed["emergency"]["reason"]})
    if feed.get("respondent") not in (None, "unknown"):
        events.append({"t": 0.0, "type": "respondent", "value": feed["respondent"]})
    duration = seconds(call.get("ended_at"))
    events.sort(key=lambda event: event["t"])  # stable: equal times keep the order above
    events.append({"t": duration, "type": "ended", "status": call.get("status"), "end_outcome": call.get("end_outcome")})
    return {
        "card_id": feed.get("card_id"),
        "language": call.get("language_used"),
        "duration_s": int(duration),
        "events": events,
        "result": feed,
    }


def load_recording(directory: Path) -> dict | None:
    try:
        recording = json.loads((directory / TIMELINE_NAME).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.exception("the recorded call file could not be read")
        return None
    return recording if isinstance(recording, dict) else None


def audio_file(directory: Path) -> Path | None:
    for name in AUDIO_NAMES:
        if (directory / name).is_file():
            return directory / name
    return None
