from app.models import Consent, Resident
from app.risk import risk_score

SCRIPTED_PHONES = [f"+910000000{n:03d}" for n in range(1, 31)]  # test numbers for Churu's scripted thirty


def add_ward(store) -> None:
    store.set("districts", "d1", {"id": "d1", "name": "Testpur", "state": "MH", "lat": 21.1, "lng": 79.1})
    store.set("wards", "w1", {"id": "w1", "name": "Ward 1", "district_id": "d1", "worker_id": "k1", "lat": 21.1, "lng": 79.1})
    store.set("workers", "k1", {"id": "k1", "name": "Asha One", "phone": "+910000000091", "ward_ids": ["w1"]})


def add_resident(store, rid: str, **overrides) -> Resident:
    base = dict(
        id=rid, name=f"Resident {rid}", phone="+910000000001", age=70, language="hi",
        district_id="d1", ward_id="w1", lat=21.1, lng=79.1,
        lives_alone=False, outdoor_worker=False, conditions=[], consent=Consent(given=True),
    )
    base.update(overrides)
    resident = Resident(**base)
    resident.risk_score = risk_score(
        resident.age, resident.lives_alone, resident.outdoor_worker, resident.conditions
    )
    store.set("residents", rid, resident.model_dump())
    return resident


def queue_one_call(store, tz, now, **resident):
    """A dispatched round with one resident, r1. Returns (round_id, call_id, queue)."""
    from app.dispatch import RecordingQueue, plan_round
    from app.ingest.alerts import record_alert
    from app.ingest.replay import make_replay_alert

    add_ward(store)
    store.set(
        "facilities",
        "cool-1",
        {"id": "cool-1", "name": "Ward 1 cooling centre", "type": "cooling_centre", "ward_id": "w1", "lat": 21.1, "lng": 79.1},
    )
    round_id = record_alert(store, make_replay_alert("d1", "heat", "red", now), tz).id
    add_resident(store, "r1", **resident)
    queue = RecordingQueue()
    plan_round(store, queue, round_id, now, tz)
    pin_round(store, round_id, now)
    return round_id, f"{round_id}_r1_1", queue


def pin_round(store, round_id, now) -> None:
    """Make this the round that visitors see, as the demo rebuild does."""
    store.set("demo", "current", {"id": "current", "round_id": round_id, "built_at": now})


def add_visitor_call(store, round_id, visitor_id, now, tz, resident_id="v-1", ward_id="w1", district_id="d1", **answers) -> str:
    """A finished call made by a visitor, with its temporary resident and, if anything was
    answered, its case. Returns the call id."""
    from app.dispatch import RecordingQueue, call_id_for, handle_call_outcome
    from app.models import Call

    add_resident(
        store, resident_id, name="Visitor Character", phone="", ward_id=ward_id, district_id=district_id, visitor_id=visitor_id
    )
    call = Call(
        id=call_id_for(round_id, resident_id, 1), round_id=round_id, resident_id=resident_id,
        channel="browser", visitor_id=visitor_id, created_at=now,
    )
    store.set("calls", call.id, call.model_dump())
    if answers:
        store.update(
            "calls", call.id,
            {f"answers.{q}": {"value": v, "quote": "", "at": now} for q, v in answers.items()},
        )
    handle_call_outcome(store, RecordingQueue(), call.id, "completed", now, tz)
    return call.id


def seeded_demo(store, now, tz) -> str:
    """Every district's register with a scripted Churu round, for tests. Returns its id."""
    return seeded_region(store, now, tz)


def seeded_region(store, now, tz) -> str:
    """Every district's register with a scripted Churu round, for tests. Returns its id."""
    from seed.seed import build_all, load_seed
    from tests.scripted_round import scripted_round

    load_seed(store, build_all(SCRIPTED_PHONES))
    return scripted_round(store, "churu", now, tz)
