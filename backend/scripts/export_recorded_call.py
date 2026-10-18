"""Turn one finished call into the recorded call that visitors without a microphone watch.

Run from backend/, with the same settings as the server that holds the call:
    python scripts/export_recorded_call.py CALL_ID [AUDIO_FILE]
Use one of the team's own browser calls as a synthetic character. AUDIO_FILE (.webm, .mp3,
.m4a or .wav) is a recording of that call made by the caller; it is copied beside the timeline.
Both files are committed: check that nothing personal was said on the call."""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.encoders import jsonable_encoder  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_store  # noqa: E402
from app.dispatch import FINAL_STATUSES  # noqa: E402
from app.feed import load_feed  # noqa: E402
from app.recorded import AUDIO_NAMES, RECORDED_DIR, TIMELINE_NAME, build_timeline  # noqa: E402


def main(arguments: list[str]) -> int:
    if not 1 <= len(arguments) <= 2:
        print(__doc__)
        return 2
    store = get_store(get_settings())
    call = store.get("calls", arguments[0])
    if call is None or call["status"] not in FINAL_STATUSES:
        print("That call does not exist or has not finished.")
        return 1
    timeline = build_timeline(call, load_feed(store, call))
    text = json.dumps(jsonable_encoder(timeline), ensure_ascii=False, indent=1)
    (RECORDED_DIR / TIMELINE_NAME).write_text(text, encoding="utf-8")
    print(f"Wrote {len(timeline['events'])} events to {RECORDED_DIR / TIMELINE_NAME}")
    if len(arguments) == 2:
        source = Path(arguments[1])
        wanted = f"recorded_call{source.suffix.lower()}"
        if wanted not in AUDIO_NAMES:
            print(f"The audio must be one of: {', '.join(AUDIO_NAMES)}")
            return 1
        for name in AUDIO_NAMES:
            (RECORDED_DIR / name).unlink(missing_ok=True)
        shutil.copyfile(source, RECORDED_DIR / wanted)
        print(f"Copied the audio to {RECORDED_DIR / wanted}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
