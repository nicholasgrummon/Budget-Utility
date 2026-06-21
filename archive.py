"""Automatic monthly archiving: snapshot any fully-elapsed month into Archive/ as CSV.

Tracks the last month it has seen in the `meta` table. Every time
check_and_archive_past_months() runs (on bot startup and on a daily timer),
it archives any month between the last-seen month and the current one,
then advances the marker. The current, still-in-progress month is never
archived.
"""
from datetime import date
from pathlib import Path

from db import get_connection
from export import write_month_csvs
from month_utils import next_month

ARCHIVE_DIR = Path(__file__).parent / "Archive"


def _get_meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def check_and_archive_past_months(today: date | None = None) -> list[str]:
    """Archive any months fully in the past that haven't been archived yet.
    Returns the list of 'YYYY-MM' labels that were archived."""
    today = today or date.today()
    current_label = f"{today.year:04d}-{today.month:02d}"
    archived = []

    conn = get_connection()
    try:
        last_seen = _get_meta(conn, "last_seen_month")
        if last_seen is None:
            _set_meta(conn, "last_seen_month", current_label)
            conn.commit()
            return archived

        year, month = map(int, last_seen.split("-"))
        while f"{year:04d}-{month:02d}" < current_label:
            write_month_csvs(year, month, ARCHIVE_DIR)
            archived.append(f"{year:04d}-{month:02d}")
            year, month = next_month(year, month)

        _set_meta(conn, "last_seen_month", current_label)
        conn.commit()
    finally:
        conn.close()

    return archived
