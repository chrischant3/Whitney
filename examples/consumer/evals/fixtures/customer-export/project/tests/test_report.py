from client import orders_url, summarise


def test_orders_url_names_the_month():
    assert orders_url("2026-10").endswith("/orders?month=2026-10")


def test_summarise_counts_orders_per_country():
    rows = [{"country": "GB"}, {"country": "GB"}, {"country": "FR"}]
    assert summarise(rows) == {"GB": 2, "FR": 1}
