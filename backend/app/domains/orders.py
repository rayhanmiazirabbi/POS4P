from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    AppendOnlyMixin,
    Base,
    StoreScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    money_column,
    quantity_column,
)


class OrderStatus(str, Enum):
    PENDING = "pending"
    RESERVED = "reserved"
    ACCEPTED = "accepted"
    PREPARING = "preparing"
    READY = "ready"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset({OrderStatus.RESERVED, OrderStatus.CANCELLED}),
    OrderStatus.RESERVED: frozenset({OrderStatus.ACCEPTED, OrderStatus.CANCELLED}),
    OrderStatus.ACCEPTED: frozenset({OrderStatus.PREPARING, OrderStatus.CANCELLED}),
    OrderStatus.PREPARING: frozenset({OrderStatus.READY, OrderStatus.CANCELLED}),
    OrderStatus.READY: frozenset({OrderStatus.COMPLETED, OrderStatus.CANCELLED}),
    OrderStatus.COMPLETED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}


def transition_order(status: OrderStatus, target: OrderStatus) -> OrderStatus:
    if target not in _TRANSITIONS[status]:
        raise ValueError(f"cannot transition order from {status.value} to {target.value}")
    return target


class Order(StoreScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key"),)

    customer_id: Mapped[UUID | None] = mapped_column(ForeignKey("customers.id"))
    status: Mapped[OrderStatus] = mapped_column(default=OrderStatus.PENDING, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    total: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    delivery_address: Mapped[dict | None] = mapped_column(JSON)
    prescription_required: Mapped[bool] = mapped_column(default=False, nullable=False)


class OrderItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "order_items"

    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    store_product_id: Mapped[UUID] = mapped_column(ForeignKey("store_products.id"), nullable=False)
    product_name: Mapped[str] = mapped_column(String(240), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(quantity_column(), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(money_column(), nullable=False)


class OrderStatusHistory(AppendOnlyMixin, StoreScopedMixin, UUIDPrimaryKeyMixin, Base):
    __tablename__ = "order_status_history"

    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    from_status: Mapped[OrderStatus | None] = mapped_column()
    to_status: Mapped[OrderStatus] = mapped_column(nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True)
class OrderTransition:
    order_id: UUID
    from_status: OrderStatus
    to_status: OrderStatus


def apply_order_transition(order_id: UUID, status: OrderStatus, target: OrderStatus) -> OrderTransition:
    return OrderTransition(order_id, status, transition_order(status, target))
