"""Plain-text formatting helpers for bot replies."""
from datetime import datetime


def format_remaining(entry: dict) -> str:
    category = entry["category"]
    spent = entry["spent"]
    allowance = entry["allowance"]
    label = f" ({entry['month']:02d}/{entry['year']})"
    if allowance is None:
        return f"**{category}**{label}: ${spent:.2f} spent (no budget limit set)."
    remaining = entry["remaining"]
    if remaining < 0:
        return f"**{category}**{label}: ${spent:.2f} spent of ${allowance:.2f} — over by ${-remaining:.2f}!"
    return f"**{category}**{label}: ${remaining:.2f} left of ${allowance:.2f} (${spent:.2f} spent so far)."


def format_dashboard(entries: list[dict], year: int, month: int) -> str:
    lines = [f"**Budget Dashboard — {month:02d}/{year}**"]
    for entry in entries:
        allowance = entry["allowance"]
        spent = entry["spent"]
        if allowance is None:
            lines.append(f"• {entry['category']}: ${spent:.2f} spent (no limit)")
        else:
            remaining = entry["remaining"]
            flag = " ⚠️" if remaining < 0 else ""
            lines.append(f"• {entry['category']}: ${spent:.2f} / ${allowance:.2f} (${remaining:.2f} left{flag})")
    return "\n".join(lines)


def format_added(expense) -> str:
    desc = f" — {expense.description}" if expense.description else ""
    return (
        f"Added **{expense.category}**: {expense.item} ${expense.amount:.2f} "
        f"on {expense.date.strftime('%m-%d-%Y')}{desc}"
    )


def format_expense_line(row: dict) -> str:
    d = datetime.fromisoformat(row["date"]).strftime("%m-%d-%Y")
    return f"{row['item']} ${row['amount']:.2f} on {d}"


def format_removed(category: str, row: dict, duplicate_count: int = 1) -> str:
    note = f" (one of {duplicate_count} identical entries; the rest are untouched)" if duplicate_count > 1 else ""
    return f"Removed **{category}**: {format_expense_line(row)}{note}"


def format_no_match(category: str) -> str:
    return f"No matching **{category}** expense found to remove."


def format_removal_candidates(category: str, rows: list[dict]) -> str:
    lines = "\n".join(f"• {format_expense_line(row)}" for row in rows)
    return f"Found {len(rows)} matching **{category}** expenses — add the amount or date to pick one:\n{lines}"


def format_expense_list(category: str, year: int, month: int, rows: list[dict]) -> str:
    label = f"{month:02d}/{year}"
    if not rows:
        return f"No **{category}** purchases found for {label}."
    lines = [f"**{category} purchases — {label}**"]
    total = 0.0
    for row in rows:
        total += row["amount"]
        desc = f" — {row['description']}" if row.get("description") else ""
        lines.append(f"• {format_expense_line(row)}{desc}")
    lines.append(f"Total: ${total:.2f}")
    return "\n".join(lines)
