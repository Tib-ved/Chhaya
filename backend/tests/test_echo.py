import pytest

from app.call.echo import is_echo, normalise

# Lines from the calls of 13 October: on each, some "resident" lines were the agent's own
# words coming back down the line, and answers were recorded straight after them.

ECHOES = [
    ("काई Sunita जी आज पाणी पी लियो है?", "का Sunita जी आज पाणी पी लियो है?"),
    ("क्या आपके पास पंखा या रहने के लिए कोई ठंडी जगह है?", "क्या आपके पास पंखा या रहने के लिए कोई ठंडी जगह है?"),
    ("Kamla ji, aaj thhare ghara koin hai?", "Aaj thanai ghara koin hai?"),
]
REPLIES = [
    ("क्या आज आपके साथ घर पर कोई है?", "नहीं।"),
    # a short answer that reuses the question's words must stay a reply
    ("क्या आप आज पानी पी रहे हैं?", "हाँ, पानी पी रहे हैं"),
    ("क्या आज घर पर आपके साथ कोई है?", "घर पर कोई नहीं है"),
    ("क्या आपके पास पंखा या रहने की कोई ठंडी जगह है?", "पंखा है पर बिजली नहीं आ रही"),
    ("अभी आपकी तबीयत कैसी है?", "तबीयत ठीक नहीं है, चक्कर आ रहा है"),
    ("Kya aaj aapne paani piya hai?", "haan, paani piya hai"),
    ("Kya aaj aapne paani piya hai?", "aaj paani piya nahi"),
]


@pytest.mark.parametrize("agent, heard", ECHOES)
def test_the_agents_own_words_coming_back_are_an_echo(agent, heard):
    assert is_echo(heard, [agent])


@pytest.mark.parametrize("agent, heard", REPLIES)
def test_a_reply_is_not_an_echo_even_when_it_reuses_the_questions_words(agent, heard):
    assert not is_echo(heard, [agent])


def test_an_echo_is_found_in_any_of_the_agents_turns_and_inside_a_longer_one():
    greeting = "नमस्ते। मैं ज़िला स्वास्थ्य कार्यालय की ओर से एक स्वचालित सहायक बोल रही हूँ। क्या मैं Sunita जी से बात कर रही हूँ?"
    turns = [greeting, "क्या आज आपने पानी पिया है?"]
    assert is_echo("मैं ज़िला स्वास्थ्य कार्यालय की ओर से एक स्वचालित सहायक बोल रही हूँ", turns)
    assert is_echo(greeting, turns)
    assert not is_echo("हाँ मैं Sunita बोल रही हूँ", turns)


def test_fewer_than_three_words_are_never_called_an_echo():
    assert not is_echo("पानी पिया", ["क्या आज आपने पानी पिया है?"])
    assert not is_echo("है", ["क्या आज आपने पानी पिया है?"])
    assert not is_echo("", ["क्या आज आपने पानी पिया है?"])


def test_nothing_is_an_echo_of_nothing():
    assert not is_echo("क्या आज आपने पानी पिया है", [])
    assert not is_echo("क्या आज आपने पानी पिया है", ["", "   "])


def test_text_is_compared_without_case_punctuation_or_extra_spaces():
    assert normalise("  Kamla ji,  AAJ thhare ghara koin hai? ") == ["kamla", "ji", "aaj", "thhare", "ghara", "koin", "hai"]
    assert normalise("नहीं। हाँ, पानी!") == ["नहीं", "हाँ", "पानी"]
    assert is_echo("kamla JI aaj thhare... ghara koin hai", ["Kamla ji, aaj thhare ghara koin hai?"])


def test_words_put_in_another_order_are_not_an_echo():
    assert not is_echo("hai koin ghara thhare aaj", ["Kamla ji, aaj thhare ghara koin hai?"])
