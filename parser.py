"""Free-text message parsing for the budget bot.

Supported phrasings (case-insensitive):
  "groceries: Kroger $56.10, 06-20-2026"            -> add expense (date optional, defaults to today)
  "groceries: Kroger $56.10, 06-20-2026, ran out"   -> add expense with description
  "groceries remaining budget" / "groceries remaining" / "groceries left" / "groceries budget"
                                                     -> remaining budget query (current month)
  "groceries remaining budget for may" / "groceries left in 2026-05"
                                                     -> remaining budget query for a past month
  "dashboard" / "summary"                           -> full dashboard, current month
  "dashboard for last month" / "summary in april"   -> full dashboard for a past month
  "set groceries budget 250" / "set budget groceries 250"
                                                     -> set/update an allowance (effective from today)
  "archive may" / "archive 2026-05"                 -> force a CSV re-export of that month into Archive/
  "groceries: remove Kroger 06-20-2026" / "groceries: delete Kroger $56.10"
                                                     -> remove a logged expense. Item/amount/date are all
                                                        optional — provide whichever combination uniquely
                                                        identifies the entry. If omitted, the date defaults
                                                        to "this month" rather than all-time.
  "view groceries" / "show groceries for may" / "list groceries in 2026-05"
                                                     -> list every purchase in a category for a month
                                                        (defaults to this month)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from categories import resolve_category
from month_utils import parse_month_reference

ADD_EXPENSE_RE = re.compile(
    r"^\s*(?P<category>[A-Za-z]+)\s*:\s*(?P<item>.+?)\s*\$\s*(?P<amount>\d+(?:\.\d{1,2})?)"
    r"\s*(?:,\s*(?P<date>\d{2}-\d{2}-\d{4}))?\s*(?:,\s*(?P<description>.+))?\s*$"
)

REMAINING_RE = re.compile(
    r"^\s*(?P<category>[A-Za-z]+)\s+(?:remaining\s+budget|remaining|left|budget)"
    r"(?:\s+(?:for|in)\s+(?P<month>.+))?\s*\??\s*$"
)

DASHBOARD_RE = re.compile(
    r"^\s*(?:dashboard|summary|status)(?:\s+(?:for|in)\s+(?P<month>.+))?\s*$"
)

SET_BUDGET_RE = re.compile(
    r"^\s*set\s+(?:budget\s+(?P<category1>[A-Za-z]+)|(?P<category2>[A-Za-z]+)\s+budget)"
    r"(?:\s+to)?\s+\$?(?P<amount>\d+(?:\.\d{1,2})?)\s*$"
)

ARCHIVE_RE = re.compile(r"^\s*archive\s+(?P<month>.+)\s*$")

REMOVE_RE = re.compile(
    r"^\s*(?P<category>[A-Za-z]+)\s*:\s*(?:remove|delete)\s+(?P<rest>.+?)\s*$",
    re.IGNORECASE,
)
REMOVE_AMOUNT_TOKEN_RE = re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)")
REMOVE_DATE_TOKEN_RE = re.compile(r"\b(\d{2}-\d{2}-\d{4})\b")

VIEW_RE = re.compile(
    r"^\s*(?:view|show|list)\s+(?P<category>[A-Za-z]+)(?:\s+(?:for|in)\s+(?P<month>.+))?\s*$",
    re.IGNORECASE,
)


@dataclass
class AddExpense:
    category: str
    item: str
    amount: float
    date: date
    description: str = ""


@dataclass
class RemainingQuery:
    category: str
    year: int
    month: int


@dataclass
class DashboardQuery:
    year: int
    month: int


@dataclass
class SetBudget:
    category: str
    amount: float


@dataclass
class ArchiveMonth:
    year: int
    month: int


@dataclass
class RemoveExpense:
    category: str
    item: str | None = None
    amount: float | None = None
    date: date | None = None


@dataclass
class ViewCategory:
    category: str
    year: int
    month: int


@dataclass
class ParseError:
    message: str


ParsedCommand = (
    AddExpense
    | RemainingQuery
    | DashboardQuery
    | SetBudget
    | ArchiveMonth
    | RemoveExpense
    | ViewCategory
    | ParseError
    | None
)


def _parse_date(raw: str | None) -> date:
    if not raw:
        return date.today()
    return datetime.strptime(raw, "%m-%d-%Y").date()


def _resolve_month(raw: str | None) -> tuple[int, int] | ParseError:
    year_month = parse_month_reference(raw, date.today())
    if year_month is None:
        return ParseError(f"Couldn't understand the month '{raw}'. Try a name like 'may' or 'YYYY-MM'.")
    return year_month


def _parse_remove_rest(rest: str) -> tuple[str | None, float | None, date | None]:
    """Pull an optional $amount and an optional MM-DD-YYYY date out of free text;
    whatever's left over is treated as the item/merchant name."""
    amount = None
    m = REMOVE_AMOUNT_TOKEN_RE.search(rest)
    if m:
        amount = float(m.group(1))
        rest = rest[: m.start()] + rest[m.end() :]

    expense_date = None
    m = REMOVE_DATE_TOKEN_RE.search(rest)
    if m:
        expense_date = datetime.strptime(m.group(1), "%m-%d-%Y").date()
        rest = rest[: m.start()] + rest[m.end() :]

    item = re.sub(r"[,\s]+", " ", rest).strip(" ,")
    return (item or None, amount, expense_date)


def parse_message(text: str) -> ParsedCommand:
    text = text.strip()
    if not text:
        return None

    m = DASHBOARD_RE.match(text)
    if m:
        resolved = _resolve_month(m.group("month"))
        if isinstance(resolved, ParseError):
            return resolved
        return DashboardQuery(year=resolved[0], month=resolved[1])

    m = ARCHIVE_RE.match(text)
    if m:
        resolved = _resolve_month(m.group("month"))
        if isinstance(resolved, ParseError):
            return resolved
        return ArchiveMonth(year=resolved[0], month=resolved[1])

    m = VIEW_RE.match(text)
    if m:
        category = resolve_category(m.group("category"))
        if category is None:
            return ParseError(f"Unrecognized category '{m.group('category')}'.")
        resolved = _resolve_month(m.group("month"))
        if isinstance(resolved, ParseError):
            return resolved
        return ViewCategory(category=category, year=resolved[0], month=resolved[1])

    m = SET_BUDGET_RE.match(text)
    if m:
        raw_category = m.group("category1") or m.group("category2")
        category = resolve_category(raw_category)
        if category is None:
            return ParseError(f"Unrecognized category '{raw_category}'.")
        return SetBudget(category=category, amount=float(m.group("amount")))

    m = REMOVE_RE.match(text)
    if m:
        category = resolve_category(m.group("category"))
        if category is None:
            return ParseError(f"Unrecognized category '{m.group('category')}'.")
        item, amount, expense_date = _parse_remove_rest(m.group("rest"))
        return RemoveExpense(category=category, item=item, amount=amount, date=expense_date)

    m = ADD_EXPENSE_RE.match(text)
    if m:
        category = resolve_category(m.group("category"))
        if category is None:
            return ParseError(f"Unrecognized category '{m.group('category')}'.")
        try:
            expense_date = _parse_date(m.group("date"))
        except ValueError:
            return ParseError("Date must be in MM-DD-YYYY format.")
        return AddExpense(
            category=category,
            item=m.group("item").strip(),
            amount=float(m.group("amount")),
            date=expense_date,
            description=(m.group("description") or "").strip(),
        )

    m = REMAINING_RE.match(text)
    if m:
        category = resolve_category(m.group("category"))
        if category is None:
            return ParseError(f"Unrecognized category '{m.group('category')}'.")
        resolved = _resolve_month(m.group("month"))
        if isinstance(resolved, ParseError):
            return resolved
        return RemainingQuery(category=category, year=resolved[0], month=resolved[1])

    return None
