import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

COOKIE = "chhaya_demo_ops"


def assigned_role(settings, email):
    if not email:
        return None
    if email in {e.strip().lower() for e in settings.operations_admin_emails.split(",") if e.strip()}:
        return "admin", None
    worker = {e.lower(): w for e, w in settings.operations_crew_accounts.items()}.get(email)
    return ("crew", worker) if worker else None


@dataclass(frozen=True)
class Principal:
    owner: str
    role: str
    worker_id: str | None = None
    demo: bool = False

    def admin(self):
        if self.role != "admin":
            raise HTTPException(403, "administrator access required")


def check_origin(request: Request):
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "cross-site request refused")
    if origin:
        source = urlsplit(origin)
        target = urlsplit(str(request.url))
        local = source.hostname in ("localhost", "127.0.0.1") and target.hostname in ("localhost", "127.0.0.1")
        if not local and source.netloc != target.netloc:
            raise HTTPException(403, "cross-origin request refused")


def principal(request, auth, settings, store, now):
    check_origin(request)
    if auth.secret_matches(request.headers.get("x-internal-secret", "")):
        return Principal("internal", "admin")
    email = auth.email_from_cookies(request.cookies)
    if email:
        role = assigned_role(settings, email)
        if not role:
            raise HTTPException(403, "ask an administrator to configure your operations role")
        return Principal(hashlib.sha256(email.encode()).hexdigest(), *role)
    if auth.enabled:
        raise HTTPException(401, "sign in required")
    token = request.cookies.get(COOKIE, "")
    session = store.get("ops_sessions", hashlib.sha256(token.encode()).hexdigest()) if 20 <= len(token) <= 100 else None
    if not session or session["expires_at"] <= now:
        raise HTTPException(401, "open an operations demo session first")
    role = request.headers.get("x-demo-role", "admin")
    worker = request.headers.get("x-demo-worker")
    if role not in ("admin", "crew"):
        raise HTTPException(422, "invalid demo role")
    return Principal(session["id"], role, worker, True)


def new_demo_session(store, now):
    token = secrets.token_urlsafe(32)
    owner = hashlib.sha256(token.encode()).hexdigest()
    store.create("ops_sessions", owner, {"id": owner, "expires_at": now + timedelta(days=7)})
    return token
