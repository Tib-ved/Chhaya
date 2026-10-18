import re

RETRIEVED_AT = "2026-10-13"
SOURCE = "Integrated Government Online Directory, National Informatics Centre"
STATE_DISTRICTS = {
    "RJ": ("Rajasthan", "Ajmer|Alwar|Balotra|Banswara|Baran|Barmer|Beawar|Bharatpur|Bhilwara|Bikaner|Bundi|Chittorgarh|Churu|Dausa|Deeg|Dholpur|Didwana-Kuchaman|Dungarpur|Ganganagar|Hanumangarh|Jaipur|Jaisalmer|Jalore|Jhalawar|Jhunjhunu|Jodhpur|Karauli|Khairthal-Tijara|Kota|Kotputli-Behror|Nagaur|Pali|Phalodi|Pratapgarh|Rajsamand|Salumbar|Sawai Madhopur|Sikar|Sirohi|Tonk|Udaipur"),
    "TS": ("Telangana", "Adilabad|Bhadradri Kothagudem|Hanumakonda|Hyderabad|Jagitial|Jangoan|Jayashankar Bhupalapally|Jogulamba Gadwal|Kamareddy|Karimnagar|Khammam|Kumuram Bheem Asifabad|Mahabubabad|Mahabubnagar|Mancherial|Medak|Medchal Malkajgiri|Mulugu|Nagarkurnool|Nalgonda|Narayanpet|Nirmal|Nizamabad|Peddapalli|Rajanna Sircilla|Ranga Reddy|Sangareddy|Siddipet|Suryapet|Vikarabad|Wanaparthy|Warangal|Yadadri Bhuvanagiri"),
    "MP": ("Madhya Pradesh", "Agar-Malwa|Alirajpur|Anuppur|Ashoknagar|Balaghat|Barwani|Betul|Bhind|Bhopal|Burhanpur|Chhatarpur|Chhindwara|Damoh|Datia|Dewas|Dhar|Dindori|Guna|Gwalior|Harda|Indore|Jabalpur|Jhabua|Katni|Khandwa (East Nimar)|Khargone (West Nimar)|Mauganj|Maihar|Mandla|Mandsaur|Morena|Narmadapuram|Narsimhapur|Neemuch|Niwari|Pandhurna|Panna|Raisen|Rajgarh|Ratlam|Rewa|Sagar|Satna|Sehore|Seoni|Shahdol|Shajapur|Sheopur|Shivpuri|Sidhi|Singrauli|Tikamgarh|Ujjain|Umaria|Vidisha"),
    "MH": ("Maharashtra", "Ahilyanagar|Akola|Amravati|Beed|Bhandara|Buldhana|Chandrapur|Chhatrapati Sambhajinagar|Dharashiv|Dhule|Gadchiroli|Gondia|Hingoli|Jalgaon|Jalna|Kolhapur|Latur|Mumbai|Mumbai Suburban|Nagpur|Nanded|Nandurbar|Nashik|Palghar|Parbhani|Pune|Raigad|Ratnagiri|Sangli|Satara|Sindhudurg|Solapur|Thane|Wardha|Washim|Yavatmal"),
}


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def state_code(value: str) -> str:
    return next((code for code, (name, _) in STATE_DISTRICTS.items() if value.lower() in (code.lower(), name.lower())), value)


def directory(store) -> dict:
    registered = store.query("districts")
    wards = store.query("wards")
    states = []
    for code, (name, names) in STATE_DISTRICTS.items():
        districts = []
        for district_name in names.split("|"):
            found = next((d for d in registered if state_code(d.get("state", "")) == code and slug(d["name"]) == slug(district_name)), None)
            district_id = found["id"] if found else f"{code.lower()}-{slug(district_name)}"
            districts.append({
                "id": district_id, "name": district_name, "state_id": code,
                "onboarded": found is not None,
                "wards": [{"id": w["id"], "name": w["name"], "synthetic": w.get("synthetic", False)} for w in wards if w["district_id"] == district_id],
            })
        states.append({"id": code, "name": name, "source_url": f"https://igod.gov.in/sg/{code}/E042/organizations", "districts": districts})
    return {"states": states, "source": SOURCE, "retrieved_at": RETRIEVED_AT, "ward_note": "Only onboarded wards are listed. The wards listed are real wards from official lists; resident data is mocked."}
