from __future__ import annotations

from decimal import Decimal
import re
from typing import Annotated

from pydantic import AfterValidator, StrictStr


UINT256_MAX = 2**256 - 1
_UINT_RE = re.compile(r"^(0|[1-9][0-9]*)$")
_HUMAN_AMOUNT_RE = re.compile(r"^(0|[1-9][0-9]*)(?:\.([0-9]{1,6}))?$")


def validate_uint256_string(value: str) -> str:
    if type(value) is not str or not _UINT_RE.fullmatch(value):
        raise ValueError("must be a canonical unsigned decimal integer string")
    number = int(value)
    if number > UINT256_MAX:
        raise ValueError("exceeds uint256")
    return value


UInt256String = Annotated[StrictStr, AfterValidator(validate_uint256_string)]


def human_amount_to_atomic(value: str, decimals: int = 6) -> str:
    if type(value) is not str or decimals != 6:
        raise ValueError("amount must be a string and decimals must be 6")
    match = _HUMAN_AMOUNT_RE.fullmatch(value)
    if not match:
        raise ValueError("amount must be canonical decimal text with at most 6 decimal places")
    whole, fraction = value.split(".", 1) if "." in value else (value, "")
    atomic = int(whole) * 10**decimals + int(fraction.ljust(decimals, "0") or "0")
    if atomic > UINT256_MAX:
        raise ValueError("amount exceeds uint256")
    return str(atomic)


def decimal_to_uint_string(value: Decimal) -> str:
    if value != value.to_integral_value() or value < 0 or value > UINT256_MAX:
        raise ValueError("database value is not uint256")
    if value == 0:
        return "0"
    return format(value, "f").split(".", 1)[0]
