"""Prices, in pence."""


def with_vat(amount: int, rate: int = 20) -> int:
    """`amount` with VAT at `rate` percent added, rounded to the nearest penny."""
    return round(amount * (100 + rate) / 100)
