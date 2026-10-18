"""Which districts get a heat alert today (spec L6): within each state, the hottest district is red and
every other district above the state's mean peak is orange. A showcase rule, labelled as such on screen;
the IMD rule against each district's climatological normal is the later upgrade."""
from collections import defaultdict


def alerts_by_state(districts: list[dict]) -> list[dict]:
    by_state: dict[str, list[dict]] = defaultdict(list)
    for item in districts:
        if item.get("peak_c") is not None:
            by_state[item["state"]].append(item)
    alerts = []
    for state, items in sorted(by_state.items()):
        if len(items) < 2:
            continue
        # Compared as the text gives it, so an orange is never "0.0 °C above" the average.
        mean = round(sum(i["peak_c"] for i in items) / len(items), 1)
        ranked = sorted(items, key=lambda i: (-i["peak_c"], i["peak_hour"] if i["peak_hour"] is not None else 99, i["district_id"]))
        for rank, item in enumerate(ranked, start=1):
            if rank == 1:
                rule, severity = "hottest_in_state", "red"
            elif item["peak_c"] > mean:
                rule, severity = "above_state_mean", "orange"
            else:
                continue
            alerts.append({"district_id": item["district_id"], "severity": severity, "reason": {
                "district_peak_c": item["peak_c"], "peak_hour": item["peak_hour"], "state": state,
                "state_mean_c": mean, "state_max_c": ranked[0]["peak_c"], "rank_in_state": rank,
                "districts_with_data": len(items), "rule": rule,
            }})
    return alerts


def reason_text(reason: dict, district_name: str) -> str:
    gap = round(reason["district_peak_c"] - reason["state_mean_c"], 1)
    hour = f" at {reason['peak_hour']:02d}:00" if reason.get("peak_hour") is not None else ""
    if reason["rule"] == "hottest_in_state":
        return f"Hottest in {reason['state']} today: {reason['district_peak_c']} °C{hour}, {gap} °C above the state average"
    return f"{reason['district_peak_c']} °C{hour}, {gap} °C above the {reason['state']} average"
