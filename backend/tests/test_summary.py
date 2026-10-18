import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.cases import mark_visited
from app.config import Settings
from app.dispatch import RecordingQueue
from app.report import build_report
from app.summary import clean_summary, refresh_summaries, write_summary
from seed.seed import ward_ids
from tests.helpers import add_visitor_call, seeded_demo

SECRET = {"X-Internal-Secret": "s3cret"}
GOOD = (
    "Of 30 residents targeted, 24 were reached and 4 could not be reached after three attempts. "
    "There are 3 red and 16 amber cases; 5 have been visited and 14 are still open."
)


def model(reply, seen=None):
    def text_model(instruction, prompt):
        if seen is not None:
            seen.append(prompt)
        if isinstance(reply, Exception):
            raise reply
        return reply
    return text_model


@pytest.fixture
def demo(store, tz, now):
    return seeded_demo(store, now, tz)


def test_a_summary_is_written_from_the_reports_own_figures(store, demo, now):
    seen = []
    assert write_summary(store, model(GOOD, seen), demo, now) == GOOD
    for figure in ("targeted: 30", "reached: 24", "unreachable: 4", "red: 3", "amber: 16", "visited: 5"):
        assert figure in seen[0]
    report = build_report(store, demo)
    assert report["summary"] == {"text": GOOD, "written_at": now}


@pytest.mark.parametrize("reply", [
    "Of 31 residents targeted, 24 were reached.",          # 31 is not on the page
    "80% of residents were reached.",                       # a computed figure
    "24 were reached and everyone is fine.",                # reassurance
    "", None, "x" * 900, RuntimeError("model unavailable"),
])
def test_a_summary_with_a_number_that_is_not_on_the_page_is_dropped(store, demo, now, reply):
    assert write_summary(store, model(reply), demo, now) is None
    assert build_report(store, demo)["summary"] is None
    assert "summary" not in store.get("rounds", demo)


def test_numbers_written_as_words_and_decimals_of_listed_figures_pass():
    allowed = {"30", "24", "126"}
    assert clean_summary("Three wards. 24 of 30 reached; median 126.0 minutes.", allowed) is not None
    assert clean_summary("24 of 30 reached; median 126.5 minutes.", allowed) is None


def test_the_summary_disappears_when_the_shared_figures_change(store, demo, now):
    write_summary(store, model(GOOD), demo, now)
    case = next(c for c in store.query("cases", round_id=demo) if c["status"] == "open" and c["tier"] == "amber")
    mark_visited(store, case["id"], "advised_ok", now)
    assert build_report(store, demo)["summary"] is None
    assert refresh_summaries(store, model(GOOD.replace("5 have", "6 have").replace("14 are", "13 are")), [demo], now) == 1
    assert build_report(store, demo)["summary"]["text"].startswith("Of 30")


def test_a_visitors_own_call_does_not_hide_the_summary(store, demo, tz, now):
    write_summary(store, model(GOOD), demo, now)
    add_visitor_call(store, demo, "visitor-aaaaaaaaaaaa", now, tz, resident_id="v-1", ward_id=ward_ids("churu")[0], feeling="mild_symptoms")
    mine = build_report(store, demo, "visitor-aaaaaaaaaaaa")
    assert mine["includes_yours"] is True
    assert mine["summary"]["text"] == GOOD  # written before their call; the page says when
    seen = []
    assert refresh_summaries(store, model(GOOD, seen), [demo], now) == 0  # nothing shared changed
    assert seen == []


def test_refresh_writes_only_what_is_missing_or_out_of_date(store, demo, now):
    seen = []
    assert refresh_summaries(store, model(GOOD, seen), [demo, "no-such-round"], now) == 1
    assert refresh_summaries(store, model(GOOD, seen), [demo], now) == 0
    assert len(seen) == 1


def test_the_team_can_ask_for_a_summary(store, demo, now):
    settings = Settings(_env_file=None, internal_secret="s3cret")
    client = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now, text_model=model(GOOD)))
    assert client.post(f"/admin/rounds/{demo}/summary").status_code == 401
    assert client.post(f"/admin/rounds/{demo}/summary", headers=SECRET).json() == {"summary": GOOD}
    assert client.get(f"/api/rounds/{demo}/report").json()["summary"]["text"] == GOOD
    assert client.post("/admin/rounds/nope/summary", headers=SECRET).status_code == 404

    without = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now))
    assert without.post(f"/admin/rounds/{demo}/summary", headers=SECRET).status_code == 503


def test_opening_the_days_rounds_writes_no_summary_and_never_asks_the_model(store, demo, now):
    settings = Settings(_env_file=None, internal_secret="s3cret")
    seen = []
    ok = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now, text_model=model(GOOD, seen)))
    round_id = ok.post("/admin/demo/rebuild", headers=SECRET).json()["round_id"]
    assert "summary" not in store.get("rounds", round_id)  # a real round has nothing to sum up yet
    assert ok.get(f"/api/rounds/{round_id}/report").json()["summary"] is None
    assert seen == []

    broken = TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now, text_model=model(RuntimeError("down"))))
    assert broken.post("/admin/demo/rebuild", headers=SECRET).status_code == 200


def test_grouped_digits_are_one_number():
    assert clean_summary("1,200 residents", {"1", "200"}) is None
    assert clean_summary("1,200 residents", {"1200"}) == "1,200 residents"


def test_the_summary_disappears_when_a_reason_or_a_visit_time_changes(store, demo, now):
    from datetime import timedelta

    write_summary(store, model(GOOD), demo, now)
    cases = store.query("cases", round_id=demo)
    open_case = next(c for c in cases if c["status"] == "open" and c["tier"] == "amber")
    store.update("cases", open_case["id"], {"reasons": list(open_case["reasons"]) + ["X9"]})
    assert build_report(store, demo)["summary"] is None
    store.update("cases", open_case["id"], {"reasons": open_case["reasons"]})
    assert build_report(store, demo)["summary"] is not None
    visited = next(c for c in cases if c["status"] in ("visited", "resolved") and c.get("visited_at"))
    store.update("cases", visited["id"], {"visited_at": visited["visited_at"] + timedelta(minutes=7)})
    assert build_report(store, demo)["summary"] is None


def test_the_summary_disappears_when_the_alert_is_raised(store, demo, now):
    alert_id = store.get("rounds", demo)["alert_id"]
    store.update("alerts", alert_id, {"severity": "orange"})
    write_summary(store, model(GOOD), demo, now)
    assert build_report(store, demo)["summary"]["text"] == GOOD
    store.update("alerts", alert_id, {"severity": "red"})  # the paragraph still says orange
    assert build_report(store, demo)["summary"] is None
    seen = []
    assert refresh_summaries(store, model(GOOD, seen), [demo], now) == 1
    assert "Warning: red heat" in seen[0]
    assert build_report(store, demo)["summary"]["text"] == GOOD
    store.update("alerts", alert_id, {"hazard": "cold"})
    assert build_report(store, demo)["summary"] is None


def test_a_round_that_goes_away_between_reads_does_not_stop_the_rest(store, demo, now):
    store.set("rounds", "ghost", {**store.get("rounds", demo), "id": "ghost"})
    real_get = store.get

    def get(collection, doc_id):
        found = real_get(collection, doc_id)
        if (collection, doc_id) == ("rounds", "ghost") and found is not None:
            store.delete("rounds", "ghost")  # a rebuild removes it just after the first read
        return found

    store.get = get
    seen = []
    assert refresh_summaries(store, model(GOOD, seen), ["ghost", demo], now) == 1
    assert len(seen) == 1
    assert build_report(store, demo)["summary"]["text"] == GOOD


def test_a_round_removed_while_the_model_writes_does_not_stop_the_rest(store, demo, now):
    store.set("rounds", "ghost", {**store.get("rounds", demo), "id": "ghost"})

    def text_model(instruction, prompt):
        store.delete("rounds", "ghost")
        return "The round is under way."

    assert refresh_summaries(store, text_model, ["ghost", demo], now) == 1
    assert build_report(store, demo)["summary"]["text"] == "The round is under way."


def test_a_number_inside_a_label_the_model_was_shown_may_be_repeated(store, demo, now):
    # "Alone and aged 70 or over" is a problem's name: 70 is not a figure, but it was shown.
    text = "24 of 30 residents were reached. The commonest problems included 4 who were alone and aged 70 or over."
    seen = []
    written = write_summary(store, model(text, seen), demo, now)
    assert "aged 70 or over" in seen[0]
    assert written == text


def test_the_prompt_carries_headline_figures_not_every_row(store, demo, now):
    seen = []
    write_summary(store, model(GOOD, seen), demo, now)
    prompt = seen[0]
    names = [w["name"] for w in store.query("wards", district_id="churu")]
    assert names and not [name for name in names if name in prompt]  # the ward table stays on the page
    assert prompt.count("problem:") == 3
    for figure in ("targeted: 30", "reached: 24", "red: 3", "amber: 16", "visited: 5", "still open: 14"):
        assert figure in prompt
