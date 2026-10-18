"""One real run of the live weather fetch, on an in-memory store seeded with the register.

Run by a person, from backend/:

    .venv/Scripts/python -m scripts.live_weather_check

The key is WEATHER_API_KEY, or else the `weather-api-key` secret in GCP_PROJECT (app.secret_manager).

It calls the Weather API for every ward and every district (64 calls: 48 wards and 16 districts), so run it sparingly.
Nothing is written to Firestore. It prints, per state, each district's peak, the state mean, and
which districts the state rule made red or orange; then the number of API calls. It never prints the key."""
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import Settings
from app.db import MemoryStore
from app.ingest.live_weather import ALERT_SOURCE, run_live_fetch
from app.secret_manager import read_secret
from app.weather import fetch_days, fetch_forecast
from seed.seed import build_all, load_seed

TZ = ZoneInfo("Asia/Kolkata")


def main() -> int:
    settings = Settings()
    key = os.environ.get("WEATHER_API_KEY", "").strip() or read_secret(settings.gcp_project, settings.weather_api_key_secret)
    if not key:
        print("Set WEATHER_API_KEY, or GCP_PROJECT with `gcloud auth application-default login` to read it from Secret Manager.")
        return 2
    store = MemoryStore()
    load_seed(store, build_all([]))
    calls = {"hours": 0, "days": 0}

    def hours(lat, lng):
        calls["hours"] += 1
        return fetch_forecast(key, lat, lng)

    def days(lat, lng):
        calls["days"] += 1
        return fetch_days(key, lat, lng)

    summary = run_live_fetch(store, Settings(), datetime.now(timezone.utc), TZ, hours, days)
    names = {d["id"]: d["name"] for d in store.query("districts")}
    severity = {a["district_id"]: a["severity"] for a in store.query("alerts") if a["source"] == ALERT_SOURCE}
    by_state = defaultdict(list)
    for snap in store.query("weather", area_kind="district"):
        top = snap.get("peak")
        by_state[snap["state"]].append((snap["district_id"], top["temp_c"] if top else None, top["hour"] if top else None))
    for state, items in sorted(by_state.items()):
        known = [t for _, t, _ in items if t is not None]
        mean = f"{sum(known) / len(known):.1f}" if known else "none"
        print(f"\n{state}  (mean of peaks {mean})")
        for district_id, temp, hour in sorted(items, key=lambda i: (i[1] is None, -(i[1] or 0))):
            shown = f"{temp:.1f} C at {hour:02d}:00" if temp is not None else "no data"
            print(f"  {names.get(district_id, district_id):<16} {shown:<18} {severity.get(district_id, '')}")
    print(f"\nAPI calls: {calls['hours']} hourly + {calls['days']} daily = {calls['hours'] + calls['days']}")
    print("Summary:", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
