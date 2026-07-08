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
  "groceries: edit Kroger split $6.72 moveto grooming"
                                                     -> split $6.72 off the matched Kroger expense and
                                                        move that portion to Grooming.
  "groceries: edit Kroger $56.10 moveto grooming"   -> move the entire matched expense to Grooming.
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

# #EventName at end of message (terminated by comma or end-of-string); stripped before other matching
EVENT_TAG_RE = re.compile(r"\s*#([^#,\n]+?)\s*$")

EVENT_QUERY_RE = re.compile(r"^\s*event\s+(?P<name>.+?)\s*$", re.IGNORECASE)
EVENT_LIST_RE = re.compile(
    r"^\s*events?(?:\s+(?:for\s+)?(?P<months>\d+)(?:\s+months?)?)?\s*$", re.IGNORECASE
)
SET_EVENT_LIMIT_RE = re.compile(
    r"^\s*set\s+event\s+(?:limit|budget)\s+(?P<name>.+?)\s+\$?(?P<amount>\d+(?:\.\d{1,2})?)\s*$",
    re.IGNORECASE,
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

EDIT_RE = re.compile(
    r"^\s*(?P<category>[A-Za-z]+)\s*:\s*edit\s+(?P<rest>.+?)\s*$",
    re.IGNORECASE,
)
EDIT_MOVETO_RE = re.compile(r"\bmoveto\s+([A-Za-z]+)\b", re.IGNORECASE)
EDIT_SPLIT_RE = re.compile(r"\bsplit\s+\$?\s*(\d+(?:\.\d{1,2})?)\b", re.IGNORECASE)


@dataclass
class AddExpense:
    category: str
    item: str
    amount: float
    date: date
    description: str = ""
    event_name: str | None = None


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
class EditExpense:
    category: str
    item: str | None = None      # matching filter
    amount: float | None = None  # matching filter (original amount)
    date: date | None = None     # matching filter
    split: float | None = None   # amount to carve off into moveto
    moveto: str | None = None    # target category for split or full move
    new_amount: float | None = None
    new_item: str | None = None


@dataclass
class ViewCategory:
    category: str
    year: int
    month: int


@dataclass
class EventQuery:
    name: str


@dataclass
class EventList:
    months: int = 6


@dataclass
class SetEventLimit:
    name: str
    amount: float


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
    | EditExpense
    | ViewCategory
    | EventQuery
    | EventList
    | SetEventLimit
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


def _parse_edit_rest(rest: str) -> tuple:
    """Extract moveto category and split amount from edit rest, then delegate the
    remainder to _parse_remove_rest to get item/amount/date matching filters."""
    moveto_raw = None
    m = EDIT_MOVETO_RE.search(rest)
    if m:
        moveto_raw = m.group(1)
        rest = rest[: m.start()] + rest[m.end() :]

    split = None
    m = EDIT_SPLIT_RE.search(rest)
    if m:
        split = float(m.group(1))
        rest = rest[: m.start()] + rest[m.end() :]

    item, amount, expense_date = _parse_remove_rest(rest)
    return item, amount, expense_date, split, moveto_raw


def parse_message(text: str) -> ParsedCommand:
    text = text.strip()
    if not text:
        return None

    # Strip #EventName tag from the message before pattern matching.
    # Only AddExpense uses it; other commands silently ignore it.
    event_name: str | None = None
    m = EVENT_TAG_RE.search(text)
    if m:
        event_name = m.group(1).strip()
        text = text[: m.start()].strip()

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

    m = EVENT_LIST_RE.match(text)
    if m:
        months_raw = m.group("months")
        return EventList(months=int(months_raw) if months_raw else 6)

    m = EVENT_QUERY_RE.match(text)
    if m:
        return EventQuery(name=m.group("name"))

    m = VIEW_RE.match(text)
    if m:
        category = resolve_category(m.group("category"))
        if category is None:
            return ParseError(f"Unrecognized category '{m.group('category')}'.")
        resolved = _resolve_month(m.group("month"))
        if isinstance(resolved, ParseError):
            return resolved
        return ViewCategory(category=category, year=resolved[0], month=resolved[1])

    m = SET_EVENT_LIMIT_RE.match(text)
    if m:
        return SetEventLimit(name=m.group("name").strip(), amount=float(m.group("amount")))

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

    m = EDIT_RE.match(text)
    if m:
        category = resolve_category(m.group("category"))
        if category is None:
            return ParseError(f"Unrecognized category '{m.group('category')}'.")
        item, amount, expense_date, split, moveto_raw = _parse_edit_rest(m.group("rest"))
        moveto = None
        if moveto_raw is not None:
            moveto = resolve_category(moveto_raw)
            if moveto is None:
                return ParseError(f"Unrecognized target category '{moveto_raw}'.")
        if split is None and moveto is None:
            return ParseError(
                "Specify 'moveto <category>' to move the expense, or 'split $X moveto <category>' to carve off a portion."
            )
        return EditExpense(category=category, item=item, amount=amount, date=expense_date, split=split, moveto=moveto)

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
            event_name=event_name,
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
