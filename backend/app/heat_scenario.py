"""The heat scenario: a fixed hot-day forecast for each Rajasthan district, run through the same
trigger rules as the real forecast (app.ingest.forecast), so the 07:00 job raises the alert the
way a live forecast would. The curves are in seed/fixtures/heat_scenario.json."""
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.ingest.forecast import alerts_for, extremes
from app.models import Alert

FIXTURE = Path(__file__).resolve().parent.parent / "seed" / "fixtures" / "heat_scenario.json"
ALERT_HOURS = 48  # still valid when the next morning's job runs


def load_scenario() -> dict[str, dict]:
    """district id -> {"name", "hours": [{"hour", "temp_c"}, ...]}, hours 6 to 20."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def forecast_payload(district_id: str, day: date) -> dict:
    """The district's curve for `day`, in the shape the Weather API forecast has."""
    curve = load_scenario()[district_id]
    return {"forecastHours": [
        {
            "temperature": {"degrees": hour["temp_c"]},
            "displayDateTime": {"year": day.year, "month": day.month, "day": day.day, "hours": hour["hour"]},
        }
        for hour in curve["hours"]
    ]}


def scenario_alert(district: dict, now: datetime, tz: ZoneInfo, settings) -> Alert | None:
    """The heat alert the district's curve calls for under the trigger rules, or None if the
    curve crosses no threshold or the district has no curve."""
    district_id = district["id"]
    curve = load_scenario().get(district_id)
    if curve is None:
        return None
    day = now.astimezone(tz).date()
    found = extremes(forecast_payload(district_id, day), day)
    if found is None:
        return None
    heat = next((a for a in alerts_for(district, found, now, tz, settings) if a.hazard == "heat"), None)
    if heat is None:
        return None
    threshold = settings.heat_red_c if heat.severity == "red" else settings.heat_orange_c
    return heat.model_copy(update={
        "id": f"scenario_{district_id}_heat_{day:%Y%m%d}",
        "source": "scenario",
        "valid_to": now + timedelta(hours=ALERT_HOURS),
        "raw": {
            "scenario": curve["name"], "peak_c": found["max_c"], "peak_hour": found["max_hour"],
            "threshold_c": threshold, "severity": heat.severity,
            "rule": f"forecast maximum at or above the {heat.severity} heat threshold",
            "forecast_hours": curve["hours"],
        },
    })
