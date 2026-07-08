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


def format_dashboard(entries: list[dict], year: int, month: int, event_total: float = 0.0) -> str:
    lines = [f"**Budget Dashboard — {month:02d}/{year}**"]
    total = 0.0
    bills = 0.0
    for entry in entries:
        allowance = entry["allowance"]
        spent = entry["spent"]
        total += spent
        if entry["category"] == "Bills":
            bills += spent
        if allowance is None:
            lines.append(f"• {entry['category']}: ${spent:.2f} spent (no limit)")
        else:
            remaining = entry["remaining"]
            flag = " ⚠️" if remaining < 0 else ""
            lines.append(f"• {entry['category']}: ${spent:.2f} / ${allowance:.2f} (${remaining:.2f} left{flag})")
    lines.append(f"Total: ${total:.2f} spent")
    lines.append(f"Discretionary: ${total - bills:.2f} spent")
    if event_total > 0:
        lines.append(f"+ ${event_total:.2f} in event expenses (not included above)")
    return "\n".join(lines)


def format_added(expense) -> str:
    desc = f" — {expense.description}" if expense.description else ""
    event = f" [#{expense.event_name}]" if expense.event_name else ""
    return (
        f"Added **{expense.category}**: {expense.item} ${expense.amount:.2f} "
        f"on {expense.date.strftime('%m-%d-%Y')}{desc}{event}"
    )


def format_expense_line(row: dict) -> str:
    d = datetime.fromisoformat(row["date"]).strftime("%m-%d-%Y")
    event = f" [#{row['event_name']}]" if row.get("event_name") else ""
    return f"{row['item']} ${row['amount']:.2f} on {d}{event}"


def format_removed(category: str, row: dict, duplicate_count: int = 1) -> str:
    note = f" (one of {duplicate_count} identical entries; the rest are untouched)" if duplicate_count > 1 else ""
    return f"Removed **{category}**: {format_expense_line(row)}{note}"


def format_no_match(category: str) -> str:
    return f"No matching **{category}** expense found to remove."


def format_removal_candidates(category: str, rows: list[dict]) -> str:
    lines = "\n".join(f"• {format_expense_line(row)}" for row in rows)
    return f"Found {len(rows)} matching **{category}** expenses — add the amount or date to pick one:\n{lines}"


def format_edited(category: str, row: dict, old_amount: float | None = None, old_item: str | None = None) -> str:
    changes = []
    if old_amount is not None:
        changes.append(f"${old_amount:.2f} → ${row['amount']:.2f}")
    if old_item is not None:
        changes.append(f"'{old_item}' → '{row['item']}'")
    return f"Edited **{category}**: {format_expense_line(row)} ({', '.join(changes)})"


def format_split(source_cat: str, moveto_cat: str, updated_row: dict, split_amount: float) -> str:
    d = datetime.fromisoformat(updated_row["date"]).strftime("%m-%d-%Y")
    return (
        f"Split **{source_cat}**: {updated_row['item']} reduced to ${updated_row['amount']:.2f} on {d}; "
        f"${split_amount:.2f} moved to **{moveto_cat}**."
    )


def format_moved(source_cat: str, moveto_cat: str, row: dict) -> str:
    return f"Moved **{source_cat}** → **{moveto_cat}**: {format_expense_line(row)}"


def format_expense_list(category: str, year: int, month: int, rows: list[dict],
                        hidden_count: int = 0) -> str:
    label = f"{month:02d}/{year}"
    lines = [f"**{category} purchases — {label}**"] if rows else []
    if not rows:
        base = f"No **{category}** purchases found for {label}."
        if hidden_count:
            base += f"\n({hidden_count} expense(s) tagged to events — use `/event` to view them.)"
        return base
    total = 0.0
    for row in rows:
        total += row["amount"]
        desc = f" — {row['description']}" if row.get("description") else ""
        lines.append(f"• {format_expense_line(row)}{desc}")
    lines.append(f"Total: ${total:.2f}")
    if hidden_count:
        lines.append(f"({hidden_count} expense(s) tagged to events not shown — use `/event` to view them.)")
    return "\n".join(lines)


def format_event_view(event: dict, expenses: list[dict]) -> str:
    name = event["name"]
    allowance = event["allowance"]
    total = sum(e["amount"] for e in expenses)
    if allowance is not None:
        remaining = round(allowance - total, 2)
        flag = " ⚠️" if remaining < 0 else ""
        budget_str = f"${total:.2f} / ${allowance:.2f} (${remaining:.2f} left{flag})"
    else:
        budget_str = f"${total:.2f} total"
    lines = [f"**Event: {name}** — {budget_str}"]
    if not expenses:
        lines.append("No expenses logged yet.")
        return "\n".join(lines)
    by_cat: dict[str, list[dict]] = {}
    for exp in sorted(expenses, key=lambda e: e["date"]):
        by_cat.setdefault(exp["category"], []).append(exp)
    for cat, rows in by_cat.items():
        cat_total = sum(r["amount"] for r in rows)
        lines.append(f"**{cat}** — ${cat_total:.2f}")
        for row in rows:
            desc = f" — {row['description']}" if row.get("description") else ""
            lines.append(f"  • {format_expense_line(row)}{desc}")
    return "\n".join(lines)


def format_event_list(events: list[dict], months: int) -> str:
    if not events:
        return f"No events with activity in the past {months} month(s)."
    lines = [f"**Events — past {months} month(s)**"]
    for ev in events:
        name = ev["name"]
        total = ev["total"]
        allowance = ev["allowance"]
        if allowance is not None:
            remaining = round(allowance - total, 2)
            flag = " ⚠️" if remaining < 0 else ""
            lines.append(f"• **{name}**: ${total:.2f} / ${allowance:.2f} (${remaining:.2f} left{flag})")
        else:
            lines.append(f"• **{name}**: ${total:.2f} spent (no limit)")
    return "\n".join(lines)
