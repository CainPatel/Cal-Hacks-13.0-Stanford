"""Deterministic parsing of the strings printed on a pay app.

Everything here either returns the exact value printed or raises. Nothing
rounds, infers, or repairs: a cell we can't read with certainty is a parse
failure, not a guess.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


class CellParseError(ValueError):
    pass


# One number, optional $ and thousands separators, optional parentheses or a
# leading minus for negatives. Thousands groups must be well formed so that
# "1,23" or "12,3456" (likely misreads) are rejected instead of accepted.
_MONEY = re.compile(
    r"""^
    (?P<neg_open>\()?
    (?P<sign>-)?
    \$?
    (?P<num>\d{1,3}(?:,\d{3})+|\d+)
    (?P<frac>\.\d+)?
    (?P<neg_close>\))?
    $""",
    re.VERBOSE,
)
_DASHES = {"-", "–", "—"}


def _normalize(text: str) -> str:
    # Collapse internal spaces ("$ 1,234.00", "( 500 )") but not newlines:
    # a newline inside a numeric cell means two values were merged.
    if "\n" in text.strip():
        raise CellParseError(f"cell holds more than one line: {text!r}")
    return re.sub(r"\s+", "", text)


def parse_money(text: str | None, *, blank_is_zero: bool = True) -> Decimal:
    """Parse a printed dollar amount exactly.

    A blank cell or a lone dash is zero, the convention on AIA forms, unless
    blank_is_zero is False (for columns that must be filled in).
    """
    raw = _normalize(text or "")
    if raw == "" or raw in _DASHES:
        if blank_is_zero:
            return Decimal("0")
        raise CellParseError("required amount is blank")
    m = _MONEY.match(raw)
    if not m:
        raise CellParseError(f"not a dollar amount: {text!r}")
    if bool(m["neg_open"]) != bool(m["neg_close"]):
        raise CellParseError(f"unbalanced parentheses: {text!r}")
    if m["neg_open"] and m["sign"]:
        raise CellParseError(f"double negative: {text!r}")
    try:
        value = Decimal(m["num"].replace(",", "") + (m["frac"] or ""))
    except InvalidOperation as e:
        raise CellParseError(f"not a dollar amount: {text!r}") from e
    return -value if (m["neg_open"] or m["sign"]) else value


_PCT = re.compile(r"^(?P<sign>-)?(?P<num>\d+(?:\.\d+)?|\.\d+)(?P<pct>%)?$")


def parse_pct(text: str | None) -> tuple[Decimal, bool]:
    """Parse a percent cell. Returns (value, had_percent_sign).

    The value is returned as printed; callers decide whether a sheet that
    prints fractions (0.60) instead of points (60%) needs scaling.
    """
    raw = _normalize(text or "")
    if raw == "" or raw in _DASHES:
        return Decimal("0"), False
    m = _PCT.match(raw)
    if not m:
        raise CellParseError(f"not a percentage: {text!r}")
    value = Decimal(m["num"])
    return (-value if m["sign"] else value), bool(m["pct"])


# %y before %Y: strptime's %Y accepts "26" as year 0026.
_DATE_FORMATS = ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%b. %d, %Y")


def parse_date(text: str) -> date:
    """Parse a US-style form date. Day-first dates are not accepted."""
    cleaned = re.sub(r"\s+", " ", text.strip())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise CellParseError(f"unrecognized date: {text!r}")
