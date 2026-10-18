from datetime import datetime, timezone

import pytest

from app.config import Settings, check_settings
from app.dispatch import RecordingQueue
from app.tasks import CloudTasksQueue, build_queue

STRONG = "a-long-random-value-not-in-any-repository"
RUN_AT = datetime(2026, 10, 14, 4, 30, 15, tzinfo=timezone.utc)


def a_queue(posted, **overrides):
    arguments = dict(
        project="chhaya-pilot", location="asia-south1", queue="chhaya-calls",
        target="https://chhaya.example.test", secret=STRONG,
        service_account="runner@chhaya-pilot.iam.gserviceaccount.com",
        post=lambda url, body: posted.append((url, body)),
    )
    arguments.update(overrides)
    return CloudTasksQueue(**arguments)


def test_a_task_asks_the_dial_endpoint_at_the_due_time():
    posted = []
    a_queue(posted).enqueue("churu_heat_20261014_r01_1", RUN_AT)
    (url, body), = posted
    assert url == "https://cloudtasks.googleapis.com/v2/projects/chhaya-pilot/locations/asia-south1/queues/chhaya-calls/tasks"
    task = body["task"]
    assert "name" not in task  # unnamed: the same call can be queued again later
    assert task["scheduleTime"] == "2026-10-14T04:30:15Z"
    request = task["httpRequest"]
    assert request["httpMethod"] == "POST"
    assert request["url"] == "https://chhaya.example.test/internal/calls/churu_heat_20261014_r01_1/dial"
    assert request["headers"] == {"X-Internal-Secret": STRONG}
    assert request["oidcToken"] == {
        "serviceAccountEmail": "runner@chhaya-pilot.iam.gserviceaccount.com",
        "audience": "https://chhaya.example.test",
    }


def test_without_a_service_account_no_identity_token_is_asked_for():
    posted = []
    a_queue(posted, service_account="").enqueue("c1", RUN_AT)
    assert "oidcToken" not in posted[0][1]["task"]["httpRequest"]


def test_a_queue_failure_is_logged_and_never_raised(caplog):
    def refuse(url, body):
        raise ConnectionError(f"could not reach the queue with {STRONG}")

    a_queue([], post=refuse).enqueue("c1", RUN_AT)
    assert "c1" in caplog.text and "ConnectionError" in caplog.text
    assert STRONG not in caplog.text  # only the error's type is logged


def test_the_queue_is_chosen_from_the_settings():
    assert isinstance(build_queue(Settings(_env_file=None)), RecordingQueue)
    settings = Settings(
        _env_file=None, internal_secret=STRONG, gcp_project="chhaya-pilot",
        tasks_queue="chhaya-calls", tasks_target="https://chhaya.example.test/",
    )
    queue = build_queue(settings)
    assert isinstance(queue, CloudTasksQueue)
    posted = []
    queue._post = lambda url, body: posted.append(body)
    queue.enqueue("c1", RUN_AT)
    assert posted[0]["task"]["httpRequest"]["url"] == "https://chhaya.example.test/internal/calls/c1/dial"


@pytest.mark.parametrize("missing", ["tasks_target", "gcp_project"])
def test_a_half_configured_queue_stops_start_up(missing):
    values = dict(internal_secret=STRONG, gcp_project="chhaya-pilot", tasks_queue="chhaya-calls", tasks_target="https://x.example.test")
    values[missing] = ""
    with pytest.raises(RuntimeError, match="TASKS_QUEUE"):
        check_settings(Settings(_env_file=None, **values), env={})


def test_the_target_must_be_an_https_address():
    settings = Settings(_env_file=None, internal_secret=STRONG, gcp_project="p", tasks_queue="q", tasks_target="http://x.example.test")
    with pytest.raises(RuntimeError, match="TASKS_TARGET"):
        check_settings(settings, env={})


def test_a_queue_with_the_default_secret_stops_start_up():
    settings = Settings(_env_file=None, gcp_project="p", tasks_queue="q", tasks_target="https://x.example.test")
    with pytest.raises(RuntimeError, match="INTERNAL_SECRET"):
        check_settings(settings, env={})
