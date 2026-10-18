"""A visitor's test call on their own phone: which numbers may be rung, what a call may cost,
and the limits that keep a day's spend under the budget. The ledger never holds the number,
only a keyed hash of it."""
import hmac
import math
import re

from app.config import Settings
from app.visitor import LimitReached

E164 = re.compile(r"\+[1-9]\d{7,14}")
PROVIDER_GRACE_SECONDS = 30  # Twilio's own time limit sits this far above the agent's


def allowed_codes(settings: Settings) -> list[str]:
    return [code.strip().lstrip("+") for code in settings.visitor_phone_countries.split(",") if code.strip()]


def country_code(phone: str, allowed: list[str]) -> str | None:
    """The calling code of an E.164 number if it is on the list, else None."""
    if not isinstance(phone, str) or not E164.fullmatch(phone):
        return None
    digits = phone[1:]
    for code in sorted(allowed, key=len, reverse=True):  # 852 before 85, if both were listed
        if digits.startswith(code):
            return code
    return None


def phone_key(secret: str, phone: str) -> str:
    return hmac.new(secret.encode(), f"phone:{phone}".encode(), "sha256").hexdigest()[:20]


def _minutes(seconds: int) -> int:
    return max(0, math.ceil(seconds / 60))


def reservation_usd(settings: Settings, code: str) -> float:
    """The most a call can cost: Twilio bills whole minutes up to its own time limit."""
    longest = settings.visitor_phone_max_seconds + PROVIDER_GRACE_SECONDS
    return round(settings.visitor_phone_rates_usd[code] * _minutes(longest), 4)


def settled_usd(settings: Settings, code: str, duration_s: int) -> float:
    return round(settings.visitor_phone_rates_usd[code] * _minutes(duration_s), 4)


def check_phone_limits(
    entries_today: list[dict], settings: Settings, key: str, address_key: str, cost: float
) -> None:
    """Raise LimitReached if one more phone call would break a phone-only limit. Every phone
    call asked for counts toward its number and address, failed or not: someone ringing a
    stranger must not get more tries by making calls fail."""
    phone = [entry for entry in entries_today if entry.get("channel") == "phone"]
    if sum(1 for entry in phone if entry.get("phone_key") == key) >= settings.visitor_phone_calls_per_number:
        raise LimitReached("phone_number_daily", 429)
    if sum(1 for entry in phone if entry.get("address_key") == address_key) >= settings.address_phone_calls_per_day:
        raise LimitReached("address_phone_daily", 429)
    spent = sum(float(entry.get("cost_usd") or 0) for entry in phone)
    if spent + cost > settings.visitor_phone_daily_budget_usd + 1e-9:
        raise LimitReached("phone_budget", 429)
