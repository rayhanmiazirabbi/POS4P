"""End-to-end coverage for the inventory operations added on top of the core
ledger: adjustments with reasons, the movement ledger view, rack management,
stocktake sessions, reorder suggestions, and the cost reports.

The fixtures and helpers mirror ``test_inventory.py`` so both files read the same
way; they are separate because these endpoints came later and fail for their own
reasons.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.models import Role, StoreProduct
from tests.conftest import access_token_for


def _headers(tenant: dict[str, Any], *, idempotency_key: str | None = None, role: Role = Role.OWNER) -> dict[str, str]:
    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=role,
        store_id=tenant["store"].id,
    )
    headers = {"Authorization": f"Bearer {token}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


async def _make_store_product(
    session: Any,
    tenant: dict[str, Any],
    *,
    sku: str = "SKU-1",
    rack: str | None = None,
    minimum_stock: Decimal = Decimal(0),
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
        rack=rack,
        active=True,
    )
    session.add(store_product)
    await session.flush()
    return store_product


async def _receive(session: Any, tenant: dict[str, Any], store_product: StoreProduct, quantity: str, **kwargs: Any) -> None:
    from app.context import RequestContext
    from app.services.inventory import receive_batch

    await receive_batch(
        session,
        RequestContext(
            organization_id=tenant["organization"].id,
            user_id=tenant["owner"].id,
            role=Role.OWNER,
            store_id=tenant["store"].id,
        ),
        store_product.id,
        batch_number=kwargs.pop("batch_number", "B1"),
        expiry_date=kwargs.pop("expiry_date", date.today() + timedelta(days=365)),
        unit_cost=kwargs.pop("unit_cost", Decimal("5.00")),
        quantity=Decimal(quantity),
        idempotency_key=kwargs.pop("idempotency_key", f"key-{UUID(int=store_product.id.int % 2**62)}-{quantity}"),
        **kwargs,
    )


# --- adjustments carry their reason into the ledger ----------------------------


async def test_adjustment_reason_surfaces_in_movement_ledger(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    store_product = await _make_store_product(session, tenant)
    await _receive(session, tenant, store_product, "10")
    await session.commit()

    adjust = await client.post(
        "/inventory/adjustments",
        json={
            "storeProductId": str(store_product.id),
            "quantity": "-3",
            "reason": "Damp stockade damaged by monsoon leak",
        },
        headers=_headers(tenant),
    )
    assert adjust.status_code == 201, adjust.text

    ledger = await client.get("/inventory/movements", headers=_headers(tenant))
    assert ledger.status_code == 200, ledger.text
    rows = ledger.json()["data"]["items"]
    adjustment = next(row for row in rows if row["movementType"] == "adjustment")
    assert adjustment["quantity"] == "-3.0000"
    assert adjustment["reason"] == "Damp stockade damaged by monsoon leak"
    assert adjustment["sku"] == "SKU-1"
    assert adjustment["productName"] == "Paracetamol"
    assert adjustment["batchNumber"] is None


async def test_movement_ledger_filters_by_product_and_type(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    store_product = await _make_store_product(session, tenant)
    await _receive(session, tenant, store_product, "10")
    await session.commit()

    receipts = await client.get(
        "/inventory/movements",
        params={"storeProductId": str(store_product.id), "movementType": "receipt"},
        headers=_headers(tenant),
    )
    assert receipts.status_code == 200, receipts.text
    rows = receipts.json()["data"]["items"]
    assert len(rows) == 1
    assert rows[0]["movementType"] == "receipt"
    assert rows[0]["quantity"] == "10.0000"

    sales_only = await client.get(
        "/inventory/movements",
        params={"movementType": "sale"},
        headers=_headers(tenant),
    )
    assert sales_only.json()["data"]["total"] == 0


# --- rack management ------------------------------------------------------------


async def test_racks_list_and_rename_normalize_labels(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    await _make_store_product(session, tenant, sku="SKU-1", rack="Rack  1 ")
    await _make_store_product(session, tenant, sku="SKU-2", rack="rack 1")
    await _make_store_product(session, tenant, sku="SKU-3", rack="Rack 2")
    await session.commit()

    racks = await client.get(
        "/inventory/racks", params={"storeId": str(tenant["store"].id)}, headers=_headers(tenant)
    )
    assert racks.status_code == 200, racks.text
    labels = {row["rack"]: row["itemCount"] for row in racks.json()["data"]}
    # "Rack  1 " and "rack 1" are listed as written -- normalization groups them
    # only on rename, where the operator says they are the same shelf.
    assert labels == {"Rack 1": 1, "rack 1": 1, "Rack 2": 1}

    renamed = await client.post(
        "/inventory/racks/rename",
        json={
            "storeId": str(tenant["store"].id),
            "fromRack": "rack 1",
            "toRack": "Rack 1",
        },
        headers=_headers(tenant),
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["data"]["itemCount"] == 1

    after = await client.get(
        "/inventory/racks", params={"storeId": str(tenant["store"].id)}, headers=_headers(tenant)
    )
    labels = {row["rack"]: row["itemCount"] for row in after.json()["data"]}
    assert labels == {"Rack 1": 2, "Rack 2": 1}


async def test_intake_normalizes_rack_whitespace(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    store_product = await _make_store_product(session, tenant, rack="  Rack   7  ")
    await session.commit()
    assert store_product.rack == "  Rack   7  "  # written before normalization existed

    intake = await client.post(
        "/inventory/intakes",
        json={
            "source": "opening_stock",
            "storeProductId": str(store_product.id),
            "shelf": {"rack": "  Rack   8  "},
            "quantity": "5",
        },
        headers={**_headers(tenant), "Idempotency-Key": "intake-rack-normalize-0001"},
    )
    assert intake.status_code == 201, intake.text
    assert intake.json()["data"]["rack"] == "Rack 8"


# --- stocktake ------------------------------------------------------------------


async def test_stocktake_books_variances_and_closes(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    store_product = await _make_store_product(session, tenant)
    other = await _make_store_product(session, tenant, sku="SKU-2")
    await _receive(session, tenant, store_product, "10")
    await _receive(session, tenant, other, "4")
    await session.commit()

    opened = await client.post(
        "/inventory/stocktakes", json={"note": "Quarterly count"}, headers=_headers(tenant)
    )
    assert opened.status_code == 201, opened.text
    stocktake_id = opened.json()["data"]["id"]

    # Count one short, one exact.
    for body in (
        {"storeProductId": str(store_product.id), "countedQuantity": "8"},
        {"storeProductId": str(other.id), "countedQuantity": "4"},
    ):
        line = await client.post(f"/inventory/stocktakes/{stocktake_id}/items", json=body, headers=_headers(tenant))
        assert line.status_code == 201, line.text

    view = await client.get(f"/inventory/stocktakes/{stocktake_id}", headers=_headers(tenant))
    lines = {row["sku"]: row for row in view.json()["data"]["lines"]}
    assert lines["SKU-1"]["variance"] == "2.0000"  # system minus counted
    assert lines["SKU-1"]["systemQuantity"] == "10.0000"
    assert lines["SKU-2"]["variance"] == "0.0000"

    finalized = await client.post(f"/inventory/stocktakes/{stocktake_id}/finalize", headers=_headers(tenant))
    assert finalized.status_code == 200, finalized.text
    summary = finalized.json()["data"]
    assert summary["correctedLines"] == 1
    assert summary["unchangedLines"] == 1
    assert summary["stocktake"]["status"] == "completed"

    stock = await client.get(
        "/inventory/stock", params={"storeId": str(tenant["store"].id)}, headers=_headers(tenant)
    )
    on_hand = {
        row["storeProductId"]: row["onHand"]
        for row in stock.json()["data"]
    }
    assert on_hand[str(store_product.id)] == "8.0000"

    # A closed session cannot be edited or finalized again.
    again = await client.post(
        f"/inventory/stocktakes/{stocktake_id}/items",
        json={"storeProductId": str(store_product.id), "countedQuantity": "9"},
        headers=_headers(tenant),
    )
    assert again.status_code == 409

    ledger = await client.get("/inventory/movements", headers=_headers(tenant))
    correction = next(
        row for row in ledger.json()["data"]["items"] if row["referenceType"] == "stocktake"
    )
    assert correction["quantity"] == "-2.0000"
    assert correction["reason"] is not None and "Quarterly count" in correction["reason"]


async def test_stocktake_requires_manager(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    # Role comes from the membership row, not the token claim, so the guard is
    # asserted at the service boundary like the adjustment role tests are.
    from app.context import RequestContext
    from app.errors import Forbidden
    from app.services.inventory import create_stocktake

    import pytest

    with pytest.raises(Forbidden):
        await create_stocktake(
            session,
            RequestContext(
                organization_id=tenant["organization"].id,
                user_id=tenant["owner"].id,
                role=Role.CASHIER,
                store_id=tenant["store"].id,
            ),
            note="count",
        )


# --- reorder suggestions ----------------------------------------------------------


async def test_reorder_suggestions_refill_below_minimum(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    low = await _make_store_product(session, tenant, minimum_stock=Decimal("10"))
    healthy = await _make_store_product(session, tenant, sku="SKU-2", minimum_stock=Decimal("2"))
    await _receive(session, tenant, low, "3")
    await _receive(session, tenant, healthy, "50")
    await session.commit()

    response = await client.get(
        "/inventory/reorder-suggestions",
        params={"storeId": str(tenant["store"].id)},
        headers=_headers(tenant),
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]
    assert [row["sku"] for row in items] == ["SKU-1"]
    # Refill to twice the minimum: 2 * 10 - 3 = 17.
    assert items[0]["suggestedQuantity"] == "17.0000"
    assert items[0]["available"] == "3.0000"


# --- valuation, dead stock, COGS -------------------------------------------------


async def test_valuation_dead_stock_and_windowed_cogs(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    from app.domains.inventory import InventoryMovement, InventoryMovementType
    from app.security import utc_now

    seller = await _make_store_product(session, tenant)
    dusty = await _make_store_product(session, tenant, sku="SKU-2", rack="Rack 9")
    await _receive(session, tenant, seller, "10", unit_cost=Decimal("5.00"))
    await _receive(session, tenant, dusty, "6", unit_cost=Decimal("7.00"))
    # A sale long enough ago that `seller` is alive but `dusty` never sold.
    old_sale = InventoryMovement(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        store_product_id=seller.id,
        batch_id=None,
        movement_type=InventoryMovementType.SALE,
        quantity=Decimal("-2"),
        idempotency_key="ledger-old-sale-0001",
        occurred_at=utc_now() - timedelta(days=5),
        actor_user_id=tenant["owner"].id,
    )
    session.add(old_sale)
    await session.commit()

    valuation = await client.get("/reports/inventory-valuation", headers=_headers(tenant))
    assert valuation.status_code == 200, valuation.text
    data = valuation.json()["data"]
    lines = {row["sku"]: row for row in data["lines"]}
    # Valuation is batch-sourced, so the batchless sale above does not touch it:
    # seller: 10 units at 5 = 50; dusty: 6 at 7 = 42. A batchless correction has
    # no cost attached, which is the contract this pins.
    assert lines["SKU-1"]["valueAtCost"] == "50.00"
    assert lines["SKU-2"]["valueAtCost"] == "42.00"
    assert lines["SKU-2"]["rack"] == "Rack 9"
    assert data["totalValueAtCost"] == "92.00"

    dead = await client.get("/reports/dead-stock", params={"idleDays": 90}, headers=_headers(tenant))
    assert dead.status_code == 200, dead.text
    dead_lines = [row["sku"] for row in dead.json()["data"]["lines"]]
    assert dead_lines == ["SKU-2"]

    cogs = await client.get(
        "/reports/cogs",
        params={"from": "2020-01-01T00:00:00Z", "to": "2100-01-01T00:00:00Z"},
        headers=_headers(tenant),
    )
    assert cogs.status_code == 200, cogs.text
    # The sale movement above carries no batch, so batch-costed COGS is zero;
    # the assertion pins the contract, not a lucky number.
    assert cogs.json()["data"]["costOfGoodsSold"] == "0.00"


async def test_valuation_forbidden_to_cashier(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    # Same membership-not-claim rule as the stocktake guard: assert at the
    # service, where the role actually lives.
    from app.context import RequestContext
    from app.errors import Forbidden
    from app.services.reports import inventory_valuation

    import pytest

    with pytest.raises(Forbidden):
        await inventory_valuation(
            session,
            RequestContext(
                organization_id=tenant["organization"].id,
                user_id=tenant["owner"].id,
                role=Role.CASHIER,
                store_id=tenant["store"].id,
            ),
        )
