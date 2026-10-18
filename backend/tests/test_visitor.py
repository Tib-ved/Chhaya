from datetime import timedelta

import pytest

from app.config import Settings
from app.dispatch import RecordingQueue, handle_call_outcome, mark_call_started
from app.models import VISITOR_USAGE
from tests.scripted_round import scripted_round
from app.demo_cards import CARDS, LANGUAGES, public_cards
from app.visitor import (
    LimitReached, NoDemoRound, clean_visitor_id, create_visitor_call,
    forget_old_usage, latest_call,
)
from seed.seed import ward_ids
from tests.helpers import seeded_demo

ALICE = "visitor-aaaaaaaaaaaa"
BOB = "visitor-bbbbbbbbbbbb"
HOME, CAFE = "203.0.113.7", "198.51.100.9"


@pytest.fixture
def settings():
    return Settings(_env_file=None, internal_secret="s3cret")


@pytest.fixture
def demo(store, now, tz):
    return seeded_demo(store, now, tz)


def start(store, settings, now, tz, visitor=ALICE, address=HOME, card="alone_dizzy", language="hi"):
    return create_visitor_call(store, settings, card, language, visitor, address, now, tz)


def test_the_four_cards_and_five_languages():
    assert sorted(CARDS) == ["alone_dizzy", "emergency", "family_answers", "must_work"]
    assert [l["code"] for l in LANGUAGES] == ["hi", "en", "mr", "gu", "mwr"]
    assert [l["code"] for l in LANGUAGES if l["experimental"]] == ["mwr"]
    for card in public_cards():
        assert set(card) == {"id", "name", "age", "ward_id", "role", "nudge"}


@pytest.mark.parametrize("bad", ["", "short", "x" * 65, "has space in it here", "semi;colon-aaaaaaaaaa", None, 12345678901234567])
def test_a_visitor_id_must_have_the_expected_shape(bad):
    assert clean_visitor_id(bad) is None


def test_a_browser_made_id_is_accepted():
    assert clean_visitor_id("3f2b8c1e-7a4d-4e2a-9b1c-5d6e7f8a9b0c") == "3f2b8c1e-7a4d-4e2a-9b1c-5d6e7f8a9b0c"


def test_a_call_gets_a_temporary_resident_copied_from_the_card(store, settings, demo, now, tz):
    made = start(store, settings, now, tz, card="alone_dizzy", language="mr")
    assert made["expires_in"] == 60
    call = store.get("calls", made["call_id"])
    assert (call["round_id"], call["channel"], call["status"], call["attempt"]) == (demo, "browser", "queued", 1)
    assert call["visitor_id"] == ALICE
    assert call["created_at"] == now
    assert call["browser_ticket"] == made["ticket"]
    assert call["browser_ticket_expires"] == now + timedelta(seconds=60)

    resident = store.get("residents", call["resident_id"])
    assert resident["id"].startswith("v-")
    assert (resident["name"], resident["age"], resident["language"]) == ("Kamla Devi", 72, "mr")
    assert (resident["lives_alone"], resident["conditions"]) == (True, ["diabetes"])
    assert (resident["ward_id"], resident["district_id"]) == (ward_ids("churu")[0], "churu")
    assert resident["phone"] == ""
    assert (resident["visitor_id"], resident["card_id"]) == (ALICE, "alone_dizzy")
    assert resident["risk_score"] == 5  # 65 to 74, lives alone, one condition


def test_an_unknown_card_or_no_demo_round_is_refused_and_writes_nothing(store, settings, now, tz):
    with pytest.raises(NoDemoRound):
        start(store, settings, now, tz)
    seeded_demo(store, now, tz)
    with pytest.raises(KeyError):
        start(store, settings, now, tz, card="astronaut")
    assert store.query(VISITOR_USAGE) == []
    assert store.query("calls", visitor_id=ALICE) == []


def test_three_calls_an_hour_for_one_visitor(store, settings, demo, now, tz):
    for minutes in (0, 10, 20):
        start(store, settings, now + timedelta(minutes=minutes), tz, address=f"203.0.113.{minutes}")
    with pytest.raises(LimitReached) as refusal:
        start(store, settings, now + timedelta(minutes=30), tz, address=CAFE)
    assert (refusal.value.reason, refusal.value.status) == ("visitor_hourly", 429)
    assert refusal.value.retry_after_s == 30 * 60
    start(store, settings, now + timedelta(minutes=30), tz, visitor=BOB, address=CAFE)  # someone else may
    start(store, settings, now + timedelta(minutes=61), tz, address=CAFE)  # and so may Alice, later


def test_six_calls_an_hour_for_one_address(store, settings, demo, now, tz):
    for n in range(6):
        start(store, settings, now + timedelta(minutes=5 * n), tz, visitor=f"visitor-{n:012d}")
    with pytest.raises(LimitReached) as refusal:
        start(store, settings, now + timedelta(minutes=40), tz, visitor=BOB)
    assert (refusal.value.reason, refusal.value.status) == ("address_hourly", 429)
    assert refusal.value.retry_after_s == 20 * 60
    start(store, settings, now + timedelta(minutes=40), tz, visitor=BOB, address=CAFE)


def test_three_calls_at_once(store, settings, demo, now, tz):
    for n in range(3):
        made = start(store, settings, now, tz, visitor=f"visitor-{n:012d}", address=f"203.0.113.{n}")
        store.update("calls", made["call_id"], {"status": "in_progress", "started_at": now})
    with pytest.raises(LimitReached) as refusal:
        start(store, settings, now + timedelta(seconds=30), tz, visitor=BOB, address=CAFE)
    assert (refusal.value.reason, refusal.value.status, refusal.value.retry_after_s) == ("lines_busy", 503, None)
    store.update("calls", made["call_id"], {"status": "completed"})
    start(store, settings, now + timedelta(seconds=40), tz, visitor=BOB, address=CAFE)


def test_an_unused_ticket_frees_its_line_after_a_minute(store, settings, demo, now, tz):
    for n in range(3):
        start(store, settings, now, tz, visitor=f"visitor-{n:012d}", address=f"203.0.113.{n}")
    with pytest.raises(LimitReached, match="lines_busy"):
        start(store, settings, now + timedelta(seconds=59), tz, visitor=BOB, address=CAFE)
    start(store, settings, now + timedelta(seconds=61), tz, visitor=BOB, address=CAFE)


def test_a_call_stuck_in_progress_stops_holding_a_line(store, settings, demo, now, tz):
    for n in range(3):
        made = start(store, settings, now, tz, visitor=f"visitor-{n:012d}", address=f"203.0.113.{n}")
        store.update("calls", made["call_id"], {"status": "in_progress", "started_at": now})
    limit = settings.max_call_seconds + 120
    with pytest.raises(LimitReached, match="lines_busy"):
        start(store, settings, now + timedelta(seconds=limit - 1), tz, visitor=BOB, address=CAFE)
    start(store, settings, now + timedelta(seconds=limit + 1), tz, visitor=BOB, address=CAFE)


def test_the_daily_cap(store, demo, now, tz):
    settings = Settings(_env_file=None, internal_secret="s3cret", visitor_calls_per_day=4, visitor_calls_at_once=99)
    for n in range(4):
        made = start(store, settings, now + timedelta(minutes=n), tz, visitor=f"visitor-{n:012d}", address=f"203.0.113.{n}")
        mark_call_started(store, made["call_id"], "in_progress", now + timedelta(minutes=n))
    with pytest.raises(LimitReached) as refusal:
        start(store, settings, now + timedelta(minutes=5), tz, visitor=BOB, address=CAFE)
    assert (refusal.value.reason, refusal.value.status, refusal.value.retry_after_s) == ("daily_cap", 429, None)
    start(store, settings, now + timedelta(days=1), tz, visitor=BOB, address=CAFE)  # a new day


def test_tickets_nobody_uses_do_not_use_up_the_days_calls(store, demo, now, tz):
    settings = Settings(_env_file=None, internal_secret="s3cret", visitor_calls_per_day=2, visitor_calls_at_once=99)
    made = [
        start(store, settings, now + timedelta(minutes=n), tz, visitor=f"visitor-{n:012d}", address=f"203.0.113.{n}")
        for n in range(5)
    ]  # five tickets, none of them used: more than the day's calls, and nobody is shut out
    mark_call_started(store, made[0]["call_id"], "in_progress", now + timedelta(minutes=6))
    start(store, settings, now + timedelta(minutes=7), tz, visitor=BOB, address=CAFE)  # one started: room for one more
    mark_call_started(store, made[1]["call_id"], "in_progress", now + timedelta(minutes=8))
    with pytest.raises(LimitReached) as refusal:
        start(store, settings, now + timedelta(minutes=9), tz, visitor="visitor-cccccccccccc", address="192.0.2.1")
    assert (refusal.value.reason, refusal.value.status) == ("daily_cap", 429)


def test_a_started_call_still_counts_after_it_has_ended(store, demo, now, tz):
    settings = Settings(_env_file=None, internal_secret="s3cret", visitor_calls_per_day=1, visitor_calls_at_once=99)
    made = start(store, settings, now, tz)
    mark_call_started(store, made["call_id"], "in_progress", now)
    handle_call_outcome(store, RecordingQueue(), made["call_id"], "no_answer", now + timedelta(minutes=1), tz)
    with pytest.raises(LimitReached, match="daily_cap"):
        start(store, settings, now + timedelta(minutes=2), tz, visitor=BOB, address=CAFE)


def test_the_days_tickets_have_a_ceiling_of_their_own(store, demo, now, tz):
    settings = Settings(
        _env_file=None, internal_secret="s3cret", visitor_tickets_per_day=4, visitor_calls_at_once=99,
    )
    assert Settings(_env_file=None).visitor_tickets_per_day == 1500
    for n in range(4):
        start(store, settings, now + timedelta(minutes=n), tz, visitor=f"visitor-{n:012d}", address=f"203.0.113.{n}")
    with pytest.raises(LimitReached) as refusal:
        start(store, settings, now + timedelta(minutes=5), tz, visitor=BOB, address=CAFE)
    assert (refusal.value.reason, refusal.value.status, refusal.value.retry_after_s) == ("daily_cap", 429, None)
    start(store, settings, now + timedelta(days=1), tz, visitor=BOB, address=CAFE)  # a new day


def test_marking_a_visitor_call_started_marks_its_ledger_record(store, settings, demo, now, tz):
    made = start(store, settings, now, tz)
    assert "started" not in store.get(VISITOR_USAGE, made["call_id"])
    mark_call_started(store, made["call_id"], "dialing", now)  # not yet an agent session
    assert "started" not in store.get(VISITOR_USAGE, made["call_id"])
    mark_call_started(store, made["call_id"], "in_progress", now)
    assert store.get(VISITOR_USAGE, made["call_id"])["started"] is True
    assert store.get("calls", made["call_id"])["status"] == "in_progress"


def test_a_call_starts_even_if_its_ledger_record_is_missing_or_the_store_fails(
    store, settings, demo, now, tz, monkeypatch, caplog
):
    gone = start(store, settings, now, tz)
    store.delete(VISITOR_USAGE, gone["call_id"])
    mark_call_started(store, gone["call_id"], "in_progress", now)
    assert store.get("calls", gone["call_id"])["status"] == "in_progress"
    assert store.get(VISITOR_USAGE, gone["call_id"]) is None  # never made up afterwards

    broken = start(store, settings, now, tz, visitor=BOB, address=CAFE)
    real_update = store.update

    def failing_update(collection, doc_id, fields):
        if collection == VISITOR_USAGE:
            raise RuntimeError("store down")
        return real_update(collection, doc_id, fields)

    monkeypatch.setattr(store, "update", failing_update)
    mark_call_started(store, broken["call_id"], "in_progress", now)
    assert store.get("calls", broken["call_id"])["status"] == "in_progress"
    assert [record.levelname for record in caplog.records] == ["WARNING", "ERROR"]


def test_a_team_call_marks_no_ledger_record(store, demo, now, tz, caplog):
    call = next(c for c in store.query("calls", round_id=demo) if c["status"] == "queued")
    mark_call_started(store, call["id"], "in_progress", now)
    assert store.get("calls", call["id"])["status"] == "in_progress"
    assert store.query(VISITOR_USAGE) == [] and caplog.records == []


def test_ipv6_addresses_in_one_slash_64_are_one_caller(store, demo, now, tz):
    settings = Settings(_env_file=None, internal_secret="s3cret", address_calls_per_hour=2, visitor_calls_at_once=99)
    for n, address in enumerate(("2001:db8:1:2::1", "2001:db8:1:2:aaaa:bbbb:cccc:dddd")):
        start(store, settings, now, tz, visitor=f"visitor-{n:012d}", address=address)
    with pytest.raises(LimitReached, match="address_hourly"):
        start(store, settings, now, tz, visitor=BOB, address="2001:0db8:0001:0002:ffff::9")
    start(store, settings, now, tz, visitor=BOB, address="2001:db8:1:3::1")  # the next /64 is someone else


def test_ipv4_addresses_and_unparsable_ones_count_one_by_one(store, demo, now, tz):
    settings = Settings(_env_file=None, internal_secret="s3cret", address_calls_per_hour=1, visitor_calls_at_once=99)
    addresses = ("203.0.113.7", "203.0.113.8", "unknown", "not an address", "::ffff:198.51.100.1", "::ffff:198.51.100.2")
    for n, address in enumerate(addresses):
        start(store, settings, now, tz, visitor=f"visitor-{n:012d}", address=address)
    for address in ("203.0.113.7", "unknown", "198.51.100.1"):  # the last: the same caller, written as IPv4
        with pytest.raises(LimitReached, match="address_hourly"):
            start(store, settings, now, tz, visitor=BOB, address=address)


def test_a_refused_call_writes_nothing(store, demo, now, tz):
    settings = Settings(_env_file=None, internal_secret="s3cret", visitor_calls_per_hour=1)
    start(store, settings, now, tz)
    residents, calls, usage = (len(store.query(c)) for c in ("residents", "calls", VISITOR_USAGE))
    with pytest.raises(LimitReached):
        start(store, settings, now + timedelta(minutes=1), tz)
    assert (len(store.query("residents")), len(store.query("calls")), len(store.query(VISITOR_USAGE))) == (residents, calls, usage)


def test_a_call_whose_agent_never_started_is_not_counted_against_the_visitor(store, demo, now, tz):
    settings = Settings(_env_file=None, internal_secret="s3cret", visitor_calls_per_hour=1, visitor_calls_per_day=1)
    made = start(store, settings, now, tz)
    store.update(VISITOR_USAGE, made["call_id"], {"started": True})  # as marking it in progress does
    handle_call_outcome(store, RecordingQueue(), made["call_id"], "failed", now, tz)
    (entry,) = store.query(VISITOR_USAGE)
    assert (entry["failed"], entry["started"]) == (True, False)
    start(store, settings, now + timedelta(minutes=1), tz)  # neither the hour nor the day was used up


def test_failed_starts_from_one_address_are_limited_all_the_same(store, demo, now, tz):
    settings = Settings(_env_file=None, internal_secret="s3cret", address_failed_starts_per_hour=2)
    for n in range(2):
        made = start(store, settings, now + timedelta(minutes=n), tz, visitor=f"visitor-{n:012d}")
        handle_call_outcome(store, RecordingQueue(), made["call_id"], "failed", now, tz)
    with pytest.raises(LimitReached) as refusal:
        start(store, settings, now + timedelta(minutes=5), tz, visitor=BOB)
    assert (refusal.value.reason, refusal.value.status) == ("address_failed_starts", 429)
    assert refusal.value.retry_after_s == 55 * 60
    start(store, settings, now + timedelta(minutes=5), tz, visitor=BOB, address=CAFE)
    start(store, settings, now + timedelta(minutes=61), tz, visitor=BOB)


def test_a_started_call_whose_record_a_rebuild_removed_still_holds_its_line(store, settings, demo, now, tz):
    for n in range(3):
        made = start(store, settings, now, tz, visitor=f"visitor-{n:012d}", address=f"203.0.113.{n}")
        store.update(VISITOR_USAGE, made["call_id"], {"started": True})
        store.delete("calls", made["call_id"])  # what a rebuild does to the old round's calls
    limit = settings.max_call_seconds + 120
    with pytest.raises(LimitReached, match="lines_busy"):
        start(store, settings, now + timedelta(seconds=limit - 1), tz, visitor=BOB, address=CAFE)
    start(store, settings, now + timedelta(seconds=limit + 1), tz, visitor=BOB, address=CAFE)


def test_a_visitor_call_is_never_retried(store, settings, demo, now, tz):
    for n, status in enumerate(("failed", "no_answer", "busy")):
        made = start(store, settings, now, tz, visitor=f"visitor-{n:012d}", address=f"203.0.113.{n}")
        queue = RecordingQueue()
        handle_call_outcome(store, queue, made["call_id"], status, now, tz)
        assert queue.items == []
        call = store.get("calls", made["call_id"])
        assert store.query("calls", resident_id=call["resident_id"]) == [call]
        assert store.query("cases", resident_id=call["resident_id"]) == []


def test_the_usage_ledger_keeps_no_address(store, settings, demo, now, tz):
    start(store, settings, now, tz)
    (entry,) = store.query(VISITOR_USAGE)
    assert set(entry) == {"id", "visitor_id", "address_key", "day", "at", "channel"}
    assert HOME not in str(entry)
    assert len(entry["address_key"]) == 20


def test_latest_call_is_the_visitors_newest_in_the_pinned_round(store, settings, demo, now, tz):
    assert latest_call(store, ALICE) is None
    first = start(store, settings, now, tz)
    second = start(store, settings, now + timedelta(minutes=5), tz, card="must_work")
    assert latest_call(store, ALICE)["id"] == second["call_id"] != first["call_id"]
    assert latest_call(store, BOB) is None
    scripted_round(store, "churu", now + timedelta(minutes=10), tz)
    assert latest_call(store, ALICE) is None


def test_old_usage_is_forgotten_but_today_and_yesterday_are_kept(store, settings, demo, now, tz):
    for days_ago in (0, 1, 2, 5):
        start(store, settings, now - timedelta(days=days_ago), tz, visitor=f"visitor-{days_ago:012d}", address=str(days_ago))
    forget_old_usage(store, now, tz)
    kept = sorted((now - entry["at"]).days for entry in store.query(VISITOR_USAGE))
    assert kept == [0, 1]


def _ledger_entry(store, n, at, tz):
    store.set(VISITOR_USAGE, f"c{n}", {
        "id": f"c{n}", "visitor_id": f"visitor-{n:012d}", "address_key": f"k{n}",
        "day": f"{at.astimezone(tz):%Y%m%d}", "at": at,
    })


def test_a_call_still_being_created_holds_its_line(store, settings, demo, now, tz):
    for n in range(3):
        _ledger_entry(store, n, now, tz)  # ledger written, call document not yet
    with pytest.raises(LimitReached, match="lines_busy"):
        start(store, settings, now + timedelta(seconds=30), tz, visitor=BOB, address=CAFE)
    start(store, settings, now + timedelta(seconds=61), tz, visitor=BOB, address=CAFE)


def test_a_failed_start_leaves_nothing_behind(store, settings, demo, now, tz, monkeypatch):
    real_set = store.set

    def failing_set(collection, doc_id, data):
        if collection == "calls":
            raise RuntimeError("store down")
        return real_set(collection, doc_id, data)

    monkeypatch.setattr(store, "set", failing_set)
    residents = len(store.query("residents"))
    with pytest.raises(RuntimeError, match="store down"):
        start(store, settings, now, tz)
    assert len(store.query("residents")) == residents
    assert store.query("calls", visitor_id=ALICE) == []
    assert store.query(VISITOR_USAGE) == []
