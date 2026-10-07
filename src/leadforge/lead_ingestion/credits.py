"""The one credit-value contract (Requirement 21.2, follow-up 2026-10-06).

Credits are exact: an ``int`` or a ``Decimal`` that is a whole number of milli-Credits,
not negative and within a ceiling (``MAX_MILLI`` for one reported figure). A float
(binary error), a bool, NaN/infinity, a negative figure, one past the ceiling or one
finer than 0.001 is a contract violation, refused rather than rounded. The orchestrator
applies this at the adapter boundary, so a bad figure fails only the source that
reported it and never reaches the run's spend write; the store applies it again to the
per-source total it keeps as integer milli-Credits (``MILLI_PER_CREDIT``) in a BIGINT
column, under that column's ceiling (``MAX_STORED_MILLI``).
"""

from __future__ import annotations

from decimal import Decimal

MILLI_PER_CREDIT = Decimal(1000)
# One reported figure: a sum of many such figures stays far inside the stored ceiling.
MAX_MILLI = 2**31 - 1
# A stored total: ``source_run.credits_consumed_milli`` is a BIGINT (0010), 64-bit.
MAX_STORED_MILLI = 2**63 - 1


class CreditValueError(ValueError):
    """A credit figure outside the contract; the message names only the violation."""


def exact_credits(value: object, *, max_milli: int = MAX_MILLI) -> Decimal:
    """``value`` as an exact ``Decimal`` of Credits, at most ``max_milli``
    milli-Credits, or ``CreditValueError``."""
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise CreditValueError(
            f"credits must be an int or a Decimal, got {type(value).__name__}"
        )
    credits = Decimal(value)
    if not credits.is_finite():
        raise CreditValueError("credits are not finite")
    if credits < 0:
        raise CreditValueError("credits are negative")
    ceiling = Decimal(max_milli) / MILLI_PER_CREDIT
    if credits > ceiling:
        raise CreditValueError(f"credits exceed {ceiling} (the ceiling)")
    if not _whole_milli(credits):
        raise CreditValueError("credits are finer than 0.001 (one milli-Credit)")
    return credits


def _whole_milli(credits: Decimal) -> bool:
    """Exact, context-free: no rounding at any size (``Decimal('1.5000')`` is whole)."""
    _, digits, exponent = credits.as_tuple()
    if not isinstance(exponent, int):  # NaN/infinity: never whole
        return False
    trailing_zeros = len(digits) - len("".join(map(str, digits)).rstrip("0"))
    return exponent + trailing_zeros >= -3 or not any(digits)
