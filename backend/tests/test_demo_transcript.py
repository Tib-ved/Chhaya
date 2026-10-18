from datetime import timedelta

from tests.demo_transcript import scripted_transcript

FULL = {"feeling": "mild_symptoms", "fluids_food": "low", "company": "alone", "shelter": "adequate", "exposure": "staying_in", "help": "none"}


def test_a_scripted_call_reads_as_a_conversation(now):
    lines = scripted_transcript({"name": "Lata Shekhawat", "language": "hi"}, FULL, {"feeling": "sir mein dard hai"}, False, "self", now)
    assert [line["role"] for line in lines[:4]] == ["agent", "resident", "agent", "resident"]
    assert "Lata Shekhawat" in lines[0]["text"]
    assert lines[3]["text"] == "sir mein dard hai"  # the person's own words where the script has them
    assert lines[-1]["role"] == "agent"
    assert len(lines) == 2 + 2 * len(FULL) + 1
    assert [line["at"] for line in lines] == sorted(line["at"] for line in lines)
    assert lines[-1]["at"] - lines[0]["at"] < timedelta(minutes=3)


def test_an_emergency_ends_the_script_with_the_108_line(now):
    lines = scripted_transcript({"name": "A B", "language": "en"}, {"feeling": "severe_symptoms"}, {}, True, "self", now)
    assert "108" in lines[-1]["text"] and len(lines) == 5


def test_marwari_speakers_are_scripted_in_hindi_and_others_not_at_all(now):
    assert scripted_transcript({"name": "A B", "language": "mwr"}, FULL, {}, False, "self", now)
    assert scripted_transcript({"name": "A B", "language": "gu"}, FULL, {}, False, "self", now) == []
    assert scripted_transcript({"name": "A B", "language": "hi"}, {}, {}, False, "self", now) == []


def test_the_scripted_agent_asks_one_question_in_a_turn():
    from tests.demo_transcript import LINES

    for language, words in LINES.items():
        spoken = [words["greeting"], words["emergency"], words["close"], *words["ask"].values()]
        for line in spoken:
            assert line.count("?") <= 1, (language, line)
