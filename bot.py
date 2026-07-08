"""Discord bot front-end for the budget tool.

Supports free-text messages (DM the bot naturally, e.g. "groceries: Kroger $56.10, 06-20-2026")
as well as equivalent slash commands (/add, /remaining, /dashboard, /setbudget, /archive, /remove, /view).

Setup:
  1. cp .env.example .env   and fill in DISCORD_TOKEN (and optionally OWNER_ID).
  2. pip install -r requirements.txt
  3. python3 bot.py
"""
import os
from datetime import date, datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

import budget
from archive import check_and_archive_past_months
from categories import all_categories, resolve_category
from export import write_month_csvs, DEFAULT_ARCHIVE_DIR
from formatting import (
    format_added,
    format_dashboard,
    format_edited,
    format_event_list,
    format_event_view,
    format_expense_list,
    format_moved,
    format_no_match,
    format_remaining,
    format_removal_candidates,
    format_removed,
    format_split,
)
from month_utils import parse_month_reference
from parser import (
    AddExpense,
    ArchiveMonth,
    DashboardQuery,
    EditExpense,
    EventList,
    EventQuery,
    ParseError,
    RemainingQuery,
    RemoveExpense,
    SetBudget,
    SetEventLimit,
    ViewCategory,
    parse_message,
)

load_dotenv()

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
OWNER_ID = os.environ.get("OWNER_ID") or None  # if set, only this Discord user ID can use the bot

intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


def _authorized(user_id: int) -> bool:
    return OWNER_ID is None or str(user_id) == str(OWNER_ID)


@tasks.loop(hours=24)
async def daily_archive_check():
    archived = check_and_archive_past_months()
    if archived:
        print(f"Archived past month(s): {', '.join(archived)}")


@client.event
async def on_ready():
    budget.ensure_budget_history_seeded()
    await tree.sync()
    if not daily_archive_check.is_running():
        daily_archive_check.start()
    print(f"Logged in as {client.user}")


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user or message.author.bot:
        return
    if not _authorized(message.author.id):
        return

    parsed = parse_message(message.content)
    if parsed is None:
        return  # not a recognized command-shaped message; stay quiet

    await message.channel.send(await handle_parsed(parsed))


async def handle_parsed(parsed) -> str:
    if isinstance(parsed, ParseError):
        return f"⚠️ {parsed.message}"

    if isinstance(parsed, AddExpense):
        budget.add_expense(parsed.category, parsed.item, parsed.amount, parsed.date,
                           parsed.description, parsed.event_name)
        return format_added(parsed)

    if isinstance(parsed, RemainingQuery):
        entry = budget.get_remaining(parsed.category, parsed.year, parsed.month)
        return format_remaining(entry)

    if isinstance(parsed, DashboardQuery):
        entries = budget.get_dashboard(parsed.year, parsed.month)
        event_total = budget.get_month_event_total(parsed.year, parsed.month)
        return format_dashboard(entries, parsed.year, parsed.month, event_total)

    if isinstance(parsed, SetBudget):
        budget.set_allowance(parsed.category, parsed.amount)
        return f"Set **{parsed.category}** budget to ${parsed.amount:.2f}, effective today onward."

    if isinstance(parsed, ArchiveMonth):
        out_dir = write_month_csvs(parsed.year, parsed.month, DEFAULT_ARCHIVE_DIR)
        return f"Archived {parsed.month:02d}/{parsed.year} to {out_dir}"

    if isinstance(parsed, RemoveExpense):
        today = date.today()
        year, month = (None, None) if parsed.date else (today.year, today.month)
        matches = budget.find_matching_expenses(
            parsed.category, item=parsed.item, amount=parsed.amount, exact_date=parsed.date,
            year=year, month=month,
        )
        return resolve_removal(parsed.category, matches)

    if isinstance(parsed, EditExpense):
        today = date.today()
        year, month = (None, None) if parsed.date else (today.year, today.month)
        matches = budget.find_matching_expenses(
            parsed.category, item=parsed.item, amount=parsed.amount, exact_date=parsed.date,
            year=year, month=month,
        )
        return resolve_edit(parsed, matches)

    if isinstance(parsed, ViewCategory):
        rows = budget.get_expenses(parsed.category, parsed.year, parsed.month)
        hidden = budget.count_event_expenses_in_month(parsed.category, parsed.year, parsed.month)
        return format_expense_list(parsed.category, parsed.year, parsed.month, rows, hidden)

    if isinstance(parsed, EventQuery):
        event = budget.get_event_view(parsed.name)
        if event is None:
            return f"No event named **{parsed.name}** found."
        return format_event_view(event, event["expenses"])

    if isinstance(parsed, EventList):
        from datetime import timedelta
        cutoff = date.today().replace(day=1)
        for _ in range(parsed.months - 1):
            cutoff = (cutoff - timedelta(days=1)).replace(day=1)
        events = budget.get_events_since(cutoff)
        return format_event_list(events, parsed.months)

    if isinstance(parsed, SetEventLimit):
        budget.set_event_allowance(parsed.name, parsed.amount)
        return f"Set **{parsed.name}** event limit to ${parsed.amount:.2f}."

    return "Sorry, I didn't understand that."


def resolve_removal(category: str, matches: list[dict]) -> str:
    """Shared by the free-text handler and /remove: delete the single match, collapse
    true duplicates down to one deletion, or ask for more detail if still ambiguous."""
    if not matches:
        return format_no_match(category)

    if len(matches) == 1:
        budget.delete_expense(matches[0]["id"])
        return format_removed(category, matches[0])

    signatures = {(m["item"], m["amount"], m["date"]) for m in matches}
    if len(signatures) == 1:
        budget.delete_expense(matches[0]["id"])
        return format_removed(category, matches[0], duplicate_count=len(matches))

    return format_removal_candidates(category, matches)


def resolve_edit(cmd: EditExpense, matches: list[dict]) -> str:
    """Apply a split, move, or field edit to the single identified expense."""
    if not matches:
        return format_no_match(cmd.category)

    if len(matches) > 1:
        signatures = {(m["item"], m["amount"], m["date"]) for m in matches}
        if len(signatures) > 1:
            return format_removal_candidates(cmd.category, matches)

    row = matches[0]
    expense_id = row["id"]

    try:
        if cmd.split is not None and cmd.moveto is not None:
            updated, _ = budget.split_expense(expense_id, cmd.split, cmd.moveto)
            return format_split(cmd.category, cmd.moveto, updated, cmd.split)

        if cmd.moveto is not None:
            original = budget.move_expense(expense_id, cmd.moveto)
            return format_moved(cmd.category, cmd.moveto, original)

        # field edit (new_amount / new_item)
        old_amount = row["amount"] if cmd.new_amount is not None else None
        old_item = row["item"] if cmd.new_item is not None else None
        budget.update_expense(expense_id, new_item=cmd.new_item, new_amount=cmd.new_amount)
        updated = dict(row)
        if cmd.new_amount is not None:
            updated["amount"] = cmd.new_amount
        if cmd.new_item is not None:
            updated["item"] = cmd.new_item
        return format_edited(cmd.category, updated, old_amount=old_amount, old_item=old_item)

    except ValueError as e:
        return f"⚠️ {e}"


def _resolve_month_arg(month: str) -> tuple[int, int] | None:
    return parse_month_reference(month, date.today())


def _parse_date_str(date_str: str) -> date:
    return datetime.strptime(date_str, "%m-%d-%Y").date()


@tree.command(name="add", description="Log a new expense")
@app_commands.describe(
    category="Expense category (e.g. groceries, food, entertainment)",
    item="What you bought",
    amount="Amount in dollars",
    date_str="Date as MM-DD-YYYY (defaults to today)",
    description="Optional note",
    event="Tag this expense to a named event (e.g. Sister's Graduation)",
)
async def add_cmd(
    interaction: discord.Interaction,
    category: str,
    item: str,
    amount: float,
    date_str: str = "",
    description: str = "",
    event: str = "",
):
    if not _authorized(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    resolved = resolve_category(category)
    if resolved is None:
        await interaction.response.send_message(f"Unrecognized category '{category}'.", ephemeral=True)
        return
    try:
        expense_date = date.today() if not date_str else _parse_date_str(date_str)
    except ValueError:
        await interaction.response.send_message("Date must be MM-DD-YYYY.", ephemeral=True)
        return
    event_name = event.strip() or None
    budget.add_expense(resolved, item, amount, expense_date, description, event_name)
    await interaction.response.send_message(
        format_added(AddExpense(resolved, item, amount, expense_date, description, event_name))
    )


@tree.command(name="remaining", description="Check remaining budget for a category")
@app_commands.describe(category="Expense category", month="Month to check, e.g. 'may' or '2026-05' (default: this month)")
async def remaining_cmd(interaction: discord.Interaction, category: str, month: str = ""):
    if not _authorized(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    resolved = resolve_category(category)
    if resolved is None:
        await interaction.response.send_message(f"Unrecognized category '{category}'.", ephemeral=True)
        return
    year_month = _resolve_month_arg(month)
    if year_month is None:
        await interaction.response.send_message(f"Couldn't understand the month '{month}'.", ephemeral=True)
        return
    entry = budget.get_remaining(resolved, *year_month)
    await interaction.response.send_message(format_remaining(entry))


@tree.command(name="dashboard", description="Show spending across all categories for a month")
@app_commands.describe(month="Month to check, e.g. 'may' or '2026-05' (default: this month)")
async def dashboard_cmd(interaction: discord.Interaction, month: str = ""):
    if not _authorized(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    year_month = _resolve_month_arg(month)
    if year_month is None:
        await interaction.response.send_message(f"Couldn't understand the month '{month}'.", ephemeral=True)
        return
    entries = budget.get_dashboard(*year_month)
    event_total = budget.get_month_event_total(*year_month)
    await interaction.response.send_message(format_dashboard(entries, *year_month, event_total))


@tree.command(name="setbudget", description="Set or update a category's monthly allowance (effective from today)")
@app_commands.describe(category="Expense category", amount="New allowance in dollars")
async def setbudget_cmd(interaction: discord.Interaction, category: str, amount: float):
    if not _authorized(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    resolved = resolve_category(category)
    if resolved is None:
        await interaction.response.send_message(f"Unrecognized category '{category}'.", ephemeral=True)
        return
    budget.set_allowance(resolved, amount)
    await interaction.response.send_message(f"Set **{resolved}** budget to ${amount:.2f}, effective today onward.")


@tree.command(name="remove", description="Remove a logged expense (provide enough detail to identify it)")
@app_commands.describe(
    category="Expense category",
    item="Item/merchant name (optional, partial match)",
    amount="Amount in dollars (optional)",
    date_str="Date as MM-DD-YYYY (optional; if omitted, only this month is searched)",
)
async def remove_cmd(
    interaction: discord.Interaction,
    category: str,
    item: str = "",
    amount: Optional[float] = None,
    date_str: str = "",
):
    if not _authorized(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    resolved = resolve_category(category)
    if resolved is None:
        await interaction.response.send_message(f"Unrecognized category '{category}'.", ephemeral=True)
        return

    expense_date = None
    if date_str:
        try:
            expense_date = _parse_date_str(date_str)
        except ValueError:
            await interaction.response.send_message("Date must be MM-DD-YYYY.", ephemeral=True)
            return

    today = date.today()
    year, month = (None, None) if expense_date else (today.year, today.month)
    matches = budget.find_matching_expenses(
        resolved, item=item or None, amount=amount, exact_date=expense_date, year=year, month=month,
    )
    await interaction.response.send_message(resolve_removal(resolved, matches))


@tree.command(name="edit", description="Edit an expense: split off a partial amount to another category, move it, or update fields")
@app_commands.describe(
    category="Source expense category",
    item="Item/merchant name (optional, partial match to identify the expense)",
    amount="Original amount (optional, used to identify the expense)",
    date_str="Date as MM-DD-YYYY (optional, used to identify the expense)",
    split="Dollar amount to carve off and move to 'moveto' (leave blank to move the whole expense)",
    moveto="Target category for the split amount or the full expense",
    new_amount="Replace the expense's amount with this value",
    new_item="Rename the expense's item/merchant name",
)
async def edit_cmd(
    interaction: discord.Interaction,
    category: str,
    item: str = "",
    amount: Optional[float] = None,
    date_str: str = "",
    split: Optional[float] = None,
    moveto: str = "",
    new_amount: Optional[float] = None,
    new_item: str = "",
):
    if not _authorized(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    resolved_cat = resolve_category(category)
    if resolved_cat is None:
        await interaction.response.send_message(f"Unrecognized category '{category}'.", ephemeral=True)
        return

    resolved_moveto = None
    if moveto:
        resolved_moveto = resolve_category(moveto)
        if resolved_moveto is None:
            await interaction.response.send_message(f"Unrecognized target category '{moveto}'.", ephemeral=True)
            return

    if split is None and resolved_moveto is None and new_amount is None and not new_item:
        await interaction.response.send_message(
            "Specify at least one action: 'moveto', 'split + moveto', 'new_amount', or 'new_item'.",
            ephemeral=True,
        )
        return

    expense_date = None
    if date_str:
        try:
            expense_date = _parse_date_str(date_str)
        except ValueError:
            await interaction.response.send_message("Date must be MM-DD-YYYY.", ephemeral=True)
            return

    today = date.today()
    year, month = (None, None) if expense_date else (today.year, today.month)
    matches = budget.find_matching_expenses(
        resolved_cat, item=item or None, amount=amount, exact_date=expense_date, year=year, month=month,
    )

    from parser import EditExpense as _EditExpense
    cmd = _EditExpense(
        category=resolved_cat,
        item=item or None,
        amount=amount,
        date=expense_date,
        split=split,
        moveto=resolved_moveto,
        new_amount=new_amount,
        new_item=new_item or None,
    )
    await interaction.response.send_message(resolve_edit(cmd, matches))


@tree.command(name="archive", description="Force a CSV re-export of a month into the Archive folder")
@app_commands.describe(month="Month to archive, e.g. 'may' or '2026-05'")
async def archive_cmd(interaction: discord.Interaction, month: str):
    if not _authorized(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    year_month = _resolve_month_arg(month)
    if year_month is None:
        await interaction.response.send_message(f"Couldn't understand the month '{month}'.", ephemeral=True)
        return
    out_dir = write_month_csvs(*year_month, DEFAULT_ARCHIVE_DIR)
    await interaction.response.send_message(f"Archived {year_month[1]:02d}/{year_month[0]} to {out_dir}")


@tree.command(name="view", description="List every purchase in a category for a month")
@app_commands.describe(category="Expense category", month="Month to view, e.g. 'may' or '2026-05' (default: this month)")
async def view_cmd(interaction: discord.Interaction, category: str, month: str = ""):
    if not _authorized(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    resolved = resolve_category(category)
    if resolved is None:
        await interaction.response.send_message(f"Unrecognized category '{category}'.", ephemeral=True)
        return
    year_month = _resolve_month_arg(month)
    if year_month is None:
        await interaction.response.send_message(f"Couldn't understand the month '{month}'.", ephemeral=True)
        return
    rows = budget.get_expenses(resolved, *year_month)
    hidden = budget.count_event_expenses_in_month(resolved, *year_month)
    await interaction.response.send_message(format_expense_list(resolved, *year_month, rows, hidden))


@tree.command(name="event", description="View all expenses tagged to a named event")
@app_commands.describe(name="Event name")
async def event_cmd(interaction: discord.Interaction, name: str):
    if not _authorized(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    event = budget.get_event_view(name)
    if event is None:
        await interaction.response.send_message(f"No event named **{name}** found.", ephemeral=True)
        return
    await interaction.response.send_message(format_event_view(event, event["expenses"]))


@tree.command(name="events", description="List events with activity in the past N months")
@app_commands.describe(months="Look-back window in months (default: 6)")
async def events_cmd(interaction: discord.Interaction, months: int = 6):
    if not _authorized(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    from datetime import timedelta
    cutoff = date.today().replace(day=1)
    for _ in range(months - 1):
        cutoff = (cutoff - timedelta(days=1)).replace(day=1)
    events = budget.get_events_since(cutoff)
    await interaction.response.send_message(format_event_list(events, months))


@tree.command(name="seteventlimit", description="Set a spending limit for an event (creates it if new)")
@app_commands.describe(name="Event name", amount="Spending limit in dollars")
async def seteventlimit_cmd(interaction: discord.Interaction, name: str, amount: float):
    if not _authorized(interaction.user.id):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    budget.set_event_allowance(name, amount)
    await interaction.response.send_message(f"Set **{name}** event limit to ${amount:.2f}.")


@add_cmd.autocomplete("category")
@remaining_cmd.autocomplete("category")
@setbudget_cmd.autocomplete("category")
@remove_cmd.autocomplete("category")
@edit_cmd.autocomplete("category")
@edit_cmd.autocomplete("moveto")
@view_cmd.autocomplete("category")
async def category_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=c, value=c)
        for c in all_categories()
        if current.lower() in c.lower()
    ][:25]


@event_cmd.autocomplete("name")
@seteventlimit_cmd.autocomplete("name")
async def event_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=e, value=e)
        for e in budget.get_all_event_names()
        if current.lower() in e.lower()
    ][:25]


if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
