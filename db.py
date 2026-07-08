"""SQLite schema and connection helper for the budget tool."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "budget.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    allowance REAL,
    created_at TEXT NOT NULL DEFAULT (date('now'))
);

CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    item TEXT NOT NULL,
    amount REAL NOT NULL,
    date TEXT NOT NULL,
    description TEXT,
    event_id INTEGER REFERENCES events(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS budget_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    allowance REAL,
    effective_from TEXT NOT NULL,
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
    _migrate(conn)
    return conn


def _migrate(conn) -> None:
    """Add columns and indexes introduced after initial schema deployment."""
    try:
        conn.execute("ALTER TABLE expenses ADD COLUMN event_id INTEGER REFERENCES events(id)")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    # Safe to run every time; only creates if not already present
    conn.execute("CREATE INDEX IF NOT EXISTS idx_expenses_event_id ON expenses(event_id)")
    conn.commit()
