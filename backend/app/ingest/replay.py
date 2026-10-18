from datetime import datetime, timedelta

from app.models import Alert


def make_replay_alert(district_id: str, hazard: str, severity: str, now: datetime) -> Alert:
    return Alert(
        id=f"replay_{district_id}_{hazard}_{now:%Y%m%dT%H%M%S}",
        source="replay",
        hazard=hazard,
        severity=severity,
        district_id=district_id,
        valid_from=now,
        valid_to=now + timedelta(hours=24),
        raw={"note": "replayed for demo"},
        received_at=now,
    )
