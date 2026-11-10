import pytest

from till import refund, total


def test_total_adds_the_prices():
    assert total([250, 120]) == 370


def test_a_refund_cannot_exceed_the_payment():
    with pytest.raises(ValueError):
        refund(500, 600)
