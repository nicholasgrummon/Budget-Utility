"""SQLite schema and connection helper for the budget tool."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "budget.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    item TEXT NOT NULL,
    amount REAL NOT NULL,
    date TEXT NOT NULL,            -- ISO format YYYY-MM-DD
    description TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS budget_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    allowance REAL,              -- NULL = no limit
    effective_from TEXT NOT NULL,  -- ISO date YYYY-MM-DD; applies to this date forward until superseded
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_expenses_category_date ON expenses(category, date);
CREATE INDEX IF NOT EXISTS idx_budget_history_category_date ON budget_history(category, effective_from);
"""


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
