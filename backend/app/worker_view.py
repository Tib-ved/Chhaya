"""The ASHA worker's screens: who the workers are, and one worker's residents.

A worker's residents are those whose case is hers, and those of her ward who have no case.
A case the officer has moved to someone else leaves her list and joins theirs."""
from collections import Counter
from datetime import datetime

from app.db import Store
from app.region import NEED_LABELS, load_district, warning_of
from app.report import RoundCache


def _needs_visit(row: dict) -> bool:
    return row["tier"] in ("red", "amber")


def workers_view(
    store: Store, district_id: str, visitor_id: str | None, now: datetime,
    views: RoundCache | None = None, registers: RoundCache | None = None,
) -> dict:
    load = load_district(store, district_id, visitor_id, now, views, registers)
    wards = load.register["wards"]
    listed = []
    for worker in sorted(load.register["workers"].values(), key=lambda w: w["id"]):
        mine = [r for r in load.rows if r["worker_id"] == worker["id"] and _needs_visit(r)]
        ward_id = next((w for w in worker.get("ward_ids") or [] if w in wards), None)
        listed.append({
            "id": worker["id"],
            "name": worker["name"],
            "name_hi": worker.get("name_hi") or "",
            "ward_id": ward_id,
            "ward_name": wards[ward_id]["name"] if ward_id else None,
            "open": sum(r["case_status"] == "open" for r in mine),
            "done": sum(r["case_status"] == "visited" for r in mine),
        })
    return {"district_id": district_id, "workers": listed}


def worker_view(
    store: Store, worker_id: str, visitor_id: str | None, now: datetime,
    views: RoundCache | None = None, registers: RoundCache | None = None,
) -> dict:
    worker = store.get("workers", worker_id)
    ward = next((store.get("wards", w) for w in (worker or {}).get("ward_ids") or [] if store.get("wards", w)), None)
    if worker is None or ward is None:
        raise KeyError(f"workers/{worker_id}")
    load = load_district(store, ward["district_id"], visitor_id, now, views, registers)
    rows = [r for r in load.rows if r["worker_id"] == worker_id]
    to_visit = [r for r in rows if _needs_visit(r) and r["case_status"] == "open"]
    carry = Counter(need for r in to_visit for need in r["needs"])
    centre = next((f for f in load.register["facilities"] if f["ward_id"] == ward["id"] and f["type"] == "health_centre"), None)
    return {
        "worker": {
            "id": worker_id, "name": worker["name"], "name_hi": worker.get("name_hi") or "",
            "ward_id": ward["id"], "ward_name": ward["name"],
            "district_id": ward["district_id"], "district_name": load.register["district"]["name"],
        },
        "warning": warning_of(load),
        "counts": {
            "to_visit": len(to_visit),
            "done": sum(_needs_visit(r) and r["case_status"] == "visited" for r in rows),
            "red_open": sum(r["tier"] == "red" for r in to_visit),
            "longest_wait_minutes": max((r["waiting_minutes"] or 0 for r in to_visit), default=None),
        },
        "carry": [{"id": need, "label": NEED_LABELS[need], "count": carry[need]} for need in NEED_LABELS if carry[need]],
        "health_centre": {k: centre.get(k) for k in ("name", "lat", "lng")} if centre else None,
        "residents": rows,
    }
