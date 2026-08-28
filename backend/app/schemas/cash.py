from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiModel
from app.schemas.payments import Money


class CashSessionOpenRequest(ApiModel):
    opening_cash: Money


class CashSessionCloseRequest(ApiModel):
    counted_cash: Money
    note: Annotated[str | None, Field(min_length=1, max_length=400)] = None


class CashSessionResponse(ApiModel):
    id: UUID
    store_id: UUID
    opened_by: UUID
    opened_by_name: str
    opened_at: datetime
    closed_at: datetime | None = None
    closed_by: UUID | None = None
    closed_by_name: str | None = None
    status: str
    opening_cash: Decimal
    counted_cash: Decimal | None = None
    expected_cash: Decimal | None = None
    difference: Decimal | None = None
    closing_note: str | None = None
    cash_in: Decimal
    cash_out: Decimal


for _model in (CashSessionOpenRequest, CashSessionCloseRequest, CashSessionResponse):
    _model.model_rebuild()
