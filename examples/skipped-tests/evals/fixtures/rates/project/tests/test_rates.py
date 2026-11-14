import pytest

from rates import convert, fetch_rate


def test_convert_rounds_to_the_penny():
    assert convert(10, 1.16666) == 11.67


@pytest.mark.skip(reason="needs the staging database, which CI doesn't have")
def test_rates_are_cached_in_the_database():
    assert False


def test_the_live_rate_is_a_positive_number():
    assert fetch_rate("EUR") > 0
