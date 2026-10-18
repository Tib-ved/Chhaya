import pytest

from app.config import Settings
from app.visitor import LimitReached
from app.visitor_phone import (
    allowed_codes, check_phone_limits, country_code, phone_key, reservation_usd, settled_usd,
)


def settings(**overrides):
    return Settings(_env_file=None, internal_secret="s3cret", **overrides)


@pytest.mark.parametrize("phone, expected", [
    ("+919812345678", "91"),
    ("+85291234567", "852"),
    ("+886912345678", "886"),
    ("+61412345678", "61"),
    ("+6591234567", "65"),
    ("+8613812345678", None),   # China: not on the list
    ("+14155550100", None),     # US: not on the list
    ("919812345678", None),     # no plus
    ("+91 98123 45678", None),  # spaces: the web sends E.164 only
    ("+91", None),
    ("", None),
])
def test_country_code(phone, expected):
    assert country_code(phone, allowed_codes(settings())) == expected


def test_every_listed_country_has_a_rate():
    s = settings()
    assert set(allowed_codes(s)) == {"91", "81", "82", "852", "886", "65", "60", "62", "63", "66", "84", "61", "64"}
    assert set(allowed_codes(s)) <= set(s.visitor_phone_rates_usd)


def test_the_reservation_is_the_longest_call_twilio_can_bill():
    s = settings(visitor_phone_rates_usd={"91": 0.10}, visitor_phone_countries="91")
    assert reservation_usd(s, "91") == pytest.approx(0.40)  # ceil((180 + 30) / 60) = 4 minutes


def test_settled_cost_rounds_up_to_the_minute():
    s = settings(visitor_phone_rates_usd={"91": 0.10}, visitor_phone_countries="91")
    assert settled_usd(s, "91", 0) == 0
    assert settled_usd(s, "91", 1) == pytest.approx(0.10)
    assert settled_usd(s, "91", 61) == pytest.approx(0.20)


def test_the_number_is_hashed_not_kept():
    key = phone_key("s3cret", "+919812345678")
    assert "9812345678" not in key and key == phone_key("s3cret", "+919812345678")
    assert key != phone_key("other", "+919812345678")


def entry(**fields):
    return {"channel": "phone", "phone_key": "p1", "address_key": "a1", "cost_usd": 0.4, **fields}


def test_a_number_gets_two_calls_a_day():
    check_phone_limits([entry()], settings(), "p1", "a1", 0.4)
    with pytest.raises(LimitReached) as refused:
        check_phone_limits([entry(), entry(failed=True)], settings(), "p1", "a9", 0.4)
    assert refused.value.reason == "phone_number_daily" and refused.value.status == 429


def test_an_address_gets_four_phone_calls_a_day():
    four = [entry(phone_key=f"p{n}") for n in range(4)]
    with pytest.raises(LimitReached) as refused:
        check_phone_limits(four, settings(), "new", "a1", 0.4)
    assert refused.value.reason == "address_phone_daily"


def test_browser_calls_do_not_count_toward_phone_limits():
    browser = [{"channel": "browser", "address_key": "a1"} for _ in range(10)]
    check_phone_limits(browser, settings(), "p1", "a1", 0.4)


def test_the_budget_counts_reserved_and_settled_spend():
    spent = [entry(phone_key=f"p{n}", address_key=f"a{n}", cost_usd=2.0) for n in range(3)]  # 6.0
    check_phone_limits(spent, settings(visitor_phone_daily_budget_usd=8.0), "new", "new", 2.0)  # exactly 8.0
    with pytest.raises(LimitReached) as refused:
        check_phone_limits(spent, settings(visitor_phone_daily_budget_usd=8.0), "new", "new", 2.01)
    assert refused.value.reason == "phone_budget"
