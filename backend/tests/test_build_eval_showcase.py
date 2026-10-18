import json
import re

from scripts.build_eval_showcase import build

PERSONAS = [{"id": "p1", "expected_tier": "green"}, {"id": "emergency_x", "expected_tier": "red"}]


def _conv(i, persona, tier, recorded_at, **extra):
    return {"id": i, "persona_id": persona, "language": "en", "status": "completed", "tier": tier,
            "recorded_at": recorded_at, "commit": "abc", "model": "m1", "answers": {}, "transcript": [], **extra}


def _library(dates=("2026-10-17T10:00:00+00:00", "2026-10-17T11:00:00+00:00")):
    return {"conversations": [_conv("a", "p1", "green", dates[0]), _conv("b", "emergency_x", "red", dates[1])]}


HISTORY = [{"date": "2026-10-14", "commit": "x", "what": "one", "before": "1 s", "after": "2 s"},
           {"fill": "emergency_line", "date": "2026-10-17", "commit": None, "what": "Emergency line (call 108) spoken before the call ends",
            "before": "5 of 9", "after": None}]


def _build(**kw):
    args = dict(library=_library(), personas=PERSONAS, ask_report=None, adversarial_report=None, latency=None,
                history=HISTORY, limitations=["a limit"], commit="c0ffee", now="2026-10-17T12:00:00+05:30",
                live_model="live-m")
    args.update(kw)
    return build(**args)


def test_nothing_optional_gives_empty_parts():
    out = _build()
    assert out["version"] == 2 and out["commit"] == "c0ffee" and out["generated_at"] == "2026-10-17T12:00:00+05:30"
    assert out["ask"] is None and out["refusals"] is None and out["latency"]["voice"] is None
    assert out["voice"]["conversations"] == 2 and out["voice"]["model"] == "m1"
    assert out["limitations"] == ["a limit"]
    assert out["latency"]["voice_pickup_earlier"]["after_s"] == "0.7–0.9"
    assert out["latency"]["voice_pickup_earlier"]["model"] == "gemini-3.8-live"


def test_ask_report_rows_are_copied():
    report = {"commit": "k", "model": "gm", "run_at": "2026-10-17T09:00:00+05:30", "cases": 15, "passed": 13,
              "expected": 15, "complete": False, "rows": [{"case": "q1", "passed": True}],
              "latency_s": {"p50": 1, "p95": 2, "max": 3}, "steps_s": {}}
    ask = _build(ask_report=report)["ask"]
    assert ask["rows"] == report["rows"] and ask["passed"] == 13 and ask["cases"] == 15
    assert ask["expected"] == 15 and ask["complete"] is False and ask["steps_s"] is None
    assert ask["latency_s"]["max"] == 3 and ask["model"] == "gm"


def test_refusals_come_from_the_adversarial_summary():
    old = {"commit": "z", "summaries": [{"set": "golden", "cases": 15, "passed": 13},
                                        {"set": "adversarial", "cases": 22, "passed": 22}]}
    assert _build(adversarial_report=old)["refusals"] == {
        "run_at": "2026-10-14T16:25:00+05:30", "cases": 22, "passed": 22, "rerun": False, "model": "gemini-3.8-flash"}


def test_two_recording_dates_are_both_listed():
    lib = _library(("2026-10-14T10:00:00+00:00", "2026-10-17T11:00:00+00:00"))
    assert len(_build(library=lib)["voice"]["recorded"]) == 2


def test_latency_is_passed_through_with_the_live_model():
    lat = {"turns": 3, "reply_s": None, "pickup_s": None}
    assert _build(latency=lat)["latency"]["voice"] == {**lat, "model": "live-m"}
    assert "model" not in lat  # the input is not changed


def test_emergency_history_row_is_filled_from_the_score():
    lib = _library()
    lib["conversations"][1].update(emergency={"reason": "x"}, tools=[{"name": "flag_emergency", "line": 0}],
                                   transcript=[{"role": "agent", "text": "call 108"}])
    row = _build(library=lib)["history"][-1]
    assert row["after"] == "1 of 1" and row["commit"] == "abc" and row["date"] == "2026-10-17" and row["model"] == "m1"
    assert HISTORY[-1]["after"] is None  # the input is not changed


def test_unknown_flag_position_is_noted():
    lib = _library()
    lib["conversations"][1]["emergency"] = {"reason": "x"}
    assert _build(library=lib)["history"][-1]["after"] == "0 of 1 (flag moment not recorded for 1)"


def test_no_phone_numbers_in_output():
    text = json.dumps(_build(), ensure_ascii=False)
    assert not re.search(r"\+?\d{10,}", text)


def test_emergency_row_takes_the_recordings_date_not_the_builds():
    row = _build(library=_library(("2026-10-14T10:00:00+00:00",) * 2))["history"][-1]
    assert row["date"] == "2026-10-14" and row["commit"] == "abc" and row["model"] == "m1"


def test_emergency_row_has_no_commit_when_recording_spans_runs():
    lib = _library(("2026-10-14T10:00:00+00:00", "2026-10-17T11:00:00+00:00"))
    row = _build(library=lib)["history"][-1]
    assert row["commit"] is None and row["date"] == "2026-10-17"


def test_real_history_has_exactly_one_fill_row():
    from scripts.build_eval_showcase import SHOWCASE
    rows = json.loads((SHOWCASE / "history.json").read_text(encoding="utf-8"))
    assert [r.get("fill") for r in rows].count("emergency_line") == 1


def test_a_stand_in_run_is_marked_in_the_ask_block():
    report = {"model": "claude-opus-5-5", "stand_in": True, "small_model": "claude-haiku-5-5", "judge_model": "gemini-3.8-flash",
              "rows": []}
    ask = _build(ask_report=report)["ask"]
    assert ask["stand_in"] is True and ask["small_model"] == "claude-haiku-5-5" and ask["judge_model"] == "gemini-3.8-flash"


def test_an_old_ask_report_is_not_a_stand_in():
    ask = _build(ask_report={"model": "gm", "rows": []})["ask"]
    assert ask["stand_in"] is False and ask["small_model"] is None and ask["judge_model"] is None


def test_a_stand_in_recording_is_marked_with_the_residents_model():
    voice = _build(library={**_library(), "stand_in": True, "resident_model": "claude-haiku-5-5"})["voice"]
    assert voice["stand_in"] is True and voice["resident_model"] == "claude-haiku-5-5"
    plain = _build()["voice"]
    assert plain["stand_in"] is False and plain["resident_model"] is None
