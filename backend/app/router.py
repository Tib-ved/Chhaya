"""Sends each request to the live app or the demo app (spec L10, L12).

Both apps are the same create_app, built on the live store and on the demo DatasetStore, so every
route, cache and background job works per dataset without knowing about modes."""
from contextlib import AsyncExitStack
from urllib.parse import parse_qs

# Live rounds are filled by replay only and never dial (spec L2), so every dial, dispatch and demo
# rebuild belongs to the demo dataset, including the callbacks Cloud Tasks and Cloud Scheduler post
# without a mode. /internal/sweep is not here: it runs per dataset (one scheduler job for each mode).
# A replayed alert opens a round only the demo dataset can dispatch, so it never lands in live data.
ALWAYS_DEMO_PREFIXES = (
    "/api/visitor", "/api/demo", "/browser", "/twilio", "/dev/call", "/admin/calls",
    "/internal/calls/", "/internal/rounds/", "/internal/demo/", "/admin/demo/", "/admin/alerts/replay",
)
ALWAYS_LIVE_PATHS = ("/internal/weather/fetch",)
# Under /twilio, but by mode: an operations notification's receipt belongs to the dataset its
# proposal is in, and its callback URL carries that mode (app.operations).
BY_MODE_PATHS = ("/twilio/operations/status",)


def pick_dataset(scope) -> str:
    path = scope.get("path", "")
    if path.startswith(ALWAYS_DEMO_PREFIXES) and path not in BY_MODE_PATHS:
        return "demo"
    if path in ALWAYS_LIVE_PATHS:
        return "live"
    mode = parse_qs(scope.get("query_string", b"").decode("latin-1")).get("mode", ["live"])[0]
    return "demo" if mode == "demo" else "live"


class ModeRouter:
    def __init__(self, live_app, demo_app) -> None:
        self.apps = {"live": live_app, "demo": demo_app}

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        await self.apps[pick_dataset(scope)](scope, receive, send)

    async def _lifespan(self, receive, send) -> None:
        await receive()  # lifespan.startup
        async with AsyncExitStack() as stack:
            try:
                for app in self.apps.values():
                    await stack.enter_async_context(app.router.lifespan_context(app))
            except Exception as error:
                await send({"type": "lifespan.startup.failed", "message": str(error)})
                return
            await send({"type": "lifespan.startup.complete"})
            await receive()  # lifespan.shutdown
        await send({"type": "lifespan.shutdown.complete"})


def build_apps(store, queue, settings, **create_app_kwargs) -> ModeRouter:
    from app.api import create_app
    from app.datasets import DatasetStore

    # Each app creates its own worker thread pool at start-up and sets it as the loop's default
    # executor; the second one to start wins. Harmless: both pools are the same size and both live.
    live = create_app(DatasetStore(store, "live"), queue, settings, **create_app_kwargs)
    # One warm-up is enough: the voice client is shared by both apps (app.call.live._clients).
    demo_settings = settings.model_copy(update={"live_warm_up": False})
    demo = create_app(DatasetStore(store, "demo"), queue, demo_settings, **create_app_kwargs)
    return ModeRouter(live, demo)
