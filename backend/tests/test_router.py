from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.router import ModeRouter


def labelled(name, started):
    app = FastAPI()

    @app.on_event("startup")
    async def up():
        started.append(name)

    @app.get("/{path:path}")
    def which(path: str):
        return {"app": name}

    return app


def client():
    started = []
    router = ModeRouter(labelled("live", started), labelled("demo", started))
    return TestClient(router), started


def test_mode_picks_the_app_and_unknown_modes_are_live():
    c, _ = client()
    with c:
        assert c.get("/api/region").json()["app"] == "live"
        assert c.get("/api/region?mode=demo").json()["app"] == "demo"
        assert c.get("/api/region?mode=live").json()["app"] == "live"
        assert c.get("/api/region?mode=foo").json()["app"] == "live"


def test_visitor_and_call_paths_are_always_demo_and_the_fetch_always_live():
    c, _ = client()
    with c:
        for path in ("/api/visitor/calls", "/api/demo", "/twilio/status", "/dev/call"):
            assert c.get(path + "?mode=live").json()["app"] == "demo", path
        assert c.get("/internal/weather/fetch?mode=demo").json()["app"] == "live"


def test_both_apps_start_up():
    c, started = client()
    with c:
        pass
    assert sorted(started) == ["demo", "live"]


def test_dials_dispatches_and_demo_rebuilds_are_always_demo():
    c, _ = client()
    with c:
        for path in ("/internal/calls/churu_heat_20261015_c1/dial", "/internal/rounds/churu_heat_20261015/dispatch",
                     "/internal/demo/rebuild", "/admin/demo/rebuild"):
            assert c.get(path).json()["app"] == "demo", path
            assert c.get(path + "?mode=live").json()["app"] == "demo", path


def test_the_sweep_follows_mode():
    c, _ = client()
    with c:
        assert c.get("/internal/sweep").json()["app"] == "live"
        assert c.get("/internal/sweep?mode=demo").json()["app"] == "demo"
