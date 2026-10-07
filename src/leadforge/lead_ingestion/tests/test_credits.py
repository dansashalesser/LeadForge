"""The one credit-value contract: int or Decimal, exact to a milli-Credit, >= 0.

Follow-up (2026-10-06) to Requirement 21.2: what an adapter reports as spent is
checked here, at the adapter->orchestrator boundary, so a bad figure is that source's
own failure and never reaches (and breaks) the run's spend write.
"""

import random
from decimal import Decimal
from typing import Any

import pytest

from leadforge.lead_ingestion.credits import MAX_MILLI, CreditValueError, exact_credits


# Verifies: specs/lead-source-adapters/requirements.md#21.2
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, Decimal(0)),
        (3, Decimal(3)),
        (Decimal("1.5"), Decimal("1.5")),
        (Decimal("0.001"), Decimal("0.001")),
        (Decimal("2.000"), Decimal(2)),
        (Decimal("1.5000000"), Decimal("1.5")),
        (Decimal("0E-10"), Decimal(0)),
        (Decimal("2147483.647"), Decimal("2147483.647")),  # the store's ceiling
    ],
)
def test_int_and_milli_exact_decimal_are_accepted(
    value: Any, expected: Decimal
) -> None:
    result = exact_credits(value)
    assert isinstance(result, Decimal)
    assert result == expected


# Verifies: specs/lead-source-adapters/requirements.md#21.2
@pytest.mark.parametrize(
    ("value", "violation"),
    [
        (0.1, "float"),
        (1.0, "float"),
        (True, "bool"),
        ("1.5", "str"),
        (Decimal("0.0005"), "finer than 0.001"),
        # Beyond the default 28-digit context: still refused, never rounded to pass.
        (Decimal("1." + "0" * 30 + "1"), "finer than 0.001"),
        (Decimal("NaN"), "not finite"),
        (Decimal("sNaN"), "not finite"),
        (Decimal("Infinity"), "not finite"),
        (-1, "negative"),
        (Decimal("-0.5"), "negative"),
        (Decimal("2147483.648"), "exceed"),
        (10**40, "exceed"),
        (Decimal("1E+1000"), "exceed"),
    ],
)
def test_anything_else_is_a_named_contract_violation(
    value: Any, violation: str
) -> None:
    with pytest.raises(CreditValueError) as refused:
        exact_credits(value)
    assert violation in str(refused.value)


# Verifies: specs/lead-source-adapters/requirements.md#21.2 (property)
def test_every_whole_milli_figure_round_trips_exactly() -> None:
    rng = random.Random(20261006)
    for milli in [0, 1, 999, 1000, MAX_MILLI] + [
        rng.randrange(MAX_MILLI) for _ in range(500)
    ]:
        value = Decimal(milli) / 1000
        assert exact_credits(value) * 1000 == milli
