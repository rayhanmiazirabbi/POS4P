from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.errors import ValidationError

#: The offline provider is deterministic on purpose: identical input must give
#: identical output so tests and audits can reason about what the "model" said.
PROVIDER_NAME = "deterministic-offline"
MODEL_VERSION = "rules-1.0"

LINE_PATTERN = re.compile(
    r"^\s*(?P<quantity>\d+(?:[.,]\d+)?)\s*[x*]?\s+(?P<description>.+?)\s+@\s*(?P<unit_cost>\d+(?:\.\d+)?)\s*$"
)
CART_ITEM_PATTERN = re.compile(r"^\s*(?P<quantity>\d+(?:[.,]\d+)?)?\s*(?P<name>.+?)\s*$")


@dataclass(frozen=True)
class ExtractedLine:
    description: str
    quantity: Decimal
    unit_cost: Decimal
    confidence: Decimal


@dataclass(frozen=True)
class ParsedCartItem:
    name: str
    quantity: Decimal
    confidence: Decimal


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", "."))
    except InvalidOperation:
        return None


class InvoiceOCRAdapter:
    """Turns invoice text into structured lines without any network call.

    Recognized line shape: ``12 x Napa Extra 500mg @ 1.20``. Anything else comes
    back with a low confidence so the job lands in ``needs_review`` instead of a
    staff member silently ordering garbage.
    """

    name = PROVIDER_NAME
    model_version = MODEL_VERSION

    def extract_lines(self, text: str) -> list[ExtractedLine]:
        if not text or not text.strip():
            raise ValidationError("Invoice text must not be empty")
        lines: list[ExtractedLine] = []
        for raw_line in text.splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            match = LINE_PATTERN.match(raw_line)
            if match is None:
                lines.append(
                    ExtractedLine(
                        description=raw_line,
                        quantity=Decimal(0),
                        unit_cost=Decimal(0),
                        confidence=Decimal("0.20"),
                    )
                )
                continue
            quantity = _decimal(match.group("quantity")) or Decimal(0)
            unit_cost = _decimal(match.group("unit_cost")) or Decimal(0)
            well_formed = quantity > 0 and unit_cost > 0
            lines.append(
                ExtractedLine(
                    description=match.group("description"),
                    quantity=quantity,
                    unit_cost=unit_cost,
                    confidence=Decimal("0.98") if well_formed else Decimal("0.40"),
                )
            )
        return lines


class VoiceCartAdapter:
    """Maps a transcript like ``2 napa extra, amoxicillin`` onto cart items."""

    name = PROVIDER_NAME
    model_version = MODEL_VERSION

    def parse_items(self, transcript: str) -> list[ParsedCartItem]:
        if not transcript or not transcript.strip():
            raise ValidationError("Transcript must not be empty")
        items: list[ParsedCartItem] = []
        for fragment in re.split(r"[,;\n]", transcript):
            fragment = fragment.strip().strip(".")
            if not fragment:
                continue
            match = CART_ITEM_PATTERN.match(fragment)
            if match is None:
                continue
            name = match.group("name").strip()
            if not name:
                continue
            quantity_text = match.group("quantity")
            quantity = _decimal(quantity_text) if quantity_text else Decimal(1)
            if quantity is None or quantity <= 0:
                quantity = Decimal(1)
            items.append(
                ParsedCartItem(
                    name=name,
                    quantity=quantity,
                    confidence=Decimal("0.95") if quantity_text else Decimal("0.75"),
                )
            )
        return items
