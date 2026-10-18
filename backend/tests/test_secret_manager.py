import base64

import pytest

import app.secret_manager as sm
from app.secret_manager import read_secret


@pytest.fixture(autouse=True)
def empty_cache():
    sm._cache.clear()
    yield
    sm._cache.clear()


def answering(value: str, asked: list[str]):
    def get(url: str) -> dict:
        asked.append(url)
        return {"payload": {"data": base64.b64encode(value.encode()).decode()}}
    return get


def test_reads_the_latest_version_trimmed_and_once():
    asked: list[str] = []
    get = answering("the-key\n", asked)
    assert read_secret("chhaya-pilot", "weather-api-key", get) == "the-key"
    assert read_secret("chhaya-pilot", "weather-api-key", get) == "the-key"
    assert asked == ["https://secretmanager.googleapis.com/v1/projects/chhaya-pilot/secrets/weather-api-key/versions/latest:access"]


def test_no_project_or_name_asks_nothing():
    asked: list[str] = []
    assert read_secret("", "weather-api-key", answering("k", asked)) == ""
    assert read_secret("chhaya-pilot", "", answering("k", asked)) == ""
    assert asked == []


def test_a_failure_is_empty_and_not_kept():
    def refused(url: str) -> dict:
        raise PermissionError("403")

    assert read_secret("chhaya-pilot", "weather-api-key", refused) == ""
    assert read_secret("chhaya-pilot", "weather-api-key", answering("k", [])) == "k"
