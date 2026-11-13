from shop.cart import total


def test_total_adds_vat():
    assert total([100, 150]) == 300
