"""Export a month's SQLite data to a self-contained CSV snapshot.

Used both by the automatic monthly archiver (archive.py) and for manual,
on-demand exports/re-exports.

Manual usage:  python3 export.py [YYYY-MM] [dest_dir]
Defaults to last month, written into Archive/.
"""
import csv
import sys
from datetime import date, datetime
from pathlib import Path

from budget import get_dashboard, get_expenses
from categories import all_categories
from month_utils import previous_month

ROOT = Path(__file__).parent
DEFAULT_ARCHIVE_DIR = ROOT / "Archive"

CATEGORY_TO_FILE = {
    "Groceries": "groceries.csv",
    "Food": "food.csv",
    "Entertainment": "entertainment.csv",
    "Bills": "bills.csv",
    "Transportation": "transportation.csv",
    "Grooming": "grooming.csv",
    "Subscriptions": "subscriptions.csv",
    "Projects": "projects.csv",
}


def write_month_csvs(year: int, month: int, dest_root: Path) -> Path:
    """Write one self-contained folder for the given month: one CSV per category
    plus a budget.csv with that month's actual allowances and usage totals."""
    folder_name = f"{date(year, month, 1).strftime('%b')}_{year}"
    out_dir = dest_root / folder_name
    out_dir.mkdir(parents=True, exist_ok=True)

    for category in all_categories():
        filename = CATEGORY_TO_FILE[category]
        rows = get_expenses(category, year, month)
        with open(out_dir / filename, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Item", "Charge", "Date", "Additional_Description"])
            for row in rows:
                d = datetime.fromisoformat(row["date"]).strftime("%m-%d-%Y")
                writer.writerow([row["item"], row["amount"], d, row["description"] or ""])

    with open(out_dir / "budget.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Category", "Allowance", "Usage"])
        for entry in get_dashboard(year, month):
            writer.writerow([entry["category"], entry["allowance"] if entry["allowance"] is not None else "", entry["spent"]])

    return out_dir


def main():
    if len(sys.argv) > 1:
        year, month = map(int, sys.argv[1].split("-"))
    else:
        year, month = previous_month(date.today().year, date.today().month)

    dest_root = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_ARCHIVE_DIR
    out_dir = write_month_csvs(year, month, dest_root)
    print(f"Exported {year}-{month:02d} to {out_dir}")


if __name__ == "__main__":
    main()
