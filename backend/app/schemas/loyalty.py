from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiModel


class LoyaltyEnrollRequest(ApiModel):
    customer_id: UUID


class LoyaltyTransactionRequest(ApiModel):
    transaction_type: Literal["earn", "redeem", "refund", "bonus", "adjust", "expire"]
    points: Annotated[int, Field()]
    source_type: Annotated[str, Field(min_length=1, max_length=80)]
    source_id: UUID
    expires_at: datetime | None = None


class LoyaltyAccountResponse(ApiModel):
    id: UUID
    organization_id: UUID
    customer_id: UUID
    balance: int
    active: bool


class LoyaltyTransactionResponse(ApiModel):
    id: UUID
    account_id: UUID
    transaction_type: str
    points: int
    balance_after: int | None = None
    source_type: str
    source_id: UUID
    expires_at: datetime | None
    created_at: datetime


class LoyaltyBalanceResponse(ApiModel):
    account: LoyaltyAccountResponse
    rebuilt_from_ledger: bool = False


class LoyaltyRebuildResponse(ApiModel):
    account: LoyaltyAccountResponse
    ledger_total: int
