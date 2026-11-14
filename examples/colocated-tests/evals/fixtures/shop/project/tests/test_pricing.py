from shop.pricing import with_vat


def test_with_vat_adds_twenty_percent():
    assert with_vat(100) == 120
