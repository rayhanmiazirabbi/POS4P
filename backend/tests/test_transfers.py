from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.context import RequestContext
from app.domains.inventory import (
    InventoryBalance,
    InventoryMovement,
)
from app.domains.products import PharmacyProduct
from app.models import Role, StoreProduct
from app.services.inventory import receive_batch
from tests._phase4_helpers import role_headers
from tests.conftest import access_token_for


def _store_headers(tenant: dict[str, Any], store: Any) -> dict[str, str]:
    """Owner headers scoped to a specific branch -- receiving happens there."""
    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=Role.OWNER,
        store_id=store.id,
    )
    return {"Authorization": f"Bearer {token}"}


def _context(
    tenant: dict[str, Any], store_id: Any, *, role: Role = Role.OWNER
) -> RequestContext:
    return RequestContext(
        organization_id=tenant["organization"].id,
        user_id=tenant["owner"].id,
        role=role,
        store_id=store_id,
    )


async def _second_store(session: Any, tenant: dict[str, Any]) -> Any:
    from app.models import Store, StoreUser

    store = Store(
        organization_id=tenant["organization"].id,
        name="Branch B",
        code="BRANCH-B",
        timezone="Asia/Dhaka",
        currency="BDT",
        settings={},
    )
    session.add(store)
    await session.flush()
    session.add(
        StoreUser(store_id=store.id, user_id=tenant["owner"].id, role=Role.OWNER, active=True)
    )
    await session.flush()
    return store


async def _product_in_both_stores(
    session: Any, tenant: dict[str, Any], store_b: Any, *, sku: str = "SKU-X"
) -> tuple[StoreProduct, StoreProduct]:
    product = PharmacyProduct(
        organization_id=tenant["organization"].id, name="Ibuprofen", unit="box", active=True
    )
    session.add(product)
    await session.flush()
    source = StoreProduct(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        pharmacy_product_id=product.id,
        sku=f"{sku}-A",
        sale_price=Decimal("10.00"),
        minimum_stock=0,
        active=True,
    )
    destination = StoreProduct(
        organization_id=tenant["organization"].id,
        store_id=store_b.id,
        pharmacy_product_id=product.id,
        sku=f"{sku}-B",
        sale_price=Decimal("10.00"),
        minimum_stock=0,
        active=True,
    )
    session.add_all([source, destination])
    await session.flush()
    return source, destination


async def _receive_stock(session: Any, tenant: dict[str, Any], sp: StoreProduct, qty: str) -> None:
    await receive_batch(
        session,
        _context(tenant, tenant["store"].id),
        sp.id,
        batch_number="B1",
        expiry_date=date.today() + timedelta(days=365),
        unit_cost=Decimal("5.00"),
        quantity=Decimal(qty),
        idempotency_key=uuid4().hex * 2,
    )
    await session.commit()


# --- happy path ---------------------------------------------------------------


async def test_transfer_ship_then_receive_moves_stock_between_branches(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    auth_headers: Callable[..., dict[str, str]],
) -> None:
    headers = auth_headers(tenant)
    store_b = await _second_store(session, tenant)
    source_sp, destination_sp = await _product_in_both_stores(session, tenant, store_b)
    await session.commit()
    await _receive_stock(session, tenant, source_sp, "100")

    create = await client.post(
        "/inventory/transfers",
        json={
            "transferNumber": f"TRF-{uuid4().hex[:8].upper()}",
            "fromStoreId": str(tenant["store"].id),
            "toStoreId": str(store_b.id),
            "items": [{"storeProductId": str(source_sp.id), "quantity": "25"}],
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    transfer_id = create.json()["data"]["id"]
    assert create.json()["data"]["status"] == "draft"

    ship = await client.post(f"/inventory/transfers/{transfer_id}/ship", headers=headers)
    assert ship.status_code == 200, ship.text
    assert ship.json()["data"]["status"] == "in_transit"

    # Ship is idempotent by status.
    again = await client.post(f"/inventory/transfers/{transfer_id}/ship", headers=headers)
    assert again.status_code == 200

    balance_a = await session.scalar(
        select(InventoryBalance).where(InventoryBalance.store_product_id == source_sp.id)
    )
    assert Decimal(balance_a.on_hand) == Decimal("75")  # type: ignore[union-attr]

    receive = await client.post(
        f"/inventory/transfers/{transfer_id}/receive", headers=_store_headers(tenant, store_b)
    )
    assert receive.status_code == 200, receive.text
    assert receive.json()["data"]["status"] == "received"

    receive_again = await client.post(
        f"/inventory/transfers/{transfer_id}/receive", headers=_store_headers(tenant, store_b)
    )
    assert receive_again.status_code == 200  # idempotent, no double receipt

    balance_b = await session.scalar(
        select(InventoryBalance).where(InventoryBalance.store_product_id == destination_sp.id)
    )
    assert Decimal(balance_b.on_hand) == Decimal("25")  # type: ignore[union-attr]
    movements = list(
        await session.scalars(
            select(InventoryMovement).order_by(InventoryMovement.occurred_at)
        )
    )
    types = sorted(m.movement_type.value for m in movements)
    assert types.count("receipt") == 2 and types.count("transfer") == 1


async def test_ship_rejects_shortfall(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    auth_headers: Callable[..., dict[str, str]],
) -> None:
    headers = auth_headers(tenant)
    store_b = await _second_store(session, tenant)
    source_sp, _destination = await _product_in_both_stores(session, tenant, store_b)
    await session.commit()
    await _receive_stock(session, tenant, source_sp, "5")

    create = await client.post(
        "/inventory/transfers",
        json={
            "transferNumber": f"TRF-{uuid4().hex[:8].upper()}",
            "fromStoreId": str(tenant["store"].id),
            "toStoreId": str(store_b.id),
            "items": [{"storeProductId": str(source_sp.id), "quantity": "50"}],
        },
        headers=headers,
    )
    assert create.status_code == 201
    transfer_id = create.json()["data"]["id"]
    ship = await client.post(f"/inventory/transfers/{transfer_id}/ship", headers=headers)
    assert ship.status_code == 409
    assert ship.json()["code"] == "INSUFFICIENT_STOCK"


async def test_create_is_idempotent_on_transfer_number(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    auth_headers: Callable[..., dict[str, str]],
) -> None:
    headers = auth_headers(tenant)
    store_b = await _second_store(session, tenant)
    source_sp, _destination = await _product_in_both_stores(session, tenant, store_b)
    await session.commit()

    body = {
        "transferNumber": "TRF-DUP-0001",
        "fromStoreId": str(tenant["store"].id),
        "toStoreId": str(store_b.id),
        "items": [{"storeProductId": str(source_sp.id), "quantity": "3"}],
    }
    first = await client.post("/inventory/transfers", json=body, headers=headers)
    assert first.status_code == 201
    second = await client.post("/inventory/transfers", json=body, headers=headers)
    assert second.status_code == 201
    assert second.json()["data"]["id"] == first.json()["data"]["id"]


async def test_cancel_only_applies_to_drafts(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    auth_headers: Callable[..., dict[str, str]],
) -> None:
    headers = auth_headers(tenant)
    store_b = await _second_store(session, tenant)
    source_sp, _destination = await _product_in_both_stores(session, tenant, store_b)
    await session.commit()
    await _receive_stock(session, tenant, source_sp, "10")

    create = await client.post(
        "/inventory/transfers",
        json={
            "transferNumber": f"TRF-{uuid4().hex[:8].upper()}",
            "fromStoreId": str(tenant["store"].id),
            "toStoreId": str(store_b.id),
            "items": [{"storeProductId": str(source_sp.id), "quantity": "2"}],
        },
        headers=headers,
    )
    transfer_id = create.json()["data"]["id"]
    cancel = await client.post(f"/inventory/transfers/{transfer_id}/cancel", headers=headers)
    assert cancel.status_code == 200
    assert cancel.json()["data"]["status"] == "cancelled"

    ship = await client.post(f"/inventory/transfers/{transfer_id}/ship", headers=headers)
    assert ship.status_code == 409


async def test_same_source_and_destination_rejected(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    auth_headers: Callable[..., dict[str, str]],
) -> None:
    headers = auth_headers(tenant)
    response = await client.post(
        "/inventory/transfers",
        json={
            "transferNumber": f"TRF-{uuid4().hex[:8].upper()}",
            "fromStoreId": str(tenant["store"].id),
            "toStoreId": str(tenant["store"].id),
            "items": [{"storeProductId": str(uuid4()), "quantity": "1"}],
        },
        headers=headers,
    )
    assert response.status_code == 422


async def test_cashier_cannot_create_transfers(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    auth_headers: Callable[..., dict[str, str]],
) -> None:
    headers = await role_headers(session, tenant, Role.CASHIER)
    store_b = await _second_store(session, tenant)
    source_sp, _destination = await _product_in_both_stores(session, tenant, store_b)
    await session.commit()
    response = await client.post(
        "/inventory/transfers",
        json={
            "transferNumber": f"TRF-{uuid4().hex[:8].upper()}",
            "fromStoreId": str(tenant["store"].id),
            "toStoreId": str(store_b.id),
            "items": [{"storeProductId": str(source_sp.id), "quantity": "1"}],
        },
        headers=headers,
    )
    assert response.status_code == 403
