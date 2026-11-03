"""Tiny payments helper."""

import os


def charge(amount_pence: int) -> dict:
    """Pretend to charge a card; returns the request we would send."""
    return {"amount": amount_pence, "key": os.environ.get("PAYMENTS_SECRET_KEY")}
