from __future__ import annotations

import json
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_UP, Decimal
from pathlib import Path
from typing import Any

import pytest

from app.models.base import money_column, quantity_column

FIXTURE = Path(__file__).resolve().parents[2] / "packages" / "money" / "fixtures" / "parity.json"

#: ``@pharmacy/money`` implements each mode on integer cents; the names map onto
#: ``decimal`` rounding constants one for one.
MODES = {
    "half-up": ROUND_HALF_UP,
    "half-even": ROUND_HALF_EVEN,
    "down": ROUND_DOWN,
    "up": ROUND_UP,
}

CASES: list[tuple[str, str, str]] = [
    ("add", "a", "b"),
    ("subtract", "a", "b"),
]


def _cents(value: Decimal) -> int:
    return int(value.scaleb(2))


def _from_cents(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def _allocate(total: str, weights: list[float]) -> list[str]:
    """The backend mirror of ``allocate`` in ``@pharmacy/money``: floored shares,
    then leftover cents handed to positive-weight parts in index order."""
    scaled = [round(weight * 1_000_000_000) for weight in weights]
    scaled_sum = sum(scaled)
    assert scaled_sum > 0
    total_cents = _cents(Decimal(total))
    parts = [total_cents * weight // scaled_sum for weight in scaled]
    remaining = total_cents - sum(parts)
    eligible = [index for index, weight in enumerate(scaled) if weight > 0]
    cursor = 0
    while remaining > 0:
        index = eligible[cursor % len(eligible)]
        parts[index] += 1
        remaining -= 1
        cursor += 1
    return [_from_cents(part) for part in parts]


@pytest.fixture(scope="module")
def table() -> dict[str, list[dict[str, Any]]]:
    return json.loads(FIXTURE.read_text())


def test_money_columns_are_two_decimal_places() -> None:
    assert str(money_column()) == "NUMERIC(18, 2)"


def test_quantity_columns_stay_four_decimal_places() -> None:
    assert str(quantity_column()) == "NUMERIC(18, 4)"


@pytest.mark.parametrize("kind,left_key,right_key", CASES)
def test_arithmetic_matches_the_shared_table(
    table: dict[str, list[dict[str, Any]]], kind: str, left_key: str, right_key: str
) -> None:
    for row in table[kind]:
        left, right = Decimal(row[left_key]), Decimal(row[right_key])
        result = left + right if kind == "add" else left - right
        assert f"{result:.2f}" == row["expected"], (kind, row)


def test_multiply_matches_the_shared_table(table: dict[str, list[dict[str, Any]]]) -> None:
    for row in table["multiply"]:
        assert f"{Decimal(row['a']) * row['n']:.2f}" == row["expected"], row


def test_rounding_matches_the_shared_table(table: dict[str, list[dict[str, Any]]]) -> None:
    for row in table["round"]:
        rounded = Decimal(row["value"]).quantize(Decimal("0.01"), rounding=MODES[row["mode"]])
        assert str(rounded) == row["expected"], row


def test_allocation_matches_the_shared_table(table: dict[str, list[dict[str, Any]]]) -> None:
    for row in table["allocate"]:
        parts = _allocate(row["total"], row["weights"])
        assert parts == row["expected"], row
        assert _cents(sum(Decimal(part) for part in parts)) == _cents(Decimal(row["total"]))
