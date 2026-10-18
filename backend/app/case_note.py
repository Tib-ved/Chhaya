"""A two-line note on a case, written by the text model from what the call recorded.

The note is for the worker who will visit. It is never read by triage, it is stored only if
it passes the fixed checks below, and a case that already has a note is left alone. So is a
case the model has been asked about MAX_NOTE_TRIES times without a usable note."""
import logging
import re

from app.db import Store
from app.textgen import TextModel
from app.triage import REASON_LABELS

logger = logging.getLogger(__name__)
MAX_NOTE_CHARS = 280
MAX_NOTE_TRIES = 2  # a case whose note failed this often is not asked for again
# Never said to a worker: that someone is fine, what they have, or what to give them.
# One table of patterns, matched without regard to case by forbidden() below. It catches the
# common ways of saying these things, not every possible one. \b keeps a pattern to whole
# words, so that "wellness", "well water", "unstable" and "doors" are not refused.
_STATE = r"(?:\b(?:is|are|was|were|seems?|seemed|feels?|felt|looks?|looked|sounds?|sounded|appears?|appeared|doing)|['’](?:s|re))"
_DEGREE = r"(?:\s+(?:doing|feeling|very|quite|really|perfectly|completely|otherwise|now|just|also))*"
_MEDICINE = r"(?:\b(?:tablets?|pills?|doses?|ors|syrups?|injections?|crocin|dolo)\b|\d\s?mg\b|\bmg\b)"
FORBIDDEN = (
    # reassurance: someone is, seems, feels, looks or is doing fine, well, okay or all right
    _STATE + _DEGREE + r"\s+(?:fine|well(?!\s+water)|okay|ok|all\s?right|alright|healthy|safe)\b",
    r"\bnothing to worry", r"\bno need to\b", r"\bno visit", r"\bno concerns?\b",
    r"\bnot urgent\b", r"\bstable\b",
    # a diagnosis
    r"diagnos", r"heat[\s-]?stroke", r"dehydrat", r"hypotherm", r"infection", r"fever",
    # a medicine by name, or being told to give or take one
    r"paracetamol", r"aspirin", r"ibuprofen", r"antibiotic", r"\bcrocin\b", r"\bdolo\b",
    r"\b(?:give|take)s?\b[^.;!?]*?" + _MEDICINE,
)
_FORBIDDEN = re.compile("|".join(f"(?:{pattern})" for pattern in FORBIDDEN), re.IGNORECASE)
INSTRUCTION = (
    "You write a short note for a frontline health worker who is about to visit a resident "
    "after an automated wellness call during a heatwave or cold wave. Write in plain English, "
    "at most two short lines and 40 words. Line 1: what the person said or what was learned. "
    "Line 2: what to check on the visit. Use only what is given to you. Never say or imply "
    "that the person is fine, healthy or safe. Never name a diagnosis or a medicine. Do not "
    "mention a tier or a rule code. Output the note only."
)


def forbidden(text: str) -> bool:
    """Whether the text says something from the fixed list. The summary check uses this too."""
    return _FORBIDDEN.search(text) is not None


def clean_note(text: object) -> str | None:
    """The note as it may be stored, or None if it fails a check."""
    if not isinstance(text, str):
        return None
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.strip().splitlines() if line.strip()]
    note = "\n".join(lines[:2])
    if not note or len(note) > MAX_NOTE_CHARS:
        return None
    return None if forbidden(note) else note


def note_prompt(resident: dict, case: dict, calls: list[dict]) -> str:
    lines = [
        f"Resident: aged {resident.get('age')}"
        + (", lives alone" if resident.get("lives_alone") else "")
        + (", works outdoors" if resident.get("outdoor_worker") else "")
        + (f", conditions: {', '.join(resident['conditions'])}" if resident.get("conditions") else ""),
        "Why the case was opened: " + "; ".join(REASON_LABELS.get(r, r) for r in case["reasons"]),
    ]
    for call in calls:
        if call.get("respondent") in ("family", "other"):
            lines.append(f"The call was answered by someone else ({call['respondent']}).")
        emergency = call.get("emergency") or {}
        if emergency.get("flagged"):
            lines.append(f"Emergency flagged: {str(emergency.get('reason', ''))[:200]}")
        for question, answer in (call.get("answers") or {}).items():
            quote = f' ("{str(answer.get("quote"))[:200]}")' if answer.get("quote") else ""
            lines.append(f"Recorded {question} = {answer.get('value')}{quote}")
        spoken = [entry for entry in call.get("transcript") or [] if entry.get("role") in ("agent", "resident")]
        if spoken:
            lines.append("Transcript, as spoken:")
            lines += [f"  {entry['role']}: {str(entry.get('text', ''))[:300]}" for entry in spoken[-40:]]
    return "\n".join(lines)


def write_case_note(store: Store, text_model: TextModel, round_id: str, resident_id: str) -> str | None:
    """Write the note for one case if it needs one. Returns the note stored, or None.
    Never raises for anything the model does."""
    case = store.get("cases", f"{round_id}_{resident_id}")
    if case is None or case.get("note") or case["tier"] == "green":
        return None
    tries = case.get("note_tries") or 0
    if tries >= MAX_NOTE_TRIES:
        return None  # asked often enough: the model calls go to the cases still waiting
    calls = sorted(store.query("calls", round_id=round_id, resident_id=resident_id), key=lambda c: c["attempt"])
    if not any(
        call.get("answers") or any(entry.get("role") == "resident" for entry in call.get("transcript") or [])
        for call in calls
    ):
        return None  # nobody spoke: there is nothing to write from
    resident = store.get("residents", resident_id) or {}
    try:
        # Counted before asking, and on its own, so that a failure of any kind is counted.
        store.update("cases", case["id"], {"note_tries": tries + 1})
    except KeyError:
        return None  # the case went away meanwhile (a rebuild)
    try:
        written = text_model(INSTRUCTION, note_prompt(resident, case, calls))
    except Exception as error:  # only the type: a message can carry the caller's words
        logger.error("no note for case %s: the text model failed (%s)", case["id"], type(error).__name__)
        return None
    note = clean_note(written)
    if note is None:
        logger.warning("no note for case %s: what was written did not pass the checks", case["id"])
        return None
    try:
        store.update("cases", case["id"], {"note": note})
    except KeyError:
        return None  # the case went away meanwhile (a rebuild)
    return note


def note_for_call(store: Store, text_model: TextModel, call_id: str) -> str | None:
    call = store.get("calls", call_id)
    if call is None:
        return None
    return write_case_note(store, text_model, call["round_id"], call["resident_id"])


def backfill_notes(store: Store, text_model: TextModel, round_ids: list[str], limit: int = 5) -> int:
    """Write notes that are missing in these rounds, at most `limit` model calls in all."""
    asked = written = 0

    def counted(instruction: str, prompt: str) -> str:
        nonlocal asked
        asked += 1
        return text_model(instruction, prompt)

    for round_id in round_ids:
        for case in sorted(store.query("cases", round_id=round_id), key=lambda c: c["id"]):
            if asked >= limit:
                return written
            if case.get("note") or case["tier"] == "green":
                continue
            # A case nobody spoke on is passed over inside without asking the model.
            if write_case_note(store, counted, round_id, case["resident_id"]):
                written += 1
    return written
