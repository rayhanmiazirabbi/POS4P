"""Purchase orders: lifecycle, permissions, conversion into purchase drafts."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from app.domains.purchasing import Purchase, PurchaseItem
from app.models import AuditLog, OutboxEvent, Role
from tests.test_purchasing import _make_store_product, _make_supplier

PO_KEY = "po-create-key-0000000001"


def _headers(tenant: dict[str, Any], *, role: Role = Role.OWNER) -> dict[str, str]:
    from tests.conftest import access_token_for

    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=role,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"{uuid4().hex}{uuid4().hex}"[:32]}


async def _create_po(
    client: Any, tenant: dict[str, Any], *, items: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    body = {
        "supplierId": None,
        "items": items
        or [{"name": "Napa Extra 500mg", "quantity": "10", "estUnitCost": "5.50"}],
    }
    response = await client.post("/purchase-orders", json=body, headers=_headers(tenant))
    assert response.status_code == 201, response.text
    return response.json()["data"]


# --- creation and editing -------------------------------------------------------


async def test_create_and_edit_draft(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    data = await _create_po(client, tenant)
    assert data["status"] == "draft"
    assert data["supplierId"] is None
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Napa Extra 500mg"

    added = await client.post(
        f"/purchase-orders/{data['id']}/items",
        json={"name": "Seclo 20mg", "quantity": "4"},
        headers=_headers(tenant),
    )
    assert added.status_code == 201
    item_id = added.json()["data"]["id"]

    patched = await client.patch(
        f"/purchase-orders/{data['id']}/items/{item_id}",
        json={"quantity": "6", "estUnitCost": "2.25"},
        headers=_headers(tenant),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["quantity"] == "6"

    removed = await client.delete(
        f"/purchase-orders/{data['id']}/items/{item_id}", headers=_headers(tenant)
    )
    assert removed.status_code == 200

    detail = await client.get(f"/purchase-orders/{data['id']}", headers=_headers(tenant))
    assert len(detail.json()["data"]["items"]) == 1

    action = await session.scalar(select(AuditLog.action).where(AuditLog.action == "purchase_order.created"))
    assert action == "purchase_order.created"
    event = await session.scalar(
        select(OutboxEvent.event_type).where(OutboxEvent.event_type == "purchase_order.updated")
    )
    assert event is not None


async def test_duplicate_idempotency_key_conflicts(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    headers = _headers(tenant)
    body = {"items": []}
    first = await client.post("/purchase-orders", json=body, headers=headers)
    assert first.status_code == 201
    second = await client.post("/purchase-orders", json=body, headers=headers)
    assert second.status_code == 409
    assert second.json()["code"] == "IDEMPOTENCY_CONFLICT"


async def test_missing_or_short_idempotency_key_rejected(
    client: Any, tenant: dict[str, Any]
) -> None:
    headers = _headers(tenant)
    headers.pop("Idempotency-Key")
    response = await client.post("/purchase-orders", json={"items": []}, headers=headers)
    assert response.status_code == 422


# --- lifecycle transitions --------------------------------------------------------


async def test_order_then_close_then_nothing(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    data = await _create_po(client, tenant)

    closed_too_early = await client.post(f"/purchase-orders/{data['id']}/close", headers=_headers(tenant))
    assert closed_too_early.status_code == 409

    ordered = await client.post(f"/purchase-orders/{data['id']}/order", headers=_headers(tenant))
    assert ordered.status_code == 200
    assert ordered.json()["data"]["status"] == "ordered"
    assert ordered.json()["data"]["orderedAt"] is not None

    reorder = await client.post(f"/purchase-orders/{data['id']}/order", headers=_headers(tenant))
    assert reorder.status_code == 409

    edited = await client.post(
        f"/purchase-orders/{data['id']}/items",
        json={"name": "Late line", "quantity": "1"},
        headers=_headers(tenant),
    )
    assert edited.status_code == 409

    cancelled = await client.post(f"/purchase-orders/{data['id']}/cancel", headers=_headers(tenant))
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "cancelled"


async def test_cancel_from_draft_and_close_from_ordered(
    client: Any, tenant: dict[str, Any]
) -> None:
    draft = await _create_po(client, tenant)
    cancelled = await client.post(f"/purchase-orders/{draft['id']}/cancel", headers=_headers(tenant))
    assert cancelled.status_code == 200
    recancel = await client.post(f"/purchase-orders/{draft['id']}/cancel", headers=_headers(tenant))
    assert recancel.status_code == 409

    ordered = await _create_po(client, tenant)
    await client.post(f"/purchase-orders/{ordered['id']}/order", headers=_headers(tenant))
    closed = await client.post(f"/purchase-orders/{ordered['id']}/close", headers=_headers(tenant))
    assert closed.status_code == 200
    assert closed.json()["data"]["status"] == "closed"
    reclose = await client.post(f"/purchase-orders/{ordered['id']}/close", headers=_headers(tenant))
    assert reclose.status_code == 409


async def test_list_filters_by_status(client: Any, tenant: dict[str, Any]) -> None:
    await _create_po(client, tenant)
    listed = await client.get("/purchase-orders?status=draft", headers=_headers(tenant))
    assert listed.status_code == 200
    page = listed.json()["data"]
    assert page["total"] >= 1
    assert all(row["status"] == "draft" for row in page["items"])


# --- roles ----------------------------------------------------------------------


async def test_cashier_manages_orders_but_not_conversion(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    make_user: Any,
    make_membership: Any,
) -> None:
    from tests.test_catalog import _bearer
    from tests.test_products import _auth_session

    cashier = await make_user(phone="+8801700000077", display_name="Cashier PO")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])
    auth_session = await _auth_session(session, cashier, tenant["organization"], tenant["store"])
    await session.commit()
    cashier_headers = {
        **_bearer(auth_session, cashier, tenant["organization"], store=tenant["store"], role=Role.CASHIER),
        "Idempotency-Key": f"cashier-{uuid4().hex}"[:32],
    }

    created = await client.post(
        "/purchase-orders",
        json={"items": [{"name": "Free-text order line", "quantity": "2"}]},
        headers=cashier_headers,
    )
    assert created.status_code == 201
    po_id = created.json()["data"]["id"]

    marked = await client.post(f"/purchase-orders/{po_id}/order", headers=cashier_headers)
    assert marked.status_code == 200

    forbidden = await client.post(
        f"/purchase-orders/{po_id}/to-purchase",
        json={"supplierId": str(uuid4())},
        headers=cashier_headers,
    )
    assert forbidden.status_code == 403


# --- conversion -----------------------------------------------------------------


async def test_convert_resolves_skips_and_closes(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    from app.domains.catalog import CatalogProduct

    catalog_product = CatalogProduct(name="Adoptme 100mg", package_unit="tablet", country_code="BD")
    session.add(catalog_product)
    shelf_row = await _make_store_product(session, tenant, sku="SKU-PO-1")
    await session.flush()

    lines = [
        {
            "name": shelf_row.sku,
            "quantity": "3",
            "estUnitCost": "8.00",
            "pharmacyProductId": str(shelf_row.pharmacy_product_id),
        },
        {
            "name": catalog_product.name,
            "quantity": "5",
            "estUnitCost": "2.50",
            "catalogProductId": str(catalog_product.id),
        },
        {"name": "Mystery extra", "quantity": "1"},
    ]
    data = await _create_po(client, tenant, items=lines)

    adopt = await client.post(
        "/products/adopt",
        json={"catalogProductId": str(catalog_product.id), "salePrice": "9.00"},
        headers=_headers(tenant),
    )
    assert adopt.status_code == 201, adopt.text

    supplier = await _make_supplier(session, tenant)
    await session.commit()

    result = await client.post(
        f"/purchase-orders/{data['id']}/to-purchase",
        json={"supplierId": str(supplier.id)},
        headers=_headers(tenant),
    )
    assert result.status_code == 200, result.text
    payload = result.json()["data"]
    assert payload["convertedCount"] == 2
    assert [line["name"] for line in payload["skipped"]] == ["Mystery extra"]

    purchase = await client.get(f"/purchases/{payload['purchaseId']}", headers=_headers(tenant))
    assert purchase.status_code == 200
    purchase_data = purchase.json()["data"]
    assert purchase_data["status"] == "draft"
    assert purchase_data["note"].startswith("From PO ")
    assert {item["batchNumber"] for item in purchase_data["items"]} == {"PENDING"}
    expected_total = Decimal("24.00") + Decimal("12.50")
    assert Decimal(purchase_data["totalAmount"]) == expected_total

    detail = await client.get(f"/purchase-orders/{data['id']}", headers=_headers(tenant))
    assert detail.json()["data"]["status"] == "closed"

    total_purchases = await session.scalar(select(func.count()).select_from(Purchase))
    assert int(total_purchases or 0) == 1
    item_rows = list(await session.scalars(select(PurchaseItem)))
    assert len(item_rows) == 2


async def test_convert_without_anything_resolvable_conflicts(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    supplier = await _make_supplier(session, tenant)
    await session.commit()
    data = await _create_po(
        client, tenant, items=[{"name": "Unresolvable thing", "quantity": "1"}]
    )
    response = await client.post(
        f"/purchase-orders/{data['id']}/to-purchase",
        json={"supplierId": str(supplier.id)},
        headers=_headers(tenant),
    )
    assert response.status_code == 409


async def test_convert_requires_a_supplier(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    shelf = await _make_store_product(session, tenant, sku="SKU-PO-SUP")
    await session.flush()
    data = await _create_po(
        client,
        tenant,
        items=[
            {
                "name": "Line",
                "quantity": "1",
                "estUnitCost": "1.00",
                "pharmacyProductId": str(shelf.pharmacy_product_id),
            }
        ],
    )
    response = await client.post(
        f"/purchase-orders/{data['id']}/to-purchase", json={}, headers=_headers(tenant)
    )
    assert response.status_code == 422


async def test_second_convert_of_a_closed_order_conflicts(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """The row lock plus post-lock status recheck: after a successful convert,
    a second attempt (however it arrives) sees the order closed and refuses,
    and no second purchase draft exists."""
    shelf = await _make_store_product(session, tenant, sku="SKU-PO-TWICE")
    supplier = await _make_supplier(session, tenant)
    await session.flush()
    data = await _create_po(
        client,
        tenant,
        items=[
            {
                "name": "Line",
                "quantity": "2",
                "estUnitCost": "3.00",
                "pharmacyProductId": str(shelf.pharmacy_product_id),
            }
        ],
    )
    payload = {"supplierId": str(supplier.id)}
    first = await client.post(
        f"/purchase-orders/{data['id']}/to-purchase", json=payload, headers=_headers(tenant)
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        f"/purchase-orders/{data['id']}/to-purchase", json=payload, headers=_headers(tenant)
    )
    assert second.status_code == 409
    total_purchases = await session.scalar(select(func.count()).select_from(Purchase))
    assert int(total_purchases or 0) == 1


# --- receiving reconciliation ---------------------------------------------------


async def test_partial_receipts_track_remaining_and_finish_the_order(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    supplier = await _make_supplier(session, tenant)
    shelf = await _make_store_product(session, tenant, sku="PO-PARTIAL")
    await session.commit()
    created = await client.post(
        "/purchase-orders",
        json={
            "supplierId": str(supplier.id),
            "items": [{
                "name": "Partial delivery",
                "quantity": "10",
                "estUnitCost": "5.00",
                "pharmacyProductId": str(shelf.pharmacy_product_id),
            }],
        },
        headers=_headers(tenant),
    )
    order = created.json()["data"]
    item_id = order["items"][0]["id"]
    await client.post(f"/purchase-orders/{order['id']}/order", headers=_headers(tenant))

    first = await client.post(
        "/purchases/receive",
        json={
            "purchaseOrderId": order["id"],
            "supplierId": str(supplier.id),
            "items": [{
                "purchaseOrderItemId": item_id,
                "storeProductId": str(shelf.id),
                "quantity": "4",
                "unitCost": "5.00",
            }],
        },
        headers={**_headers(tenant), "Idempotency-Key": "po-partial-receive-000001"},
    )
    assert first.status_code == 201, first.text
    assert first.json()["data"]["purchaseOrderId"] == order["id"]
    detail = await client.get(f"/purchase-orders/{order['id']}", headers=_headers(tenant))
    assert detail.json()["data"]["status"] == "partially_received"
    assert detail.json()["data"]["items"][0]["receivedQuantity"] == "4.0000"
    assert detail.json()["data"]["items"][0]["remainingQuantity"] == "6.0000"

    second = await client.post(
        "/purchases/receive",
        json={
            "purchaseOrderId": order["id"],
            "supplierId": str(supplier.id),
            "items": [{
                "purchaseOrderItemId": item_id,
                "storeProductId": str(shelf.id),
                "quantity": "7",
                "unitCost": "5.00",
            }],
        },
        headers={**_headers(tenant), "Idempotency-Key": "po-final-receive-0000001"},
    )
    assert second.status_code == 201, second.text
    complete = await client.get(f"/purchase-orders/{order['id']}", headers=_headers(tenant))
    assert complete.json()["data"]["status"] == "received"
    assert complete.json()["data"]["items"][0]["receivedQuantity"] == "11.0000"
    assert complete.json()["data"]["items"][0]["remainingQuantity"] == "0"


async def test_order_receipt_rejects_an_item_from_another_order(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    supplier = await _make_supplier(session, tenant)
    shelf = await _make_store_product(session, tenant, sku="PO-WRONG-LINE")
    await session.commit()
    first = await _create_po(client, tenant, items=[{
        "name": "First", "quantity": "1", "pharmacyProductId": str(shelf.pharmacy_product_id)
    }])
    second = await _create_po(client, tenant, items=[{
        "name": "Second", "quantity": "1", "pharmacyProductId": str(shelf.pharmacy_product_id)
    }])
    await client.post(f"/purchase-orders/{first['id']}/order", headers=_headers(tenant))
    response = await client.post(
        "/purchases/receive",
        json={
            "purchaseOrderId": first["id"],
            "supplierId": str(supplier.id),
            "items": [{
                "purchaseOrderItemId": second["items"][0]["id"],
                "storeProductId": str(shelf.id),
                "quantity": "1",
            }],
        },
        headers={**_headers(tenant), "Idempotency-Key": "po-wrong-line-0000000001"},
    )
    assert response.status_code == 404
