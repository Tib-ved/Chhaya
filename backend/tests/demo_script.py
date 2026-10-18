"""What happened to each of the 30 synthetic residents in the scripted demo round.

Keyed by resident id from seed/seed.py. A resident not listed here had an uneventful call.
Each entry may hold:
  answers   answers that differ from an uneventful call
  asked     the only questions answered before the call ended (default: all of them)
  quotes    the person's words, by question id. Drafts for a native speaker to correct.
            Residents registered as Marathi or Gujarati speakers have no quotes on purpose.
  status    how the call ended (default "completed")
  respondent  who answered (default "self")
  emergency   the reason an emergency was flagged
  failures  for a resident nobody reached: (status, minutes after the first attempt) per attempt
  note      the worker's note on the case, as the text model would write it
  visit     (outcome id, minutes after the round opened) for a case a worker has visited
  escalate  (reason, minutes after the round opened) for a case its worker has flagged to the officer
"""

NOBODY_ANSWERED = (("no_answer", 0), ("no_answer", 20), ("busy", 80))

SCRIPT: dict[str, dict] = {
    "r02": {
        "answers": {"feeling": "severe_symptoms"},
        "asked": ("feeling",),
        "emergency": "chest pain and trouble breathing",
        "quotes": {"feeling": "seene mein dard hai, saans lene mein takleef ho rahi hai"},
        "visit": ("hospital", 98),
        "note": "Reported chest pain and trouble breathing; emergency flagged.\nConfirm an ambulance was called and who is with him.",
    },
    "r15": {
        "answers": {"feeling": "mild_symptoms", "fluids_food": "low"},
        "quotes": {"feeling": "I feel a little dizzy", "fluids_food": "I have had very little water today"},
        "visit": ("supplies_given", 130),
        "note": "Says she feels a little dizzy and has had very little water. Lives alone.\nCheck fluids and whether she can keep cool today.",
    },
    "r12": {"failures": NOBODY_ANSWERED},
    "r07": {
        "answers": {"feeling": "severe_symptoms"},
        "quotes": {"feeling": "baar baar ulti ho rahi hai, khadi nahi ho pa rahi"},
        "note": "Reports vomiting again and again and cannot stand up. Lives alone.\nNeeds to be seen urgently; check whether anyone can stay with her.",
    },
    "r24": {
        "answers": {"fluids_food": "none"},
        "quotes": {"fluids_food": "subah se paani nahi piya, ghar mein bhi nahi hai"},
        "note": "Has had no water since morning and there is none at home. Lives alone.\nBring drinking water; check how he is managing.",
    },
    "r22": {
        "respondent": "family",
        "answers": {"feeling": "unclear"},
        "quotes": {"feeling": "woh so rahe hain, mujhe theek se pata nahi"},
        "note": "A family member answered: he is asleep and they could not say how he is.\nSee him in person and ask how he has been since morning.",
    },
    "r14": {
        "note": "Says he is managing and has what he needs. Lives alone, aged 79.\nLook in once today while the warning lasts.",
        "quotes": {"feeling": "main theek hoon"}, "visit": ("advised_ok", 150),
    },
    "r16": {
        "note": "Says he is managing and has what he needs. Lives alone, aged 77.\nLook in once today while the warning lasts.",
        "quotes": {"feeling": "I am fine"},
    },
    "r05": {
        "answers": {"feeling": "mild_symptoms"},
        "quotes": {"feeling": "sir mein dard hai aur kamzori lag rahi hai"},
        "visit": ("referred", 115),
        "note": "Reports a headache and weakness.\nCheck how she is now and whether she is drinking water.",
    },
    "r11": {"failures": NOBODY_ANSWERED},
    "r18": {
        "answers": {"shelter": "none"},
        "quotes": {"shelter": "bijli nahi hai, pankha band pada hai"},
        "note": "No electricity and the fan is not working. Lives alone.\nCheck for a cooler place to stay; the cooling centre is nearby.",
        "escalate": ("missing", 170),
    },
    "r19": {
        "answers": {"help": "wants_visit"},
        "quotes": {"help": "koi aa kar dekh le to achha rahega"},
        "note": "Asked for someone to come by today. Lives alone.\nAsk what she needs.",
    },
    "r29": {
        "answers": {"medicine": "out"},
        "quotes": {"medicine": "sugar ki dawai khatam ho gayi hai"},
        "visit": ("supplies_given", 160),
        "note": "Her diabetes medicine has run out. Lives alone.\nCheck what she takes and help arrange a supply.",
    },
    "r30": {
        "answers": {"feeling": "mild_symptoms"},
        "quotes": {"feeling": "thodi kamzori lag rahi hai"},
        "note": "Says he feels a little weak.\nCheck how he is now and whether he is drinking water.",
    },
    "r21": {"failures": NOBODY_ANSWERED},
    "r03": {
        "note": "The call ended early, after two questions; she had said she had water.\nFinish the check in person.",
        "status": "dropped", "asked": ("feeling", "fluids_food"), "quotes": {"feeling": "theek hoon"},
    },
    "r08": {
        "answers": {"fluids_food": "low"},
        "quotes": {"fluids_food": "paani thoda hi bacha hai"},
        "note": "Water at home is running short. Lives alone.\nCheck his water supply for the next days.",
    },
    "r27": {
        "note": "Has to go out to work today despite a breathing condition.\nAdvise shade and water; ask about the hours she works outside.",
        "quotes": {"exposure": "kaam par jaana hi padega"},
    },
    "r13": {"failures": (("no_answer", 0),)},
    "r20": {"failures": NOBODY_ANSWERED},
    "r26": {"failures": (("no_answer", 0), ("busy", 20))},
}


def uneventful(resident: dict) -> dict[str, str]:
    """The answers of a resident with nothing to report, true to their own record."""
    answers = {
        "feeling": "ok",
        "fluids_food": "enough",
        "company": "alone" if resident.get("lives_alone") else "with_someone",
        "shelter": "adequate",
        "exposure": "must_go_out" if resident.get("outdoor_worker") else "staying_in",
        "help": "none",
    }
    if resident.get("conditions"):
        answers["medicine"] = "has"
    return answers


def answers_for(resident: dict, line: dict) -> dict[str, str]:
    answers = {**uneventful(resident), **line.get("answers", {})}
    if "asked" in line:
        answers = {question: value for question, value in answers.items() if question in line["asked"]}
    return answers
