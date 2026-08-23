from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.context import RequestContext
from app.domains.inventory import InventoryBatch, InventoryMovementType
from app.main import app
from app.models import Organization, Role, StoreProduct
from app.services.inventory import (
    InsufficientStock,
    adjust_stock,
    allocate_fefo_for_product,
    consume_allocations,
    rebuild_balances_from_ledger,
    receive_batch,
)
from app.services.stores import business_date
from tests.conftest import access_token_for

KEY = "idempotency-receive-key-000001"


def _context(tenant: dict[str, Any], *, role: Role = Role.OWNER) -> RequestContext:
    return RequestContext(
        organization_id=tenant["organization"].id,
        user_id=tenant["owner"].id,
        role=role,
        store_id=tenant["store"].id,
    )


async def _make_store_product(
    session: Any, tenant: dict[str, Any], *, sku: str = "SKU-1", minimum_stock: Decimal = Decimal(0)
) -> StoreProduct:
    from app.domains.products import PharmacyProduct

    product = PharmacyProduct(
        organization_id=tenant["organization"].id, name="Paracetamol", unit="box", active=True
    )
    session.add(product)
    await session.flush()
    store_product = StoreProduct(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        pharmacy_product_id=product.id,
        sku=sku,
        sale_price=Decimal("10.00"),
        minimum_stock=minimum_stock,
        active=True,
    )
    session.add(store_product)
    await session.flush()
    return store_product


async def _receive(
    session: Any, tenant: dict[str, Any], store_product: StoreProduct, quantity: str, **kwargs: Any
) -> None:
    await receive_batch(
        session,
        _context(tenant),
        store_product.id,
        batch_number=kwargs.pop("batch_number", "B1"),
        expiry_date=kwargs.pop("expiry_date", date.today() + timedelta(days=365)),
        unit_cost=Decimal("5.00"),
        quantity=Decimal(quantity),
        idempotency_key=kwargs.pop("idempotency_key", uuid4().hex * 2)[:64],
        **kwargs,
    )


# --- receive idempotency ------------------------------------------------------


async def test_receive_batch_is_idempotent_on_key(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    store_product = await _make_store_product(session, tenant)
    await session.commit()
    headers = _owner_headers(tenant)

    body = {
        "storeId": str(store_product.id),
        "batchNumber": "B-001",
        "expiryDate": str(date.today() + timedelta(days=200)),
        "unitCost": "5.50",
        "quantity": "100",
    }
    first = await client.post(
        "/inventory/receive", json=_payload(store_product), headers={**headers, "Idempotency-Key": KEY}
    )
    assert first.status_code == 201, first.text
    second = await client.post(
        "/inventory/receive", json=_payload(store_product), headers={**headers, "Idempotency-Key": KEY}
    )
    assert second.status_code == 201
    assert first.json()["data"]["batch"]["id"] == second.json()["data"]["batch"]["id"]
    assert first.json()["data"]["balance"] == second.json()["data"]["balance"]
    assert second.json()["data"]["balance"]["onHand"] == "100.0000"

    from sqlalchemy import func, select

    from app.domains.inventory import InventoryBatch, InventoryMovement

    assert (await session.scalar(select(func.count()).select_from(InventoryBatch))) == 1
    assert (await session.scalar(select(func.count()).select_from(InventoryMovement))) == 1


def _payload(store_product: StoreProduct) -> dict[str, Any]:
    return {
        "storeProductId": str(store_product.id),
        "batchNumber": "B-001",
        "expiryDate": str(date.today() + timedelta(days=200)),
        "unitCost": "5.50",
        "quantity": "100",
    }


def _owner_headers(tenant: dict[str, Any]) -> dict[str, str]:
    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=Role.OWNER,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}


# --- FEFO ---------------------------------------------------------------------


async def test_fefo_allocation_skips_expired_and_orders_by_expiry(
    session: Any, tenant: dict[str, Any]
) -> None:
    sp = await _make_store_product(session, tenant)
    today = date.today()
    await _receive(session, tenant, sp, "50", batch_number="SOON", expiry_date=today + timedelta(days=30))
    await _receive(session, tenant, sp, "50", batch_number="EXPIRED", expiry_date=today - timedelta(days=1))
    await _receive(session, tenant, sp, "50", batch_number="LATER", expiry_date=today + timedelta(days=90))
    await _receive(session, tenant, sp, "50", batch_number="NOEXP", expiry_date=None)
    await session.commit()

    result = await allocate_fefo_for_product(
        session, _context(tenant), sp.id, Decimal("120"), as_of=today
    )
    assert result.ok is True
    numbers = []
    from sqlalchemy import select

    from app.domains.inventory import InventoryBatch

    batches = {
        b.id: b.batch_number
        for b in await session.scalars(select(InventoryBatch).where(InventoryBatch.store_product_id == sp.id))
    }
    for allocation in result.allocations:
        numbers.append(batches[allocation.batch_id])
    # The expired batch must never be touched; earliest expiry first, no-expiry last.
    assert numbers[0] == "SOON"
    assert numbers[-1] == "NOEXP"
    assert "EXPIRED" not in numbers


@pytest.mark.parametrize("utc_hour", [12, 20])
async def test_fefo_expiry_cutoff_uses_the_store_calendar_not_utc(
    session: Any, tenant: dict[str, Any], monkeypatch: Any, utc_hour: int
) -> None:
    """Overnight, a batch that expired must stay un-dispensable.

    Every other FEFO test passes ``as_of`` explicitly, so none of them exercise the
    default -- which is the path the POS actually uses. On UTC that default lags the
    branch's own date from 18:00 to midnight (00:00-06:00 in Dhaka), leaving a batch
    that expired overnight eligible until dawn. Dispensing expired medicine is the
    single failure FEFO exists to prevent, so the boundary is pinned here rather
    than left to whichever hour the suite happens to run at.
    """
    frozen = datetime(2026, 8, 22, utc_hour, 30, tzinfo=UTC)
    monkeypatch.setattr("app.services.stores.utc_now", lambda: frozen)

    store_today = business_date(tenant["store"])
    sp = await _make_store_product(session, tenant)
    await _receive(
        session, tenant, sp, "5", batch_number="OLD", expiry_date=store_today - timedelta(days=1)
    )
    await _receive(
        session, tenant, sp, "5", batch_number="GOOD", expiry_date=store_today + timedelta(days=30)
    )
    await session.commit()

    # No explicit as_of: this is the call the till makes.
    result = await allocate_fefo_for_product(session, _context(tenant), sp.id, Decimal("5"))
    assert result.ok is True
    assert result.allocated == Decimal("5")

    batches = {
        b.id: b.batch_number
        for b in await session.scalars(
            select(InventoryBatch).where(InventoryBatch.store_product_id == sp.id)
        )
    }
    assert [batches[a.batch_id] for a in result.allocations] == ["GOOD"]


async def test_insufficient_stock_returns_explicit_shortfall_and_consume_rejects(
    session: Any, tenant: dict[str, Any]
) -> None:
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp, "30")
    await session.commit()

    result = await allocate_fefo_for_product(
        session, _context(tenant), sp.id, Decimal("100"), as_of=date.today()
    )
    assert result.ok is False
    assert result.allocated == Decimal("30")
    assert result.shortfall == Decimal("70")

    from app.context import RequestContext as Ctx

    ctx: Ctx = _context(tenant)
    from app.domains.inventory import Allocation

    overdrawn = [*result.allocations, Allocation(result.allocations[0].batch_id, Decimal("1"))]
    with pytest.raises(InsufficientStock):
        await consume_allocations(
            session, ctx, sp.id, overdrawn, InventoryMovementType.SALE
        )


async def test_consume_allocations_decrement_balances_per_batch(
    session: Any, tenant: dict[str, Any]
) -> None:
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp, "40")
    await session.commit()
    ctx = _context(tenant)
    result = await allocate_fefo_for_product(session, ctx, sp.id, Decimal("15"), as_of=date.today())
    movements = await consume_allocations(
        session, ctx, sp.id, result.allocations, InventoryMovementType.SALE
    )
    assert len(movements) == 1
    on_hand, reserved = await get_stock_pair(session, tenant, sp)
    assert on_hand == Decimal("25")
    rebuilt = await rebuild_balances_from_ledger(session, tenant["store"].id)
    assert rebuilt[sp.id] == Decimal("25")


async def get_stock_pair(session: Any, tenant: dict[str, Any], sp: StoreProduct) -> tuple[Decimal, Decimal]:
    from app.services.inventory import get_stock

    return await get_stock(session, _context(tenant), sp.id)


# --- adjustments ---------------------------------------------------------------


async def test_negative_adjustment_beyond_stock_is_rejected(
    session: Any, tenant: dict[str, Any]
) -> None:
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp, "10")
    await session.commit()
    with pytest.raises(InsufficientStock):
        await adjust_stock(
            session,
            _context(tenant),
            sp.id,
            quantity=Decimal("-11"),
            reason="Broken in transit",
        )


async def test_adjustment_endpoint_requires_manager_role(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp, "10")
    from datetime import timedelta

    from app.models import Session as SessionModel
    from app.security import generate_token, hash_token, utc_now

    cashier = await make_user(phone="+8801700000031", display_name="Cashier")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])
    cashier_session = SessionModel(
        user_id=cashier.id,
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        refresh_token_hash=hash_token(generate_token()),
        expires_at=utc_now() + timedelta(days=30),
    )
    session.add(cashier_session)
    await session.commit()

    cashier_token = access_token_for(
        session_id=cashier_session.id,
        user_id=cashier.id,
        organization_id=tenant["organization"].id,
        role=Role.CASHIER,
        store_id=tenant["store"].id,
    )
    response = await client.post(
        "/inventory/adjustments",
        json={
            "storeProductId": str(sp.id),
            "quantity": "-2",
            "reason": "Damaged strip",
        },
        headers={"Authorization": f"Bearer {cashier_token}"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"

    ok = await client.post(
        "/inventory/adjustments",
        json={"storeProductId": str(sp.id), "quantity": "-2", "reason": "Damaged strip"},
        headers=_owner_headers(tenant),
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["data"]["onHand"] == "8.0000"


# --- expiring / low stock -------------------------------------------------------


async def test_expiring_query_returns_batches_within_window(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    sp = await _make_store_product(session, tenant)
    today = date.today()
    await _receive(session, tenant, sp, "5", batch_number="SOON", expiry_date=today + timedelta(days=10))
    await _receive(session, tenant, sp, "5", batch_number="FAR", expiry_date=today + timedelta(days=400))
    await session.commit()

    response = await client.get(
        f"/inventory/expiring?storeId={tenant['store'].id}&withinDays=30",
        headers=_owner_headers(tenant),
    )
    assert response.status_code == 200
    items = response.json()["data"]
    assert [item["batchNumber"] for item in items] == ["SOON"]
    assert items[0]["daysUntilExpiry"] <= 30


async def test_low_stock_lists_products_below_minimum(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    low_sp = await _make_store_product(session, tenant, sku="LOW", minimum_stock=Decimal("20"))
    healthy_sp = await _make_store_product(session, tenant, sku="OK", minimum_stock=Decimal("5"))
    await _receive(session, tenant, low_sp, "10")
    await _receive(session, tenant, healthy_sp, "50")
    await session.commit()

    response = await client.get(
        f"/inventory/low-stock?storeId={tenant['store'].id}", headers=_owner_headers(tenant)
    )
    assert response.status_code == 200
    skus = [item["sku"] for item in response.json()["data"]]
    assert skus == ["LOW"]


# --- rebuild equality -------------------------------------------------------------


async def test_rebuild_matches_incremental_balances(session: Any, tenant: dict[str, Any]) -> None:
    from sqlalchemy import select

    from app.domains.inventory import InventoryBalance

    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp, "60")
    await session.commit()
    ctx = _context(tenant)
    result = await allocate_fefo_for_product(session, ctx, sp.id, Decimal("18"), as_of=date.today())
    await consume_allocations(session, ctx, sp.id, result.allocations, InventoryMovementType.SALE)
    await adjust_stock(session, ctx, sp.id, quantity=Decimal("-7"), reason="Count correction")
    await session.commit()

    incremental = {
        row.store_product_id: Decimal(row.on_hand)
        for row in await session.scalars(
            select(InventoryBalance).where(InventoryBalance.store_id == tenant["store"].id)
        )
    }
    rebuilt = await rebuild_balances_from_ledger(session, tenant["store"].id)
    assert rebuilt == incremental
    assert rebuilt[sp.id] == Decimal("35")


# --- cross-tenant isolation ----------------------------------------------------------


async def test_cross_tenant_store_scoped_reads_are_not_found(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_store: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    other_org: Organization = await make_organization(name="Rival", slug="rival")
    rival = await make_user(phone="+8801799999999", display_name="Rival Owner")
    other_store = await make_store(other_org, code="RIVAL")
    await make_membership(other_org, rival, Role.OWNER, other_store)
    await session.commit()
    sp = await _make_store_product(session, tenant)
    await session.commit()

    headers = _owner_headers(tenant)
    foreign = await client.get(
        f"/inventory/stock?storeId={other_store.id}", headers=headers
    )
    assert foreign.status_code == 404
    product = await client.get(f"/inventory/stock/{sp.id}/batches", headers=headers)
    assert product.status_code == 200  # own tenant product is readable


async def test_foreign_store_product_is_not_found(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    sp = await _make_store_product(session, tenant)
    await session.commit()
    response = await client.post(
        "/inventory/adjustments",
        json={"storeProductId": str(sp.id), "quantity": "-1", "reason": "nope"},
        headers={**_owner_headers(tenant)},
    )
    assert response.status_code != 500

