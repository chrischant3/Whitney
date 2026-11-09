"""The report service's API client and its monthly summary."""

from collections import Counter

BASE_URL = "https://api.test.example.com"


def orders_url(month: str) -> str:
    """The URL of one month's orders, such as orders_url("2026-10")."""
    return f"{BASE_URL}/orders?month={month}"


def summarise(rows: list[dict[str, str]]) -> dict[str, int]:
    """Orders per country, from rows holding a "country" field."""
    return dict(Counter(row["country"] for row in rows))
