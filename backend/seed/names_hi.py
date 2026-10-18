"""The seed's names in Devanagari, for the worker's Hindi screens, for all four states. Drafts for a native
speaker to correct. Every name is synthetic."""

FIRST_HI = {
    "Sunita": "सुनीता", "Ramesh": "रमेश", "Kamla": "कमला", "Mohan": "मोहन", "Shanta": "शांता",
    "Vijay": "विजय", "Lata": "लता", "Suresh": "सुरेश", "Pushpa": "पुष्पा", "Anil": "अनिल",
    "Meena": "मीना", "Prakash": "प्रकाश", "Savitri": "सावित्री", "Ganesh": "गणेश", "Usha": "उषा",
    "Dinesh": "दिनेश", "Sarla": "सरला", "Mahesh": "महेश", "Indu": "इंदु", "Ashok": "अशोक",
    "Nirmala": "निर्मला", "Kishor": "किशोर", "Vimal": "विमल", "Hari": "हरि", "Rekha": "रेखा",
    "Balram": "बलराम", "Geeta": "गीता", "Narayan": "नारायण", "Kusum": "कुसुम", "Jagdish": "जगदीश",
    # Telangana
    "Lakshmi": "लक्ष्मी", "Venkatesh": "वेंकटेश", "Padma": "पद्मा", "Srinivas": "श्रीनिवास", "Saroja": "सरोजा",
    "Ramulu": "रामुलु", "Anjali": "अंजलि", "Narasimha": "नरसिम्हा", "Yadamma": "यादम्मा", "Mallesh": "मल्लेश",
    "Sujatha": "सुजाता", "Ravi": "रवि", "Kavitha": "कविता", "Raju": "राजू", "Bharathi": "भारती",
    "Satyanarayana": "सत्यनारायण", "Swarupa": "स्वरूपा", "Krishna": "कृष्णा", "Renuka": "रेणुका", "Balaiah": "बलैया",
    # Madhya Pradesh
    "Ramkali": "रामकली", "Shivnarayan": "शिवनारायण", "Kamlesh": "कमलेश", "Bhagwati": "भगवती", "Rajendra": "राजेंद्र",
    "Sushma": "सुषमा", "Govind": "गोविंद", "Rambai": "रामबाई", "Omprakash": "ओमप्रकाश", "Saroj": "सरोज",
    "Mangilal": "मांगीलाल", "Badri": "बद्री", "Durga": "दुर्गा", "Hiralal": "हीरालाल", "Ratanlal": "रतनलाल",
    "Shanti": "शांति", "Bhagirath": "भागीरथ",
    # Maharashtra
    "Sunanda": "सुनंदा", "Vitthal": "विट्ठल", "Mangal": "मंगल", "Dattatray": "दत्तात्रय", "Shobha": "शोभा",
    "Pandurang": "पांडुरंग", "Asha": "आशा", "Sanjay": "संजय", "Vandana": "वंदना", "Balasaheb": "बालासाहेब",
    "Sushila": "सुशीला", "Tukaram": "तुकाराम", "Lalita": "ललिता", "Shankar": "शंकर", "Kalpana": "कल्पना",
    "Maruti": "मारुती", "Nanda": "नंदा", "Ganpat": "गणपत", "Rukmini": "रुक्मिणी", "Prabhakar": "प्रभाकर",
}
LAST_HI = {
    "Sharma": "शर्मा", "Saini": "सैनी", "Jangir": "जांगिड़", "Prajapat": "प्रजापत", "Meghwal": "मेघवाल",
    "Rathore": "राठौड़", "Shekhawat": "शेखावत", "Choudhary": "चौधरी", "Soni": "सोनी", "Khan": "ख़ान",
    "Reddy": "रेड्डी", "Goud": "गौड़", "Rao": "राव", "Yadav": "यादव", "Naidu": "नायडू", "Chary": "चारी",
    "Mudiraj": "मुदिराज", "Varma": "वर्मा", "Shaik": "शेख़", "Kumar": "कुमार",
    "Patidar": "पाटीदार", "Malviya": "मालवीय", "Chouhan": "चौहान", "Sisodiya": "सिसोदिया", "Ahirwar": "अहिरवार",
    "Parmar": "परमार", "Jain": "जैन", "Mewada": "मेवाड़ा",
    "Patil": "पाटील", "Jadhav": "जाधव", "Pawar": "पवार", "Shinde": "शिंदे", "Kale": "काळे", "More": "मोरे",
    "Deshmukh": "देशमुख", "Gaikwad": "गायकवाड़", "Kulkarni": "कुलकर्णी", "Shaikh": "शेख़",
}
# Three ASHA workers per district, in ward order: (name, name in Devanagari).
WORKERS = {
    "churu": [("Sunita Kanwar", "सुनीता कंवर"), ("Meena Bishnoi", "मीना बिश्नोई"), ("Kavita Saini", "कविता सैनी")],
    "bikaner": [("Pushpa Godara", "पुष्पा गोदारा"), ("Sarita Meghwal", "सरिता मेघवाल"), ("Anita Vyas", "अनीता व्यास")],
    "jhunjhunu": [("Manju Jangir", "मंजू जांगिड़"), ("Rekha Saini", "रेखा सैनी"), ("Suman Poonia", "सुमन पूनिया")],
    "sikar": [("Geeta Dhaka", "गीता ढाका"), ("Kiran Kumawat", "किरण कुमावत"), ("Babita Jat", "बबीता जाट")],
    "jayashankar-bhupalapally": [("Swapna Goud", "स्वप्ना गौड़"), ("Rajitha Reddy", "रजिता रेड्डी"), ("Sunitha Mudiraj", "सुनीता मुदिराज")],
    "jogulamba-gadwal": [("Manjula Naidu", "मंजुला नायडू"), ("Saritha Yadav", "सरिता यादव"), ("Shaheen Shaik", "शाहीन शेख़")],
    "narayanpet": [("Laxmi Chary", "लक्ष्मी चारी"), ("Pushpalatha Rao", "पुष्पलता राव"), ("Aruna Goud", "अरुणा गौड़")],
    "yadadri-bhuvanagiri": [("Sravani Reddy", "श्रावणी रेड्डी"), ("Vijaya Varma", "विजया वर्मा"), ("Padmavathi Kumar", "पद्मावती कुमार")],
    "dewas": [("Rekha Patidar", "रेखा पाटीदार"), ("Sangeeta Malviya", "संगीता मालवीय"), ("Mamta Chouhan", "ममता चौहान")],
    "katni": [("Pooja Tiwari", "पूजा तिवारी"), ("Asha Kushwaha", "आशा कुशवाहा"), ("Neelam Soni", "नीलम सोनी")],
    "shahdol": [("Sunita Baiga", "सुनीता बैगा"), ("Kiran Patel", "किरण पटेल"), ("Lalita Singh", "ललिता सिंह")],
    "ratlam": [("Anita Jain", "अनीता जैन"), ("Kanta Rathore", "कांता राठौर"), ("Hemlata Patidar", "हेमलता पाटीदार")],
    "raigad": [("Shubhangi Patil", "शुभांगी पाटील"), ("Manisha More", "मनीषा मोरे"), ("Swati Kale", "स्वाती काळे")],
    "sangli": [("Sunita Jadhav", "सुनीता जाधव"), ("Vaishali Shinde", "वैशाली शिंदे"), ("Archana Pawar", "अर्चना पवार")],
    "solapur": [("Jyoti Gaikwad", "ज्योती गायकवाड़"), ("Shabana Shaikh", "शबाना शेख़"), ("Pratibha Deshmukh", "प्रतिभा देशमुख")],
    "yavatmal": [("Rupali Kulkarni", "रूपाली कुलकर्णी"), ("Sarika Jadhav", "सारिका जाधव"), ("Meera Shinde", "मीरा शिंदे")],
}


def name_hi(name: str) -> str:
    """'Lata Shekhawat' -> 'लता शेखावत'. Raises KeyError for a name outside the seed's lists."""
    first, last = name.split(" ", 1)
    return f"{FIRST_HI[first]} {LAST_HI[last]}"
