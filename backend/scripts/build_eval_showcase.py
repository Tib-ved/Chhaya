"""Assembles the one results file behind the "How we test Chhaya" page, offline and with no model calls.

It scores the recorded AI-resident library, adds the newest Ask Chhaya report that has per-question rows,
the 14 October refusals result, the voice latency summary (if there is one) and the improvement list,
and writes app/static/eval_latest.json. Run from backend/:
    python -m scripts.build_eval_showcase [--out PATH]"""
import argparse
import copy
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from evals.voice.score import FIXTURES, score_library  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
REPORTS = BACKEND / "evals" / "ask_chhaya" / "reports"
ADVERSARIAL_REPORT = REPORTS / "2026-10-14_1625.json"
ADVERSARIAL_RUN_AT = "2026-10-14T16:25:00+05:30"
ADVERSARIAL_MODEL = "gemini-3.8-flash"  # run.py's default on 14 October
LATENCY = BACKEND / "evals" / "voice" / "latency.json"
SHOWCASE = BACKEND / "evals" / "showcase"
DEFAULT_OUT = BACKEND / "app" / "static" / "eval_latest.json"
PICKUP_EARLIER = {"date": "2026-10-15", "before_s": "4.3–4.6", "after_s": "0.7–0.9", "commit": "01c93f3",
                  "model": "gemini-3.8-live"}


def _ask(report: dict | None) -> dict | None:
    if not report:
        return None
    return {"run_at": report.get("run_at"), "commit": report.get("commit"), "model": report.get("model"),
            "stand_in": bool(report.get("stand_in")), "small_model": report.get("small_model"),
            "judge_model": report.get("judge_model"), "cases": report.get("cases"), "passed": report.get("passed"), "expected": report.get("expected"),
            "complete": report.get("complete"), "latency_s": report.get("latency_s"),
            "steps_s": report.get("steps_s") or None, "rows": report.get("rows", [])}


def _refusals(report: dict | None) -> dict | None:
    summary = next((s for s in (report or {}).get("summaries", []) if s.get("set") == "adversarial"), None)
    if not summary:
        return None
    return {"run_at": ADVERSARIAL_RUN_AT, "cases": summary["cases"], "passed": summary["passed"], "rerun": False,
            "model": ADVERSARIAL_MODEL}


def _emergency_after(emergencies: dict) -> str:
    text = f"{emergencies['line_spoken']} of {emergencies['flagged']}"
    # "unknown": the moment of the flag was not recorded, so a 108 line anywhere after the resident's last words counted.
    return text + (f" (flag moment not recorded for {emergencies['unknown']})" if emergencies["unknown"] else "")


def build(library: dict, personas: list[dict], ask_report: dict | None, adversarial_report: dict | None,
          latency: dict | None, history: list[dict], limitations: list[str], commit: str, now: str,
          live_model: str) -> dict:
    voice = score_library(library, personas)
    # The model is the recording's own; when the recording spans several, the newest date's is shown.
    voice = {"model": (voice["recorded"][-1]["model"] if voice["recorded"] else None), **voice}
    # A recording with Claude standing in for the text model is labelled as such, with the resident's model.
    voice["stand_in"] = bool(voice.get("stand_in"))
    voice["resident_model"] = library.get("resident_model")
    rows = copy.deepcopy(history)
    newest = voice["recorded"][-1] if voice["recorded"] else {}
    for row in rows:
        if row.get("fill") == "emergency_line":
            # The result belongs to the recording, so it carries the recording's date, commit and model.
            row["after"] = _emergency_after(voice["emergencies"])
            row["date"] = newest.get("date") or now[:10]
            row["commit"] = newest.get("commit") if len(voice["recorded"]) == 1 else None
            row["model"] = None if voice.get("stand_in") else newest.get("model")  # a stand-in is named in EVALUATION.md, not on the page
    return {"version": 2, "generated_at": now, "commit": commit, "voice": voice, "ask": _ask(ask_report),
            "refusals": _refusals(adversarial_report),
            # The latency was measured on live calls, so it carries the live model the service runs.
            "latency": {"voice": {**latency, "model": live_model} if latency else None,
                        "voice_pickup_earlier": dict(PICKUP_EARLIER)},
            "history": rows, "limitations": list(limitations)}


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _newest_ask_report() -> dict | None:
    for path in sorted(REPORTS.glob("*.json"), reverse=True):
        report = _load(path)
        if report.get("rows"):
            return report
    return None


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=BACKEND, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    out = parser.parse_args().out
    result = build(
        library=_load(FIXTURES / "ai_calls.json"), personas=_load(FIXTURES / "ai_personas.json")["personas"],
        ask_report=_newest_ask_report(),
        adversarial_report=_load(ADVERSARIAL_REPORT) if ADVERSARIAL_REPORT.exists() else None,
        latency=_load(LATENCY) if LATENCY.exists() else None,
        history=_load(SHOWCASE / "history.json"), limitations=_load(SHOWCASE / "limitations.json"),
        commit=_commit(), now=datetime.now().astimezone().isoformat(timespec="seconds"),
        live_model=get_settings().live_model)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
