"""Record the library of AI-resident conversations that fill each day's rounds.

Each persona in seed/fixtures/ai_personas.json is played by the text model, in each of its
languages, on a call with Chhaya's agent: the real instruction, the real tools and the real
after-call path decide the answers and the tier. Run from backend/, with the text model set:
    python -m scripts.record_ai_calls [--only ID[,ID...]] [--limit N] [--out PATH] [--merge]
                                      [--batch-size N] [--pause-seconds S] [--workers N]
                                      [--claude [--claude-agent-model M] [--claude-resident-model M]]
The conversations are run one after another, or up to --workers N at once within a batch (the
file keeps the plan's order). A conversation that fails is reported and left out;
with --merge the file's earlier conversations that were not recorded again are kept. --only takes
one persona id or several, separated by commas. With --batch-size N the conversations are run in
batches of N, the file is written after every batch (conversations recorded in this run replace
those with the same id, the others are kept) and the script waits --pause-seconds (default 180)
between batches; the default of 0 is one batch. Each conversation carries its own recorded_at,
commit and model, and the places of its tool calls in the transcript. A request the model service
refuses on policy grounds stops the run: what is recorded is written and the exit code is 3.
A rate limit (429) is waited out in the model call, 20, 40 and 80 s; three conversations in a row
that still fail on one stop the run the same way, with exit code 5. Any other conversation that
fails is left out and the exit code is 1.
With --claude, Claude stands in for the text model on both sides (evals.voice.claude_models; run
with backend/.venv-claude and ANTHROPIC_API_KEY): each conversation carries the agent's model and
its resident_model, and the file is marked "stand_in": true. GCP_PROJECT and TEXT_MODEL are then
not needed.
The resident's name is the token {name} in the output. Check the transcripts before committing."""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import time as clock
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai_resident import (  # noqa: E402
    is_policy_refusal, is_rate_limited, make_agent_model, make_resident_model, run_conversation,
)
from app.config import get_settings  # noqa: E402
from app.db import MemoryStore  # noqa: E402
from app.dispatch import RecordingQueue, call_id_for, plan_round  # noqa: E402
from app.heat_scenario import scenario_alert  # noqa: E402
from app.ingest.alerts import record_alert  # noqa: E402
from app.models import Consent, Resident  # noqa: E402
from app.risk import risk_score  # noqa: E402
from seed.names_hi import name_hi  # noqa: E402
from seed.seed import _district  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
PERSONAS = BACKEND / "seed" / "fixtures" / "ai_personas.json"
DEFAULT_OUT = BACKEND / "seed" / "fixtures" / "ai_calls.json"
NAME_TOKEN = "{name}"
DISTRICT_ID = "churu"
# This many conversations in a row failing on a rate limit (after backing off) end the run.
RATE_LIMIT_STOP = 3
RESIDENT_ID = "ai-resident"
TEST_PHONE = "+910000000001"  # a test number: the call is never dialled
# The name the agent is given for the resident, by gender, replaced by the token in the output.
NAMES = {"f": "Kamla Saini", "m": "Mohan Jangir"}
AGES = {"under_60": 45, "60_69": 66, "70_79": 74, "80_plus": 83}
# The model-free entries: no conversation, only failed attempts as (status, minutes after the first).
NOBODY_ANSWERED = [["no_answer", 0], ["no_answer", 20], ["busy", 80]]
ALWAYS_BUSY = [["busy", 0], ["busy", 20], ["busy", 80]]


def load_personas(path: Path = PERSONAS) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["personas"]


def conversation_id(persona_id: str, language: str) -> str:
    return f"{persona_id}_{language}"


def plan(personas: list[dict], only: str | None = None, limit: int | None = None) -> list[tuple[dict, str]]:
    """Every (persona, language) to record, in the file's order. `only` is one persona id or
    several, separated by commas."""
    wanted = {part.strip() for part in only.split(",") if part.strip()} if only else None
    chosen = [(p, language) for p in personas if wanted is None or p["id"] in wanted for language in p["language"]]
    return chosen[:limit] if limit is not None else chosen


def _name_patterns(name: str) -> list[re.Pattern]:
    """The resident's name as the agent or the resident might say it: whole, in Devanagari, or in
    part. Longest first, so a whole name becomes one token."""
    first, _, last = name.partition(" ")
    forms = [name, name_hi(name), *name_hi(name).split(" "), first, last]
    unique = sorted({form for form in forms if form}, key=len, reverse=True)
    # An honorific may be joined to the name ("Mohanji", "मोहनजी"); it stays, the name goes.
    return [re.compile(rf"(?<!\w){re.escape(form)}(?=ji\b|जी|(?!\w))") for form in unique]


def tokenise(text, name: str):
    """The text with the resident's name replaced by the token."""
    if not isinstance(text, str):
        return text
    for pattern in _name_patterns(name):
        text = pattern.sub(NAME_TOKEN, text)
    return text


def build_call(persona: dict, language: str, now: datetime, tz: ZoneInfo, settings) -> tuple[MemoryStore, str, str]:
    """A fresh store with Churu's wards, today's scenario round and one queued call to a resident
    made from the persona. Returns (store, call_id, the name the agent is given)."""
    store = MemoryStore()
    register = _district(0, DISTRICT_ID, "Churu", "Rajasthan", [0])
    for collection in ("districts", "wards", "workers", "facilities"):
        for doc in register[collection]:
            store.set(collection, doc["id"], doc)
    district = register["districts"][0]
    alert = scenario_alert(district, now, tz, settings)
    if alert is None:
        raise RuntimeError("the heat scenario raises no alert for Churu")
    round_id = record_alert(store, alert, tz).id
    store.update("rounds", round_id, {
        "peak_temp_c": alert.raw["peak_c"], "peak_hour_local": alert.raw["peak_hour"],
        "forecast_hours": alert.raw["forecast_hours"],
    })
    profile = persona["profile"]
    ward = register["wards"][0]
    name = NAMES[persona.get("gender", "f")]
    age = AGES[profile["age_band"]]
    conditions = [persona.get("condition", "heart")] if profile["conditions"] else []
    resident = Resident(
        id=RESIDENT_ID, name=name, phone=TEST_PHONE, age=age, language=language,
        district_id=DISTRICT_ID, ward_id=ward["id"], lat=ward["lat"], lng=ward["lng"],
        lives_alone=profile["lives_alone"], outdoor_worker=profile["outdoor_worker"],
        conditions=conditions, consent=Consent(given=True),
    )
    resident.risk_score = risk_score(age, resident.lives_alone, resident.outdoor_worker, conditions)
    store.set("residents", RESIDENT_ID, {**resident.model_dump(), "name_hi": name_hi(name)})
    plan_round(store, RecordingQueue(), round_id, now, tz)
    return store, call_id_for(round_id, RESIDENT_ID, 1), name


def record_one(persona: dict, language: str, agent, resident, now: datetime, tz: ZoneInfo, settings,
               labels: dict | None = None) -> dict:
    """One conversation, as it goes into the library. `labels` replace or add to its model fields."""
    store, call_id, name = build_call(persona, language, now, tz, settings)
    playing = {**persona, "language": language}
    result = run_conversation(store, call_id, playing, agent, resident, lambda: now, settings, tz)
    answers = result["answers"]
    emergency = result["emergency"]
    error = result.get("error")
    if isinstance(error, Exception):
        raise error  # never a recorded call; the run stops on a refusal, and lists any other as failed
    tools = [
        {"name": e["name"], "line": e["line"], **({"refused": e["refused"]} if e.get("refused") else {})}
        for e in result["timeline"] if e.get("kind") == "tool" and "line" in e
    ]
    return {
        "id": conversation_id(persona["id"], language),
        "persona_id": persona["id"],
        "language": language,
        "profile": dict(persona["profile"]),
        "status": result["status"],
        "respondent": result["respondent"],
        "emergency": tokenise(emergency.get("reason"), name) if isinstance(emergency, dict) else tokenise(emergency, name),
        "answers": {q: a.get("value") for q, a in answers.items()},
        "quotes": {q: tokenise(a["quote"], name) for q, a in answers.items() if a.get("quote")},
        "tier": result["tier"],
        "transcript": [{"role": e["role"], "text": tokenise(e["text"], name)} for e in result["transcript"]],
        "tools": tools,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "commit": git_commit(),
        "model": settings.text_model,
        **(labels or {}),
    }


def _model_free(entry_id: str, status: str, failures: list) -> dict:
    return {
        "id": entry_id, "persona_id": None, "language": None, "profile": None,
        "status": status, "respondent": "unknown", "emergency": None, "answers": {}, "quotes": {},
        "tier": "amber",  # no answer after three attempts (A8)
        "transcript": [], "failures": [list(f) for f in failures],
    }


def model_free_entries() -> list[dict]:
    return [_model_free("nobody_answered", "no_answer", NOBODY_ANSWERED), _model_free("busy", "busy", ALWAYS_BUSY)]


def build_library(chosen, agent, resident, settings, now, tz, existing: list[dict] | None = None, log=print,
                  limited: list[int] | None = None, workers: int = 1, labels: dict | None = None):
    """Record every chosen conversation, up to `workers` at once, in the plan's order. Returns
    (conversations, errors), with the earlier conversations not recorded again kept after the new
    ones and the model-free entries last. A failed conversation is listed in the errors and left
    out. A request the model service refuses on policy grounds ends the batch with `stopped` set,
    and nothing more is started. Three conversations in a row (in the order they finish) that fail
    on a rate limit end it too. Conversations already running then finish and are kept. Returns
    (conversations, errors, stopped), where stopped is "" or why: "policy" or "rate limit"."""
    by_position: dict[int, dict] = {}
    errors: list[tuple[str, str]] = []
    stopped = ""
    limited = limited if limited is not None else [0]  # rate-limited in a row; main keeps it across batches
    queue = list(enumerate(chosen, 1))
    workers = max(1, workers)
    running: dict = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while queue or running:
            while queue and not stopped and len(running) < workers:
                position, (persona, language) = queue.pop(0)
                cid = conversation_id(persona["id"], language)
                log(f"[{position}/{len(chosen)}] {cid}")
                future = pool.submit(record_one, persona, language, agent, resident, now, tz, settings, labels)
                running[future] = (position, cid)
            if not running:
                break  # stopped: nothing more is started
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                position, cid = running.pop(future)
                error = future.exception()
                if error is None:
                    by_position[position] = future.result()
                    limited[0] = 0
                elif is_policy_refusal(error):
                    stopped = "policy"
                    log(f"  {cid} refused: {error}")
                else:  # reported, and the next one goes on
                    errors.append((cid, f"{type(error).__name__}: {error}"))
                    log(f"  {cid} failed: {type(error).__name__}: {error}")
                    limited[0] = limited[0] + 1 if is_rate_limited(error) else 0
                    if limited[0] >= RATE_LIMIT_STOP:
                        stopped = stopped or "rate limit"
    recorded = [by_position[position] for position in sorted(by_position)]
    done = {entry["id"] for entry in recorded}
    free_ids = {entry["id"] for entry in model_free_entries()}
    kept = [e for e in existing or [] if e.get("id") not in done and e.get("id") not in free_ids]
    return recorded + kept + model_free_entries(), errors, stopped


def mismatches(conversations: list[dict], personas: list[dict]) -> list[dict]:
    """The conversations whose tier is not the persona's expected tier. They are kept."""
    expected = {p["id"]: p.get("expected_tier") for p in personas}
    return [c for c in conversations if c.get("persona_id") in expected and c.get("tier") != expected[c["persona_id"]]]


def summary(conversations: list[dict], personas: list[dict]) -> str:
    expected = {p["id"]: p.get("expected_tier") for p in personas}
    rows = [("conversation", "status", "respondent", "tier", "expected", "lines")]
    for c in conversations:
        if c.get("persona_id") is None:
            continue
        rows.append((c["id"], str(c["status"]), str(c["respondent"]), str(c["tier"]),
                     str(expected.get(c["persona_id"])), str(len(c["transcript"]))))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(w) for cell, w in zip(row, widths)) for row in rows)


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=BACKEND, capture_output=True, text=True, timeout=10)
        return (out.stdout.strip() if out.returncode == 0 else "") or "unknown"
    except Exception:
        return "unknown"


def write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=1, default=str)
            file.write("\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def recording_time(tz: ZoneInfo) -> datetime:
    """10:00 today in the district, within calling hours, after the 07:00 alert."""
    today = datetime.now(tz).date()
    return datetime.combine(today, time(10, 0), tz).astimezone(timezone.utc)


def main(arguments: list[str] | None = None, agent=None, resident=None, settings=None, log=print) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="record these personas only: an id, or several separated by commas")
    parser.add_argument("--limit", type=int, help="record at most this many conversations")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the library file to write")
    parser.add_argument("--merge", action="store_true", help="keep the file's conversations not recorded again")
    parser.add_argument("--batch-size", type=int, default=0, help="conversations per batch; 0 is one batch")
    parser.add_argument("--pause-seconds", type=float, default=180, help="the wait between batches")
    parser.add_argument("--workers", type=int, default=1, help="conversations run at once within a batch")
    parser.add_argument("--claude", action="store_true", help="Claude stands in for the text model on both sides")
    parser.add_argument("--claude-agent-model", default="claude-sonnet-5-5", help="Chhaya's agent, with --claude")
    parser.add_argument("--claude-resident-model", default="claude-haiku-5-5", help="the resident, with --claude")
    options = parser.parse_args(arguments)

    settings = settings or get_settings()
    personas = load_personas()
    chosen = plan(personas, options.only, options.limit)
    if not chosen:
        log(f"No persona matches {options.only!r}.")
        return 2
    model = settings.text_model
    labels: dict = {}
    if options.claude:
        from evals.voice import claude_models  # only in backend/.venv-claude; never in the service

        model = options.claude_agent_model
        labels = {"model": model, "resident_model": options.claude_resident_model}
        agent = agent or claude_models.make_claude_agent_model(options.claude_agent_model)
        resident = resident or claude_models.make_claude_resident_model(options.claude_resident_model)
    if agent is None or resident is None:
        if not settings.text_model.strip() or not settings.gcp_project.strip():
            log("Set GCP_PROJECT and TEXT_MODEL (and TEXT_LOCATION) to record with the text model.")
            return 2
        agent = agent or make_agent_model(settings)
        resident = resident or make_resident_model(settings)
    existing: list[dict] = []
    if options.merge and options.out.is_file():
        existing = json.loads(options.out.read_text(encoding="utf-8")).get("conversations", [])

    tz = ZoneInfo(settings.local_tz)
    size = options.batch_size if options.batch_size > 0 else len(chosen)
    batches = [chosen[i:i + size] for i in range(0, len(chosen), size)]
    conversations: list[dict] = existing
    errors: list[tuple[str, str]] = []
    stopped = ""
    limited = [0]
    started = datetime.now(timezone.utc).isoformat()
    for number, batch in enumerate(batches, 1):
        if number > 1 and options.pause_seconds > 0:
            log(f"Waiting {options.pause_seconds:g} s before batch {number} of {len(batches)}.")
            clock.sleep(options.pause_seconds)
        # What is already recorded stays, so the file after each batch holds everything so far.
        conversations, failed, stopped = build_library(
            batch, agent, resident, settings, recording_time(tz), tz, conversations, log, limited,
            options.workers, labels)
        errors += failed
        stand_in = {"stand_in": True, "resident_model": options.claude_resident_model} if options.claude else {}
        write_atomic(options.out, {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "commit": git_commit(),
            "model": model,
            **stand_in,
            "conversations": conversations,
        })
        if stopped:
            break
    if stopped:
        count = sum(1 for c in conversations if c.get("persona_id") and c.get("recorded_at", "") >= started)
        service = "Claude" if options.claude else "Vertex"
        if stopped == "rate limit":
            log(f"STOPPED: {service} rate limit after {count} conversations")
        else:
            log(f"STOPPED: {service} refused a request on policy grounds after {count} conversations")

    log("")
    log(summary(conversations, personas))
    wrong = mismatches(conversations, personas)
    expected = {p["id"]: p.get("expected_tier") for p in personas}
    log("")
    log(f"{len(wrong)} conversation(s) with a tier other than expected:")
    for c in wrong:
        log(f"  {c['id']}: {c['tier']} (expected {expected[c['persona_id']]}), status {c['status']}")
    if errors:
        log(f"{len(errors)} conversation(s) failed and were left out:")
        for cid, message in errors:
            log(f"  {cid}: {message}")
    log(f"Wrote {len(conversations)} entries to {options.out}")
    return {"policy": 3, "rate limit": 5}.get(stopped, 1 if errors else 0)


if __name__ == "__main__":
    sys.exit(main())
