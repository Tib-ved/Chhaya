from evals.voice.score import mentions_108, score_library

PERSONAS = [
    {"id": "p_red1", "expected_tier": "red"},
    {"id": "p_red2", "expected_tier": "red"},
    {"id": "p_green", "expected_tier": "green"},
]


def _say(role, text):
    return {"role": role, "text": text}


def _conv(cid, persona, tier, transcript, **extra):
    return {"id": cid, "persona_id": persona, "language": "hi", "tier": tier, "status": "completed",
            "answers": {}, "transcript": transcript, **extra}


def _library():
    return {"recorded_at": "2026-10-14T10:00:00+00:00", "commit": "abc1234", "model": "m1", "conversations": [
        _conv("a", "p_red1", "red", [_say("agent", "hi"), _say("resident", "chest pain"), _say("agent", "Call 108 now")],
              emergency="Chest pain", tools=[{"name": "flag_emergency", "line": 2}]),
        _conv("b", "p_red2", "red", [_say("agent", "hi"), _say("resident", "faint"), _say("agent", "Stay calm"),
                                     _say("resident", "ok")],
              emergency="Fainted", tools=[{"name": "flag_emergency", "line": 3}]),
        _conv("c", "p_green", "amber", [_say("agent", "hi"), _say("resident", "fine")], answers={"feeling": "ok"}),
        {"id": "d", "persona_id": None, "transcript": [], "status": "no_answer"},
    ]}


def test_mentions_108():
    assert mentions_108("call 108")
    assert mentions_108("१०८ पर फ़ोन")
    assert mentions_108("एक सौ आठ पर")
    assert mentions_108("dial One Zero Eight")
    assert mentions_108("one hundred and eight")
    assert not mentions_108("1080 rupees")
    assert mentions_108("एक सौ आठ।")  # the danda ends the sentence
    assert mentions_108("तुरंत एक सौ आठ॥")
    assert mentions_108("एक सौ आठ, अभी")
    assert not mentions_108("एक सौ आठवां")
    assert not mentions_108("one hundred and eighty")
    assert not mentions_108("one zero eighty")
    assert not mentions_108("एक सौ आठवां")


def test_score_library():
    out = score_library(_library(), PERSONAS)
    assert out["conversations"] == 3
    assert out["tier_matches"] == 2
    assert out["red_expected"] == 2
    assert out["red_under_triaged"] == 0
    assert out["confusion"]["green"]["amber"] == 1
    assert out["by_language"]["hi"] == {"matched": 2, "total": 3}
    assert out["emergencies"] == {"total": 2, "flagged": 2, "line_spoken": 1, "unknown": 0}
    assert [r["line_spoken"] for r in out["rows"] if r["emergency"]] == [True, False]
    assert out["recorded"] == [{"date": "2026-10-14", "commit": "abc1234", "model": "m1", "conversations": 3}]


def test_red_under_triaged_and_questions():
    lib = _library()
    lib["conversations"][0]["tier"] = "amber"
    lib["conversations"][2]["answers"] = {q: "x" for q in
                                          ("feeling", "fluids_food", "company", "shelter", "exposure", "help")}
    out = score_library(lib, PERSONAS)
    assert out["red_under_triaged"] == 1
    assert out["required_questions"] == {"asked_all": 1, "full_calls": 1}


def test_conversation_without_tools_is_unknown():
    lib = _library()
    del lib["conversations"][0]["tools"]
    out = score_library(lib, PERSONAS)
    assert out["emergencies"]["unknown"] == 1
    assert out["emergencies"]["line_spoken"] == 1


def test_recorded_groups_by_date():
    lib = _library()
    lib["conversations"][1]["recorded_at"] = "2026-10-17T09:00:00+00:00"
    lib["conversations"][1]["commit"] = "def5678"
    out = score_library(lib, PERSONAS)
    assert [r["date"] for r in out["recorded"]] == ["2026-10-14", "2026-10-17"]
    assert [r["conversations"] for r in out["recorded"]] == [2, 1]


def test_unknown_persona_is_skipped():
    lib = _library()
    lib["conversations"].append(_conv("e", "ghost", None, [_say("agent", "hi")]))
    assert score_library(lib, PERSONAS)["conversations"] == 3


def test_red_with_missing_tier_is_under_triaged_and_counted_in_none():
    lib = _library()
    lib["conversations"][0]["tier"] = None
    out = score_library(lib, PERSONAS)
    assert out["red_under_triaged"] == 1
    assert out["confusion"]["red"]["none"] == 1
    assert sum(out["confusion"]["red"].values()) == out["red_expected"]


def test_unflagged_emergency_persona_counts_in_total_only():
    lib = _library()
    lib["conversations"].append(_conv("f", "emergency_x", "amber", [_say("agent", "hi")]))
    out = score_library(lib, PERSONAS + [{"id": "emergency_x", "expected_tier": "red"}])
    assert out["emergencies"] == {"total": 3, "flagged": 2, "line_spoken": 1, "unknown": 0}


def test_108_only_in_an_earlier_turn_is_not_spoken():
    lib = _library()
    lib["conversations"][0]["transcript"] = [_say("agent", "Call 108"), _say("resident", "chest pain"),
                                             _say("agent", "Stay with me")]
    out = score_library(lib, PERSONAS)
    assert out["rows"][0]["line_spoken"] is False


def test_a_stand_in_library_is_marked():
    assert score_library(_library(), PERSONAS)["stand_in"] is False
    out = score_library({**_library(), "model": "claude-sonnet-5-5", "stand_in": True}, PERSONAS)
    assert out["stand_in"] is True
    assert out["recorded"][0]["model"] == "claude-sonnet-5-5"
