"""Queues calls on Cloud Tasks, which asks the dial endpoint for each one when it is due.

Uses the Cloud Tasks REST API with the service's own credentials, so no client library is
needed. Tasks are unnamed: the same call can be queued again (the sweep does that for a call
whose task was refused), and the dial endpoint's atomic claim is what prevents a second call."""
import logging
from datetime import datetime, timezone
from typing import Callable

from app.dispatch import RecordingQueue, TaskQueue

logger = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _authorised_post() -> Callable[[str, dict], None]:
    """A function that POSTs JSON as this service. The session is made on first use."""
    sessions: list = []

    def post(url: str, body: dict) -> None:
        if not sessions:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(scopes=SCOPES)
            sessions.append(AuthorizedSession(credentials))
        response = sessions[0].post(url, json=body, timeout=10)
        response.raise_for_status()

    return post


class CloudTasksQueue:
    def __init__(
        self,
        project: str,
        location: str,
        queue: str,
        target: str,
        secret: str,
        service_account: str = "",
        post: Callable[[str, dict], None] | None = None,
    ) -> None:
        self._url = f"https://cloudtasks.googleapis.com/v2/projects/{project}/locations/{location}/queues/{queue}/tasks"
        self._target = target.rstrip("/")
        self._secret = secret
        self._service_account = service_account
        self._post = post or _authorised_post()

    def enqueue(self, call_id: str, run_at: datetime) -> None:
        """Ask for the call to be dialled at run_at. Never raises: a call that could not be
        queued stays `queued` with its due time, and the sweep queues it again."""
        request: dict = {
            "httpMethod": "POST",
            "url": f"{self._target}/internal/calls/{call_id}/dial",
            "headers": {"X-Internal-Secret": self._secret},
        }
        if self._service_account:
            # For a service that only lets signed-in callers through Cloud Run's own door.
            request["oidcToken"] = {"serviceAccountEmail": self._service_account, "audience": self._target}
        task = {
            "httpRequest": request,
            "scheduleTime": run_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            self._post(self._url, {"task": task})
        except Exception as error:  # only the type: a message can carry the request and its secret
            logger.error("could not queue call %s (%s)", call_id, type(error).__name__)


def build_queue(settings) -> TaskQueue:
    """The real queue when one is configured, else one that only remembers (tests, local use)."""
    if not settings.tasks_queue.strip():
        return RecordingQueue()
    return CloudTasksQueue(
        project=settings.gcp_project,
        location=settings.tasks_location,
        queue=settings.tasks_queue.strip(),
        target=settings.tasks_target.strip(),
        secret=settings.internal_secret,
        service_account=settings.tasks_service_account.strip(),
    )
