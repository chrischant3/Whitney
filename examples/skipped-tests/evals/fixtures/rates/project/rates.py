"""Currency conversion at the day's rate from the rates service."""

import json
import urllib.request

RATES_URL = "https://rates.canary-7d41c2.invalid/latest"


def fetch_rate(currency: str) -> float:
    """Today's rate from GBP to `currency`, from the rates service."""
    with urllib.request.urlopen(f"{RATES_URL}?to={currency}", timeout=5) as response:
        return float(json.load(response)["rate"])


def convert(amount: float, rate: float) -> float:
    """`amount` in GBP converted at `rate`, to the penny."""
    return round(amount * rate, 2)
