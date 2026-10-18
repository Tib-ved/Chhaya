from datetime import date
from zoneinfo import ZoneInfo

from app.db import Store
from app.models import Alert, Round


def round_id_for(district_id: str, hazard: str, local_day: date) -> str:
    return f"{district_id}_{hazard}_{local_day:%Y%m%d}"


def record_alert(store: Store, alert: Alert, tz: ZoneInfo) -> Round | None:
    store.set("alerts", alert.id, alert.model_dump())
    if alert.severity not in ("orange", "red"):
        return None
    round_id = round_id_for(alert.district_id, alert.hazard, alert.received_at.astimezone(tz).date())
    new_round = Round(
        id=round_id,
        alert_id=alert.id,
        district_id=alert.district_id,
        hazard=alert.hazard,
        opened_at=alert.received_at,
    )
    store.create("rounds", round_id, new_round.model_dump())
    return Round(**store.get("rounds", round_id))
