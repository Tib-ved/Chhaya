import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.dispatch import RecordingQueue
from app.web import SPA_ROUTES

HTML = {"Accept": "text/html"}


@pytest.fixture
def staged(tmp_path):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>Chhaya</title><div id=root></div>", encoding="utf-8")
    (web / "assets" / "app-1a2b.js").write_text("console.log('app')", encoding="utf-8")
    (web / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("not for anyone", encoding="utf-8")
    return web


def client_for(store, now, web_dir) -> TestClient:
    settings = Settings(_env_file=None, internal_secret="s3cret", web_dir=str(web_dir))
    return TestClient(create_app(store, RecordingQueue(), settings, clock=lambda: now))


def test_the_routes_of_the_web_app():
    assert SPA_ROUTES == {"", "alert", "call", "visit", "report", "evidence", "team", "crew", "welcome", "regional", "regional/overview", "regional/weather", "regional/districts", "regional/critical", "regional/agent-tester", "regional/report", "regional/team", "regional/dispatch", "ward", "ward/overview", "ward/visits", "ward/map"}


@pytest.mark.parametrize("path", ["/", "/alert", "/call", "/visit", "/report", "/evidence", "/team", "/visit/", "/crew", "/welcome", "/regional/overview", "/regional/weather", "/regional/districts", "/regional/critical", "/regional/agent-tester", "/regional/report", "/regional/team", "/regional/dispatch", "/ward/overview", "/ward/visits", "/ward/map"])
def test_every_screen_gets_the_index_page(store, now, staged, path):
    response = client_for(store, now, staged).get(path, headers=HTML)
    assert response.status_code == 200
    assert "<div id=root>" in response.text
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"
    policy = response.headers["content-security-policy"]
    assert "default-src 'none'" in policy and "frame-ancestors 'none'" in policy
    assert "wss://testserver" in policy  # the call's socket, on this same host


def test_built_files_are_served_and_assets_may_be_kept_for_ever(store, now, staged):
    client = client_for(store, now, staged)
    script = client.get("/assets/app-1a2b.js")
    assert script.status_code == 200 and "console.log" in script.text
    assert "immutable" in script.headers["cache-control"]
    icon = client.get("/favicon.svg")
    assert icon.status_code == 200
    assert "immutable" not in icon.headers.get("cache-control", "")


@pytest.mark.parametrize("path", [
    "/assets/../../secret.txt", "/../secret.txt", "/assets/%2e%2e/%2e%2e/secret.txt",
    "/assets/..%2f..%2fsecret.txt", "/assets/nope.js", "/unknown-screen", "/assets/",
])
def test_nothing_outside_the_web_directory_is_served(store, now, staged, path):
    response = client_for(store, now, staged).get(path)
    assert response.status_code == 404
    assert "not for anyone" not in response.text


@pytest.mark.parametrize("path", ["/api/nope", "/internal/nope", "/admin/nope", "/auth/nope", "/twilio/nope", "/browser/nope"])
def test_unknown_api_paths_are_still_not_found(store, now, staged, path):
    response = client_for(store, now, staged).get(path, headers=HTML)
    assert response.status_code == 404
    assert "<div id=root>" not in response.text


def test_the_api_still_answers_when_the_web_app_is_staged(store, now, staged):
    client = client_for(store, now, staged)
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/api/rounds/current").json() == {"round": None}


def test_with_nothing_staged_the_service_is_as_before(store, now, tmp_path):
    client = client_for(store, now, tmp_path / "empty")
    assert client.get("/").status_code == 404
    assert client.get("/health").json() == {"ok": True}


@pytest.mark.parametrize("path", ["/officer", "/officer/districts", "/officer/districts/churu", "/officer/wards/churu-ward-2", "/officer/residents/r08", "/officer/test", "/worker", "/worker/residents/r08"])
def test_every_role_screen_gets_the_index_page(store, now, staged, path):
    response = client_for(store, now, staged).get(path, headers=HTML)
    assert response.status_code == 200 and "<div id=root>" in response.text
    assert response.headers["cache-control"] == "no-cache"
