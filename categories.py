"""Canonical expense categories and free-text alias matching."""

# date tracking began; used as the effective_from for each category's first allowance
TRACKING_START = "2026-06-01"

# canonical name -> default starting allowance (None = no limit)
DEFAULT_ALLOWANCES = {
    "Groceries": 200,
    "Food": 100,
    "Entertainment": 50,
    "Projects": 200,
    "Subscriptions": 150,
    "Bills": None,
    "Transportation": 150,
    "Grooming": None,
    "Rainy Day": None,
}

ALIASES = {
    "groceries": "Groceries",
    "grocery": "Groceries",
    "food": "Food",
    "entertainment": "Entertainment",
    "fun": "Entertainment",
    "projects": "Projects",
    "project": "Projects",
    "subscriptions": "Subscriptions",
    "subscription": "Subscriptions",
    "subs": "Subscriptions",
    "bills": "Bills",
    "bill": "Bills",
    "transportation": "Transportation",
    "transport": "Transportation",
    "grooming": "Grooming",
    "rainy": "Rainy Day",
    "rainy day": "Rainy Day",
    "rainyday": "Rainy Day",
    "one-time": "Rainy Day",
    "onetime": "Rainy Day",
    "irregular": "Rainy Day",
}


def resolve_category(text: str) -> str | None:
    """Map free-text input to a canonical category name, or None if unrecognized."""
    return ALIASES.get(text.strip().lower())


def all_categories() -> list[str]:
    return list(DEFAULT_ALLOWANCES.keys())
