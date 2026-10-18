import logging

# Nothing else sets up logging, so without this the app's own INFO lines (a call's pickup and
# reply times among them) never reach the service's logs. Libraries stay at WARNING: httpx at
# INFO would log each Weather API address, key and all.
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("app").setLevel(logging.INFO)

from app.call.twilio_bridge import build_telephony, warn_if_phone_calls_impossible
from app.config import check_settings, get_settings
from app.datasets import DatasetStore
from app.db import get_store
from app.router import build_apps
from app.tasks import build_queue
from app.textgen import make_text_model

settings = get_settings()
check_settings(settings)
store = get_store(settings)

if settings.seed_on_start and settings.store == "memory":
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from app.demo import open_rounds
    from seed.seed import build_all, load_seed

    phones = [p.strip() for p in settings.team_phones.split(",") if p.strip()]
    load_seed(store, build_all(phones))  # the register is shared by both datasets
    if settings.demo_district_id == "churu":  # the seed's district; its heat day is the demo dataset's
        from app.dispatch import RecordingQueue
        from app.replay import release_due

        tz = ZoneInfo(settings.local_tz)
        now = datetime.now(timezone.utc)
        morning = now.astimezone(tz).replace(hour=7, minute=0, second=0, microsecond=0)
        demo_store = DatasetStore(store, "demo")
        open_rounds(demo_store, settings.demo_district_id, morning, tz)
        release_due(demo_store, RecordingQueue(), now, tz)

warn_if_phone_calls_impossible(settings)
place_call, validate_twilio = build_telephony(settings)
open_session = None
if settings.fake_live:
    from app.call.fake_live import open_fake_session as open_session

# Live and demo: one app each, picked per request by its mode (app.router).
app = build_apps(
    store, build_queue(settings), settings,
    open_session=open_session, place_call=place_call, validate_twilio=validate_twilio,
    text_model=make_text_model(settings),
)
