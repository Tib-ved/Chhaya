"""Tells the agent's own voice, coming back down the line, from a reply.

On real calls (13 October) some lines transcribed as the person's were the agent's own words,
word for word, and answers were recorded straight after them. Pure text comparison: nothing
here is stored or logged."""
import unicodedata
from difflib import SequenceMatcher

# Fewer words than this are never called an echo: "haan" and "nahi" are in every question's
# answer, and a short reply must never be thrown away.
MIN_WORDS = 3
# A word heard slightly differently ("thhare" as "thanai") still counts as the agent's word,
# when the words on both sides of it are the agent's. At the start or the end of a line it has
# to be much closer: that is where a reply puts its own word ("haan, ...", "... nahi").
SIMILAR_WORD = 0.5
SIMILAR_WORD_AT_AN_END = 0.75
# How many words of its own a line may have and still be an echo: none in a short line, one in
# every eight of a long one. A reply that reuses the question's words has a word of its own
# ("haan, paani pi rahe hain"; "ghar par koi nahi hai"), and that is what keeps it a reply.
OWN_WORDS_PER = 8


def normalise(text: str) -> list[str]:
    """The words of `text`: lower-cased, with punctuation and extra spaces removed."""
    kept = "".join(
        " " if unicodedata.category(char)[0] in "PSZC" else char for char in unicodedata.normalize("NFC", text)
    )
    return kept.lower().split()


def _own_words(heard: list[str], spoken: list[str]) -> int:
    """How many of the heard words are neither the agent's, in order, nor close to the agent's
    words they stand in place of."""
    own = 0
    matcher = SequenceMatcher(None, spoken, heard, autojunk=False)
    for kind, spoken_from, spoken_to, heard_from, heard_to in matcher.get_opcodes():
        if kind == "insert":
            own += heard_to - heard_from
        elif kind == "replace":
            theirs, ours = heard[heard_from:heard_to], spoken[spoken_from:spoken_to]
            # Word against word, lined up from the start of the stretch they replace or from its
            # end, whichever fits better: a line that starts or ends inside the agent's
            # sentence replaces a longer stretch than itself.
            at_an_end = heard_from == 0 or heard_to == len(heard)
            close = SIMILAR_WORD_AT_AN_END if at_an_end else SIMILAR_WORD
            own += min(_unlike(theirs, ours, close), _unlike(theirs[::-1], ours[::-1], close))
    return own


def _unlike(theirs: list[str], ours: list[str], close: float) -> int:
    """How many of `theirs` are not close to the word of `ours` in the same place."""
    alike = sum(SequenceMatcher(None, their, our, autojunk=False).ratio() >= close for their, our in zip(theirs, ours))
    return len(theirs) - alike


def is_echo(heard: str, agent_turns: list[str]) -> bool:
    """Whether `heard` is the agent's own words from one of `agent_turns` coming back.

    It is when it has at least MIN_WORDS words and all of them (all but one in eight, in a long
    line) are that turn's words in the same order, or close to the words they stand in for."""
    words = normalise(heard)
    if len(words) < MIN_WORDS:
        return False
    allowed = len(words) // OWN_WORDS_PER
    return any(
        spoken and _own_words(words, spoken) <= allowed
        for spoken in (normalise(turn) for turn in agent_turns)
    )
