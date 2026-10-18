"""A transcript for a scripted demo call, written from the answers the script gives it.

Real calls are transcribed as they are spoken. A scripted call was never spoken, so its lines
come from the tables here: romanised Hindi for Hindi and Marwari speakers, English for English
speakers, nothing for anyone else. Drafts for a native speaker to correct."""
from datetime import datetime, timedelta

ORDER = ("feeling", "fluids_food", "company", "shelter", "exposure", "medicine", "help")
SECONDS_PER_LINE = 6
LINES = {
    "hi": {
        "greeting": "Namaste. Main zila swasthya karyalaya ki taraf se ek swachalit sahayak bol rahi hoon. Kya main {name} ji se baat kar rahi hoon?",
        "self": "Haan ji, boliye.",
        "family": "Nahi, main unke parivar se bol rahi hoon.",
        "emergency": "Yeh gambhir ho sakta hai. Abhi 108 par phone kijiye, ya paas mein kisi se kahiye. Main swasthya karyakarta ko abhi soochna de rahi hoon.",
        "close": "Dhanyavaad. Ek swasthya karyakarta aapse milne aa sakti hain ya phone kar sakti hain. Apna dhyan rakhiye.",
        "ask": {
            "feeling": "Aaj bahut garmi hai. Abhi aapki tabiyat kaisi hai?",
            "fluids_food": "Kya ghar mein peene ka paani aur khana hai?",
            "company": "Kya aaj ghar par koi aapke saath hai?",
            "shelter": "Kya ghar mein pankha chal raha hai?",
            "exposure": "Kya aaj aapko bahar jaana ya bahar kaam karna hai?",
            "medicine": "Kya aapki roz ki dawaiyan aapke paas hain?",
            "help": "Kya aaj aapko kisi madad ki zaroorat hai?",
        },
        "reply": {
            "feeling": {"ok": "Theek hoon, koi takleef nahi hai.", "mild_symptoms": "Thoda chakkar aa raha hai.", "severe_symptoms": "Bahut takleef hai, khada nahi hua ja raha.", "unclear": "Pata nahi, kuch theek nahi lag raha."},
            "fluids_food": {"enough": "Haan, paani bhi hai aur khana bhi.", "low": "Paani thoda hi bacha hai.", "none": "Ghar mein paani khatam ho gaya hai."},
            "company": {"with_someone": "Haan, parivar saath hai.", "alone": "Nahi, ghar par koi nahi hai."},
            "shelter": {"adequate": "Haan, pankha chal raha hai.", "partial": "Bijli aati jaati rehti hai.", "none": "Pankha nahi hai."},
            "exposure": {"staying_in": "Nahi, aaj ghar par hi hoon.", "must_go_out": "Haan, kaam par jaana hi padega."},
            "medicine": {"has": "Haan, dawai rakhi hai.", "running_low": "Do din ki hi bachi hai.", "out": "Dawai khatam ho gayi hai."},
            "help": {"none": "Nahi, abhi kuch nahi chahiye.", "wants_visit": "Koi aa jaye to achha rahega.", "urgent": "Haan, jaldi kisi ko bhejiye."},
        },
    },
    "en": {
        "greeting": "Hello. This is an automated assistant calling from the district health office. Am I speaking with {name}?",
        "self": "Yes, speaking.",
        "family": "No, I am from the family.",
        "emergency": "This could be serious. Please call 108 now, or ask someone nearby to. I am alerting a health worker right away.",
        "close": "Thank you. A health worker may visit or call you. Please take care.",
        "ask": {
            "feeling": "It is very hot today. How are you feeling right now?",
            "fluids_food": "Is there drinking water and food at home?",
            "company": "Is anyone with you at home today?",
            "shelter": "Is a fan running at home?",
            "exposure": "Do you need to go out or work outside today?",
            "medicine": "Do you have your regular medicines?",
            "help": "Is there anything you need help with today?",
        },
        "reply": {
            "feeling": {"ok": "I am fine, no trouble.", "mild_symptoms": "I feel a little dizzy.", "severe_symptoms": "I feel very unwell, I cannot stand up.", "unclear": "I am not sure, something feels off."},
            "fluids_food": {"enough": "Yes, there is water and food.", "low": "There is only a little water left.", "none": "The water at home has run out."},
            "company": {"with_someone": "Yes, my family is here.", "alone": "No, nobody is at home."},
            "shelter": {"adequate": "Yes, the fan is running.", "partial": "The electricity comes and goes.", "none": "There is no fan."},
            "exposure": {"staying_in": "No, I am staying in today.", "must_go_out": "Yes, I have to go to work."},
            "medicine": {"has": "Yes, I have them.", "running_low": "Only two days are left.", "out": "They have run out."},
            "help": {"none": "No, nothing right now.", "wants_visit": "It would be good if someone came by.", "urgent": "Yes, please send someone quickly."},
        },
    },
}
SPOKEN_IN = {"hi": "hi", "mwr": "hi", "en": "en"}  # Marwari speakers are served in Hindi


def scripted_transcript(
    resident: dict, answers: dict[str, str], quotes: dict[str, str], emergency: bool, respondent: str, at: datetime
) -> list[dict]:
    """The lines of a scripted call, oldest first, or [] where no script exists for the
    resident's language or nothing was answered. Every line is timed after `at`."""
    language = SPOKEN_IN.get(resident.get("language"))
    if language is None or not answers:
        return []
    words, lines = LINES[language], []

    def say(role: str, text: str) -> None:
        lines.append({"role": role, "text": text, "at": at + timedelta(seconds=SECONDS_PER_LINE * len(lines))})

    say("agent", words["greeting"].format(name=resident["name"]))
    say("resident", words["family" if respondent == "family" else "self"])
    for question in ORDER:
        if question not in answers:
            continue
        say("agent", words["ask"][question])
        say("resident", quotes.get(question) or words["reply"][question].get(answers[question], answers[question]))
        if question == "feeling" and emergency:
            say("agent", words["emergency"])
            return lines
    say("agent", words["close"])
    return lines
