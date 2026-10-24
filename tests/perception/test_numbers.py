from datetime import date
from decimal import Decimal

import pytest

from perception.numbers import CellParseError, parse_date, parse_money, parse_pct


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$1,234.56", "1234.56"),
        ("1234.56", "1234.56"),
        ("$ 1,234.00", "1234.00"),
        ("(500.00)", "-500.00"),
        ("-500", "-500"),
        ("-$500.00", "-500.00"),
        ("0.00", "0.00"),
        ("", "0"),
        (None, "0"),
        ("-", "0"),
        ("—", "0"),
        ("1,000,000", "1000000"),
        ("12.345", "12.345"),  # kept exactly, not rounded
    ],
)
def test_parse_money(text, expected):
    assert parse_money(text) == Decimal(expected)


@pytest.mark.parametrize(
    "text",
    [
        "1,23",  # malformed thousands group
        "12,3456",
        "1.234,56",  # European format
        "(500",
        "500)",
        "(-500)",
        "1,000.00-",
        "100.00\n200.00",  # two values merged into one cell
        "12a",
        "$",
        "1234.",
    ],
)
def test_parse_money_rejects(text):
    with pytest.raises(CellParseError):
        parse_money(text)


def test_required_blank_money_raises():
    with pytest.raises(CellParseError):
        parse_money("", blank_is_zero=False)


@pytest.mark.parametrize(
    "text,value,sign",
    [("60%", "60", True), ("60.00 %", "60.00", True), ("60", "60", False), ("0.6", "0.6", False), ("", "0", False)],
)
def test_parse_pct(text, value, sign):
    assert parse_pct(text) == (Decimal(value), sign)


def test_parse_pct_rejects():
    with pytest.raises(CellParseError):
        parse_pct("sixty")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("10/31/2026", date(2026, 10, 31)),
        ("10/31/26", date(2026, 10, 31)),  # not year 0026
        ("2026-10-31", date(2026, 10, 31)),
        ("October 31, 2026", date(2026, 10, 31)),
        ("Oct 31, 2026", date(2026, 10, 31)),
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def test_parse_date_rejects_day_first():
    with pytest.raises(CellParseError):
        parse_date("31/10/2026")
