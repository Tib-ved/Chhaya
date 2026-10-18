"""Loads the human-editable call guidance and turns it into the agent's instructions."""
import re
from functools import lru_cache
from pathlib import Path
from typing import get_args

import yaml

from app.models import Language
from app.triage import ALLOWED_VALUES

GUIDANCE_DIR = Path(__file__).parent / "guidance"
END_CALL_OUTCOMES = ("completed", "declined", "wrong_person", "unable_to_understand", "emergency", "no_person")


@lru_cache
def _read(relative: str) -> dict:
    path = GUIDANCE_DIR / relative
    if not path.is_file():
        raise KeyError(relative)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_conduct() -> dict:
    return _read("conduct.yaml")


def _plain_name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[a-z]{2,10}", name):
        raise KeyError(name)
    return name


def _one_line(text: str) -> str:
    """Flatten whitespace and cut to 80 characters, for text that comes from the database."""
    return re.sub(r"\s+", " ", str(text)).strip()[:80]


# The grammatical forms the agent uses for itself, by the gender it speaks as.
SPEAKER_FORMS = {"woman": "feminine", "man": "masculine"}
# The examples in the locale files are written in the feminine.
# What a person who cannot speak can press, by the call's channel (a call's "channel" field).
HELP_BUTTONS = {"phone": "any button on their phone", "browser": "the help button on the screen"}
FEMININE_EXAMPLE_NOTE = "(The example uses feminine forms for the speaker; say them in the masculine.)"


def load_hazard(hazard: str) -> dict:
    return _read(f"{_plain_name(hazard)}.yaml")


def load_locale(language: str) -> dict:
    return _read(f"locales/{_plain_name(language)}.yaml")


def questions_for(hazard: str, has_conditions: bool) -> list[dict]:
    return [
        question
        for question in load_hazard(hazard)["questions"]
        if question.get("only_if") != "has_conditions" or has_conditions
    ]


def supported_languages() -> list[str]:
    """The names of the languages a call can be held in: those the data model allows."""
    return [load_locale(code)["language"] for code in get_args(Language)]


def _wording(item: dict, language: str) -> str:
    """A question as the agent is given it: in English on a call in English, otherwise in Hindi.
    The English in the script is the meaning; on a real call in Marwari it was read out as written."""
    return item["ask"] if language == "en" else item["hi"]


def question_order(hazard: str, has_conditions: bool) -> list[str]:
    """The ids of the questions, in the order the script asks them."""
    return [question["id"] for question in questions_for(hazard, has_conditions)]


def always_asked(hazard: str, has_conditions: bool, language: str) -> dict[str, list[str]]:
    """The follow-ups that everyone is asked, by question id, for the questions that have any,
    in the words the agent was given for a call in `language`.

    The call's tool handler enforces them: the script alone was not enough (on a real call the
    agent recorded "ok" after the first reply and asked neither)."""
    asked = {
        question["id"]: [_wording(item, language) for item in question.get("then", []) if item["when"] == "always"]
        for question in questions_for(hazard, has_conditions)
    }
    return {question_id: asks for question_id, asks in asked.items() if asks}


def _flat(text: str) -> str:
    return " ".join(str(text).split())


def _one_per_turn(asks: list[str]) -> str:
    quoted = "; ".join(f'"{ask}"' for ask in asks)
    return f", one per turn: {quoted}" if len(asks) > 1 else f": {quoted}"


def _profile(resident: dict, conduct: dict, hazard: dict) -> tuple[list[str], dict[str, list[str]]]:
    """What the records say about the resident: the facts, and hints under the topics they
    shape. Of the emergency contact, only that there is one: never a name or a number."""
    profile = conduct["profile"]
    names = conduct["condition_names"]
    conditions = ", ".join(_one_line(names.get(str(c), c)) for c in resident.get("conditions") or [])
    facts: list[str] = []
    hints: dict[str, list[str]] = {}
    if resident.get("lives_alone"):
        alone = profile["lives_alone"]
        facts.append(alone["fact"])
        example = alone["ask"] if resident["language"] == "en" else alone["hi"]
        hints["company"] = [f'{alone["hint"]} "{example}"']
    if resident.get("outdoor_worker"):
        facts.append(profile["outdoor_worker"]["fact"])
        hints["exposure"] = [hazard["outdoor_worker"]]
    if conditions:
        facts.append(profile["conditions"]["fact"].format(conditions=conditions))
        hints["medicine"] = [profile["conditions"]["hint"].format(conditions=conditions)]
    if resident.get("emergency_contact"):
        facts.append(_flat(profile["emergency_contact"]["fact"]))
    return facts, hints


def build_instruction(
    resident: dict,
    hazard: str,
    peak_temp_c: float | None = None,
    facility_name: str | None = None,
    speaks_as: str = "woman",
    channel: str = "phone",
) -> str:
    """The agent's instruction: who it is, the language, the person, how to talk, the rules and
    the topics to cover. Kept short: a long instruction delays the first spoken turn."""
    forms = SPEAKER_FORMS[speaks_as]
    conduct = load_conduct()
    guidance = load_hazard(hazard)
    locale = load_locale(resident["language"])
    language = resident["language"]
    has_conditions = bool(resident.get("conditions"))
    spoken = locale["language"]
    # Google's recommended wording for holding a Live model to one language.
    respond_in = f"RESPOND IN {spoken.upper()}. YOU MUST RESPOND UNMISTAKABLY IN {spoken.upper()}."

    lines = [
        _flat(conduct["identity"]),
        # Must match the voice: on real calls the agent called itself a woman in a man's voice.
        f"You speak as a {speaks_as}. In Hindi, Marwari, Marathi and Gujarati always use {forms} "
        "forms for yourself, the closing line included.",
        "",
        respond_in,
        f"LANGUAGE: {spoken}. {_flat(locale['speak'])}",
    ]
    if locale.get("connectors"):
        lines.append(f"Now and then, a natural word such as: {locale['connectors']}.")
    if locale.get("greeting_example"):
        lines.append(f"An example of how to open the call: {locale['greeting_example']}")
        if speaks_as == "man":
            lines.append(FEMININE_EXAMPLE_NOTE)
    lines += ["", f"TODAY: {guidance['context']}"]
    if peak_temp_c is not None:
        if hazard == "cold":
            lines.append(f"The forecast for tonight falls to about {round(peak_temp_c)} degrees Celsius.")
        else:
            lines.append(f"The forecast for today reaches about {round(peak_temp_c)} degrees Celsius.")
    lines.append(f"YOU ARE CALLING: {_one_line(resident['name'])}, aged {resident['age']}.")
    facts, hints = _profile(resident, conduct, guidance)
    if facts:
        lines.append(_flat(conduct["profile"]["heading"]))
        lines += [f"- {fact}" for fact in facts]
    if facility_name:
        lines.append(
            f"Nearest place for help or shelter: {_one_line(facility_name)}. Mention it only if they have "
            "nowhere suitable to stay or ask where to go. Never tell someone who reports symptoms to travel there alone."
        )

    lines += ["", f"HOW TO TALK: {_flat(conduct['style'])}"]
    lines += ["", "RULES YOU MUST FOLLOW:"]
    languages = ", ".join(supported_languages())
    help_button = HELP_BUTTONS.get(channel, HELP_BUTTONS["phone"])
    lines += [
        f"- {_flat(rule['text']).replace('{languages}', languages).replace('{help_button}', help_button)}"
        for rule in conduct["rules"]
    ]
    lines += ["", f"WHO IS ON THE LINE: {_flat(conduct['respondent'])}"]

    lines += [
        "",
        "TOPICS TO COVER. Always open with feeling, then let the answer choose what comes next. "
        f"{_flat(guidance['after_feeling'])} Otherwise follow the person: any order is fine, with help "
        "last. Cover every topic before you close: the health worker needs them all. If a reply "
        "already answers a later topic, do not ask it from scratch: when you reach it, confirm it in "
        "a few words and record it after they reply. Record each topic with record_answer, its id "
        "and one listed value.",
    ]
    if language == "hi":
        lines.append("The wording below is a guide: say it in your own natural Hindi.")
    elif language != "en":
        lines.append(
            f"The wording below is in Hindi, to give the meaning. Say it in {spoken}, in your own "
            "natural words. Never read it out in English."
        )
    for question in questions_for(hazard, has_conditions):
        topic = question["id"]
        wording = (locale.get("phrasing") or {}).get(topic) or _wording(question, language)
        lines.append(f'* {topic}: "{wording}"')
        lines += [f"    {hint}" for hint in hints.get(topic, [])]
        then = question.get("then") or []
        steps = [_wording(item, language) for item in then if item["when"] == "always"]
        if steps:
            lines.append(
                f"    Before you record {topic}, also ask{_one_per_turn(steps)}. record_answer is "
                "refused until you have, except for the most worrying answer."
            )
        conditional: dict[str, list[str]] = {}
        for item in then:
            if item["when"] != "always":
                conditional.setdefault(item["when"], []).append(_wording(item, language))
        lines += [f"    If {when}, also ask{_one_per_turn(asks)}" for when, asks in conditional.items()]
        lines += [f"    {value} = {meaning}" for value, meaning in question["values"].items()]
        scenarios = [*(conduct.get("scenarios") or {}).get(topic, []), *question.get("scenarios", [])]
        lines += [f"    - {_flat(scenario)}" for scenario in scenarios]

    lines += [
        "",
        "EMERGENCY SIGNS (any one means: follow the emergency rule now): "
        + "; ".join(guidance["emergency_signs"])
        + ".",
    ]
    if locale.get("emergency_line"):
        lines.append(f"The emergency line: {locale['emergency_line']}")
        if speaks_as == "man":
            lines.append(FEMININE_EXAMPLE_NOTE)
    else:
        lines.append(
            f"The emergency line, in {spoken}: tell them to call 108 now, or to ask someone nearby "
            "to call, and that a health worker is being alerted."
        )

    lines += ["", "ADVICE YOU MAY GIVE (at most two, only from this list):"]
    lines += [f"- {item}" for item in guidance["advice"]]
    special = guidance.get("special") or []
    if special:
        lines += ["", "SAFETY WARNINGS (give at once when they apply; not counted in the two):"]
        lines += [f"- {item}" for item in special]

    lines += [
        "",
        "CLOSING LINE (only after a normal finish; not after a wrong number, a refusal, an emergency, "
        "or with someone who is not family). Say it in your own words; promise no visit and never "
        f"imply they are fine: {_flat(conduct['closing'])}",
    ]
    # Said last because it is what a long call loses first.
    lines += [
        "",
        f"REMEMBER: {respond_in} Ask one question at a time and wait for the answer; record only what "
        "the person actually said.",
    ]
    return "\n".join(lines)


def opening_prompt(resident: dict) -> str:
    return (
        "The call has just been answered. Greet the person, say that you are Chhaya, an AI agent "
        "calling on behalf of the district health office because of the weather warning, and ask "
        f"whether you are speaking with {_one_line(resident['name'])}."
    )


TOOL_DECLARATIONS = [
    {
        "name": "set_respondent",
        "description": "Record who is speaking on the call.",
        "parameters": {
            "type": "object",
            "properties": {"type": {"type": "string", "enum": ["self", "family", "other"]}},
            "required": ["type"],
        },
    },
    {
        "name": "record_answer",
        "description": (
            "Record the answer the person has just given to one question, using one of that question's "
            "listed values. Call it only after they have replied to that question."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question_id": {"type": "string", "enum": sorted(ALLOWED_VALUES)},
                "value": {"type": "string"},
                "quote": {"type": "string", "description": "A few of the person's own words."},
            },
            "required": ["question_id", "value"],
        },
    },
    {
        "name": "flag_emergency",
        "description": "Call at once when any emergency sign is mentioned. A health worker is alerted.",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
    {
        "name": "end_call",
        "description": "End the call after saying goodbye.",
        "parameters": {
            "type": "object",
            "properties": {"outcome": {"type": "string", "enum": list(END_CALL_OUTCOMES)}},
            "required": ["outcome"],
        },
    },
]
