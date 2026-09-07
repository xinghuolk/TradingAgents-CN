"""Exact addition for finite broker decimals without changing caller context."""

from collections.abc import Iterable
from decimal import Decimal, localcontext


def exact_sum(values: Iterable[Decimal]) -> Decimal:
    values = tuple(values)
    if not values:
        return Decimal(0)
    exponent = min(value.as_tuple().exponent for value in values)
    leading = max(value.adjusted() for value in values)
    # Aligned coefficient widths plus carry digits for every possible addend.
    with localcontext() as context:
        context.prec = max(1, leading - exponent + 1) + len(str(len(values)))
        return sum(values, Decimal(0))
