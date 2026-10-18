"""A visitor's own call: a temporary resident and one browser call in the pinned demo round,
and the limits on how many such calls may be started.

The limits are counted from a small ledger in the store (one record per call asked for, marked
`started` once its agent is started), so the ledger is shared across instances and restarts.
A call whose agent never started is kept, marked `failed`: it counts toward nothing but the
day's ticket ceiling and its address's failed starts.
The check-then-record step is exact only within one process; across instances a burst can
overshoot a limit slightly. The ledger keeps a keyed hash of the network address, never the
address itself."""
import hmac
import ipaddress
import math
import re
import secrets
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.call.browser_bridge import TICKET_SECONDS, new_ticket
from app.config import Settings
from app.db import Store
from app.demo import pinned_round_id
from app.demo_cards import CARDS
from app.dispatch import FINAL_STATUSES, call_id_for
from app.models import VISITOR_USAGE, Call, Consent, Resident
from app.risk import risk_score

VISITOR_ID = re.compile(r"[A-Za-z0-9-]{16,64}")  # what crypto.randomUUID() makes, with room to spare
HOUR = timedelta(hours=1)
CONNECT_SECONDS = 120  # longest a call may take from ticket to first word and still hold a line
_checking = threading.Lock()  # one check-and-record at a time in this process; other instances are not covered


class NoDemoRound(Exception):
    """There is no pinned demo round to call in (it is being rebuilt, or was never built)."""


class LimitReached(Exception):
    def __init__(self, reason: str, status: int, retry_after_s: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.retry_after_s = retry_after_s


def clean_visitor_id(value: object) -> str | None:
    """The visitor id if it has the expected shape, else None. It names a browser, not a person."""
    return value if isinstance(value, str) and VISITOR_ID.fullmatch(value) else None


def _network(address: str) -> str:
    """What one caller's address stands for when counting. An IPv6 caller is given a whole /64
    and can use any address in it, so the /64 is what counts. Anything else is left as it is."""
    try:
        parsed = ipaddress.ip_address(address.strip())
    except ValueError:
        return address
    if parsed.version == 4:
        return address
    if parsed.ipv4_mapped is not None:  # an IPv4 caller written as IPv6: all of them share one /64
        return str(parsed.ipv4_mapped)
    return f"{ipaddress.IPv6Address(int(parsed) >> 64 << 64)}/64"


def _address_key(secret: str, address: str) -> str:
    return hmac.new(secret.encode(), _network(address).encode(), "sha256").hexdigest()[:20]


def _day(moment: datetime, tz: ZoneInfo) -> str:
    return f"{moment.astimezone(tz):%Y%m%d}"


def _recent_usage(store: Store, now: datetime, tz: ZoneInfo) -> list[dict]:
    """Today's ledger, plus yesterday's while the past hour still reaches into it."""
    days = sorted({_day(now, tz), _day(now - HOUR, tz)})
    return [entry for day in days for entry in store.query(VISITOR_USAGE, day=day)]


def _lines_in_use(store: Store, entries: list[dict], now: datetime, settings: Settings) -> int:
    """Visitor calls that hold a line now: waiting to connect, connecting, or talking.
    A ticket nobody used, and a call left unfinished by a crash, stop counting on their own."""
    longest = timedelta(seconds=settings.max_call_seconds + CONNECT_SECONDS)
    in_use = 0
    for entry in entries:
        age = now - entry["at"]
        if age > longest:
            continue
        call = store.get("calls", entry["id"])
        if call is None:
            # Being created (its ledger entry is written first) counts while fresh. A call whose
            # agent had started, and whose record a rebuild has removed, may still be talking.
            held = longest if entry.get("started") else timedelta(seconds=TICKET_SECONDS)
            if age <= held:
                in_use += 1
            continue
        if call["status"] in FINAL_STATUSES:
            continue
        if call["status"] == "queued" and age > timedelta(seconds=TICKET_SECONDS):
            continue
        in_use += 1
    return in_use


def _check_limits(
    store: Store, settings: Settings, visitor_id: str, key: str, now: datetime, tz: ZoneInfo
) -> None:
    entries = _recent_usage(store, now, tz)
    today = _day(now, tz)
    asked_today = [entry for entry in entries if entry["day"] == today]
    # Only a call whose agent was started uses up the day's calls: a ticket costs nothing, so
    # tickets nobody uses must not shut everyone else out. They have a far higher ceiling of
    # their own, so that the ledger cannot grow without bound either.
    started_today = sum(1 for entry in asked_today if entry.get("started"))
    if started_today >= settings.visitor_calls_per_day or len(asked_today) >= settings.visitor_tickets_per_day:
        raise LimitReached("daily_cap", 429)
    counted = [entry for entry in entries if not entry.get("failed")]
    failed_here = [entry for entry in entries if entry.get("failed") and entry["address_key"] == key]
    for reason, mine, limit in (
        ("visitor_hourly", [e for e in counted if e["visitor_id"] == visitor_id], settings.visitor_calls_per_hour),
        ("address_hourly", [e for e in counted if e["address_key"] == key], settings.address_calls_per_hour),
        ("address_failed_starts", failed_here, settings.address_failed_starts_per_hour),
    ):
        within_hour = sorted(e["at"] for e in mine if e["at"] > now - HOUR)
        if len(within_hour) >= limit:
            # when enough of them have left the past hour for one more to fit
            frees_at = within_hour[len(within_hour) - limit] + HOUR
            raise LimitReached(reason, 429, max(1, math.ceil((frees_at - now).total_seconds())))
    if _lines_in_use(store, entries, now, settings) >= settings.visitor_calls_at_once:
        raise LimitReached("lines_busy", 503)


def create_visitor_call(
    store: Store,
    settings: Settings,
    card_id: str,
    language: str,
    visitor_id: str,
    address: str,
    now: datetime,
    tz: ZoneInfo,
    phone: str | None = None,
) -> dict:
    """Start a visitor's call: a temporary resident copied from the card, one browser call in
    the pinned round, and the ticket that opens it. Returns {call_id, ticket, expires_in}.
    With `phone`, the call is a phone call to that number (already checked by the caller): no
    ticket, and the phone-only limits apply. Returns {call_id} then.

    Raises KeyError for an unknown card, NoDemoRound, or LimitReached. Nothing is written then."""
    card = CARDS.get(card_id)
    if card is None:
        resident_source = store.get("residents", card_id)
        if resident_source:
            card = {
                "id": resident_source["id"],
                "name": resident_source["name"],
                "name_hi": resident_source.get("name_hi", ""),
                "age": resident_source.get("age", 65),
                "ward_id": resident_source.get("ward_id") or "churu-ward-2",
                "lives_alone": resident_source.get("lives_alone", False),
                "outdoor_worker": resident_source.get("outdoor_worker", False),
                "conditions": resident_source.get("conditions", []),
                "offset": (0.0, 0.0),
                "role": f"You are {resident_source['name']}, {resident_source.get('age', 65)}.",
                "nudge": "Talk about how you are feeling in the heat today.",
            }
        elif card_id.startswith("test-") or card_id.startswith("custom_") or card_id.startswith("r") or "-" in card_id:
            card = dict(CARDS["alone_dizzy"])
            card["id"] = card_id
        else:
            raise KeyError(f"cards/{card_id}")
    round_id = pinned_round_id(store)
    current = store.get("rounds", round_id) if round_id else None
    ward = store.get("wards", card["ward_id"]) if current else None
    if ward is None and current:
        ward = next((w for w in store.query("wards") if w.get("district_id") == current.get("district_id")), None) or next(iter(store.query("wards")), None)
    if current is None or ward is None:
        raise NoDemoRound()

    resident_id = "v-" + secrets.token_hex(8)
    call_id = call_id_for(round_id, resident_id, 1)
    usage = {
        "id": call_id,
        "visitor_id": visitor_id,
        "address_key": _address_key(settings.internal_secret, address),
        "day": _day(now, tz),
        "at": now,
        "channel": "phone" if phone else "browser",
    }
    if phone:
        from app.visitor_phone import allowed_codes, check_phone_limits, country_code, phone_key, reservation_usd

        code = country_code(phone, allowed_codes(settings))
        if code is None or code not in settings.visitor_phone_rates_usd:
            raise ValueError("the number cannot be rung")  # the route checks first; this is a guard
        usage.update(phone_key=phone_key(settings.internal_secret, phone), country=code,
                     cost_usd=reservation_usd(settings, code))
    with _checking:
        _check_limits(store, settings, visitor_id, usage["address_key"], now, tz)
        if phone:
            today = [entry for entry in _recent_usage(store, now, tz) if entry["day"] == usage["day"]]
            check_phone_limits(today, settings, usage["phone_key"], usage["address_key"], usage["cost_usd"])
        store.set(VISITOR_USAGE, call_id, usage)
    try:
        resident = Resident(
            id=resident_id,
            name=card["name"],
            name_hi=card.get("name_hi", ""),
            phone=phone or "",  # a browser call is never dialled; a phone call's number is erased when it ends
            age=card["age"],
            language=language,
            district_id=current["district_id"],
            ward_id=card["ward_id"],
            lat=round(ward["lat"] + card["offset"][0], 6),
            lng=round(ward["lng"] + card["offset"][1], 6),
            lives_alone=card["lives_alone"],
            outdoor_worker=card["outdoor_worker"],
            conditions=card["conditions"],
            consent=Consent(given=True, at=now, method="demo visitor"),
            risk_score=risk_score(card["age"], card["lives_alone"], card["outdoor_worker"], card["conditions"]),
            visitor_id=visitor_id,
            card_id=card_id,
        )
        store.set("residents", resident_id, resident.model_dump())
        call = Call(
            id=call_id, round_id=round_id, resident_id=resident_id, channel="phone" if phone else "browser",
            visitor_id=visitor_id, created_at=now, **({} if phone else new_ticket(now)),
        )
        store.set("calls", call_id, call.model_dump())
    except Exception:
        store.delete(VISITOR_USAGE, call_id)  # a call that was never made is not counted
        try:
            store.delete("residents", resident_id)
        except Exception:
            pass  # best effort: the original error is the one to raise
        raise
    if phone:
        return {"call_id": call_id}
    return {"call_id": call_id, "ticket": call.browser_ticket, "expires_in": TICKET_SECONDS}


def latest_call(store: Store, visitor_id: str) -> dict | None:
    """The visitor's most recent call in the pinned round, or None."""
    round_id = pinned_round_id(store)
    calls = [c for c in store.query("calls", visitor_id=visitor_id) if c["round_id"] == round_id]
    return max(calls, key=lambda c: (c["created_at"], c["id"])) if calls else None


def forget_old_usage(store: Store, now: datetime, tz: ZoneInfo) -> None:
    """Drop ledger records older than yesterday. Today's and yesterday's still count."""
    keep = {_day(now, tz), _day(now - timedelta(days=1), tz)}
    for entry in store.query(VISITOR_USAGE):
        if entry["day"] not in keep:
            store.delete(VISITOR_USAGE, entry["id"])
