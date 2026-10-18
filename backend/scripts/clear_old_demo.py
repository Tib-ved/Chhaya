"""One-off, at the first deploy of the two datasets: clear the old demo records out of the plain
(live) collections. Before the split the demo's alerts, rounds, cases and calls lived there.

Run from backend/, once, against the real store:

    .venv/Scripts/python -m scripts.clear_old_demo --yes

It deletes every alert whose source is `declared` or `scenario`, the rounds opened by those alerts,
the cases and calls of those rounds, and the plain `demo/current` pointer if it points at one of
those rounds. Live records (source `forecast_state_rule`) are left alone. Without --yes it only
counts what it would delete."""
import argparse
import sys

from app.db import Store

DEMO_SOURCES = ("declared", "scenario")


def clear_old_demo(store: Store, apply: bool) -> dict:
    alert_ids = {a["id"] for a in store.query("alerts") if a.get("source") in DEMO_SOURCES}
    rounds = [r for r in store.query("rounds") if r.get("alert_id") in alert_ids]
    round_ids = {r["id"] for r in rounds}
    cases = [c for c in store.query("cases") if c.get("round_id") in round_ids]
    calls = [c for c in store.query("calls") if c.get("round_id") in round_ids]
    pointer = store.get("demo", "current")
    drop_pointer = bool(pointer) and (pointer.get("round_id") in round_ids
                                      or bool(set((pointer.get("rounds") or {}).values()) & round_ids))
    if apply:
        for collection, items in (("calls", calls), ("cases", cases), ("rounds", rounds)):
            for item in items:
                store.delete(collection, item["id"])
        for alert_id in alert_ids:
            store.delete("alerts", alert_id)
        if drop_pointer:
            store.delete("demo", "current")
    return {"alerts": len(alert_ids), "rounds": len(rounds), "cases": len(cases), "calls": len(calls),
            "demo": 1 if drop_pointer else 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--yes", action="store_true", help="really delete; without it nothing changes")
    args = parser.parse_args(argv)
    if not args.yes:
        print("Refusing to delete without --yes. Counts of what would go:")
    from app.config import get_settings
    from app.db import get_store
    counts = clear_old_demo(get_store(get_settings()), apply=args.yes)
    print(("Deleted: " if args.yes else "Would delete: ") + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return 0 if args.yes else 1


if __name__ == "__main__":
    sys.exit(main())
