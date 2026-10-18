from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn

from app.api import create_app
from app.config import Settings
from app.db import MemoryStore
from app.demo import open_rounds
from app.dispatch import RecordingQueue
from seed.seed import build_all, load_seed


def main():
    store = MemoryStore()
    settings = Settings(_env_file=None, web_dir=str(Path(__file__).resolve().parents[2] / "web" / "dist"))
    load_seed(store, build_all([]))
    tz = ZoneInfo(settings.local_tz)
    now = datetime.now(timezone.utc)
    morning = now.astimezone(tz).replace(hour=7, minute=0, second=0, microsecond=0)
    queue = RecordingQueue()
    open_rounds(store, "churu", morning, tz)
    from app.replay import release_due
    release_due(store, queue, now, tz)
    uvicorn.run(create_app(store, queue, settings), host="127.0.0.1", port=8091)


if __name__ == "__main__":
    main()
