from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.domains.orders import OrderStatus
from app.schemas.base import ApiModel


class OrderItemRequest(ApiModel):
    store_product_id: UUID
    quantity: Annotated[Decimal, Field(gt=0, decimal_places=4)]


class OrderCreateRequest(ApiModel):
    items: Annotated[list[OrderItemRequest], Field(min_length=1)]
    customer_id: UUID | None = None
    fulfillment: Annotated[str, Field(pattern=r"^(pickup|delivery)$")] = "pickup"
    delivery_address: dict | None = None


class OrderItemResponse(ApiModel):
    id: UUID
    store_product_id: UUID
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


class OrderStatusHistoryResponse(ApiModel):
    id: UUID
    from_status: str | None = None
    to_status: str
    actor_user_id: UUID | None = None
    created_at: datetime


class OrderResponse(ApiModel):
    id: UUID
    organization_id: UUID
    store_id: UUID
    customer_id: UUID | None = None
    status: OrderStatus
    subtotal: Decimal
    total: Decimal
    prescription_required: bool
    delivery_address: dict | None = None
    created_at: datetime
    items: list[OrderItemResponse] = []
    history: list[OrderStatusHistoryResponse] = []


class OrderTransitionRequest(ApiModel):
    status: OrderStatus
