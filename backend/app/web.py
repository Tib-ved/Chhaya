"""Serves the built web app from this same service, so there is one address for everything.

The build (web/dist) is copied into app/static/web before a deploy; see scripts/stage_web.py.
With nothing there, nothing is registered and the service is an API only."""
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

DEFAULT_WEB_DIR = Path(__file__).parent / "static" / "web"
# The screens. The app routes these in the browser, so each one is answered with the index page.
SPA_ROUTES = frozenset({
    "", "alert", "call", "visit", "report", "evidence", "team", "crew", "welcome",
    "regional", "regional/overview", "regional/weather", "regional/districts", "regional/critical",
    "regional/agent-tester", "regional/report", "regional/team", "regional/dispatch",
    "ward", "ward/overview", "ward/visits", "ward/map",
})
# Sections whose every path is a screen: /officer/districts/churu, /worker/residents/r08.
SPA_SECTIONS = frozenset({"officer", "worker"})
# First path segments that belong to the server. An unknown path under one is a 404, never the app.
RESERVED = frozenset({
    "api", "admin", "internal", "browser", "twilio", "auth", "dev",
    "health", "healthz", "login", "docs", "redoc", "openapi.json",
})
HOST = re.compile(r"[A-Za-z0-9.-]{1,255}(:[0-9]{1,5})?")
KEEP_FOR_EVER = {"Cache-Control": "public, max-age=31536000, immutable"}  # built names carry a hash
ASK_EACH_TIME = {"Cache-Control": "no-cache"}


def web_policy(host: str) -> dict[str, str]:
    """The content security policy for the app's page: its own files, Google Fonts and Google
    Maps, scripts from blob: (the microphone worklet), and the call's socket on this host."""
    sockets = f" wss://{host} ws://{host}" if HOST.fullmatch(host) else ""
    policy = {
        "default-src": "'none'",
        "script-src": "'self' blob: https://maps.googleapis.com",
        "style-src": "'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src": "'self' https://fonts.gstatic.com",
        "img-src": "'self' data: https://*.googleapis.com https://*.gstatic.com",
        "connect-src": "'self' https://*.googleapis.com" + sockets,
        "media-src": "'self' blob:",
        "worker-src": "'self' blob:",
        "base-uri": "'none'",
        "form-action": "'none'",
        "frame-ancestors": "'none'",
    }
    return {"Content-Security-Policy": "; ".join(f"{name} {value}" for name, value in policy.items())}


def register_web(app: FastAPI, directory: Path) -> bool:
    """Serve the web app found in `directory`. Returns False, registering nothing, if there is
    no index.html there. Call it after every other route: it answers whatever is left."""
    index = directory / "index.html"
    if not index.is_file():
        return False
    root = directory.resolve()
    index = index.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def web(path: str, request: Request) -> FileResponse:
        first = path.split("/", 1)[0]
        if first in RESERVED:
            raise HTTPException(status_code=404, detail="not found")
        if path.strip("/") in SPA_ROUTES or first in SPA_SECTIONS:
            headers = {**web_policy(request.headers.get("host", "")), **ASK_EACH_TIME}
            return FileResponse(index, media_type="text/html", headers=headers)
        try:
            wanted = (root / path).resolve()
            inside = wanted.is_relative_to(root) and wanted != index and wanted.is_file()
        except (OSError, ValueError):  # a name the file system cannot even look up
            inside = False
        if not inside:
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(wanted, headers=KEEP_FOR_EVER if first == "assets" else ASK_EACH_TIME)

    return True
