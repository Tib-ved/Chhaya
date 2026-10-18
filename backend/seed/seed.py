"""Synthetic residents in sixteen real districts of four states. The districts and wards are
real (seed/fixtures/wards.json names the sources); the people, addresses and phone numbers are
not. Only Rajasthan's districts are under a heat alert; the rest are register_only."""
import random
import sys
from datetime import datetime, timezone

from app.db import Store
from app.dispatch import PHONE_RE
from app.models import VISITOR_USAGE
from app.risk import risk_score
from seed.names_hi import WORKERS, name_hi
from seed.regions import ALERT_STATE, DISTRICTS, LANGUAGES, NAMES, load_wards, ward_display

DISTRICT_ID = "churu"  # the district whose first thirty residents the hand-written script describes
SCRIPTED = 30
WARD_SIZE = (30, 50)
CONDITIONS = ["heart", "diabetes", "kidney", "respiratory", "disability"]
CONSENT_AT = datetime(2026, 4, 1, tzinfo=timezone.utc)
FIRST_NAMES, LAST_NAMES = NAMES[ALERT_STATE]


def ward_ids(district_id: str) -> list[str]:
    return [f"{district_id}-ward-{w['number']}" for w in load_wards()[district_id]["wards"]]


def _resident(rng, rid, district_id, ward, first, last, language, outdoor_worker, n) -> dict:
    """One mocked resident. `n` numbers them across the whole seed, for unique phone numbers."""
    age = rng.randint(32, 55) if outdoor_worker else rng.randint(60, 88)
    lives_alone = rng.random() < 0.4
    conditions = sorted(rng.sample(CONDITIONS, rng.choice([0, 0, 1, 1, 2])))
    return {
        "id": rid, "name": f"{first} {last}", "name_hi": name_hi(f"{first} {last}"),
        "phone": "", "age": age, "language": language,
        "district_id": district_id, "ward_id": ward["id"],
        "lat": round(ward["lat"] + rng.uniform(-0.008, 0.008), 6),
        "lng": round(ward["lng"] + rng.uniform(-0.008, 0.008), 6),
        "lives_alone": lives_alone, "outdoor_worker": outdoor_worker, "conditions": conditions,
        "emergency_contact": {"name": f"Relative of {first}", "phone": ""},
        "consent": {"given": True, "at": CONSENT_AT, "method": "synthetic"},
        "risk_score": risk_score(age, lives_alone, outdoor_worker, conditions),
    }


def _district(index: int, district_id: str, name: str, state: str, counter: list[int]) -> dict[str, list[dict]]:
    """A district's register. `counter` is shared across districts so every phone is unique."""
    entry = load_wards()[district_id]
    record = {"id": district_id, "name": name, "state": state, "imd_id": "", "lat": entry["lat"], "lng": entry["lng"]}
    if entry.get("lgd_code"):
        record["lgd_code"] = entry["lgd_code"]
    if state != ALERT_STATE:
        record["register_only"] = True
    wards, workers, facilities = [], [], []
    for position, official in enumerate(entry["wards"]):
        number, label = official["number"], ward_display(official)
        ward = {
            "id": f"{district_id}-ward-{number}", "name": label, "number": number, "district_id": district_id,
            "worker_id": f"{district_id}-worker-{number}",
            "lat": round(entry["lat"] + (position - 1) * 0.0065, 4), "lng": round(entry["lng"] + (position - 1) * 0.009, 4),
        }
        if official.get("lgd_code"):
            ward["lgd_code"] = official["lgd_code"]
        wards.append(ward)
        worker_name, worker_hi = WORKERS[district_id][position]
        workers.append({"id": ward["worker_id"], "name": worker_name, "name_hi": worker_hi,
                        "phone": "", "ward_ids": [ward["id"]]})
        facilities.append({"id": f"{district_id}-cool-{number}", "name": f"{label} cooling centre", "type": "cooling_centre",
                           "ward_id": ward["id"], "lat": ward["lat"] + 0.002, "lng": ward["lng"] + 0.002})
        facilities.append({"id": f"{district_id}-phc-{number}", "name": f"{label} health centre", "type": "health_centre",
                           "ward_id": ward["id"], "lat": ward["lat"] - 0.002, "lng": ward["lng"] - 0.002})

    residents = []
    sizes = random.Random(f"{district_id}:sizes")
    targets = [sizes.randint(*WARD_SIZE) for _ in wards]
    first_names, last_names = NAMES[state]
    if district_id == DISTRICT_ID:
        # The scripted thirty, drawn exactly as before so the script still describes them.
        rng = random.Random(7)
        languages = LANGUAGES[state][:]
        rng.shuffle(languages)
        for n in range(SCRIPTED):
            counter[0] += 1
            residents.append(_resident(rng, f"r{n + 1:02d}", district_id, wards[n % 3], FIRST_NAMES[n],
                                       LAST_NAMES[n % len(LAST_NAMES)], languages[n], n % 7 == 5, counter[0]))
    rng = random.Random(f"{district_id}:residents")
    for position, ward in enumerate(wards):
        already = sum(1 for r in residents if r["ward_id"] == ward["id"])
        for k in range(already, targets[position]):
            counter[0] += 1
            rid = f"r{len(residents) + 1:02d}" if district_id == DISTRICT_ID else f"{district_id}-{ward['number']}-{k + 1:02d}"
            residents.append(_resident(rng, rid, district_id, ward, rng.choice(first_names), rng.choice(last_names),
                                       rng.choice(LANGUAGES[state]), k % 7 == 5, counter[0]))
    return {"districts": [record], "wards": wards, "workers": workers, "facilities": facilities, "residents": residents}


def _check_team_phones(team_phones: list[str]) -> None:
    if len(team_phones) > SCRIPTED:
        raise ValueError(f"at most {SCRIPTED} team phones can be used, got {len(team_phones)}")
    for phone in team_phones:
        if not PHONE_RE.fullmatch(phone):
            raise ValueError(f"team phone {phone!r} is not in international format, for example +910000000001")


def build_all(team_phones: list[str]) -> dict[str, list[dict]]:
    """Every district's register. Team phones replace the numbers of Churu's first residents."""
    _check_team_phones(team_phones)
    data: dict[str, list[dict]] = {"districts": [], "wards": [], "workers": [], "facilities": [], "residents": []}
    counter = [0]
    for index, (district_id, name, state) in enumerate(DISTRICTS):
        for collection, documents in _district(index, district_id, name, state, counter).items():
            data[collection].extend(documents)
    for resident, phone in zip(data["residents"], team_phones):
        resident["phone"] = phone
    return data


def build_seed(team_phones: list[str]) -> dict[str, list[dict]]:
    """Churu's register alone, for tests that need one district."""
    data = build_all(team_phones)
    return {
        "districts": [d for d in data["districts"] if d["id"] == DISTRICT_ID],
        **{c: [x for x in data[c] if x.get("district_id", "") == DISTRICT_ID or x.get("ward_id", "").startswith(f"{DISTRICT_ID}-")]
           for c in ("wards", "residents", "facilities")},
        "workers": [w for w in data["workers"] if w["id"].startswith(f"{DISTRICT_ID}-")],
    }


def load_seed(store: Store, data: dict[str, list[dict]]) -> None:
    for collection, documents in data.items():
        for document in documents:
            store.set(collection, document["id"], document)


REGISTER = ("districts", "wards", "workers", "facilities", "residents")


def prune_register(store: Store, data: dict[str, list[dict]]) -> dict[str, int]:
    """Delete register records the seed no longer lists, such as the wards of an older seed.
    A visitor's temporary resident is left alone: the rebuild removes it with its round."""
    deleted = {}
    for collection in REGISTER:
        keep = {d["id"] for d in data[collection]}
        stale = [d for d in store.query(collection) if d["id"] not in keep and not d.get("visitor_id")]
        for document in stale:
            store.delete(collection, document["id"])
        deleted[collection] = len(stale)
    return deleted


def reset_district(store: Store, district_id: str) -> dict[str, int]:
    """Delete everything a day of demos made for one district: its rounds with their alerts,
    calls and cases, visitors' temporary residents, the usage ledger and the demo pointer.
    The register (residents, wards, workers, facilities) is kept. Returns what was deleted."""
    deleted = {"rounds": 0, "alerts": 0, "calls": 0, "cases": 0, "residents": 0, "usage": 0, "demo": 0}
    for current in store.query("rounds", district_id=district_id):
        for collection in ("calls", "cases"):
            for document in store.query(collection, round_id=current["id"]):
                store.delete(collection, document["id"])
                deleted[collection] += 1
        store.delete("rounds", current["id"])
        deleted["rounds"] += 1
    for alert in store.query("alerts", district_id=district_id):
        store.delete("alerts", alert["id"])
        deleted["alerts"] += 1
    for resident in store.query("residents", district_id=district_id):
        if resident.get("visitor_id"):
            store.delete("residents", resident["id"])
            deleted["residents"] += 1
    for entry in store.query(VISITOR_USAGE):
        store.delete(VISITOR_USAGE, entry["id"])
        deleted["usage"] += 1
    if store.get("demo", "current") is not None:
        store.delete("demo", "current")
        deleted["demo"] += 1
    return deleted


def main(arguments: list[str] | None = None) -> int:
    from app.config import get_settings
    from app.db import get_store

    arguments = sys.argv[1:] if arguments is None else arguments
    settings = get_settings()
    store = get_store(settings)
    phones = [p.strip() for p in settings.team_phones.split(",") if p.strip()]
    data = build_all(phones)
    warned = [d["id"] for d in data["districts"] if not d.get("register_only")]
    if "--reset" in arguments:
        if "--yes" not in arguments:
            typed = input(
                f"This deletes every round, call and case of {', '.join(warned)} and every register "
                f"record the seed no longer lists, in the '{settings.store}' store. Type reset to go on: "
            )
            if typed.strip() != "reset":
                print("Nothing was deleted.")
                return 1
        for district_id in warned:
            print(district_id, "deleted:", reset_district(store, district_id))
    load_seed(store, data)
    if "--reset" in arguments:
        print("pruned:", prune_register(store, data))
    print({collection: len(documents) for collection, documents in data.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
