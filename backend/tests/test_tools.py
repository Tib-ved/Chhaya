import logging

import pytest

from app.call.tools import SEVERITY, CallTools
from app.dispatch import RecordingQueue, plan_round
from app.ingest.alerts import record_alert
from app.ingest.replay import make_replay_alert
from app.triage import ALLOWED_VALUES
from tests.helpers import add_resident, add_ward


@pytest.fixture
def call(store, tz, now):
    add_ward(store)
    round_id = record_alert(store, make_replay_alert("d1", "heat", "red", now), tz).id
    add_resident(store, "r1", age=80)
    plan_round(store, RecordingQueue(), round_id, now, tz)
    call_id = f"{round_id}_r1_1"
    store.update("calls", call_id, {"status": "in_progress", "started_at": now})
    return round_id, call_id


@pytest.fixture
def tools(store, call, now):
    return CallTools(store, call[1], clock=lambda: now)


def heard(tools):
    """The person replies, as they do before any answer that is recorded."""
    tools.add_transcript("resident", "haan")
    return tools


def test_severity_covers_every_allowed_value():
    assert set(SEVERITY) == set(ALLOWED_VALUES)
    for question, ordered in SEVERITY.items():
        assert set(ordered) == ALLOWED_VALUES[question], question


def test_record_answer_writes_value_quote_and_time(store, call, tools, now):
    response = heard(tools).handle("record_answer", {"question_id": "feeling", "value": "ok", "quote": "theek hoon"})
    assert response == {"result": "recorded"}
    assert store.get("calls", call[1])["answers"]["feeling"] == {"value": "ok", "quote": "theek hoon", "at": now}


def test_record_answer_without_quote(store, call, tools):
    heard(tools).handle("record_answer", {"question_id": "company", "value": "alone"})
    assert store.get("calls", call[1])["answers"]["company"]["quote"] == ""


def test_record_answer_rejects_unknown_question_and_value(store, call, tools):
    bad_question = heard(tools).handle("record_answer", {"question_id": "mood", "value": "ok"})
    assert bad_question["error"]
    assert bad_question["allowed_question_ids"] == sorted(ALLOWED_VALUES)
    bad_value = heard(tools).handle("record_answer", {"question_id": "feeling", "value": "fine"})
    assert bad_value["error"]
    assert bad_value["allowed_values"] == SEVERITY["feeling"]
    not_text = tools.handle("record_answer", {"question_id": "feeling", "value": ["ok"]})
    assert not_text["error"]
    assert store.get("calls", call[1])["answers"] == {}


def test_record_answer_keeps_the_worst_value(store, call, tools):
    heard(tools).handle("record_answer", {"question_id": "feeling", "value": "mild_symptoms", "quote": "chakkar"})
    response = heard(tools).handle("record_answer", {"question_id": "feeling", "value": "ok", "quote": "ab theek"})
    assert response == {"result": "kept_earlier_answer"}
    stored = store.get("calls", call[1])["answers"]["feeling"]
    assert stored["value"] == "mild_symptoms"
    assert stored["quote"] == "chakkar"
    tools.handle("record_answer", {"question_id": "feeling", "value": "severe_symptoms"})
    assert store.get("calls", call[1])["answers"]["feeling"]["value"] == "severe_symptoms"


def test_severe_answer_opens_a_red_case_during_the_call(store, call, tools):
    tools.handle("record_answer", {"question_id": "feeling", "value": "severe_symptoms"})
    assert store.get("cases", f"{call[0]}_r1")["tier"] == "red"


def test_urgent_help_opens_a_red_case_during_the_call(store, call, tools):
    tools.handle("record_answer", {"question_id": "help", "value": "urgent"})
    assert store.get("cases", f"{call[0]}_r1")["tier"] == "red"


def test_ordinary_answers_open_no_case_during_the_call(store, call, tools):
    heard(tools).handle("record_answer", {"question_id": "feeling", "value": "mild_symptoms"})
    heard(tools).handle("record_answer", {"question_id": "fluids_food", "value": "low"})
    assert store.query("cases") == []


def test_flag_emergency_writes_and_opens_a_red_case(store, call, tools, now):
    response = tools.handle("flag_emergency", {"reason": "fainted this morning"})
    assert response["result"] == "flagged"
    assert store.get("calls", call[1])["emergency"] == {"flagged": True, "reason": "fainted this morning", "at": now}
    case = store.get("cases", f"{call[0]}_r1")
    assert case["tier"] == "red"
    assert "R1" in case["reasons"]


def test_emergency_procedure_end_to_end(store, call, tools):
    tools.handle("flag_emergency", {"reason": "chest pain"})
    tools.handle("record_answer", {"question_id": "feeling", "value": "severe_symptoms"})
    tools.add_transcript("agent", "Please call 108 now.")
    assert tools.handle("end_call", {"outcome": "emergency"}) == {"result": "ending"}
    case = store.get("cases", f"{call[0]}_r1")
    assert case["tier"] == "red"
    assert "R1" in case["reasons"]
    assert "R2" in case["reasons"]
    assert tools.final_status("ended_by_agent") == "completed"


def test_set_respondent(store, call, tools):
    assert tools.handle("set_respondent", {"type": "family"}) == {"result": "recorded"}
    assert store.get("calls", call[1])["respondent"] == "family"
    assert "error" in tools.handle("set_respondent", {"type": "neighbour"})
    assert store.get("calls", call[1])["respondent"] == "family"


def test_end_call_sets_ended_and_validates(tools):
    assert tools.ended is None
    assert "error" in tools.handle("end_call", {"outcome": "bored"})
    assert tools.ended is None
    assert tools.handle("end_call", {"outcome": "declined"}) == {"result": "ending"}
    assert tools.ended == "declined"


def test_unknown_tool_and_missing_arguments_return_errors(tools):
    assert "error" in tools.handle("order_pizza", {})
    assert "error" in tools.handle("record_answer", {"value": "ok"})
    assert tools.handle("flag_emergency", {})["result"] == "flagged"  # a missing reason is still recorded


def test_transcript_is_buffered_then_flushed(store, call, tools, now):
    tools.add_transcript("agent", "Namaste")
    tools.add_transcript("resident", "haan ji")
    tools.add_transcript("resident", "   ")  # ignored
    assert store.get("calls", call[1])["transcript"] == []
    tools.flush()
    assert store.get("calls", call[1])["transcript"] == [
        {"role": "agent", "text": "Namaste", "at": now},
        {"role": "resident", "text": "haan ji", "at": now},
    ]
    tools.flush()  # idempotent
    assert len(store.get("calls", call[1])["transcript"]) == 2


def test_consecutive_fragments_from_one_speaker_are_joined(store, call, tools):
    tools.add_transcript("agent", "Nama")
    tools.add_transcript("agent", "ste ji")
    tools.add_transcript("resident", "haan")
    tools.flush()
    assert [t["text"] for t in store.get("calls", call[1])["transcript"]] == ["Namaste ji", "haan"]


@pytest.mark.parametrize(
    "reason, ended, expected",
    [
        ("ended_by_agent", "completed", "completed"),
        ("ended_by_agent", "declined", "completed"),
        ("ended_by_agent", "unable_to_understand", "completed"),
        ("ended_by_agent", "emergency", "completed"),
        ("caller_hung_up", None, "dropped"),
        ("timeout", None, "dropped"),
        ("error", None, "dropped"),
        ("caller_hung_up", "completed", "completed"),
        ("open_failed", None, "failed"),
    ],
)
def test_final_status(tools, reason, ended, expected):
    tools.ended = ended
    assert tools.final_status(reason) == expected


def test_handlers_cope_with_a_missing_call_document(store, now):
    ghost = CallTools(store, "no_such_call", clock=lambda: now)
    for name, args in [
        ("record_answer", {"question_id": "feeling", "value": "ok"}),
        ("record_answer", {"question_id": "feeling", "value": "severe_symptoms"}),
        ("set_respondent", {"type": "self"}),
    ]:
        assert heard(ghost).handle(name, args) == {"error": "call not found"}, name


@pytest.mark.parametrize("args", [None, "x", ["a"], 5])
def test_non_dict_args_are_treated_as_empty(tools, args):
    assert "error" in tools.handle("record_answer", args)


def test_handle_never_raises_on_internal_errors(store, call, tools, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(store, "update", boom)
    with caplog.at_level(logging.ERROR, logger="app.call.tools"):
        response = heard(tools).handle("record_answer", {"question_id": "feeling", "value": "ok"})
    # The answer is kept in memory for flush(), so the model is told it was recorded.
    assert response == {"result": "recorded"}
    assert any("store down" in (r.exc_text or "") for r in caplog.records)
    assert any(call[1] in r.getMessage() and "answer" in r.getMessage() for r in caplog.records)


# ---- fix round 1 ----
import threading  # noqa: E402

from app.call.tools import EMERGENCY_SAY  # noqa: E402


def test_flag_emergency_says_108_even_when_the_store_fails(store, call, tools, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(store, "update", boom)
    response = tools.handle("flag_emergency", {"reason": "fainted"})
    assert response["say"] == EMERGENCY_SAY
    assert "error" in response


def test_flag_emergency_says_108_for_a_missing_call(store, now):
    ghost = CallTools(store, "no_such_call", clock=lambda: now)
    response = ghost.handle("flag_emergency", {"reason": "fainted"})
    assert response["say"] == EMERGENCY_SAY
    assert response["error"] == "call not found"


def test_flag_emergency_says_108_after_an_unexpected_exception(store, call, tools, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("get failed")

    with monkeypatch.context() as patch:
        patch.setattr(store, "get", boom)
        response = tools.handle("flag_emergency", {"reason": "fainted"})
    assert response["say"] == EMERGENCY_SAY
    # A store that cannot be read is no reason to lose the flag: it is written all the same.
    assert response["result"] == "flagged"
    assert store.get("calls", call[1])["emergency"]["flagged"] is True


@pytest.mark.parametrize("reason", ["", "   ", None, 5, ["x"]])
def test_flag_emergency_without_a_usable_reason_is_still_recorded(store, call, tools, reason):
    args = {} if reason is None else {"reason": reason}
    response = tools.handle("flag_emergency", args)
    assert response["result"] == "flagged"
    assert response["say"] == EMERGENCY_SAY
    assert store.get("calls", call[1])["emergency"]["reason"] == "no reason given"
    assert store.get("cases", f"{call[0]}_r1")["tier"] == "red"


def _boom_case(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("case failed")

    monkeypatch.setattr("app.call.tools.upsert_case", boom)


def test_record_answer_succeeds_when_opening_the_case_fails(store, call, tools, monkeypatch, caplog):
    _boom_case(monkeypatch)
    with caplog.at_level(logging.ERROR, logger="app.call.tools"):
        response = tools.handle("record_answer", {"question_id": "feeling", "value": "severe_symptoms"})
    assert response == {"result": "recorded"}
    assert store.get("calls", call[1])["answers"]["feeling"]["value"] == "severe_symptoms"
    assert any(call[1] in r.getMessage() for r in caplog.records)


def test_flag_emergency_succeeds_when_opening_the_case_fails(store, call, tools, monkeypatch):
    _boom_case(monkeypatch)
    response = tools.handle("flag_emergency", {"reason": "fainted"})
    assert response == {"result": "flagged", "say": EMERGENCY_SAY}
    assert store.get("calls", call[1])["emergency"]["flagged"] is True


def test_concurrent_answers_keep_the_worst(store, call, now):
    for _ in range(5):
        store.update("calls", call[1], {"answers": {}})
        fresh = CallTools(store, call[1], clock=lambda: now)
        barrier = threading.Barrier(20)

        def work(value):
            barrier.wait()
            fresh.handle("record_answer", {"question_id": "feeling", "value": value})

        threads = [
            threading.Thread(target=work, args=("severe_symptoms" if i % 2 else "ok",))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert store.get("calls", call[1])["answers"]["feeling"]["value"] == "severe_symptoms"


def test_non_string_identifiers_are_validation_errors(tools):
    for bad in (["feeling"], {"a": 1}):
        r = tools.handle("record_answer", {"question_id": bad, "value": "ok"})
        assert r["error"] != "internal error"
        assert r["allowed_question_ids"] == sorted(ALLOWED_VALUES)
    r = tools.handle("end_call", {"outcome": ["declined"]})
    assert r["error"] != "internal error"
    assert "allowed_values" in r
    assert tools.ended is None
    assert tools.handle(["x"], {})["error"] != "internal error"


def test_unknown_tool_error_does_not_echo_a_long_name(tools):
    r = tools.handle("x" * 500, {})
    assert "error" in r
    assert len(r["error"]) < 100


def test_first_end_call_wins_and_is_stored(store, call, tools):
    tools.handle("end_call", {"outcome": "declined"})
    assert tools.handle("end_call", {"outcome": "completed"}) == {"result": "ending"}
    assert tools.ended == "declined"
    assert store.get("calls", call[1])["end_outcome"] == "declined"


def test_end_call_still_ends_when_the_store_write_fails(store, call, tools, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(store, "update", boom)
    assert tools.handle("end_call", {"outcome": "declined"}) == {"result": "ending"}
    assert tools.ended == "declined"


def test_flag_emergency_twice_keeps_first_time_and_joins_reasons(store, call, tools, now):
    tools.handle("flag_emergency", {"reason": "fainted"})
    later = CallTools(store, call[1], clock=lambda: now.replace(year=now.year + 1))
    later.handle("flag_emergency", {"reason": "chest pain"})
    emergency = store.get("calls", call[1])["emergency"]
    assert emergency["at"] == now
    assert emergency["reason"] == "fainted; chest pain"
    later.handle("flag_emergency", {"reason": "z" * 400})
    assert len(store.get("calls", call[1])["emergency"]["reason"]) == 300


def test_flush_never_raises(store, call, tools, monkeypatch, caplog):
    tools.add_transcript("agent", "hello")

    def boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(store, "update", boom)
    with caplog.at_level(logging.ERROR, logger="app.call.tools"):
        tools.flush()
    assert any(r.exc_info for r in caplog.records)


def test_flush_writes_copies(store, call, tools):
    tools.add_transcript("agent", "hi")
    tools.flush()
    tools.add_transcript("agent", " there")
    assert store.get("calls", call[1])["transcript"][0]["text"] == "hi"


def test_transcript_entry_cap(store, call, tools):
    for i in range(500):
        tools.add_transcript("agent" if i % 2 else "resident", "x")
    tools.flush()
    entries = store.get("calls", call[1])["transcript"]
    assert len(entries) == 401
    assert entries[-1] == {"role": "system", "text": "[transcript truncated]"}
    assert sum(1 for e in entries if e["role"] == "system") == 1


def test_transcript_character_cap(store, call, tools):
    tools.add_transcript("agent", "a" * 15000)
    tools.add_transcript("resident", "b" * 15000)  # would pass 20000: dropped
    tools.add_transcript("agent", "c")  # dropped after truncation
    tools.flush()
    entries = store.get("calls", call[1])["transcript"]
    assert [e["role"] for e in entries] == ["agent", "system"]
    assert sum(len(e["text"]) for e in entries if e["role"] != "system") <= 20000


# ---- final review: a fact is never lost to a store failure

from app.dispatch import handle_call_outcome  # noqa: E402

RED_FACT_WRITES = ("emergency", "answers.")


def break_updates(store, monkeypatch, broken=lambda fields: True):
    """Make matching store.update calls raise until the returned switch is turned off."""
    real = store.update
    switch = {"on": True, "failed": 0, "broken": broken}

    def update(collection, doc_id, fields):
        if switch["on"] and switch["broken"](fields):
            switch["failed"] += 1
            raise RuntimeError("store down")
        real(collection, doc_id, fields)

    monkeypatch.setattr(store, "update", update)
    return switch


def is_red_fact_write(fields):
    return any(key.startswith(RED_FACT_WRITES) for key in fields)


def finish(store, call, tools, tz, now, reason="caller_hung_up"):
    """What conduct_call does when a call ends: flush, then record the outcome."""
    tools.flush()
    handle_call_outcome(store, RecordingQueue(), call[1], tools.final_status(reason), now, tz)
    return store.get("cases", f"{call[0]}_r1")


def test_an_emergency_that_could_not_be_written_is_written_when_the_call_ends(
    store, call, tools, tz, now, monkeypatch
):
    switch = break_updates(store, monkeypatch)
    response = tools.handle("flag_emergency", {"reason": "fainted"})
    assert response["say"] == EMERGENCY_SAY
    assert switch["failed"] == 2  # tried, then retried once
    assert store.get("calls", call[1])["emergency"] is None
    switch["on"] = False
    case = finish(store, call, tools, tz, now)
    assert store.get("calls", call[1])["emergency"] == {"flagged": True, "reason": "fainted", "at": now}
    assert case["tier"] == "red"
    assert "R1" in case["reasons"]


def test_a_severe_answer_that_could_not_be_written_is_written_when_the_call_ends(
    store, call, tools, tz, now, monkeypatch
):
    switch = break_updates(store, monkeypatch)
    response = tools.handle("record_answer", {"question_id": "feeling", "value": "severe_symptoms", "quote": "saans"})
    assert response == {"result": "recorded"}  # the model is not made to ask again
    assert switch["failed"] == 2
    assert store.get("calls", call[1])["answers"] == {}
    switch["on"] = False
    case = finish(store, call, tools, tz, now)
    assert store.get("calls", call[1])["answers"]["feeling"] == {
        "value": "severe_symptoms", "quote": "saans", "at": now,
    }
    assert case["tier"] == "red"
    assert "R2" in case["reasons"]


def test_a_write_that_fails_once_is_retried_at_once(store, call, tools, monkeypatch):
    real = store.update
    failures = []

    def update(collection, doc_id, fields):
        if not failures:
            failures.append(fields)
            raise RuntimeError("store hiccup")
        real(collection, doc_id, fields)

    monkeypatch.setattr(store, "update", update)
    assert tools.handle("flag_emergency", {"reason": "fainted"})["result"] == "flagged"
    assert store.get("calls", call[1])["emergency"]["flagged"] is True
    assert store.get("cases", f"{call[0]}_r1")["tier"] == "red"


def test_a_kept_answer_is_not_replaced_by_a_better_one(store, call, tools, monkeypatch):
    switch = break_updates(store, monkeypatch)
    tools.handle("record_answer", {"question_id": "feeling", "value": "severe_symptoms"})
    while_down = heard(tools).handle("record_answer", {"question_id": "feeling", "value": "ok"})
    assert while_down == {"result": "kept_earlier_answer"}
    switch["on"] = False
    after = heard(tools).handle("record_answer", {"question_id": "feeling", "value": "mild_symptoms"})
    assert after == {"result": "kept_earlier_answer"}
    assert store.get("calls", call[1])["answers"] == {}
    tools.flush()
    assert store.get("calls", call[1])["answers"]["feeling"]["value"] == "severe_symptoms"


def test_a_later_worse_answer_supersedes_a_kept_one(store, call, tools, monkeypatch):
    switch = break_updates(store, monkeypatch)
    heard(tools).handle("record_answer", {"question_id": "feeling", "value": "mild_symptoms"})
    switch["on"] = False
    assert tools.handle("record_answer", {"question_id": "feeling", "value": "severe_symptoms"}) == {
        "result": "recorded"
    }
    tools.flush()
    assert store.get("calls", call[1])["answers"]["feeling"]["value"] == "severe_symptoms"


def test_a_kept_respondent_and_ordinary_answer_are_written_when_the_call_ends(store, call, tools, monkeypatch):
    switch = break_updates(store, monkeypatch)
    assert tools.handle("set_respondent", {"type": "family"}) == {"result": "recorded"}
    assert heard(tools).handle("record_answer", {"question_id": "fluids_food", "value": "low"}) == {"result": "recorded"}
    switch["on"] = False
    tools.flush()
    stored = store.get("calls", call[1])
    assert stored["respondent"] == "family"
    assert stored["answers"]["fluids_food"]["value"] == "low"


def test_two_emergencies_kept_in_memory_keep_the_first_time_and_both_reasons(store, call, now, monkeypatch):
    times = iter([now, now.replace(year=now.year + 1)])
    tools = CallTools(store, call[1], clock=lambda: next(times))
    switch = break_updates(store, monkeypatch, is_red_fact_write)
    tools.handle("flag_emergency", {"reason": "fainted"})
    tools.handle("flag_emergency", {"reason": "chest pain"})
    switch["on"] = False
    tools.flush()
    assert store.get("calls", call[1])["emergency"] == {
        "flagged": True, "reason": "fainted; chest pain", "at": now,
    }


@pytest.mark.parametrize("everything_down_during_the_call", [False, True])
def test_an_emergency_ending_alone_makes_the_case_red_when_the_facts_were_never_written(
    store, call, tools, tz, now, monkeypatch, everything_down_during_the_call
):
    switch = break_updates(store, monkeypatch, is_red_fact_write)
    if everything_down_during_the_call:
        switch["broken"] = lambda fields: True
    tools.handle("flag_emergency", {"reason": "fainted"})
    tools.handle("record_answer", {"question_id": "feeling", "value": "severe_symptoms"})
    tools.add_transcript("agent", "Please call 108 now.")
    assert tools.handle("end_call", {"outcome": "emergency"}) == {"result": "ending"}
    switch["broken"] = is_red_fact_write  # the flag and the answer can never be written
    case = finish(store, call, tools, tz, now, reason="ended_by_agent")
    stored = store.get("calls", call[1])
    assert stored["emergency"] is None and stored["answers"] == {}
    assert stored["end_outcome"] == "emergency"
    assert stored["status"] == "completed"
    assert case["tier"] == "red"
    assert "R1" in case["reasons"]


def test_flush_with_the_store_still_failing_does_not_raise_and_logs_what_is_lost(
    store, call, tools, monkeypatch, caplog
):
    break_updates(store, monkeypatch)
    tools.handle("flag_emergency", {"reason": "fainted in the kitchen"})
    tools.handle("record_answer", {"question_id": "feeling", "value": "severe_symptoms", "quote": "bahut kharab"})
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="app.call.tools"):
        tools.flush()
    lost = [r.getMessage() for r in caplog.records if "emergency" in r.getMessage()]
    assert any(call[1] in message for message in lost)
    for record in caplog.records:  # the kind of fact, never its content
        assert "fainted" not in record.getMessage() and "bahut" not in record.getMessage()
        assert "severe_symptoms" not in record.getMessage()


def test_an_emergency_is_kept_when_the_call_cannot_even_be_read(store, call, tools, tz, now, monkeypatch):
    def down(*args):
        raise RuntimeError("store down")

    with monkeypatch.context() as patch:
        patch.setattr(store, "get", down)
        patch.setattr(store, "update", down)
        response = tools.handle("flag_emergency", {"reason": "fainted"})
        assert response["say"] == EMERGENCY_SAY
        assert tools.handle("record_answer", {"question_id": "help", "value": "urgent"}) == {"result": "recorded"}
    case = finish(store, call, tools, tz, now)
    assert case["tier"] == "red"
    assert {"R1", "R3"} <= set(case["reasons"])


def test_an_internal_error_other_than_the_store_is_still_an_error_reply(store, call, now):
    broken = []

    def breaking_clock():
        if broken:
            raise RuntimeError("clock broke")
        return now

    tools = heard(CallTools(store, call[1], clock=breaking_clock))
    broken.append(True)
    assert tools.handle("record_answer", {"question_id": "feeling", "value": "ok"}) == {"error": "internal error"}


def test_a_transcript_copy_is_offered_only_when_it_has_changed(store, now):
    store.set("calls", "c1", {"id": "c1", "status": "in_progress", "transcript": []})
    tools = CallTools(store, "c1", lambda: now)
    assert tools.transcript_if_changed() is None
    tools.add_transcript("agent", "Namaste")
    first = tools.transcript_if_changed()
    assert [entry["text"] for entry in first] == ["Namaste"]
    assert tools.transcript_if_changed() is None
    tools.add_transcript("agent", " ji")  # joins the same speaker's line
    assert [entry["text"] for entry in tools.transcript_if_changed()] == ["Namaste ji"]
    first[0]["text"] = "changed by the caller"
    tools.add_transcript("resident", "haan")
    assert [entry["text"] for entry in tools.transcript_if_changed()] == ["Namaste ji", "haan"]


def test_writing_a_transcript_copy_never_raises(store, now, caplog):
    tools = CallTools(store, "missing-call", lambda: now)
    tools.write_transcript([{"role": "agent", "text": "Namaste", "at": now}])  # the call does not exist
    assert "missing-call" in caplog.text


# An answer needs the person's own words: nothing is recorded that nobody said.


def said(tools, words="haan"):
    tools.add_transcript("resident", words)


def test_an_answer_is_refused_when_the_person_has_not_spoken(store, call, tools):
    tools.add_transcript("agent", "Aaj aapki tabiyat kaisi hai?")
    response = tools.handle("record_answer", {"question_id": "feeling", "value": "ok", "quote": "theek hoon"})
    assert response["error"] == "no answer heard"
    assert "wait" in response["do"]
    assert store.get("calls", call[1])["answers"] == {}


def test_one_reply_supports_one_answer(store, call, tools):
    said(tools, "theek hoon, khana bhi kha liya")
    first = tools.handle("record_answer", {"question_id": "feeling", "value": "ok", "quote": "theek hoon"})
    second = tools.handle("record_answer", {"question_id": "fluids_food", "value": "enough", "quote": "kha liya"})
    assert first == {"result": "recorded"}
    assert second["error"] == "no answer heard"
    said(tools, "haan, paani hai")
    again = tools.handle("record_answer", {"question_id": "fluids_food", "value": "enough", "quote": "paani hai"})
    assert again == {"result": "recorded"}
    assert set(store.get("calls", call[1])["answers"]) == {"feeling", "fluids_food"}


def test_four_questions_asked_in_one_breath_record_nothing(store, call, tools):
    # The team's test call of 12 October: one reply about food, then four answers nobody gave.
    quote = "Haan, mai aaj garam khana khayo hai"
    said(tools, quote)
    tools.handle("record_answer", {"question_id": "fluids_food", "value": "enough", "quote": quote})
    tools.add_transcript("agent", "Koi saath hai? Kambal hai? Bahar jaana hai? Koi madad chahiye?")
    invented = [("company", "with_someone"), ("shelter", "adequate"), ("exposure", "staying_in"), ("help", "none")]
    for question, value in invented:
        response = tools.handle("record_answer", {"question_id": question, "value": value, "quote": quote})
        assert response["error"] == "no answer heard", question
    assert set(store.get("calls", call[1])["answers"]) == {"fluids_food"}


def test_the_agents_own_words_are_not_a_reply(store, call, tools):
    said(tools)
    tools.handle("record_answer", {"question_id": "feeling", "value": "ok"})
    tools.add_transcript("agent", "Kya ghar mein koi hai?")
    assert "error" in tools.handle("record_answer", {"question_id": "company", "value": "with_someone"})


def test_blank_sound_is_not_a_reply(store, call, tools):
    tools.add_transcript("resident", "   ")
    assert "error" in tools.handle("record_answer", {"question_id": "feeling", "value": "ok"})


@pytest.mark.parametrize("question, value", [("feeling", "severe_symptoms"), ("help", "urgent")])
def test_a_red_answer_is_never_refused(store, call, tools, question, value):
    response = tools.handle("record_answer", {"question_id": question, "value": value})
    assert response == {"result": "recorded"}
    assert store.get("calls", call[1])["answers"][question]["value"] == value


def test_a_reply_after_the_transcript_is_full_still_counts(store, call, tools, monkeypatch):
    monkeypatch.setattr("app.call.tools.MAX_TRANSCRIPT_CHARS", 5)
    said(tools, "a long reply that no longer fits")
    assert tools.handle("record_answer", {"question_id": "feeling", "value": "ok"}) == {"result": "recorded"}


def test_a_refused_answer_is_logged_without_the_persons_words(store, call, tools, caplog):
    with caplog.at_level(logging.WARNING, logger="app.call.tools"):
        tools.handle("record_answer", {"question_id": "feeling", "value": "ok", "quote": "theek hoon"})
    assert call[1] in caplog.text and "feeling" in caplog.text
    assert "theek hoon" not in caplog.text


def test_the_questions_are_done_when_every_required_one_has_an_answer(store, call, tools):
    from app.triage import REQUIRED_QUESTIONS

    assert not tools.questions_done()
    for question in REQUIRED_QUESTIONS[:-1]:
        heard(tools).handle("record_answer", {"question_id": question, "value": SEVERITY[question][0]})
    assert not tools.questions_done()
    last = REQUIRED_QUESTIONS[-1]
    heard(tools).handle("record_answer", {"question_id": last, "value": SEVERITY[last][0]})
    assert tools.questions_done()


def test_an_answer_kept_through_a_store_failure_still_counts_as_given(store, call, tools, monkeypatch):
    from app.triage import REQUIRED_QUESTIONS

    def broken(*args, **kwargs):
        raise RuntimeError("store down")

    monkeypatch.setattr(store, "update", broken)
    for question in REQUIRED_QUESTIONS:
        heard(tools).handle("record_answer", {"question_id": question, "value": SEVERITY[question][0]})
    assert tools.questions_done()


# --- the timeline of a call: what happened and when, never what was said -----------------


def test_events_are_kept_in_order_and_stored_with_the_transcript(store, call, tools, now):
    tools.add_event("agent_turn", audio_seconds=2.5, interrupted=False, tools=0)
    tools.add_event("heard")
    tools.add_transcript("agent", "Namaste")
    assert "timeline" not in {key for key, value in store.get("calls", call[1]).items() if value}
    tools.flush()
    stored = store.get("calls", call[1])
    assert stored["timeline"] == [
        {"at": now, "kind": "agent_turn", "audio_seconds": 2.5, "interrupted": False, "tools": 0},
        {"at": now, "kind": "heard"},
    ]
    assert [entry["text"] for entry in stored["transcript"]] == ["Namaste"]


def test_the_running_save_stores_the_timeline_with_the_transcript(store, call, tools, now):
    tools.add_event("heard")
    tools.add_transcript("resident", "haan")
    tools.write_transcript(tools.transcript_if_changed(), tools.timeline())
    stored = store.get("calls", call[1])
    assert stored["timeline"] == [{"at": now, "kind": "heard"}]
    assert [entry["text"] for entry in stored["transcript"]] == ["haan"]
    copy = tools.timeline()
    copy[0]["kind"] = "changed"
    assert tools.timeline()[0]["kind"] == "heard"  # a copy: the worker thread never shares the list


def test_a_running_save_without_a_timeline_writes_the_transcript_alone(store, call, tools, now):
    tools.add_event("heard")
    tools.write_transcript([{"role": "agent", "text": "Namaste", "at": now}])
    stored = store.get("calls", call[1])
    assert stored["timeline"] == [] and len(stored["transcript"]) == 1


def test_only_the_last_three_hundred_events_are_kept(store, call, tools):
    for number in range(305):
        tools.add_event("heard", number=number)
    kept = tools.timeline()
    assert len(kept) == 300
    assert [kept[0]["number"], kept[-1]["number"]] == [5, 304]


def test_an_event_that_cannot_be_noted_never_raises(store, call):
    def broken_clock():
        raise RuntimeError("no clock")

    tools = CallTools(store, call[1], clock=broken_clock)
    tools.add_event("heard")
    assert tools.timeline() == []


# --- the follow-ups everyone is asked are enforced here ------------------------------------
# Heard on a real call (13 October): after "fine" the agent recorded feeling=ok at once and
# never asked the two questions about signs that the script says everyone is asked.

ALWAYS_ASKED = {"feeling": ["Any dizziness or headache?", "Any vomiting, cramps or weakness?"]}
OK = {"question_id": "feeling", "value": "ok", "quote": "theek hoon"}


ORDER = ["feeling", "fluids_food", "company", "shelter", "exposure", "help"]


@pytest.fixture
def strict(store, call, now):
    return CallTools(store, call[1], clock=lambda: now, always_asked=ALWAYS_ASKED, order=ORDER)


def exchange(tools, reply="nahi", question="Aur kuch?"):
    """The agent asks something and the person replies."""
    tools.add_transcript("agent", question)
    tools.add_transcript("resident", reply)


def test_ok_straight_after_the_first_reply_is_refused_and_the_remaining_questions_named(store, call, strict):
    exchange(strict, "theek hoon")
    response = strict.handle("record_answer", OK)
    assert response["error"] == "follow-ups not asked"
    assert response["ask_next"] == ALWAYS_ASKED["feeling"]
    assert "not yet asked every follow-up that is always asked" in response["do"]
    assert "one at a time" in response["do"] and "after the last reply" in response["do"]
    assert store.get("calls", call[1])["answers"] == {}


def test_after_one_follow_up_only_the_other_is_named(store, call, strict):
    exchange(strict, "theek hoon")
    exchange(strict)
    response = strict.handle("record_answer", OK)
    assert response["ask_next"] == ALWAYS_ASKED["feeling"][1:]
    assert store.get("calls", call[1])["answers"] == {}


def test_ok_is_accepted_once_every_follow_up_has_a_reply(store, call, strict):
    exchange(strict, "theek hoon")
    assert "error" in strict.handle("record_answer", OK)
    exchange(strict)
    exchange(strict)
    assert strict.handle("record_answer", OK) == {"result": "recorded"}
    assert store.get("calls", call[1])["answers"]["feeling"]["value"] == "ok"


def test_a_reply_heard_in_pieces_is_one_reply(store, call, strict):
    strict.add_transcript("resident", "theek")
    strict.add_transcript("resident", " hoon")
    strict.add_transcript("resident", " bilkul")
    assert strict.handle("record_answer", OK)["ask_next"] == ALWAYS_ASKED["feeling"]


def test_the_most_worrying_answer_is_never_held_back_for_follow_ups(store, call, strict):
    exchange(strict, "saans nahi aa rahi")
    response = strict.handle("record_answer", {"question_id": "feeling", "value": "severe_symptoms"})
    assert response == {"result": "recorded"}
    assert store.get("calls", call[1])["answers"]["feeling"]["value"] == "severe_symptoms"


@pytest.mark.parametrize("value", ["mild_symptoms", "unclear"])
def test_any_other_answer_waits_for_the_follow_ups_everyone_is_asked(store, call, strict, value):
    # Heard on a real call (13 October): "unclear" was recorded at once, and no question about
    # signs was ever asked.
    exchange(strict, "pata nahi")
    response = strict.handle("record_answer", {"question_id": "feeling", "value": value})
    assert response["error"] == "follow-ups not asked"
    assert response["ask_next"] == ALWAYS_ASKED["feeling"]
    assert store.get("calls", call[1])["answers"] == {}
    exchange(strict)
    exchange(strict)
    assert strict.handle("record_answer", {"question_id": "feeling", "value": value}) == {"result": "recorded"}
    assert store.get("calls", call[1])["answers"]["feeling"]["value"] == value


def test_a_worrying_answer_that_was_held_back_is_not_lost_if_the_call_ends_there(store, call, strict):
    exchange(strict, "chakkar aa raha hai")
    held = strict.handle("record_answer", {"question_id": "feeling", "value": "mild_symptoms", "quote": "chakkar"})
    assert held["error"] == "follow-ups not asked"
    strict.flush()  # the person hung up before the follow-ups
    answer = store.get("calls", call[1])["answers"]["feeling"]
    assert (answer["value"], answer["quote"]) == ("mild_symptoms", "chakkar")


def test_a_worrying_answer_that_was_held_back_is_not_replaced_by_a_better_one(store, call, strict):
    exchange(strict, "chakkar aa raha hai")
    strict.handle("record_answer", {"question_id": "feeling", "value": "mild_symptoms", "quote": "chakkar"})
    exchange(strict)
    exchange(strict)
    assert strict.handle("record_answer", OK)["result"] in ("recorded", "kept_earlier_answer")
    assert store.get("calls", call[1])["answers"]["feeling"]["value"] == "mild_symptoms"


def test_a_later_question_is_refused_while_follow_ups_are_owed(store, call, strict):
    exchange(strict, "theek hoon")  # the reply to "how are you feeling"
    exchange(strict, "haan piya", "Paani piya?")  # the agent has gone on to the water question
    water = {"question_id": "fluids_food", "value": "enough"}
    response = strict.handle("record_answer", water)
    assert response["error"] == "follow-ups not asked"
    assert response["finish_first"] == "feeling"
    assert response["ask_next"] == ALWAYS_ASKED["feeling"]  # the reply about water is not one about signs
    assert "feeling" in response["do"] and "then ask this question again" in response["do"]
    assert store.get("calls", call[1])["answers"] == {}
    again = strict.handle("record_answer", OK)  # nor does it let the first question through early
    assert again["error"] == "follow-ups not asked" and again["ask_next"] == ALWAYS_ASKED["feeling"]

    exchange(strict)
    exchange(strict)
    assert strict.handle("record_answer", OK) == {"result": "recorded"}
    # the reply about water was used up: the question is asked again, and then recorded
    assert strict.handle("record_answer", water)["error"] == "no answer heard"
    exchange(strict, "haan piya", "Paani piya?")
    assert strict.handle("record_answer", water) == {"result": "recorded"}
    assert set(store.get("calls", call[1])["answers"]) == {"feeling", "fluids_food"}


def test_the_most_worrying_answer_to_a_later_question_is_recorded_at_once(store, call, strict):
    exchange(strict, "theek hoon")
    exchange(strict, "ghar mein paani hi nahi hai", "Paani piya?")
    assert strict.handle("record_answer", {"question_id": "fluids_food", "value": "none"}) == {"result": "recorded"}


def test_a_later_question_cannot_be_stalled_either_and_the_one_skipped_is_then_let_go(store, call, strict):
    exchange(strict, "theek hoon")
    exchange(strict, "haan piya", "Paani piya?")
    water = {"question_id": "fluids_food", "value": "enough"}
    assert strict.handle("record_answer", water)["error"] == "follow-ups not asked"
    assert strict.handle("record_answer", water)["error"] == "follow-ups not asked"
    assert strict.handle("record_answer", water) == {"result": "recorded", "accepted_after_refusals": 2}
    exchange(strict, "haan, beta hai", "Koi saath hai?")
    assert strict.handle("record_answer", {"question_id": "company", "value": "with_someone"}) == {"result": "recorded"}
    assert set(store.get("calls", call[1])["answers"]) == {"fluids_food", "company"}


# --- topics in any order ---------------------------------------------------------------------
# After test calls (15 October): the call follows the person after the opening topic, so only
# the opening topic, and a topic already under way, hold the others back.

COLD_ALWAYS = {**ALWAYS_ASKED, "shelter": ["How are you keeping the room warm?"]}
COLD_ORDER = ["feeling", "fluids_food", "company", "shelter", "exposure", "medicine", "help"]


@pytest.fixture
def cold(store, call, now):
    tools = CallTools(store, call[1], clock=lambda: now, always_asked=COLD_ALWAYS, order=COLD_ORDER)
    for _ in range(3):
        exchange(tools)
    assert tools.handle("record_answer", OK) == {"result": "recorded"}
    return tools


def test_a_topic_listed_later_may_come_before_one_with_follow_ups(store, call, cold):
    # an outdoor worker is asked about going out before blankets: not held back for shelter
    exchange(cold, "subah chaar baje doodh dene jaata hoon", "Aaj subah bahar jaana hai?")
    assert cold.handle("record_answer", {"question_id": "exposure", "value": "must_go_out"}) == {"result": "recorded"}
    exchange(cold, "dawai hai", "Dil ki dawai hai?")
    assert cold.handle("record_answer", {"question_id": "medicine", "value": "has"}) == {"result": "recorded"}
    assert set(store.get("calls", call[1])["answers"]) == {"feeling", "exposure", "medicine"}


def test_a_topic_under_way_holds_the_others_back_until_it_is_finished(store, call, cold):
    exchange(cold, "kambal hai", "Kambal hai?")
    assert cold.handle("record_answer", {"question_id": "shelter", "value": "adequate"})["ask_next"] == COLD_ALWAYS["shelter"]
    exchange(cold, "haan ja raha hoon", "Bahar jaana hai?")  # the agent moved on without the follow-up
    response = cold.handle("record_answer", {"question_id": "exposure", "value": "staying_in"})
    assert response["finish_first"] == "shelter"
    exchange(cold, "angithi jalate hain", "Kamra garam kaise rakhte hain?")
    assert cold.handle("record_answer", {"question_id": "shelter", "value": "adequate"}) == {"result": "recorded"}


def test_without_the_order_of_the_questions_only_the_question_itself_is_held_back(store, call, now):
    tools = CallTools(store, call[1], clock=lambda: now, always_asked=ALWAYS_ASKED)
    exchange(tools, "theek hoon")
    exchange(tools, "haan piya", "Paani piya?")
    assert tools.handle("record_answer", {"question_id": "fluids_food", "value": "enough"}) == {"result": "recorded"}


# --- an answer needs a reply to its own question ------------------------------------------
# Heard on a real call (13 October): the husband said the resident was resting; the agent then
# asked how she felt and recorded an answer before anyone had replied to that.

UNCLEAR = {"question_id": "feeling", "value": "unclear"}


def agent_turn(tools, text, heard_during=None):
    """One spoken turn of the agent, from its first word to its end."""
    tools.agent_turn_began()
    tools.add_transcript("agent", text)
    if heard_during:
        tools.add_transcript("resident", heard_during)
    tools.agent_turn_ended()


def test_a_reply_heard_before_the_question_was_asked_does_not_answer_it(store, call, tools):
    said(tools, "nahi, woh abhi aaram kar rahi hain")
    agent_turn(tools, "Theek hai. Woh abhi kaisa mehsoos kar rahi hain?")
    response = tools.handle("record_answer", UNCLEAR)
    assert response["error"] == "no answer heard"
    said(tools, "pata nahi")
    assert tools.handle("record_answer", UNCLEAR) == {"result": "recorded"}


def test_a_reply_heard_while_the_agent_was_still_speaking_counts(store, call, tools):
    agent_turn(tools, "Aapki tabiyat kaisi hai?", heard_during="theek hoon")
    assert tools.handle("record_answer", OK) == {"result": "recorded"}


def test_an_agent_that_speaks_first_and_records_in_the_same_turn_is_not_refused(store, call, tools):
    # What the agent does on most turns: it hears the reply, asks the next question, and only
    # then records the last answer. The reply is still the one to the question before.
    agent_turn(tools, "Aapki tabiyat kaisi hai?")
    said(tools, "theek hoon")
    tools.agent_turn_began()
    tools.add_transcript("agent", "Achha. Paani piya?")
    assert tools.handle("record_answer", OK) == {"result": "recorded"}
    tools.agent_turn_ended()
    assert tools.handle("record_answer", {"question_id": "fluids_food", "value": "enough"})["error"] == "no answer heard"


def test_the_reply_that_says_who_is_speaking_is_not_an_answer(store, call, tools):
    said(tools, "nahi, main unka pati bol raha hoon")
    assert tools.handle("set_respondent", {"type": "family"}) == {"result": "recorded"}
    assert tools.handle("record_answer", UNCLEAR)["error"] == "no answer heard"
    assert store.get("calls", call[1])["answers"] == {}


def test_a_red_answer_needs_no_reply_after_the_question(store, call, tools):
    said(tools, "saans nahi aa rahi")
    agent_turn(tools, "Kya aapko saans lene mein takleef hai?")
    response = tools.handle("record_answer", {"question_id": "feeling", "value": "severe_symptoms"})
    assert response == {"result": "recorded"}


# --- a reply that turns out to be the agent's own echo is taken back ---------------------


def test_a_dropped_reply_leaves_no_trace(store, call, tools, now):
    tools.add_transcript("agent", "Kya aaj aapne paani piya hai?")
    tools.add_transcript("resident", "kya aaj")
    tools.add_transcript("resident", " aapne paani")
    tools.drop_reply()
    assert tools.transcript_if_changed() == [{"role": "agent", "text": "Kya aaj aapne paani piya hai?", "at": now}]
    assert tools.handle("record_answer", {"question_id": "fluids_food", "value": "enough"})["error"] == "no answer heard"


def test_dropping_a_reply_keeps_what_the_person_said_before_it(store, call, tools):
    said(tools, "haan")
    assert tools.handle("record_answer", OK) == {"result": "recorded"}
    tools.add_transcript("resident", " kya aaj aapne")  # joins the line before it in the transcript
    tools.drop_reply()
    assert [entry["text"] for entry in tools.transcript_if_changed()] == ["haan"]
    assert tools.handle("record_answer", {"question_id": "fluids_food", "value": "enough"})["error"] == "no answer heard"


def test_dropping_a_reply_gives_back_the_one_before_it(store, call, strict):
    exchange(strict, "theek hoon")
    exchange(strict)
    strict.add_transcript("agent", "Ulti?")
    strict.add_transcript("resident", "ulti ya kamzori")
    strict.drop_reply()
    assert strict.handle("record_answer", OK)["ask_next"] == ALWAYS_ASKED["feeling"][1:]  # two replies, not three


def test_dropping_when_there_is_no_reply_does_nothing(store, call, tools):
    tools.add_transcript("agent", "Namaste")
    tools.drop_reply()
    tools.drop_reply()
    assert [entry["text"] for entry in tools.transcript_if_changed()] == ["Namaste"]


def test_a_model_that_will_not_ask_cannot_stall_the_call(store, call, strict):
    exchange(strict, "theek hoon")
    assert strict.handle("record_answer", OK)["error"] == "follow-ups not asked"
    assert strict.handle("record_answer", OK)["error"] == "follow-ups not asked"
    assert strict.handle("record_answer", OK) == {"result": "recorded", "accepted_after_refusals": 2}
    assert store.get("calls", call[1])["answers"]["feeling"]["value"] == "ok"


def test_a_question_with_no_follow_up_for_everyone_is_recorded_as_before(store, call, strict):
    for _ in range(3):
        exchange(strict)
    assert strict.handle("record_answer", OK) == {"result": "recorded"}
    exchange(strict, "haan, parivar hai")
    response = strict.handle("record_answer", {"question_id": "company", "value": "with_someone"})
    assert response == {"result": "recorded"}


def test_nothing_is_held_back_when_a_stranger_answered(store, call, strict):
    strict.handle("set_respondent", {"type": "other"})
    exchange(strict, "pata nahi")
    assert strict.handle("record_answer", OK) == {"result": "recorded"}


def test_the_replies_are_counted_afresh_for_each_question(store, call, now):
    always = {**ALWAYS_ASKED, "shelter": ["How are you keeping the room warm?"]}
    tools = CallTools(store, call[1], clock=lambda: now, always_asked=always)
    for _ in range(3):
        exchange(tools)
    assert tools.handle("record_answer", OK) == {"result": "recorded"}
    exchange(tools, "kambal hai")
    response = tools.handle("record_answer", {"question_id": "shelter", "value": "adequate"})
    assert response["ask_next"] == ["How are you keeping the room warm?"]
    exchange(tools, "heater hai")
    assert tools.handle("record_answer", {"question_id": "shelter", "value": "adequate"}) == {"result": "recorded"}


def test_an_answer_nobody_gave_is_still_refused_first(store, call, strict):
    assert strict.handle("record_answer", OK)["error"] == "no answer heard"


def test_without_follow_ups_to_enforce_ok_after_one_reply_is_recorded_as_before(store, call, tools):
    exchange(tools, "theek hoon")
    assert tools.handle("record_answer", OK) == {"result": "recorded"}


def test_a_held_back_answer_is_logged_without_the_persons_words(store, call, strict, caplog):
    exchange(strict, "theek hoon")
    with caplog.at_level(logging.WARNING, logger="app.call.tools"):
        strict.handle("record_answer", OK)
    assert call[1] in caplog.text and "feeling" in caplog.text
    assert "theek hoon" not in caplog.text


def test_an_emergency_call_cannot_end_before_the_line_is_spoken(tools):
    tools.agent_turn_began()
    tools.handle("flag_emergency", {"reason": "chest pain"})
    refused = tools.handle("end_call", {"outcome": "completed"})
    assert refused["error"] == "say the emergency line first" and "108" in refused["say"]
    assert tools.ended is None
    tools.add_transcript("agent", "Please call 108 now.")
    assert tools.handle("end_call", {"outcome": "completed"}) == {"result": "ending"}


def test_the_line_said_in_the_same_turn_before_the_flag_counts(tools):
    tools.agent_turn_began()
    tools.add_transcript("agent", "Call 108 now, a health worker is being alerted.")
    tools.handle("flag_emergency", {"reason": "fainted"})
    assert tools.handle("end_call", {"outcome": "completed"}) == {"result": "ending"}


def test_audio_counts_as_spoken(tools):
    tools.agent_turn_began()
    tools.handle("flag_emergency", {"reason": "confused"})
    tools.agent_spoke()
    assert tools.handle("end_call", {"outcome": "completed"}) == {"result": "ending"}


def test_the_refusal_live_looks_for_is_the_one_end_call_gives(tools):
    # live.py's closing silence matches the refusal by its text: a change of wording must show here.
    import app.call.live as live
    from app.call.tools import EMERGENCY_LINE_FIRST

    tools.agent_turn_began()
    tools.handle("flag_emergency", {"reason": "chest pain"})
    refused = tools.handle("end_call", {"outcome": "completed"})
    assert refused["error"] == live.EMERGENCY_LINE_FIRST["error"] == EMERGENCY_LINE_FIRST["error"]
    assert refused["error"] in live.REFUSALS


def test_speech_from_an_earlier_turn_does_not_count(tools):
    tools.agent_turn_began()
    tools.add_transcript("agent", "How are you feeling?")
    tools.add_transcript("resident", "Chest pain.")
    tools.agent_turn_began()
    tools.handle("flag_emergency", {"reason": "chest pain"})
    assert tools.handle("end_call", {"outcome": "completed"})["error"] == "say the emergency line first"


def test_a_reply_of_tool_calls_alone_after_a_spoken_turn_owes_the_line(tools):
    # As the callers feed it: the spoken turn began and ended; the next reply is a flag and an
    # end_call with nothing said, so no turn begins.
    tools.agent_turn_began()
    tools.add_transcript("agent", "How are you feeling?")
    tools.agent_turn_ended()
    tools.add_transcript("resident", "Chest pain.")
    tools.handle("flag_emergency", {"reason": "chest pain"})
    refused = tools.handle("end_call", {"outcome": "emergency"})
    assert refused["error"] == "say the emergency line first" and "108" in refused["say"]
    assert tools.ended is None
    tools.agent_turn_began()
    tools.add_transcript("agent", "Abhi 108 par call kijiye.")
    assert tools.handle("end_call", {"outcome": "emergency"}) == {"result": "ending"}


def test_a_call_without_an_emergency_ends_as_before(tools):
    assert tools.handle("end_call", {"outcome": "completed"}) == {"result": "ending"}
