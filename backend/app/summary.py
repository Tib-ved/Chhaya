"""The one paragraph on the report that a model writes.

It may only use the figures already on the page. Every number in it is checked against those
figures, and it is stored with a fingerprint of the round so that it is never shown beside
figures it was not written from."""
import logging
import re
from datetime import datetime

from app.case_note import forbidden
from app.db import Store
from app.report import RoundCache, build_report, load_round, shared_key
from app.textgen import TextModel

logger = logging.getLogger(__name__)
MAX_SUMMARY_CHARS = 700
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
TOP_PROBLEMS = 3  # the paragraph names the commonest problems, not all of them
INSTRUCTION = (
    "You write one short paragraph for a district official, summarising a round of automated "
    "wellness calls made during a weather warning: at most three sentences and 70 words. Say how "
    "many residents were reached and not reached, how many cases are red and amber, the commonest "
    "problems, and how many cases have been visited. Do not recite every figure. Use only the "
    "figures given to you, exactly as given. Do not compute percentages, totals, differences or "
    "any number that is not listed. Do not say or imply that anyone is fine or safe, and give no "
    "medical advice. Plain English, no headings, no lists. Output the paragraph only."
)


def _plain(number: str) -> str:
    """'126.0' and '126' are the same figure."""
    return number[:-2] if number.endswith(".0") else number


def report_figures(report: dict) -> list[tuple[str, object]]:
    """The headline figures of the report page, named: what one paragraph can carry. The
    ward-by-ward table and the rarer problems stay on the page; given them, the model recites
    them all and the paragraph runs to three times its length."""
    figures: list[tuple[str, object]] = [(name, value) for name, value in report["coverage"].items()]
    for tier in ("red", "amber", "green"):
        figures.append((tier, sum(row[tier] for row in report["tiers_by_ward"])))
    figures += [(f"problem: {p['label']}", p["count"]) for p in report["problems"][:TOP_PROBLEMS]]
    response = report["response"]
    figures += [("visited", response["visited"]), ("still open", response["open"])]
    if response["median_minutes_to_visit"] is not None:
        figures.append(("median minutes from case opened to visit", response["median_minutes_to_visit"]))
    figures.append(("open red cases", len(report["open_red_cases"])))
    current = report["round"]
    if current.get("peak_temp_c") is not None:
        figures.append(("forecast temperature in Celsius", current["peak_temp_c"]))
    return figures


def clean_summary(text: object, allowed: set[str]) -> str | None:
    """The paragraph as it may be stored, or None if it fails a check."""
    if not isinstance(text, str):
        return None
    paragraph = re.sub(r"\s+", " ", text).strip()
    if not paragraph or len(paragraph) > MAX_SUMMARY_CHARS:
        return None
    if forbidden(paragraph):
        return None
    if any(_plain(number.replace(",", "")) not in allowed for number in NUMBER.findall(paragraph)):
        return None
    return paragraph


def write_summary(
    store: Store, text_model: TextModel, round_id: str, now: datetime, registers: RoundCache | None = None
) -> str | None:
    """Write and store the summary of one round. Returns the text stored, or None.
    Raises KeyError for an unknown round; never raises for anything the model does."""
    data = load_round(store, round_id, registers=registers)
    report = build_report(store, round_id, registers=registers)
    figures = report_figures(report)
    alert = report.get("alert") or {}
    district = (report.get("district") or {}).get("name", "the district")
    prompt = "\n".join(
        [f"District: {district}. Warning: {alert.get('severity', '')} {alert.get('hazard', '')}.", "Figures:"]
        + [f"- {name}: {_plain(str(value))}" for name, value in figures]
    )
    # Every number the model was shown: the figures, and any number inside a label it may
    # repeat (a problem called "Alone and aged 70 or over").
    allowed = {_plain(number.replace(",", "")) for number in NUMBER.findall(prompt)}
    try:
        written = text_model(INSTRUCTION, prompt)
    except Exception as error:
        logger.error("no summary for round %s: the text model failed (%s)", round_id, type(error).__name__)
        return None
    paragraph = clean_summary(written, allowed)
    if paragraph is None:
        logger.warning("no summary for round %s: what was written did not pass the checks", round_id)
        return None
    key = shared_key(data, report.get("alert"))
    store.update("rounds", round_id, {"summary": {"text": paragraph, "key": key, "at": now}})
    return paragraph


def refresh_summaries(
    store: Store, text_model: TextModel, round_ids: list[str], now: datetime, registers: RoundCache | None = None
) -> int:
    """Write a summary for each of these rounds that has none, or whose figures have changed."""
    written = 0
    for round_id in round_ids:
        try:
            data = load_round(store, round_id, registers=registers)
            alert = store.get("alerts", data.round["alert_id"])
            stored = data.round.get("summary") or {}
            if stored.get("text") and stored.get("key") == shared_key(data, alert):
                continue
            if write_summary(store, text_model, round_id, now, registers):
                written += 1
        except KeyError:
            continue  # the round is not there, or went away meanwhile (a rebuild): the rest go on
    return written
