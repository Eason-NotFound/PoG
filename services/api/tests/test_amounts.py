from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from pog_api.amounts import (
    UINT256_MAX,
    UInt256String,
    decimal_to_uint_string,
    human_amount_to_atomic,
)


class AmountModel(BaseModel):
    amount: UInt256String


@pytest.mark.parametrize("value", ["0", "1", str(UINT256_MAX)])
def test_uint256_accepts_canonical_strings(value):
    assert AmountModel(amount=value).amount == value


@pytest.mark.parametrize(
    "value",
    [
        -1,
        1,
        True,
        False,
        "-1",
        "+1",
        "01",
        "1.0",
        "1e3",
        "NaN",
        "Infinity",
        str(UINT256_MAX + 1),
    ],
)
def test_uint256_rejects_noncanonical_or_out_of_range(value):
    with pytest.raises(ValidationError):
        AmountModel(amount=value)


@pytest.mark.parametrize(
    ("human", "atomic"),
    [
        ("0", "0"),
        ("1", "1000000"),
        ("16.8", "16800000"),
        ("0.000001", "1"),
        ("72.000000", "72000000"),
    ],
)
def test_human_six_decimal_conversion(human, atomic):
    assert human_amount_to_atomic(human) == atomic


@pytest.mark.parametrize("value", ["1.0000001", ".1", "01", "-1", "+1", "1e2", "NaN"])
def test_human_amount_rejects_precision_and_noncanonical_text(value):
    with pytest.raises(ValueError):
        human_amount_to_atomic(value)


def test_decimal_negative_zero_normalizes_to_zero():
    assert decimal_to_uint_string(Decimal("-0")) == "0"
