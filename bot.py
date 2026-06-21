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
    format_expense_list,
    format_no_match,
    format_remaining,
    format_removal_candidates,
    format_removed,
)
from month_utils import parse_month_reference
from parser import (
    AddExpense,
    ArchiveMonth,
    DashboardQuery,
    ParseError,
    RemainingQuery,
    RemoveExpense,
    SetBudget,
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
        budget.add_expense(parsed.category, parsed.item, parsed.amount, parsed.date, parsed.description)
        return format_added(parsed)

    if isinstance(parsed, RemainingQuery):
        entry = budget.get_remaining(parsed.category, parsed.year, parsed.month)
        return format_remaining(entry)

    if isinstance(parsed, DashboardQuery):
        entries = budget.get_dashboard(parsed.year, parsed.month)
        return format_dashboard(entries, parsed.year, parsed.month)

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

    if isinstance(parsed, ViewCategory):
        rows = budget.get_expenses(parsed.category, parsed.year, parsed.month)
        return format_expense_list(parsed.category, parsed.year, parsed.month, rows)

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
)
async def add_cmd(
    interaction: discord.Interaction,
    category: str,
    item: str,
    amount: float,
    date_str: str = "",
    description: str = "",
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
    budget.add_expense(resolved, item, amount, expense_date, description)
    await interaction.response.send_message(
        format_added(AddExpense(resolved, item, amount, expense_date, description))
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
    await interaction.response.send_message(format_dashboard(entries, *year_month))


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
    await interaction.response.send_message(format_expense_list(resolved, *year_month, rows))


@add_cmd.autocomplete("category")
@remaining_cmd.autocomplete("category")
@setbudget_cmd.autocomplete("category")
@remove_cmd.autocomplete("category")
@view_cmd.autocomplete("category")
async def category_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=c, value=c)
        for c in all_categories()
        if current.lower() in c.lower()
    ][:25]


if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
