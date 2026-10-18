"""The districts of the seed: four states, four real districts each, with the names and
languages their mocked residents are given. Only Rajasthan is under a heat alert; the other
twelve districts are register_only, so the forecast check never raises an alert there.

The districts outside Rajasthan were drawn with random.Random(2026).sample(names, 4) from each
state's list in app/geography.py, and are spelt as there. In Madhya Pradesh, Rajgarh and Pandhurna
were drawn first but publish no official ward list; Shahdol and Katni replace them."""
import json
from pathlib import Path

ALERT_STATE = "Rajasthan"
DISTRICTS = [
    ("churu", "Churu", "Rajasthan"),
    ("bikaner", "Bikaner", "Rajasthan"),
    ("jhunjhunu", "Jhunjhunu", "Rajasthan"),
    ("sikar", "Sikar", "Rajasthan"),
    ("jayashankar-bhupalapally", "Jayashankar Bhupalapally", "Telangana"),
    ("jogulamba-gadwal", "Jogulamba Gadwal", "Telangana"),
    ("narayanpet", "Narayanpet", "Telangana"),
    ("yadadri-bhuvanagiri", "Yadadri Bhuvanagiri", "Telangana"),
    ("dewas", "Dewas", "Madhya Pradesh"),
    ("katni", "Katni", "Madhya Pradesh"),
    ("ratlam", "Ratlam", "Madhya Pradesh"),
    ("shahdol", "Shahdol", "Madhya Pradesh"),
    ("raigad", "Raigad", "Maharashtra"),
    ("sangli", "Sangli", "Maharashtra"),
    ("solapur", "Solapur", "Maharashtra"),
    ("yavatmal", "Yavatmal", "Maharashtra"),
]
NAMES = {
    "Rajasthan": (
        ["Sunita", "Ramesh", "Kamla", "Mohan", "Shanta", "Vijay", "Lata", "Suresh", "Pushpa", "Anil",
         "Meena", "Prakash", "Savitri", "Ganesh", "Usha", "Dinesh", "Sarla", "Mahesh", "Indu", "Ashok",
         "Nirmala", "Kishor", "Vimal", "Hari", "Rekha", "Balram", "Geeta", "Narayan", "Kusum", "Jagdish"],
        ["Sharma", "Saini", "Jangir", "Prajapat", "Meghwal", "Rathore", "Shekhawat", "Choudhary", "Soni", "Khan"],
    ),
    "Telangana": (
        ["Lakshmi", "Venkatesh", "Padma", "Srinivas", "Saroja", "Ramulu", "Anjali", "Narasimha", "Yadamma", "Mallesh",
         "Sujatha", "Ravi", "Kavitha", "Raju", "Bharathi", "Satyanarayana", "Swarupa", "Krishna", "Renuka", "Balaiah"],
        ["Reddy", "Goud", "Rao", "Yadav", "Naidu", "Chary", "Mudiraj", "Varma", "Shaik", "Kumar"],
    ),
    "Madhya Pradesh": (
        ["Ramkali", "Shivnarayan", "Kamlesh", "Bhagwati", "Rajendra", "Sushma", "Govind", "Rambai", "Omprakash", "Saroj",
         "Mangilal", "Krishna", "Badri", "Durga", "Hiralal", "Kamla", "Ratanlal", "Shanti", "Bhagirath", "Lakshmi"],
        ["Patidar", "Malviya", "Chouhan", "Sisodiya", "Rathore", "Yadav", "Ahirwar", "Parmar", "Jain", "Mewada"],
    ),
    "Maharashtra": (
        ["Sunanda", "Vitthal", "Mangal", "Dattatray", "Shobha", "Pandurang", "Asha", "Sanjay", "Vandana", "Balasaheb",
         "Sushila", "Tukaram", "Lalita", "Shankar", "Kalpana", "Maruti", "Nanda", "Ganpat", "Rukmini", "Prabhakar"],
        ["Patil", "Jadhav", "Pawar", "Shinde", "Kale", "More", "Deshmukh", "Gaikwad", "Kulkarni", "Shaikh"],
    ),
}
LANGUAGES = {
    "Rajasthan": ["hi"] * 14 + ["mwr"] * 8 + ["en"] * 3 + ["gu"] * 3 + ["mr"] * 2,
    "Telangana": ["te"] * 7 + ["ur"] * 2 + ["hi"],
    "Madhya Pradesh": ["hi"],
    "Maharashtra": ["mr"] * 4 + ["hi"],
}
WARD_FIXTURE = Path(__file__).parent / "fixtures" / "wards.json"


def load_wards() -> dict[str, dict]:
    """The fixture's entry for every district: its town, sources and three official wards."""
    return json.loads(WARD_FIXTURE.read_text(encoding="utf-8"))["districts"]


def ward_display(ward: dict) -> str:
    """'Ward 12', or 'Ward 7 – Subhash Nagar' when the official list names the ward."""
    return f"Ward {ward['number']}" + (f" – {ward['name']}" if ward.get("name") else "")
