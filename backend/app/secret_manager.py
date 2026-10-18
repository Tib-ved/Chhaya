"""Reads a secret from Secret Manager, for settings a developer has not put in backend/.env.

Uses the Secret Manager REST API with Application Default Credentials (on a laptop:
`gcloud auth application-default login`, as an account with Secret Accessor on the project), so
no client library is needed. Cloud Run is given its secrets as environment variables and never
comes here. A secret is read at most once per process; a failure is logged by name, never value."""
import base64
import logging
from typing import Callable

logger = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

_cache: dict[tuple[str, str], str] = {}


def _authorised_get(url: str) -> dict:
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=SCOPES)
    response = AuthorizedSession(credentials).get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def read_secret(project: str, name: str, get: Callable[[str], dict] = _authorised_get) -> str:
    """The latest version of secret `name` in `project`, trimmed; "" if it cannot be read."""
    if not project or not name:
        return ""
    if (project, name) in _cache:
        return _cache[(project, name)]
    url = f"https://secretmanager.googleapis.com/v1/projects/{project}/secrets/{name}/versions/latest:access"
    try:
        value = base64.b64decode(get(url)["payload"]["data"]).decode("utf-8").strip()
    except Exception as error:
        logger.warning("secret %s in %s could not be read from Secret Manager: %s", name, project, type(error).__name__)
        return ""  # not cached: a later try (after signing in) may work
    _cache[(project, name)] = value
    return value
