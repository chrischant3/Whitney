"""A shop till: totals and refunds, in pence."""


def total(prices):
    """The sum of the prices."""
    return sum(prices)


def refund(paid, amount):
    """Refund `amount` of a payment of `paid`, and return what is left of the payment."""
    return paid - amount
