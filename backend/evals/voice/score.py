"""Scores the recorded AI-resident library offline, with no model calls: tiers, missed Reds, questions, the 108 line."""
import json
import logging
import re
from collections import Counter
from pathlib import Path

from app.triage import REQUIRED_QUESTIONS

FIXTURES = Path(__file__).resolve().parents[2] / "seed" / "fixtures"
log = logging.getLogger(__name__)
TIERS = ("red", "amber", "green")
DIGITS = "0-9०-९"
EMERGENCY_TOOL = "flag_emergency"
# After आठ, a letter or matra makes another word (आठवां); the danda and double danda (U+0964-5) end a sentence.
EMERGENCY_NUMBER = re.compile(
    rf"(?<![{DIGITS}])(?:108|१०८)(?![{DIGITS}])|एक सौ आठ(?![ऀ-ॣ०-ॿ])|\bone zero eight\b|\bone hundred and eight\b", re.I)


def mentions_108(text: str) -> bool:
    return bool(EMERGENCY_NUMBER.search(text or ""))


def _agent_spoke_108(transcript: list[dict], before: int) -> bool:
    """Whether an agent line from the turn that begins after the last resident line before `before` says 108."""
    start = 0
    for i, entry in enumerate(transcript[:before]):
        if entry.get("role") == "resident":
            start = i + 1
    return any(e.get("role") == "agent" and mentions_108(e.get("text", "")) for e in transcript[start:])


def _line_spoken(conv: dict) -> tuple[bool, bool]:
    """(the 108 line was spoken, the flag's position was unknown)."""
    transcript = conv.get("transcript") or []
    flag = next((t for t in conv.get("tools") or [] if t.get("name") == EMERGENCY_TOOL and "line" in t), None)
    if flag is None:
        return _agent_spoke_108(transcript, len(transcript)), True
    return _agent_spoke_108(transcript, flag["line"]), False


def score_library(library: dict, personas: list[dict]) -> dict:
    expected = {p["id"]: p["expected_tier"] for p in personas}
    rows, recorded = [], Counter()
    confusion = {t: {u: 0 for u in TIERS + ("none",)} for t in TIERS}
    by_language: dict[str, dict] = {}
    asked_all = full_calls = red_expected = under = matches = 0
    emergencies = {"total": 0, "flagged": 0, "line_spoken": 0, "unknown": 0}
    for conv in library.get("conversations", []):
        if not conv.get("persona_id"):
            continue
        want, got = expected.get(conv["persona_id"]), conv.get("tier")
        if want is None:
            log.warning("skipping %s: persona %s is not in the personas list", conv.get("id"), conv["persona_id"])
            continue
        passed = want == got
        matches += passed
        if want in confusion:
            confusion[want][got if got in TIERS else "none"] += 1
        lang = by_language.setdefault(conv.get("language", ""), {"matched": 0, "total": 0})
        lang["total"] += 1
        lang["matched"] += passed
        if want == "red":
            red_expected += 1
            under += got != "red"
        emergency = bool(conv.get("emergency"))
        if conv.get("status") == "completed" and not emergency:
            full_calls += 1
            asked_all += all(q in (conv.get("answers") or {}) for q in REQUIRED_QUESTIONS)
        spoken = False
        emergencies["total"] += emergency or conv["persona_id"].startswith("emergency_")
        if emergency:
            spoken, unknown = _line_spoken(conv)
            emergencies["flagged"] += 1
            emergencies["line_spoken"] += spoken
            emergencies["unknown"] += unknown
        recorded[((conv.get("recorded_at") or library.get("recorded_at") or "")[:10],
                  conv.get("commit") or library.get("commit"), conv.get("model") or library.get("model"))] += 1
        rows.append({"id": conv["id"], "persona_id": conv["persona_id"], "language": conv.get("language"),
                     "expected": want, "got": got, "passed": passed, "lines": len(conv.get("transcript") or []),
                     "emergency": emergency, "line_spoken": spoken})
    return {
        "recorded": [{"date": d, "commit": c, "model": m, "conversations": n}
                     for (d, c, m), n in sorted(recorded.items(), key=lambda kv: kv[0][0])],
        # A library recorded with Claude standing in for the text model says so (record_ai_calls --claude).
        "stand_in": bool(library.get("stand_in")),
        "conversations": len(rows), "tier_matches": matches, "red_expected": red_expected,
        "red_under_triaged": under, "confusion": confusion, "by_language": by_language,
        "required_questions": {"asked_all": asked_all, "full_calls": full_calls},
        "emergencies": emergencies, "rows": rows,
    }


def main() -> None:
    library = json.loads((FIXTURES / "ai_calls.json").read_text(encoding="utf-8"))
    personas = json.loads((FIXTURES / "ai_personas.json").read_text(encoding="utf-8"))["personas"]
    print(json.dumps(score_library(library, personas), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
