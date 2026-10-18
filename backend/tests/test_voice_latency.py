"""The script that summarises voice reply and pickup times from the service's logs, run with fakes."""
import json

import pytest

from scripts import voice_latency
from scripts.voice_latency import main, summarise_lines

# Six replies, three of them with a tool, and two pickups: the numbers the tests check.
REPLY_LINES = [
    "voice reply 1.10 s after the person stopped (tool: True)",
    "voice reply 2.20 s after the person stopped (tool: True)",
    "voice reply 3.30 s after the person stopped (tool: True)",
    "voice reply 0.80 s after the person stopped (tool: False)",
    "voice reply 1.20 s after the person stopped (tool: False)",
    "voice reply 5.00 s after the person stopped (tool: False)",
]
PICKUP_LINES = ["voice pickup 0.84 s", "voice pickup 1.36 s"]


def test_the_summary_gives_nearest_rank_percentiles_for_all_replies_and_each_kind():
    summary = summarise_lines(REPLY_LINES + PICKUP_LINES)

    assert summary["turns"] == 6
    assert summary["reply_s"] == {"p50": 1.20, "p90": 5.00, "max": 5.00}
    assert summary["reply_with_tool_s"] == {"p50": 2.20, "p90": 3.30, "max": 3.30}
    assert summary["reply_without_tool_s"] == {"p50": 1.20, "p90": 5.00, "max": 5.00}
    assert summary["calls"] == 2
    assert summary["pickup_s"] == {"p50": 0.84, "max": 1.36}


def test_lines_that_do_not_match_are_ignored_and_a_prefix_is_allowed():
    lines = [
        "INFO app.call.live: voice reply 1.20 s after the person stopped (tool: False)",
        "voice reply banana s after the person stopped (tool: False)",
        "voice pickup s",
        "voice call started",
        "reply 9.99 s after the person stopped (tool: True)",
        "",
    ]

    summary = summarise_lines(lines)

    assert summary["turns"] == 1
    assert summary["reply_s"] == {"p50": 1.20, "p90": 1.20, "max": 1.20}
    assert summary["reply_with_tool_s"] is None
    assert summary["calls"] == 0
    assert summary["pickup_s"] is None


def test_no_lines_gives_no_turns_and_no_stats():
    assert summarise_lines([]) == {
        "turns": 0, "reply_s": None, "reply_with_tool_s": None, "reply_without_tool_s": None,
        "calls": 0, "pickup_s": None,
        "calls_opened_now": 0, "pickup_opened_now_s": None, "calls_pre_opened": 0, "pickup_pre_opened_s": None,
    }


def test_pickups_are_told_apart_by_how_the_voice_session_opened():
    lines = ["voice pickup 2.90 s (opened now)", "voice pickup 3.10 s (opened now)",
             "voice pickup 0.80 s (pre-opened)", "voice pickup 1.00 s"]

    summary = summarise_lines(lines)

    assert summary["calls"] == 4  # an older line, with no opening named, counts in the total only
    assert summary["calls_opened_now"] == 2
    assert summary["pickup_opened_now_s"] == {"p50": 2.90, "max": 3.10}
    assert summary["calls_pre_opened"] == 1
    assert summary["pickup_pre_opened_s"] == {"p50": 0.80, "max": 0.80}


def test_the_cli_writes_the_summary_with_its_date_and_source(tmp_path, monkeypatch, capsys):
    seen = {}

    def fake_read(since, now):
        seen["since"] = since
        return REPLY_LINES + PICKUP_LINES

    monkeypatch.setattr(voice_latency, "read_log_lines", fake_read)
    out = tmp_path / "latency.json"

    code = main(["--since", "2026-10-17T00:00:00Z", "--out", str(out)])

    assert code == 0
    assert seen["since"].isoformat() == "2026-10-17T00:00:00+00:00"
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["source"] == "Cloud Logging, chhaya"
    assert written["since"] == "2026-10-17T00:00:00Z"
    assert written["until"].endswith("+00:00")
    assert written["measured_at"].endswith("+00:00")
    assert written["turns"] == 6
    assert written["reply_s"] == {"p50": 1.20, "p90": 5.00, "max": 5.00}
    assert written["pickup_s"] == {"p50": 0.84, "max": 1.36}
    assert "turns" in capsys.readouterr().out


def test_the_cli_rejects_a_since_that_is_not_a_utc_time(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(voice_latency, "read_log_lines", lambda since, now: pytest.fail("gcloud must not run"))
    out = tmp_path / "latency.json"

    assert main(["--since", "yesterday", "--out", str(out)]) == 2
    assert main(["--since", "2026-10-17T00:00:00", "--out", str(out)]) == 2
    assert not out.exists()
    assert "--since must be" in capsys.readouterr().out


def test_the_gcloud_query_has_no_time_comparison_and_the_timestamps_are_filtered(monkeypatch):
    captured = {}
    stdout = (
        "2026-10-16T23:59:59.999999999Z\tvoice pickup 9.00 s\n"  # before the window
        "2026-10-17T00:00:00.123456789Z\tvoice pickup 0.84 s\n"  # at the window's start: kept
        "2026-10-17T03:00:00Z\tINFO app.call.live: voice reply 1.20 s after the person stopped (tool: False)\n"
        "not a timestamp\tvoice pickup 7.00 s\n"
    )

    class Done:
        returncode = 0
        stderr = ""

    Done.stdout = stdout

    def fake_run(args, **kwargs):
        captured["args"] = args
        return Done()

    monkeypatch.setattr(voice_latency.shutil, "which", lambda name: r"C:\Program Files\gcloud.cmd")
    monkeypatch.setattr(voice_latency.subprocess, "run", fake_run)
    since = voice_latency.parse_since("2026-10-17T00:00:00Z")
    now = voice_latency.parse_since("2026-10-17T04:30:00Z")

    lines = voice_latency.read_log_lines(since, now)

    assert lines == ["voice pickup 0.84 s", "INFO app.call.live: voice reply 1.20 s after the person stopped (tool: False)"]
    args = captured["args"]
    assert args[0] == r"C:\Program Files\gcloud.cmd"
    assert args[1:4] == ["logging", "read", voice_latency.QUERY]
    assert voice_latency.QUERY == ('resource.type="cloud_run_revision" AND resource.labels.service_name="chhaya" '
                                   'AND textPayload:"voice "')
    assert "timestamp" not in args[3]
    assert args[args.index("--configuration") + 1] == "chhaya"
    assert "--format=value(timestamp,textPayload)" in args
    assert "--freshness=6h" in args  # 4.5 hours, rounded up to 5, plus one


def test_a_missing_gcloud_is_a_clear_error_and_nothing_runs(monkeypatch):
    monkeypatch.setattr(voice_latency.shutil, "which", lambda name: None)
    monkeypatch.setattr(voice_latency.subprocess, "run", lambda *a, **k: pytest.fail("gcloud must not run"))
    since = voice_latency.parse_since("2026-10-17T00:00:00Z")

    with pytest.raises(RuntimeError, match="gcloud not found on PATH"):
        voice_latency.read_log_lines(since, since)


def test_the_freshness_covers_the_window_in_whole_hours_plus_one():
    start = voice_latency.parse_since("2026-10-17T00:00:00Z")

    assert voice_latency.freshness_for(start, voice_latency.parse_since("2026-10-17T01:30:00Z")) == "3h"
    assert voice_latency.freshness_for(start, voice_latency.parse_since("2026-10-17T01:00:00Z")) == "2h"
    assert voice_latency.freshness_for(start, start) == "1h"


def test_a_window_older_than_two_days_is_not_cut_short():
    start = voice_latency.parse_since("2026-10-12T00:00:00Z")
    now = voice_latency.parse_since("2026-10-17T00:00:00Z")

    assert voice_latency.freshness_for(start, now) == "121h"  # five days, plus one hour
    assert voice_latency.freshness_for(start, now) != "2d"
