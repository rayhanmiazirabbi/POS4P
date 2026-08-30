from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI
from sqlalchemy import func, select

from app.domains.inventory import InventoryBatch, InventoryBalance, InventoryMovement
from app.domains.purchasing import Purchase, PurchaseItem, PurchaseStatus
from app.domains.suppliers import SupplierLedgerEntry
from app.main import app
from app.models import AuditLog, Organization, OutboxEvent, Role, StoreProduct

CONFIRM_KEY = "confirm-key-000000000001"


def _headers(tenant: dict[str, Any], *, role: Role = Role.OWNER) -> dict[str, str]:
    from tests.conftest import access_token_for

    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=role,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}


async def _make_supplier(session: Any, tenant: dict[str, Any]) -> Any:
    from app.domains.suppliers import Supplier, SupplierStatus

    supplier = Supplier(
        organization_id=tenant["organization"].id,
        name=f"Supplier {uuid4().hex[:8]}",
        status=SupplierStatus.ACTIVE,
    )
    session.add(supplier)
    await session.flush()
    return supplier


async def _make_store_product(
    session: Any, tenant: dict[str, Any], *, sku: str = "SKU-P1"
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
        minimum_stock=Decimal(0),
        active=True,
    )
    session.add(store_product)
    await session.flush()
    return store_product


async def _create_draft(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    *,
    items: list[dict[str, Any]] | None = None,
) -> tuple[Any, StoreProduct]:
    supplier = await _make_supplier(session, tenant)
    sp = await _make_store_product(session, tenant)
    await session.commit()
    body = {
        "supplierId": str(supplier.id),
        "items": items
        or [
            {
                "storeProductId": str(sp.id),
                "quantity": "10",
                "unitCost": "5.50",
                "batchNumber": "B-100",
                "expiryDate": str(date.today() + timedelta(days=365)),
            }
        ],
    }
    response = await client.post("/purchases", json=body, headers=_headers(tenant))
    assert response.status_code == 201, response.text
    return response.json()["data"], sp


# --- draft creation -----------------------------------------------------------


async def test_create_draft_computes_total_server_side(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    data, _ = await _create_draft(client, session, tenant)
    assert data["status"] == PurchaseStatus.DRAFT.value
    assert data["totalAmount"] == "55.00"
    assert data["confirmedAt"] is None
    assert data["items"][0]["unitCost"] == "5.50"


async def test_create_draft_rejects_unknown_supplier_and_store_product(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    missing = await _create_draft(client, session, tenant)
    bad_supplier = {
        "supplierId": str(uuid4()),
        "items": [
            {
                "storeProductId": missing[1].id.__str__(),
                "quantity": "1",
                "unitCost": "1",
                "batchNumber": "X",
            }
        ],
    }
    response = await client.post("/purchases", json=bad_supplier, headers=_headers(tenant))
    assert response.status_code == 404

    supplier = await _make_supplier(session, tenant)
    await session.commit()
    bad_product = {
        "supplierId": str(supplier.id),
        "items": [
            {"storeProductId": str(uuid4()), "quantity": "1", "unitCost": "1", "batchNumber": "X"}
        ],
    }
    response = await client.post("/purchases", json=bad_product, headers=_headers(tenant))
    assert response.status_code == 404


# --- confirmation ---------------------------------------------------------------


async def test_confirm_is_atomic_and_books_everything(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    data, sp = await _create_draft(client, session, tenant)
    purchase_id = data["id"]
    supplier_id = data["supplierId"]

    confirmed = await client.post(
        f"/purchases/{purchase_id}/confirm",
        headers={**_headers(tenant), "Idempotency-Key": CONFIRM_KEY},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()["data"]
    assert body["status"] == PurchaseStatus.CONFIRMED.value
    assert body["confirmedAt"] is not None

    batches = list(await session.scalars(select(InventoryBatch)))
    movements = list(await session.scalars(select(InventoryMovement)))
    balances = list(await session.scalars(select(InventoryBalance)))
    entries = list(await session.scalars(select(SupplierLedgerEntry)))
    outbox = list(await session.scalars(select(OutboxEvent)))
    audits = list(await session.scalars(select(AuditLog)))

    assert len(batches) == 1
    assert batches[0].batch_number == "B-100"
    assert len(movements) == 1
    assert Decimal(movements[0].quantity) == Decimal("10")
    assert movements[0].reference_type == "purchase"
    assert str(movements[0].reference_id) == purchase_id
    assert len(balances) == 1
    assert Decimal(balances[0].on_hand) == Decimal("10")
    assert len(entries) == 1
    assert entries[0].entry_type == "purchase"
    assert Decimal(entries[0].amount) == Decimal("55.00")
    assert any(a.action == "purchase.confirmed" and a.entity_type == "purchase" for a in audits)
    assert len(outbox) == 1
    assert outbox[0].event_type == "purchase.confirmed"
    assert str(outbox[0].aggregate_id) == purchase_id
    assert outbox[0].payload["total_amount"] == "55.00"


async def test_double_confirm_conflicts_and_replay_is_idempotent(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    data, _ = await _create_draft(client, session, tenant)
    purchase_id = data["id"]

    first = await client.post(
        f"/purchases/{purchase_id}/confirm",
        headers={**_headers(tenant), "Idempotency-Key": CONFIRM_KEY},
    )
    assert first.status_code == 200

    # Same key replays the same result without new rows.
    replay = await client.post(
        f"/purchases/{purchase_id}/confirm",
        headers={**_headers(tenant), "Idempotency-Key": CONFIRM_KEY},
    )
    assert replay.status_code == 200
    assert replay.json()["data"]["id"] == purchase_id

    # A different key on an already-confirmed purchase conflicts.
    other = await client.post(
        f"/purchases/{purchase_id}/confirm",
        headers={**_headers(tenant), "Idempotency-Key": "other-key-00000000000001"},
    )
    assert other.status_code == 409

    count = await session.scalar(select(func.count()).select_from(InventoryMovement))
    assert int(count or 0) == 1


async def test_confirm_requires_idempotency_key_header(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    data, _ = await _create_draft(client, session, tenant)
    response = await client.post(
        f"/purchases/{data['id']}/confirm", headers=_headers(tenant)
    )
    assert response.status_code == 422


# --- direct counter receiving -------------------------------------------------


async def test_direct_receive_all_roles_can_post_credit_receipts(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    supplier = await _make_supplier(session, tenant)
    product = await _make_store_product(session, tenant, sku="ROLE-RECEIVE")
    await session.commit()
    for index, role in enumerate((Role.OWNER, Role.MANAGER, Role.CASHIER, Role.INVENTORY_STAFF)):
        response = await client.post(
            "/purchases/receive",
            json={
                "supplierId": str(supplier.id),
                "items": [{"storeProductId": str(product.id), "quantity": "1", "unitCost": "5.00"}],
            },
            headers={**_headers(tenant, role=role), "Idempotency-Key": f"role-receive-{index:02d}-00000000"},
        )
        assert response.status_code == 201, response.text
        body = response.json()["data"]
        assert body["receiptNumber"] == f"GRN-{index + 1:08d}"
        assert body["creditAmount"] == "5.00"
        assert body["lines"][0]["batchNumber"].startswith(body["receiptNumber"])


async def test_direct_receive_line_total_digital_payment_and_replay(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    supplier = await _make_supplier(session, tenant)
    await session.commit()
    key = "receive-custom-000000000001"
    payload = {
        "supplierId": str(supplier.id),
        "invoiceNumber": "SUP-44",
        "items": [{
            "customProduct": {"name": "Custom Saline", "unit": "bottle", "barcode": "CUST-SALINE-44"},
            "shelf": {"salePrice": "45.00", "sku": "SAL-44", "rack": "A1"},
            "quantity": "3",
            "lineTotal": "100.00",
        }],
        "payments": [{"method": "bkash", "amount": "40.00", "providerReference": "TX-44"}],
    }
    first = await client.post(
        "/purchases/receive",
        json=payload,
        headers={**_headers(tenant, role=Role.CASHIER), "Idempotency-Key": key},
    )
    assert first.status_code == 201, first.text
    receipt = first.json()["data"]
    assert receipt["totalAmount"] == "100.00"
    assert receipt["paidAmount"] == "40.00"
    assert receipt["creditAmount"] == "60.00"
    assert receipt["supplierBalanceAfter"] == "60.00"
    assert receipt["lines"][0]["unitCost"] == "33.33"
    assert receipt["lines"][0]["lineTotal"] == "100.00"
    assert receipt["payments"] == [{"method": "bkash", "amount": "40.00", "providerReference": "TX-44"}]

    replay = await client.post(
        "/purchases/receive",
        json=payload,
        headers={**_headers(tenant, role=Role.INVENTORY_STAFF), "Idempotency-Key": key},
    )
    assert replay.status_code == 201
    assert replay.json()["data"]["purchaseId"] == receipt["purchaseId"]
    movement_count = await session.scalar(select(func.count()).select_from(InventoryMovement))
    assert int(movement_count or 0) == 1


async def test_direct_receive_supplier_total_allows_zero_cost_stock_and_zero_value_return(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    supplier = await _make_supplier(session, tenant)
    product = await _make_store_product(session, tenant, sku="NO-COST")
    await session.commit()
    received = await client.post(
        "/purchases/receive",
        json={
            "supplierId": str(supplier.id),
            "totalAmount": "75.00",
            "items": [{"storeProductId": str(product.id), "quantity": "3"}],
        },
        headers={**_headers(tenant), "Idempotency-Key": "receive-no-cost-0000000001"},
    )
    assert received.status_code == 201, received.text
    receipt = received.json()["data"]
    assert receipt["totalAmount"] == "75.00"
    assert receipt["creditAmount"] == "75.00"
    assert receipt["lines"][0]["unitCost"] == "0.00"
    assert receipt["lines"][0]["lineTotal"] == "0.00"
    batch_cost = await session.scalar(
        select(InventoryBatch.unit_cost).where(InventoryBatch.store_product_id == product.id)
    )
    assert Decimal(batch_cost) == Decimal("0.00")

    returned = await client.post(
        f"/purchases/{receipt['purchaseId']}/returns",
        json={"lines": [{"purchaseItemId": receipt["lines"][0]["purchaseItemId"], "quantity": "1"}]},
        headers=_headers(tenant),
    )
    assert returned.status_code == 201, returned.text
    assert returned.json()["data"]["totalAmount"] == "0.00"


async def test_direct_receive_mixed_costs_uses_supplier_total_and_validates_floor(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    supplier = await _make_supplier(session, tenant)
    costed = await _make_store_product(session, tenant, sku="COSTED")
    uncosted = await _make_store_product(session, tenant, sku="UNCOSTED")
    await session.commit()
    payload = {
        "supplierId": str(supplier.id),
        "totalAmount": "80.00",
        "items": [
            {"storeProductId": str(costed.id), "quantity": "2", "unitCost": "10.00"},
            {"storeProductId": str(uncosted.id), "quantity": "4"},
        ],
    }
    response = await client.post(
        "/purchases/receive",
        json=payload,
        headers={**_headers(tenant), "Idempotency-Key": "receive-mixed-cost-0000001"},
    )
    assert response.status_code == 201, response.text
    receipt = response.json()["data"]
    assert receipt["totalAmount"] == "80.00"
    by_sku = {line["sku"]: line for line in receipt["lines"]}
    assert by_sku["COSTED"]["lineTotal"] == "20.00"
    assert by_sku["UNCOSTED"]["lineTotal"] == "0.00"

    below_costs = await client.post(
        "/purchases/receive",
        json={**payload, "totalAmount": "19.99"},
        headers={**_headers(tenant), "Idempotency-Key": "receive-below-cost-0000001"},
    )
    assert below_costs.status_code == 422
    both_costs = await client.post(
        "/purchases/receive",
        json={
            "supplierId": str(supplier.id),
            "items": [{"storeProductId": str(costed.id), "quantity": "1", "unitCost": "1.00", "lineTotal": "1.00"}],
        },
        headers={**_headers(tenant), "Idempotency-Key": "receive-both-costs-000001"},
    )
    assert both_costs.status_code == 422


async def test_direct_receive_cash_requires_and_reconciles_open_shift(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    supplier = await _make_supplier(session, tenant)
    product = await _make_store_product(session, tenant, sku="CASH-RECEIVE")
    await session.commit()
    payload = {
        "supplierId": str(supplier.id),
        "items": [{"storeProductId": str(product.id), "quantity": "5", "unitCost": "10.00"}],
        "payments": [{"method": "cash", "amount": "25.00"}],
    }
    blocked = await client.post(
        "/purchases/receive",
        json=payload,
        headers={**_headers(tenant, role=Role.CASHIER), "Idempotency-Key": "cash-blocked-000000000001"},
    )
    assert blocked.status_code == 409

    opened = await client.post(
        "/cash-sessions",
        json={"openingCash": "500.00"},
        headers=_headers(tenant, role=Role.OWNER),
    )
    assert opened.status_code == 201, opened.text
    received = await client.post(
        "/purchases/receive",
        json=payload,
        headers={**_headers(tenant, role=Role.INVENTORY_STAFF), "Idempotency-Key": "cash-received-00000000001"},
    )
    assert received.status_code == 201, received.text
    current = await client.get("/cash-sessions/current", headers=_headers(tenant))
    assert current.json()["data"]["cashOut"] == "25.00"


# --- returns ----------------------------------------------------------------------


async def test_return_reduces_stock_supplier_balance_and_rejects_overreturn(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    data, sp = await _create_draft(client, session, tenant)
    purchase_id = data["id"]
    supplier_id = data["supplierId"]
    item_id = data["items"][0]["id"]
    await client.post(
        f"/purchases/{purchase_id}/confirm",
        headers={**_headers(tenant), "Idempotency-Key": CONFIRM_KEY},
    )

    returned = await client.post(
        f"/purchases/{purchase_id}/returns",
        json={"lines": [{"purchaseItemId": item_id, "quantity": "4"}]},
        headers=_headers(tenant),
    )
    assert returned.status_code == 201, returned.text
    assert returned.json()["data"]["status"] == PurchaseStatus.RETURNED.value

    balance = await session.scalar(
        select(InventoryBalance).where(InventoryBalance.store_product_id == sp.id)
    )
    assert Decimal(balance.on_hand) == Decimal("6")
    entry_balance = await session.scalar(
        select(SupplierLedgerEntry.amount).where(
            SupplierLedgerEntry.supplier_id == UUID(supplier_id),
            SupplierLedgerEntry.entry_type == "purchase_return",
        )
    )
    assert Decimal(entry_balance) == Decimal("-22.00")

    # Over-return is rejected with 409.
    over = await client.post(
        f"/purchases/{purchase_id}/returns",
        json={"lines": [{"purchaseItemId": item_id, "quantity": "7"}]},
        headers=_headers(tenant),
    )
    assert over.status_code == 409

    # Stock beyond what remains cannot be returned either.
    exact = await client.post(
        f"/purchases/{purchase_id}/returns",
        json={"lines": [{"purchaseItemId": item_id, "quantity": "6"}]},
        headers=_headers(tenant),
    )
    assert exact.status_code == 201
    on_hand = await session.scalar(
        select(InventoryBalance.on_hand).where(InventoryBalance.store_product_id == sp.id)
    )
    assert Decimal(on_hand) == Decimal(0)


# --- reads / visibility -------------------------------------------------------------


async def test_reads_filter_and_hide_costs_from_non_managers(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    data, _ = await _create_draft(client, session, tenant)
    listing = await client.get(
        "/purchases?status=draft&supplierId="
        + data["supplierId"]
        + "&purchasedFrom="
        + data["purchasedAt"]
        + "&purchasedTo="
        + data["purchasedAt"],
        headers=_headers(tenant),
    )
    assert listing.status_code == 200
    page = listing.json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["totalAmount"] == "55.00"

    outside_range = await client.get(
        "/purchases?purchasedFrom=2099-01-01&purchasedTo=2099-12-31",
        headers=_headers(tenant),
    )
    assert outside_range.status_code == 200
    assert outside_range.json()["data"]["total"] == 0

    detail_owner = await client.get(f"/purchases/{data['id']}", headers=_headers(tenant))
    assert detail_owner.json()["data"]["items"][0]["unitCost"] == "5.50"

    cashier_headers = await _cashier_headers(session, tenant, make_user, make_membership)
    detail_cashier = await client.get(f"/purchases/{data['id']}", headers=cashier_headers)
    cashier_body = detail_cashier.json()["data"]
    assert cashier_body["totalAmount"] is None
    assert cashier_body["items"][0]["unitCost"] is None
    assert cashier_body["items"][0]["quantity"] == "10.0000"

    write_denied = await client.post(
        "/purchases",
        json={
            "supplierId": data["supplierId"],
            "items": [
                {
                    "storeProductId": data["items"][0]["storeProductId"],
                    "quantity": "1",
                    "unitCost": "1",
                    "batchNumber": "X",
                }
            ],
        },
        headers=cashier_headers,
    )
    assert write_denied.status_code == 403


async def _cashier_headers(
    session: Any,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> dict[str, str]:
    """A real CASHIER membership + auth session: roles resolve from the database."""
    from datetime import timedelta

    from app.models import Role, Session as SessionModel
    from app.security import generate_token, hash_token, utc_now
    from tests.conftest import access_token_for

    cashier = await make_user(phone="+8801777777777", display_name="Cashier")
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
    token = access_token_for(
        session_id=cashier_session.id,
        user_id=cashier.id,
        organization_id=tenant["organization"].id,
        role=Role.CASHIER,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}


async def test_cross_tenant_purchase_is_not_found(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_store: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    data, _ = await _create_draft(client, session, tenant)

    other_org: Organization = await make_organization(name="Rival", slug="rival")
    rival = await make_user(phone="+8801788888888", display_name="Rival Owner")
    rival_store = await make_store(other_org, code="RIVAL")
    await make_membership(other_org, rival, Role.OWNER, rival_store)
    from datetime import timedelta

    from app.models import Session as SessionModel
    from app.security import generate_token, hash_token, utc_now

    rival_session = SessionModel(
        user_id=rival.id,
        organization_id=other_org.id,
        store_id=rival_store.id,
        refresh_token_hash=hash_token(generate_token()),
        expires_at=utc_now() + timedelta(days=30),
    )
    session.add(rival_session)
    await session.commit()

    from tests.conftest import access_token_for

    rival_token = access_token_for(
        session_id=rival_session.id,
        user_id=rival.id,
        organization_id=other_org.id,
        role=Role.OWNER,
        store_id=rival_store.id,
    )
    response = await client.get(
        f"/purchases/{data['id']}",
        headers={"Authorization": f"Bearer {rival_token}"},
    )
    assert response.status_code == 404
