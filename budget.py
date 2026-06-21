"""Core budgeting operations: add expenses, check remaining budget, dashboard.

Allowances are versioned in `budget_history`: each change is timestamped with an
effective_from date, so looking back at a past month always reflects whatever
limit was actually active during that month, even if it's since been changed.
"""
from datetime import date

from db import get_connection
from categories import DEFAULT_ALLOWANCES, TRACKING_START, all_categories
from month_utils import last_day_of_month


def _migrate_legacy_budgets(conn) -> dict[str, float | None]:
    """Pull values out of the old single-value `budgets` table, if it still exists."""
    has_legacy = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='budgets'"
    ).fetchone()
    if not has_legacy:
        return {}
    return {row["category"]: row["allowance"] for row in conn.execute("SELECT category, allowance FROM budgets")}


def ensure_budget_history_seeded() -> None:
    """Insert an initial allowance for any category with no history yet."""
    conn = get_connection()
    try:
        legacy_values = _migrate_legacy_budgets(conn)
        existing = {row["category"] for row in conn.execute("SELECT DISTINCT category FROM budget_history")}
        for category, default_allowance in DEFAULT_ALLOWANCES.items():
            if category in existing:
                continue
            allowance = legacy_values.get(category, default_allowance)
            conn.execute(
                "INSERT INTO budget_history (category, allowance, effective_from) VALUES (?, ?, ?)",
                (category, allowance, TRACKING_START),
            )
        conn.commit()
    finally:
        conn.close()


def add_expense(category: str, item: str, amount: float, expense_date: date, description: str = "") -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO expenses (category, item, amount, date, description) VALUES (?, ?, ?, ?, ?)",
            (category, item, amount, expense_date.isoformat(), description),
        )
        conn.commit()
    finally:
        conn.close()


def get_allowance_for_month(category: str, year: int, month: int) -> float | None:
    """The allowance that was actually in effect during the given month."""
    cutoff = last_day_of_month(year, month).isoformat()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT allowance FROM budget_history WHERE category = ? AND effective_from <= ? "
            "ORDER BY effective_from DESC, id DESC LIMIT 1",
            (category, cutoff),
        ).fetchone()
        return row["allowance"] if row else None
    finally:
        conn.close()


def set_allowance(category: str, amount: float, effective_from: date | None = None) -> None:
    """Record a new allowance, effective from the given date (default: today) forward.
    Past months keep whatever allowance was active at the time."""
    effective_from = effective_from or date.today()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO budget_history (category, allowance, effective_from) VALUES (?, ?, ?)",
            (category, amount, effective_from.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_month_spent(category: str, year: int, month: int) -> float:
    conn = get_connection()
    try:
        prefix = f"{year:04d}-{month:02d}"
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses "
            "WHERE category = ? AND date LIKE ?",
            (category, f"{prefix}%"),
        ).fetchone()
        return row["total"]
    finally:
        conn.close()


def get_remaining(category: str, year: int, month: int) -> dict:
    spent = get_month_spent(category, year, month)
    allowance = get_allowance_for_month(category, year, month)
    remaining = None if allowance is None else round(allowance - spent, 2)
    return {
        "category": category,
        "year": year,
        "month": month,
        "allowance": allowance,
        "spent": round(spent, 2),
        "remaining": remaining,
    }


def get_dashboard(year: int, month: int) -> list[dict]:
    return [get_remaining(category, year, month) for category in all_categories()]


def get_expenses(category: str, year: int, month: int) -> list[dict]:
    conn = get_connection()
    try:
        prefix = f"{year:04d}-{month:02d}"
        rows = conn.execute(
            "SELECT item, amount, date, description FROM expenses "
            "WHERE category = ? AND date LIKE ? ORDER BY date",
            (category, f"{prefix}%"),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def find_matching_expenses(
    category: str,
    item: str | None = None,
    amount: float | None = None,
    exact_date: date | None = None,
    year: int | None = None,
    month: int | None = None,
) -> list[dict]:
    """Find expenses matching whichever filters are provided. If exact_date is given,
    search all-time; otherwise restrict to the given year/month (callers default this
    to the current month so a bare item name doesn't reach years into the past)."""
    conn = get_connection()
    try:
        query = "SELECT id, item, amount, date, description FROM expenses WHERE category = ?"
        params = [category]
        if item:
            query += " AND item LIKE ?"
            params.append(f"%{item}%")
        if amount is not None:
            query += " AND amount = ?"
            params.append(amount)
        if exact_date is not None:
            query += " AND date = ?"
            params.append(exact_date.isoformat())
        elif year is not None and month is not None:
            query += " AND date LIKE ?"
            params.append(f"{year:04d}-{month:02d}%")
        query += " ORDER BY date, id"
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def delete_expense(expense_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        conn.commit()
    finally:
        conn.close()
