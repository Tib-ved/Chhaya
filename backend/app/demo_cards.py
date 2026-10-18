"""The characters a visitor can take a call as, and the languages they can take it in.

Every character is synthetic. The names and nudges are to be checked by a native speaker for
local plausibility. `offset` places the character's pin near the centre of their ward."""

CARDS: dict[str, dict] = {
    "alone_dizzy": {
        "id": "alone_dizzy",
        "name": "Kamla Devi",
        "name_hi": "कमला देवी",
        "age": 72,
        "lives_alone": True,
        "outdoor_worker": False,
        "conditions": ["diabetes"],
        "ward_id": "churu-ward-2",
        "offset": (0.0031, -0.0024),
        "role": "You are Kamla Devi, 72. You live alone and have diabetes.",
        "nudge": "You feel a little dizzy and have had very little water today.",
    },
    "must_work": {
        "id": "must_work",
        "name": "Ramesh Kumar",
        "name_hi": "रमेश कुमार",
        "age": 58,
        "lives_alone": False,
        "outdoor_worker": True,
        "conditions": ["heart"],
        "ward_id": "churu-ward-30",
        "offset": (-0.0027, 0.0035),
        "role": "You are Ramesh Kumar, 58, a construction labourer with a heart condition.",
        "nudge": "You feel fine, but you have to work outside today.",
    },
    "family_answers": {
        "id": "family_answers",
        "name": "Gulab Devi",
        "name_hi": "गुलाब देवी",
        "age": 80,
        "lives_alone": False,
        "outdoor_worker": False,
        "conditions": [],
        "ward_id": "churu-ward-58",
        "offset": (0.0022, 0.0029),
        "role": "You are the daughter-in-law of Gulab Devi, 80, who lives with the family.",
        "nudge": "She is asleep. You are not sure how she is.",
    },
    "emergency": {
        "id": "emergency",
        "name": "Mohan Singh",
        "name_hi": "मोहन सिंह",
        "age": 68,
        "lives_alone": True,
        "outdoor_worker": False,
        "conditions": [],
        "ward_id": "churu-ward-2",
        "offset": (-0.0036, 0.0018),
        "role": "You are Mohan Singh, 68. You live alone.",
        "nudge": "You have chest pain and feel confused.",
    },
}

LANGUAGES = [
    {"code": "hi", "label": "Hindi", "experimental": False},
    {"code": "en", "label": "English", "experimental": False},
    {"code": "mr", "label": "Marathi", "experimental": False},
    {"code": "gu", "label": "Gujarati", "experimental": False},
    {"code": "mwr", "label": "Marwari", "experimental": True},
]

_PUBLIC = ("id", "name", "age", "ward_id", "role", "nudge")


def public_cards() -> list[dict]:
    return [{key: card[key] for key in _PUBLIC} for card in CARDS.values()]
