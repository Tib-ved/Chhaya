"""Writes backend/openapi.json, the description of every route.

Run from backend/:  python -m scripts.export_openapi
It builds the app on an in-memory store, with no .env file, no real secret and no network, so the
file is the same on every machine. A test fails if the committed file is out of date."""
import json
from pathlib import Path

from app.api import create_app
from app.config import Settings
from app.db import MemoryStore
from app.dispatch import RecordingQueue

OUTPUT = Path(__file__).resolve().parents[1] / "openapi.json"


def build_openapi() -> dict:
    settings = Settings(_env_file=None, internal_secret="not-a-real-secret")
    return create_app(MemoryStore(), RecordingQueue(), settings).openapi()


def main() -> None:
    text = json.dumps(build_openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
