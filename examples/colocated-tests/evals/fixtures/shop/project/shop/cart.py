"""A shopping cart."""

from shop.pricing import with_vat


def total(prices: list[int]) -> int:
    """The cart's total, in pence, with VAT."""
    return with_vat(sum(prices))
