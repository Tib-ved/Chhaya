"""Summarise how fast the voice agent replies and picks up, from the chhaya service's Cloud Logging.

The voice call loop logs two lines at INFO: "voice reply X s after the person stopped (tool: True|False)"
and "voice pickup X s (opened now|pre-opened)", timed from the answer to the first words with the voice
session's opening included (a browser call opens it when answered, a phone call while it rings). This reads those lines for the service since a time, prints the summary and
writes it to a file. Run from backend/, with gcloud signed in to the chhaya configuration:
    python -m scripts.voice_latency --since 2026-10-17T00:00:00Z [--out PATH]
The output file defaults to evals/voice/latency.json. Percentiles are nearest rank, in seconds to
0.01. Nothing is called on the model or on a resident: this only reads the logs."""
import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BACKEND = Path(__file__).resolve().parent.parent
DEFAULT_OUT = BACKEND / "evals" / "voice" / "latency.json"
SOURCE = "Cloud Logging, chhaya"
REPLY = re.compile(r"voice reply (\d+(?:\.\d+)?) s after the person stopped \(tool: (True|False)\)")
PICKUP = re.compile(r"voice pickup (\d+(?:\.\d+)?) s(?: \((opened now|pre-opened)\))?")
QUERY = ('resource.type="cloud_run_revision" AND resource.labels.service_name="chhaya" '
         'AND textPayload:"voice "')


def _nearest_rank(values: list[float], percent: int) -> float:
    """The value at the given percentile by nearest rank, from values already sorted."""
    return values[math.ceil(percent / 100 * len(values)) - 1]


def _stats(values: list[float], with_p90: bool = True) -> dict | None:
    """p50, p90 (if asked) and max of the values, or None when there are none."""
    if not values:
        return None
    ordered = sorted(values)
    stats = {"p50": round(_nearest_rank(ordered, 50), 2)}
    if with_p90:
        stats["p90"] = round(_nearest_rank(ordered, 90), 2)
    stats["max"] = round(ordered[-1], 2)
    return stats


def summarise_lines(lines: list[str]) -> dict:
    """The reply and pickup times in the log lines. A line that does not match is ignored."""
    replies: list[float] = []
    with_tool: list[float] = []
    without_tool: list[float] = []
    pickups: list[float] = []
    by_opening: dict[str, list[float]] = {"opened now": [], "pre-opened": []}
    for line in lines:
        reply = REPLY.search(line)
        if reply:
            seconds = float(reply.group(1))
            replies.append(seconds)
            (with_tool if reply.group(2) == "True" else without_tool).append(seconds)
            continue
        pickup = PICKUP.search(line)
        if pickup:
            pickups.append(float(pickup.group(1)))
            if pickup.group(2):
                by_opening[pickup.group(2)].append(float(pickup.group(1)))
    return {
        "turns": len(replies),
        "reply_s": _stats(replies),
        "reply_with_tool_s": _stats(with_tool),
        "reply_without_tool_s": _stats(without_tool),
        "calls": len(pickups),
        "pickup_s": _stats(pickups, with_p90=False),
        # Told apart, as a session opened when answered and one pre-opened do not compare.
        "calls_opened_now": len(by_opening["opened now"]),
        "pickup_opened_now_s": _stats(by_opening["opened now"], with_p90=False),
        "calls_pre_opened": len(by_opening["pre-opened"]),
        "pickup_pre_opened_s": _stats(by_opening["pre-opened"], with_p90=False),
    }


def parse_since(text: str) -> datetime:
    """The --since value as a UTC time. It must be ISO 8601 with an offset, such as 2026-10-17T00:00:00Z."""
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("it has no UTC offset")
    return value.astimezone(timezone.utc)


def freshness_for(since: datetime, now: datetime) -> str:
    """The gcloud --freshness that covers the window from `since` to `now`: its whole hours, rounded up, plus one."""
    hours = math.ceil((now - since).total_seconds() / 3600) + 1
    return f"{max(hours, 1)}h"


def _parse_timestamp(text: str) -> datetime:
    """A Cloud Logging timestamp such as 2026-10-17T10:00:00.123456789Z, as a UTC time."""
    # Python reads at most microseconds, so longer fractions are cut to six digits.
    fraction = re.sub(r"\.(\d+)", lambda match: "." + match.group(1)[:6].ljust(6, "0"), text, count=1)
    return datetime.fromisoformat(fraction.replace("Z", "+00:00")).astimezone(timezone.utc)


def read_log_lines(since: datetime, now: datetime) -> list[str]:
    """The text payload of each voice log line of the chhaya service at or after `since`. The query has
    no time comparison: gcloud is asked for every line in the freshness window, and the timestamps are
    checked here. gcloud is found with shutil.which, so the Windows gcloud.cmd is used."""
    executable = shutil.which("gcloud")
    if executable is None:
        raise RuntimeError("gcloud not found on PATH")
    args = [executable, "logging", "read", QUERY, "--configuration", "chhaya",
            "--format=value(timestamp,textPayload)", f"--freshness={freshness_for(since, now)}"]
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode != 0:
        raise RuntimeError(f"gcloud logging read failed: {result.stderr.strip()}")
    payloads = []
    for line in result.stdout.splitlines():
        stamp, _, payload = line.partition("\t")
        try:
            when = _parse_timestamp(stamp)
        except ValueError:
            continue  # a line without a readable timestamp cannot be placed in the window
        if when >= since:
            payloads.append(payload)
    return payloads


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", required=True, help="the start of the window, as a UTC time")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the file to write")
    options = parser.parse_args(arguments)

    try:
        since = parse_since(options.since)
    except ValueError as error:
        print(f"--since must be an ISO 8601 UTC time, such as 2026-10-17T00:00:00Z: {error}")
        return 2
    now = datetime.now(timezone.utc)
    try:
        lines = read_log_lines(since, now)
    except (RuntimeError, OSError) as error:
        print(f"Could not read the logs: {error}")
        return 1
    summary = {
        "measured_at": now.isoformat(),
        "source": SOURCE,
        "since": options.since,
        "until": now.isoformat(),
        **summarise_lines(lines),
    }
    options.out.parent.mkdir(parents=True, exist_ok=True)
    options.out.write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"Wrote {options.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
