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


def _get_or_create_event(conn, name: str) -> int:
    """Within an open connection, return the event id, creating the event if new."""
    conn.execute("INSERT OR IGNORE INTO events (name) VALUES (?)", (name,))
    return conn.execute(
        "SELECT id FROM events WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()["id"]


def add_expense(category: str, item: str, amount: float, expense_date: date,
                description: str = "", event_name: str | None = None) -> None:
    conn = get_connection()
    try:
        event_id = _get_or_create_event(conn, event_name) if event_name else None
        conn.execute(
            "INSERT INTO expenses (category, item, amount, date, description, event_id) VALUES (?, ?, ?, ?, ?, ?)",
            (category, item, amount, expense_date.isoformat(), description, event_id),
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
            "WHERE category = ? AND date LIKE ? AND event_id IS NULL",
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
    """Return non-event-tagged expenses for a category/month (used for /view and budgets)."""
    conn = get_connection()
    try:
        prefix = f"{year:04d}-{month:02d}"
        rows = conn.execute(
            "SELECT item, amount, date, description FROM expenses "
            "WHERE category = ? AND date LIKE ? AND event_id IS NULL ORDER BY date",
            (category, f"{prefix}%"),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def count_event_expenses_in_month(category: str, year: int, month: int) -> int:
    """Count how many event-tagged expenses exist for a category/month (for the hidden-count note)."""
    conn = get_connection()
    try:
        prefix = f"{year:04d}-{month:02d}"
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM expenses "
            "WHERE category = ? AND date LIKE ? AND event_id IS NOT NULL",
            (category, f"{prefix}%"),
        ).fetchone()
        return row["n"]
    finally:
        conn.close()


def get_month_event_total(year: int, month: int) -> float:
    """Total dollars in event-tagged expenses for a given month (for the dashboard footer)."""
    conn = get_connection()
    try:
        prefix = f"{year:04d}-{month:02d}"
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses "
            "WHERE event_id IS NOT NULL AND date LIKE ?",
            (f"{prefix}%",),
        ).fetchone()
        return row["total"]
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
    """Find expenses matching whichever filters are provided. Includes event-tagged expenses
    so that /remove and /edit can manage them. If exact_date is given, search all-time;
    otherwise restrict to the given year/month."""
    conn = get_connection()
    try:
        query = (
            "SELECT ex.id, ex.item, ex.amount, ex.date, ex.description, ev.name AS event_name "
            "FROM expenses ex LEFT JOIN events ev ON ev.id = ex.event_id "
            "WHERE ex.category = ?"
        )
        params: list = [category]
        if item:
            query += " AND ex.item LIKE ?"
            params.append(f"%{item}%")
        if amount is not None:
            query += " AND ex.amount = ?"
            params.append(amount)
        if exact_date is not None:
            query += " AND ex.date = ?"
            params.append(exact_date.isoformat())
        elif year is not None and month is not None:
            query += " AND ex.date LIKE ?"
            params.append(f"{year:04d}-{month:02d}%")
        query += " ORDER BY ex.date, ex.id"
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


def update_expense(expense_id: int, *, new_item: str | None = None, new_amount: float | None = None) -> None:
    if new_item is None and new_amount is None:
        return
    sets, params = [], []
    if new_item is not None:
        sets.append("item = ?")
        params.append(new_item)
    if new_amount is not None:
        sets.append("amount = ?")
        params.append(new_amount)
    params.append(expense_id)
    conn = get_connection()
    try:
        conn.execute(f"UPDATE expenses SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def split_expense(expense_id: int, split_amount: float, moveto_category: str) -> tuple[dict, dict]:
    """Reduce expense by split_amount and insert a new expense in moveto_category for that amount.
    Returns (updated_original_row, new_row)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, category, item, amount, date, description FROM expenses WHERE id = ?",
            (expense_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Expense {expense_id} not found")
        original = round(row["amount"], 2)
        split = round(split_amount, 2)
        if split >= original:
            raise ValueError(
                f"Split amount ${split:.2f} must be less than the original ${original:.2f}. "
                "Omit 'split' to move the entire expense."
            )
        remaining = round(original - split, 2)
        conn.execute("UPDATE expenses SET amount = ? WHERE id = ?", (remaining, expense_id))
        conn.execute(
            "INSERT INTO expenses (category, item, amount, date, description) VALUES (?, ?, ?, ?, ?)",
            (moveto_category, row["item"], split, row["date"], row["description"] or ""),
        )
        conn.commit()
        updated = dict(row)
        updated["amount"] = remaining
        new_row = {"item": row["item"], "amount": split, "date": row["date"], "description": row["description"] or ""}
        return updated, new_row
    finally:
        conn.close()


# ── Event functions ──────────────────────────────────────────────────────────

def get_event_view(name: str) -> dict | None:
    """All info + expenses for a named event. Returns None if the event doesn't exist."""
    conn = get_connection()
    try:
        event = conn.execute(
            "SELECT id, name, allowance FROM events WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if event is None:
            return None
        rows = conn.execute(
            "SELECT category, item, amount, date, description FROM expenses "
            "WHERE event_id = ? ORDER BY date, category",
            (event["id"],),
        ).fetchall()
        return {"id": event["id"], "name": event["name"], "allowance": event["allowance"],
                "expenses": [dict(r) for r in rows]}
    finally:
        conn.close()


def get_events_since(cutoff_date: date) -> list[dict]:
    """Events with at least one expense dated >= cutoff_date, ordered by most recent activity."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT e.id, e.name, e.allowance,
                   (SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE event_id = e.id) AS total
            FROM events e
            WHERE EXISTS (
                SELECT 1 FROM expenses WHERE event_id = e.id AND date >= ?
            )
            ORDER BY (SELECT MAX(date) FROM expenses WHERE event_id = e.id) DESC
            """,
            (cutoff_date.isoformat(),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_event_allowance(name: str, amount: float) -> None:
    """Set the spending limit for a named event, creating the event if it doesn't exist."""
    conn = get_connection()
    try:
        _get_or_create_event(conn, name)
        conn.execute(
            "UPDATE events SET allowance = ? WHERE name = ? COLLATE NOCASE", (amount, name)
        )
        conn.commit()
    finally:
        conn.close()


def get_all_event_names() -> list[str]:
    """All event names sorted alphabetically — used for slash command autocomplete."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT name FROM events ORDER BY name COLLATE NOCASE").fetchall()
        return [r["name"] for r in rows]
    finally:
        conn.close()


def get_event_expenses_for_month(year: int, month: int) -> list[dict]:
    """Event-tagged expenses for a given month — used when writing archive CSVs."""
    conn = get_connection()
    try:
        prefix = f"{year:04d}-{month:02d}"
        rows = conn.execute(
            "SELECT ev.name AS event_name, ex.category, ex.item, ex.amount, ex.date, ex.description "
            "FROM expenses ex JOIN events ev ON ev.id = ex.event_id "
            "WHERE ex.date LIKE ? ORDER BY ev.name, ex.date",
            (f"{prefix}%",),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def move_expense(expense_id: int, moveto_category: str) -> dict:
    """Move the entire expense to another category. Returns the original row."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, category, item, amount, date, description FROM expenses WHERE id = ?",
            (expense_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Expense {expense_id} not found")
        conn.execute("UPDATE expenses SET category = ? WHERE id = ?", (moveto_category, expense_id))
        conn.commit()
        return dict(row)
    finally:
        conn.close()
