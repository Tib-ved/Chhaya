"""Copy the built web app into the backend, so that a deploy of backend/ carries it.

Run from backend/:  python scripts/stage_web.py
Build the web app first (in web/: npm ci && npm run build)."""
import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
BUILT = BACKEND.parent / "web" / "dist"
STAGED = BACKEND / "app" / "static" / "web"


def main() -> int:
    if not (BUILT / "index.html").is_file():
        print(f"No built web app at {BUILT}. Run `npm ci && npm run build` in web/ first.")
        return 1
    if STAGED.exists():
        shutil.rmtree(STAGED)
    shutil.copytree(BUILT, STAGED)
    files = sum(1 for path in STAGED.rglob("*") if path.is_file())
    print(f"Staged {files} files from {BUILT} to {STAGED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
