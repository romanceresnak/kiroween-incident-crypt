"""Kubernetes resource quantities (64Mi, 10Gi, 500m, 2)."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_MULTIPLIERS: dict[str, Decimal] = {
    "": Decimal(1),
    "m": Decimal("0.001"),
    "k": Decimal(10**3), "K": Decimal(10**3), "M": Decimal(10**6), "G": Decimal(10**9),
    "T": Decimal(10**12), "P": Decimal(10**15),
    "Ki": Decimal(2**10), "Mi": Decimal(2**20), "Gi": Decimal(2**30), "Ti": Decimal(2**40),
    "Pi": Decimal(2**50),
}
_QUANTITY = re.compile(r"^([0-9]+(?:\.[0-9]+)?)(Ki|Mi|Gi|Ti|Pi|m|k|K|M|G|T|P)?$")


def parse_quantity(value: object) -> Decimal | None:
    """The quantity as a number, or None if it is not a valid quantity."""
    if not isinstance(value, str):
        return None
    match = _QUANTITY.match(value.strip())
    if not match:
        return None
    try:
        return Decimal(match.group(1)) * _MULTIPLIERS[match.group(2) or ""]
    except InvalidOperation:
        return None
