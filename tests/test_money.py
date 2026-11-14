import pytest

from app.services.money import MoneyError, format_money, money_input, parse_paise, parse_quantity


@pytest.mark.parametrize(("raw", "expected"), [
    ("0", 0), ("0.01", 1), ("10.10", 1010), ("1234.56", 123456),
    ("999999.99", 99999999),
])
def test_money_is_exact_integer_paise(raw, expected):
    value = parse_paise(raw)
    assert value == expected
    assert isinstance(value, int)
    assert parse_paise(money_input(value)) == expected


@pytest.mark.parametrize("raw", [
    None, "", "-1", "1.001", "NaN", "Infinity", "1e3", "1.2.3", "ten", "1_000",
    "99999999999999999999999999", "1,2,3", "12 34", "₹₹5", "1\n2",
])
def test_reject_malformed_or_overprecise_money(raw):
    with pytest.raises(MoneyError):
        parse_paise(raw)


@pytest.mark.parametrize("raw", ["0", "-1", "1.5", "1.0", "1e2", "NaN", "1000001", "", None])
def test_only_bounded_positive_whole_units(raw):
    with pytest.raises(ValueError):
        parse_quantity(raw)


def test_quantity_boundaries_and_exact_display():
    assert parse_quantity("1000000") == 1_000_000
    assert parse_quantity("0", minimum=0) == 0
    assert format_money(123456789, "INR") == "₹12,34,567.89"
    assert format_money(-1, "INR") == "-₹0.01"
