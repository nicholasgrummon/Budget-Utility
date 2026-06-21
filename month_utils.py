"""Helpers for resolving free-text month references (e.g. "may", "2026-05", "last month")."""
import calendar
import re
from datetime import date

MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def last_day_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def parse_month_reference(text: str | None, today: date) -> tuple[int, int] | None:
    """Resolve a free-text month reference relative to `today`. Returns (year, month), or None if unparseable."""
    if not text or not text.strip():
        return (today.year, today.month)
    text = text.strip().lower()

    if text in ("last month", "previous month"):
        return previous_month(today.year, today.month)
    if text in ("this month", "current month"):
        return (today.year, today.month)

    m = re.match(r"^(\d{4})-(\d{1,2})$", text)
    if m:
        return (int(m.group(1)), int(m.group(2)))

    m = re.match(r"^([a-z]+)(?:\s+(\d{4}))?$", text)
    if m and m.group(1) in MONTH_NAMES:
        month = MONTH_NAMES[m.group(1)]
        year = int(m.group(2)) if m.group(2) else (today.year if month <= today.month else today.year - 1)
        return (year, month)

    return None
