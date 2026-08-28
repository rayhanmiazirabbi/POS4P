from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.context import RequestContext
from app.domains.inventory import InventoryBalance, InventoryBatch, InventoryMovement
from app.domains.payments import Payment, PaymentMethod, PaymentRefund
from app.domains.sales import Sale, SaleReturn, SaleStatus
from app.models import AuditLog, Customer, OutboxEvent, Role, StoreProduct
from app.models import Session as SessionModel
from app.security import generate_token, hash_token, utc_now
from app.services.stores import business_date, local_now
from tests.conftest import access_token_for

SALE_KEY = "sale-key-000000000000001"


async def test_structured_adjustments_require_bound_pin_and_apply_in_order(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    from app.domains.customers import Customer
    from app.security import hash_secret

    tenant["owner"].pin_hash = hash_secret("2468")
    tenant["organization"].settings = {"require_pin_for_discounts": True}
    customer = Customer(
        organization_id=tenant["organization"].id,
        name="Advance Customer", due_balance=Decimal(0), advance_balance=Decimal(0),
        preferences={}, active=True,
    )
    session.add(customer)
    sp = await _make_store_product(session, tenant, price="10.00")
    await _receive(session, tenant, sp.id, quantity="20")
    items = [{
        "storeProductId": str(sp.id), "quantity": "10",
        "discount": {"mode": "percentage", "value": "10"},
    }]
    global_discount = {"mode": "percentage", "value": "10"}
    charges = [
        {"kind": "delivery", "amount": "5.00"},
        {"kind": "other", "amount": "2.00", "label": "Bag"},
    ]
    denied = await client.post(
        "/sales",
        json={"items": items, "globalDiscount": global_discount, "charges": charges, "payments": [{"method": "cash", "amount": "88.00", "receivedAmount": "88.00"}]},
        headers={**_headers(tenant), "Idempotency-Key": "structured-sale-denied-01"},
    )
    assert denied.status_code == 403
    approval = await client.post(
        "/sales/discount-approvals",
        json={"phone": tenant["owner"].phone, "pin": "2468", "items": items, "globalDiscount": global_discount, "charges": charges},
        headers=_headers(tenant),
    )
    assert approval.status_code == 201, approval.text
    response = await client.post(
        "/sales",
        json={
            "customerId": str(customer.id), "items": items, "globalDiscount": global_discount,
            "charges": charges, "advanceApplication": {"amount": "20.00", "reference": "ADV-1"},
            "discountApprovalToken": approval.json()["data"]["token"],
            "payments": [{"method": "cash", "amount": "68.00", "receivedAmount": "68.00"}],
        },
        headers={**_headers(tenant), "Idempotency-Key": "structured-sale-00000001"},
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["subtotal"] == "100.00"
    assert data["lineDiscount"] == "10.00"
    assert data["globalDiscount"] == "9.00"
    assert data["deliveryCharge"] == "5.00"
    assert data["otherFee"] == "2.00"
    assert data["total"] == "88.00"
    assert data["advanceApplied"] == "20.00"
    assert data["amountDueNow"] == "68.00"
    assert data["items"][0]["discountAmount"] == "10.00"


async def test_discount_approval_rejects_a_changed_cart(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    from app.security import hash_secret

    tenant["owner"].pin_hash = hash_secret("2468")
    tenant["organization"].settings = {"require_pin_for_discounts": True}
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id, quantity="10")
    approved_items = [{"storeProductId": str(sp.id), "quantity": "2", "discount": {"mode": "flat", "value": "1.00"}}]
    approval = await client.post(
        "/sales/discount-approvals",
        json={"phone": tenant["owner"].phone, "pin": "2468", "items": approved_items},
        headers=_headers(tenant),
    )
    token = approval.json()["data"]["token"]
    changed = [{"storeProductId": str(sp.id), "quantity": "2", "discount": {"mode": "flat", "value": "2.00"}}]
    response = await client.post(
        "/sales",
        json={"items": changed, "discountApprovalToken": token, "payments": [{"method": "cash", "amount": "18.00", "receivedAmount": "18.00"}]},
        headers={**_headers(tenant), "Idempotency-Key": "changed-cart-sale-00001"},
    )
    assert response.status_code == 403


async def test_legacy_discount_cannot_bypass_required_pin(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    from app.security import hash_secret

    tenant["owner"].pin_hash = hash_secret("2468")
    tenant["organization"].settings = {"require_pin_for_discounts": True}
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id, quantity="10")
    items = [{"storeProductId": str(sp.id), "quantity": "2"}]
    body = {
        "items": items,
        "discount": "1.00",
        "payments": [{"method": "cash", "amount": "19.00", "receivedAmount": "19.00"}],
    }
    denied = await client.post(
        "/sales",
        json=body,
        headers={**_headers(tenant), "Idempotency-Key": "legacy-pin-denied-00001"},
    )
    assert denied.status_code == 403

    approval = await client.post(
        "/sales/discount-approvals",
        json={"phone": tenant["owner"].phone, "pin": "2468", "items": items, "discount": "1.00"},
        headers=_headers(tenant),
    )
    assert approval.status_code == 201, approval.text
    accepted = await client.post(
        "/sales",
        json={**body, "discountApprovalToken": approval.json()["data"]["token"]},
        headers={**_headers(tenant), "Idempotency-Key": "legacy-pin-approved-0001"},
    )
    assert accepted.status_code == 201, accepted.text


def _headers(tenant: dict[str, Any], *, role: Role = Role.OWNER) -> dict[str, str]:
    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=role,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}


def _return_headers(tenant: dict[str, Any], key: str) -> dict[str, str]:
    """Headers for a return, keyed so each distinct return is its own request.

    ``/returns`` is idempotent, so two returns against one sale need two keys;
    reusing one is exactly what these tests must not accidentally do.
    """
    return {**_headers(tenant), "Idempotency-Key": f"return-key-{key}"}


async def _make_store_product(
    session: Any,
    tenant: dict[str, Any],
    *,
    sku: str = "SKU-S1",
    price: str = "10.00",
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
        sale_price=Decimal(price),
        minimum_stock=Decimal(0),
        active=True,
    )
    session.add(store_product)
    await session.flush()
    return store_product


async def _receive(
    session: Any,
    tenant: dict[str, Any],
    store_product_id: Any,
    *,
    quantity: str = "10",
    batch_number: str = "B1",
    expiry_days: int = 100,
) -> Any:
    from app.services.inventory import receive_batch

    context = RequestContext(
        organization_id=tenant["organization"].id,
        user_id=tenant["owner"].id,
        role=Role.OWNER,
        store_id=tenant["store"].id,
    )
    batch, _, _ = await receive_batch(
        session,
        context,
        store_product_id,
        batch_number=batch_number,
        expiry_date=date.today() + timedelta(days=expiry_days),
        unit_cost=Decimal("5.00"),
        quantity=Decimal(quantity),
        idempotency_key=f"recv-{batch_number}-{uuid4().hex[:8]}",
    )
    await session.commit()
    return batch


def _sale_body(sp_ids: list[Any], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "items": [{"storeProductId": str(pid), "quantity": "2"} for pid in sp_ids],
        "payments": [{"method": "cash", "amount": "20.00", "receivedAmount": "50.00"}],
    }
    body.update(overrides)
    return body


async def _create_sale(client: Any, tenant: dict[str, Any], body: dict[str, Any]) -> Any:
    response = await client.post(
        "/sales", json=body, headers={**_headers(tenant), "Idempotency-Key": SALE_KEY}
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


# --- guest happy path ---------------------------------------------------------


async def test_guest_sale_recomputes_totals_and_books_everything(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id, quantity="10")
    batch = await session.scalar(select(InventoryBatch))

    data = await _create_sale(
        client,
        tenant,
        # Client sends wrong totals on purpose; the server must recompute.
        {
            "items": [{"storeProductId": str(sp.id), "quantity": "3"}],
            "subtotal": "9999.00",
            "total": "1.00",
            "payments": [{"method": "cash", "amount": "30.00", "receivedAmount": "40.00"}],
        },
    )

    assert data["status"] == SaleStatus.COMPLETED.value
    assert data["receiptNumber"] is not None
    assert data["subtotal"] == "30.00"
    assert data["total"] == "30.00"
    assert data["items"][0]["unitPrice"] == "10.00"
    assert data["items"][0]["lineTotal"] == "30.00"

    movement = await session.scalar(
        select(InventoryMovement).where(InventoryMovement.movement_type == "sale")
    )
    assert movement is not None and Decimal(movement.quantity) == Decimal("-3")
    assert str(movement.reference_id) == data["id"]
    balance = await session.scalar(select(InventoryBalance))
    assert Decimal(balance.on_hand) == Decimal("7")

    outbox = list(await session.scalars(select(OutboxEvent)))
    assert any(e.event_type == "sale.created" for e in outbox)
    audits = list(await session.scalars(select(AuditLog)))
    assert any(a.action == "sale.created" for a in audits)

    # FEFO: the single batch was allocated to the sale item.
    from app.domains.sales import SaleItemBatchAllocation

    alloc = await session.scalar(select(SaleItemBatchAllocation))
    assert alloc is not None and str(alloc.batch_id) == str(batch.id)


# --- customer due ---------------------------------------------------------------


async def test_due_payment_bumps_customer_balance(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id)
    customer = Customer(
        organization_id=tenant["organization"].id, name="Karim", normalized_phone="+8801711111111"
    )
    session.add(customer)
    await session.commit()

    data = await _create_sale(
        client,
        tenant,
        {
            "customerId": str(customer.id),
            "items": [{"storeProductId": str(sp.id), "quantity": "1"}],
            "payments": [
                {"method": "cash", "amount": "5.00", "receivedAmount": "5.00"},
                {"method": "due", "amount": "5.00"},
            ],
        },
    )
    methods = sorted(p["method"] for p in data["payments"])
    assert methods == ["cash", "due"]

    # The API ran on its own session; drop the cached instance before re-reading.
    session.expunge_all()
    refreshed = await session.get(Customer, customer.id)
    assert Decimal(refreshed.due_balance) == Decimal("5.00")


async def test_split_tender_must_sum_to_total(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id)

    bad = await client.post(
        "/sales",
        json={
            "items": [{"storeProductId": str(sp.id), "quantity": "1"}],
            "payments": [
                {"method": "cash", "amount": "5.00", "receivedAmount": "6.00"},
                {"method": "bkash", "amount": "4.00"},
            ],
        },
        headers={**_headers(tenant), "Idempotency-Key": SALE_KEY},
    )
    assert bad.status_code == 409

    data = await _create_sale(
        client,
        tenant,
        {
            "items": [{"storeProductId": str(sp.id), "quantity": "1"}],
            "payments": [
                {"method": "cash", "amount": "6.00", "receivedAmount": "6.00"},
                {"method": "bkash", "amount": "4.00"},
            ],
        },
    )
    assert data["total"] == "10.00"
    assert len(data["payments"]) == 2


async def test_cash_received_below_amount_is_rejected(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id)
    response = await client.post(
        "/sales",
        json=_sale_body([sp.id], payments=[{"method": "cash", "amount": "20.00", "receivedAmount": "10.00"}]),
        headers={**_headers(tenant), "Idempotency-Key": SALE_KEY},
    )
    assert response.status_code == 409


# --- failure atomicity ------------------------------------------------------------


async def test_insufficient_stock_conflicts_without_partial_rows(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id, quantity="1")

    response = await client.post(
        "/sales",
        json=_sale_body([sp.id]),
        headers={**_headers(tenant), "Idempotency-Key": SALE_KEY},
    )
    assert response.status_code == 409

    assert int(await session.scalar(select(func.count()).select_from(Sale))) == 0
    movements = list(await session.scalars(select(InventoryMovement)))
    assert len(movements) == 1  # only the receipt remains
    balance = await session.scalar(select(InventoryBalance))
    assert Decimal(balance.on_hand) == Decimal("1")


# --- idempotency --------------------------------------------------------------------


async def test_replay_returns_same_sale_and_different_body_conflicts(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id)
    body = _sale_body([sp.id])

    first = await client.post(
        "/sales", json=body, headers={**_headers(tenant), "Idempotency-Key": SALE_KEY}
    )
    assert first.status_code == 201
    sale_id = first.json()["data"]["id"]

    replay = await client.post(
        "/sales", json=body, headers={**_headers(tenant), "Idempotency-Key": SALE_KEY}
    )
    assert replay.status_code == 201
    assert replay.json()["data"]["id"] == sale_id

    different = await client.post(
        "/sales",
        json=_sale_body([sp.id], discount="1.00"),
        headers={**_headers(tenant), "Idempotency-Key": SALE_KEY},
    )
    assert different.status_code == 409

    count = await session.scalar(
        select(func.count()).select_from(InventoryMovement).where(InventoryMovement.movement_type == "sale")
    )
    assert int(count or 0) == 1


# --- returns ------------------------------------------------------------------------


async def test_return_restores_stock_and_limits_quantity(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id, quantity="10")
    data = await _create_sale(client, tenant, _sale_body([sp.id]))
    item_id = data["items"][0]["id"]
    sale_id = data["id"]

    returned = await client.post(
        f"/sales/{sale_id}/returns",
        json={"reason": "Damaged", "lines": [{"saleItemId": item_id, "quantity": "1"}]},
        headers=_return_headers(tenant, "damaged"),
    )
    assert returned.status_code == 201, returned.text
    assert returned.json()["data"]["total"] == "-10.00"  # 1 unit at 10.00

    balance = await session.scalar(select(InventoryBalance))
    assert Decimal(balance.on_hand) == Decimal("9")  # 10 - 2 sold + 1 returned

    over = await client.post(
        f"/sales/{sale_id}/returns",
        json={"reason": "Again", "lines": [{"saleItemId": item_id, "quantity": "2"}]},
        headers=_return_headers(tenant, "again"),
    )
    assert over.status_code == 409


async def test_a_product_on_two_lines_can_be_returned_in_full(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """Everything the customer bought has to be returnable, however it was scanned.

    Nothing merges duplicate lines, so scanning the same box twice writes two
    ``sale_items`` rows. The cap used to be one line's quantity while the running
    total was accumulated per product, which meant the second line was measured
    against a budget the first had already spent: a customer returning all five
    boxes they bought was refused at the till. The cap is the sale's total for
    that product, which is what "you cannot return more than you bought" means.
    """
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id, quantity="20")
    data = await _create_sale(
        client,
        tenant,
        {
            "items": [
                {"storeProductId": str(sp.id), "quantity": "2"},
                {"storeProductId": str(sp.id), "quantity": "3"},
            ],
            "payments": [{"method": "cash", "amount": "50.00", "receivedAmount": "50.00"}],
        },
    )
    first, second = data["items"][0]["id"], data["items"][1]["id"]

    full = await client.post(
        f"/sales/{data['id']}/returns",
        json={
            "reason": "Customer changed mind",
            "lines": [
                {"saleItemId": first, "quantity": "2"},
                {"saleItemId": second, "quantity": "3"},
            ],
        },
        headers=_return_headers(tenant, "changed-mind"),
    )
    assert full.status_code == 201, full.text
    assert full.json()["data"]["total"] == "-50.00"  # 5 units at 10.00

    session.expunge_all()
    balance = await session.scalar(select(InventoryBalance))
    assert Decimal(balance.on_hand) == Decimal("20")  # 20 - 5 sold + 5 returned

    # The looser cap is still a cap: with all five back, a sixth is not owed.
    over = await client.post(
        f"/sales/{data['id']}/returns",
        json={"reason": "One more", "lines": [{"saleItemId": first, "quantity": "1"}]},
        headers=_return_headers(tenant, "one-more"),
    )
    assert over.status_code == 409, over.text


async def test_a_returned_sale_cannot_then_be_voided(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """Void and return each remove the money a different way; both is neither.

    A return subtracts a refund from a sale that stays in revenue; a void drops
    the sale out of revenue entirely. Allowing both left the day showing a refund
    against sales that no longer existed -- net revenue negative by the returned
    amount -- and restored the returned unit to the shelf a second time, so
    ``on_hand`` read higher than the stock had ever been. Both figures are
    asserted here because the stock error alone is silent: the refund happened to
    come out right only because ``refund_payments`` caps each tender by what it
    has left to give.
    """
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id, quantity="10")
    data = await _create_sale(
        client,
        tenant,
        {
            "items": [{"storeProductId": str(sp.id), "quantity": "2"}],
            "payments": [{"method": "cash", "amount": "20.00", "receivedAmount": "20.00"}],
        },
    )
    returned = await client.post(
        f"/sales/{data['id']}/returns",
        json={"reason": "One damaged", "lines": [{"saleItemId": data["items"][0]["id"], "quantity": "1"}]},
        headers=_return_headers(tenant, "one-damaged"),
    )
    assert returned.status_code == 201, returned.text

    voided = await client.post(
        f"/sales/{data['id']}/void", json={"reason": "Rung up in error"}, headers=_headers(tenant)
    )
    assert voided.status_code == 409, voided.text

    session.expunge_all()
    balance = await session.scalar(select(InventoryBalance))
    assert Decimal(balance.on_hand) == Decimal("9")  # 10 - 2 sold + 1 returned, not 11
    refunds = list(await session.scalars(select(PaymentRefund)))
    assert sum(Decimal(refund.amount) for refund in refunds) == Decimal("10.00")

    report = await client.get("/reports/today", headers=_headers(tenant))
    metrics = report.json()["data"]
    assert metrics["salesTotal"] == "20.00" and metrics["refundTotal"] == "10.00"

    # Returning the rest is the supported remedy, and it settles the whole sale.
    rest = await client.post(
        f"/sales/{data['id']}/returns",
        json={"reason": "Rung up in error", "lines": [{"saleItemId": data["items"][0]["id"], "quantity": "1"}]},
        headers=_return_headers(tenant, "the-rest"),
    )
    assert rest.status_code == 201, rest.text
    session.expunge_all()
    balance = await session.scalar(select(InventoryBalance))
    assert Decimal(balance.on_hand) == Decimal("10")
    refunds = list(await session.scalars(select(PaymentRefund)))
    assert sum(Decimal(refund.amount) for refund in refunds) == Decimal("20.00")


async def test_return_on_due_sale_reduces_customer_due(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id)
    customer = Customer(
        organization_id=tenant["organization"].id, name="Rahim", normalized_phone="+8801722222222"
    )
    session.add(customer)
    await session.commit()

    data = await _create_sale(
        client,
        tenant,
        {
            "customerId": str(customer.id),
            "items": [{"storeProductId": str(sp.id), "quantity": "2"}],
            "payments": [{"method": "due", "amount": "20.00"}],
        },
    )
    session.expunge_all()
    refreshed = await session.get(Customer, customer.id)
    assert Decimal(refreshed.due_balance) == Decimal("20.00")

    await client.post(
        f"/sales/{data['id']}/returns",
        json={
            "reason": "Wrong item",
            "lines": [{"saleItemId": data["items"][0]["id"], "quantity": "2"}],
        },
        headers=_return_headers(tenant, "wrong-item"),
    )
    session.expunge_all()
    refreshed = await session.get(Customer, customer.id)
    assert Decimal(refreshed.due_balance) == Decimal("0.00")


async def test_return_records_a_refund_against_the_tendered_payment(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """A refund has to land in the payment ledger, not only in the balance.

    ``due_balance`` is a projection over the ``due`` tenders and the refunds
    against them (cross-cutting rule 2). Reducing the balance without writing
    the refund row makes the projection unrebuildable, and leaves the day's
    payment breakdown claiming money the drawer never kept.
    """
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id)
    customer = Customer(
        organization_id=tenant["organization"].id, name="Mixed", normalized_phone="+8801733333333"
    )
    session.add(customer)
    await session.commit()

    # Half on credit, half in cash, so refund ordering is observable.
    data = await _create_sale(
        client,
        tenant,
        {
            "customerId": str(customer.id),
            "items": [{"storeProductId": str(sp.id), "quantity": "2"}],
            "payments": [
                {"method": "due", "amount": "12.00"},
                {"method": "cash", "amount": "8.00", "receivedAmount": "8.00"},
            ],
        },
    )

    returned = await client.post(
        f"/sales/{data['id']}/returns",
        json={
            "reason": "Wrong item",
            "lines": [{"saleItemId": data["items"][0]["id"], "quantity": "1"}],
        },
        headers=_return_headers(tenant, "wrong-item"),
    )
    assert returned.status_code == 201, returned.text

    refunds = list(await session.scalars(select(PaymentRefund)))
    assert refunds != [], "a return must record its refund in the payment ledger"
    assert sum(Decimal(refund.amount) for refund in refunds) == Decimal("10.00")

    # Credit is cancelled before cash leaves the drawer.
    by_method = {}
    for refund in refunds:
        payment = await session.get(Payment, refund.payment_id)
        by_method[payment.method] = by_method.get(payment.method, Decimal(0)) + Decimal(refund.amount)
    assert by_method[PaymentMethod.DUE] == Decimal("10.00")
    assert PaymentMethod.CASH not in by_method

    session.expunge_all()
    refreshed = await session.get(Customer, customer.id)
    assert Decimal(refreshed.due_balance) == Decimal("2.00")


async def test_a_return_refunds_what_the_discounted_sale_actually_took(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """A discounted sale must not refund more than it collected.

    ``sales.discount`` is a cart-level figure and ``sale_items`` has no discount
    column, so the refund used to be priced off ``unit_price`` -- the pre-discount
    price. A cart of 100.00 sold for 80.00 refunded the full 100.00, so the shop
    paid 20.00 to accept the return, and ``refund_payments`` capping each tender
    was the only thing keeping the loss down to the amount tendered.
    """
    tenant["organization"].settings = {"require_pin_for_discounts": False}
    await session.commit()
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id, quantity="10")
    data = await _create_sale(
        client,
        tenant,
        {
            "items": [{"storeProductId": str(sp.id), "quantity": "10"}],
            "discount": "20.00",
            "payments": [{"method": "cash", "amount": "80.00", "receivedAmount": "80.00"}],
        },
    )
    assert data["total"] == "80.00"

    returned = await client.post(
        f"/sales/{data['id']}/returns",
        json={"reason": "All back", "lines": [{"saleItemId": data["items"][0]["id"], "quantity": "10"}]},
        headers=_return_headers(tenant, "discounted-full"),
    )
    assert returned.status_code == 201, returned.text
    # 80.00 was taken, so 80.00 goes back -- not the 100.00 the line was rung at.
    assert returned.json()["data"]["total"] == "-80.00"
    refunds = list(await session.scalars(select(PaymentRefund)))
    assert sum(Decimal(refund.amount) for refund in refunds) == Decimal("80.00")


async def test_partial_returns_of_an_uneven_discount_add_up_exactly(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """Three partial returns of a net value that does not divide by three.

    30.00 less 10.00 is 20.00 over 3 units: 6.666... each. Rounding each return
    independently pays 6.67 three times and hands over a cent the till never took;
    truncating pays 6.66 three times and keeps one. Each refund is the difference
    between the value of everything returned so far and the value of everything
    returned before it, so the three add up to exactly 20.00.
    """
    tenant["organization"].settings = {"require_pin_for_discounts": False}
    await session.commit()
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id, quantity="10")
    data = await _create_sale(
        client,
        tenant,
        {
            "items": [{"storeProductId": str(sp.id), "quantity": "3"}],
            "discount": "10.00",
            "payments": [{"method": "cash", "amount": "20.00", "receivedAmount": "20.00"}],
        },
    )
    item_id = data["items"][0]["id"]

    for index in range(3):
        one = await client.post(
            f"/sales/{data['id']}/returns",
            json={"reason": "One back", "lines": [{"saleItemId": item_id, "quantity": "1"}]},
            headers=_return_headers(tenant, f"third-{index}"),
        )
        assert one.status_code == 201, one.text

    refunds = list(await session.scalars(select(PaymentRefund)))
    assert sum(Decimal(refund.amount) for refund in refunds) == Decimal("20.00")
    returns = list(await session.scalars(select(SaleReturn)))
    assert sum(Decimal(row.total) for row in returns) == Decimal("-20.00")


async def test_a_return_is_accepted_when_the_sale_emptied_the_shelf(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """The last box sold must still be returnable.

    Restoring stock used to run the FEFO *allocator*, which only considers batches
    with stock on the shelf -- so a sale that cleared the shelf left nothing to
    allocate against and the return was refused outright, with no way to pay the
    customer back at all. A return is a credit against movements that already
    exist; it needs no availability check.
    """
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id, quantity="2")
    data = await _create_sale(
        client,
        tenant,
        {
            "items": [{"storeProductId": str(sp.id), "quantity": "2"}],
            "payments": [{"method": "cash", "amount": "20.00", "receivedAmount": "20.00"}],
        },
    )
    session.expunge_all()
    balance = await session.scalar(select(InventoryBalance))
    assert Decimal(balance.on_hand) == Decimal("0")

    returned = await client.post(
        f"/sales/{data['id']}/returns",
        json={"reason": "Both back", "lines": [{"saleItemId": data["items"][0]["id"], "quantity": "2"}]},
        headers=_return_headers(tenant, "empty-shelf"),
    )
    assert returned.status_code == 201, returned.text
    session.expunge_all()
    balance = await session.scalar(select(InventoryBalance))
    assert Decimal(balance.on_hand) == Decimal("2")


async def test_returned_units_go_back_to_the_batch_they_were_sold_from(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """Stock returns to its own batch, not to whichever batch FEFO picks today.

    Re-running the allocator could put the units in a different batch than they
    left, and reports value returned stock at the receiving batch's ``unit_cost``
    -- so a unit sold from the older batch and restored into the newer one
    silently mis-stated gross profit and left both batch counts wrong. The sale's
    own ``sale_item_batch_allocations`` rows say where each unit came from.
    """
    sp = await _make_store_product(session, tenant)
    first = await _receive(session, tenant, sp.id, quantity="3", batch_number="B-NEAR", expiry_days=30)
    second = await _receive(session, tenant, sp.id, quantity="5", batch_number="B-FAR", expiry_days=300)

    # 4 units: FEFO empties B-NEAR (3) then takes 1 from B-FAR.
    data = await _create_sale(
        client,
        tenant,
        {
            "items": [{"storeProductId": str(sp.id), "quantity": "4"}],
            "payments": [{"method": "cash", "amount": "40.00", "receivedAmount": "40.00"}],
        },
    )
    returned = await client.post(
        f"/sales/{data['id']}/returns",
        json={"reason": "All four", "lines": [{"saleItemId": data["items"][0]["id"], "quantity": "4"}]},
        headers=_return_headers(tenant, "two-batches"),
    )
    assert returned.status_code == 201, returned.text

    restored: dict[Any, Decimal] = {}
    for movement in await session.scalars(
        select(InventoryMovement).where(InventoryMovement.reference_type == "sale_return")
    ):
        restored[movement.batch_id] = restored.get(movement.batch_id, Decimal(0)) + Decimal(movement.quantity)
    assert restored == {first.id: Decimal("3"), second.id: Decimal("1")}


async def test_a_double_tapped_return_refunds_once(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """The same ``Idempotency-Key`` twice is one return, not two.

    Returns had no key at all, so a double-tapped Refund button -- or the same
    offline event replayed by the sync feed -- paid the customer twice and put the
    stock back twice, and the second call was indistinguishable from a legitimate
    second return.
    """
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id, quantity="10")
    data = await _create_sale(client, tenant, _sale_body([sp.id]))
    body = {
        "reason": "Double tap",
        "lines": [{"saleItemId": data["items"][0]["id"], "quantity": "1"}],
    }
    headers = _return_headers(tenant, "double-tap")

    first = await client.post(f"/sales/{data['id']}/returns", json=body, headers=headers)
    second = await client.post(f"/sales/{data['id']}/returns", json=body, headers=headers)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    # The replay serves the stored body, so the caller cannot tell -- and must not
    # be able to tell -- that its retry did nothing.
    assert second.json()["data"] == first.json()["data"]

    returns = list(await session.scalars(select(SaleReturn)))
    assert len(returns) == 1
    refunds = list(await session.scalars(select(PaymentRefund)))
    assert sum(Decimal(refund.amount) for refund in refunds) == Decimal("10.00")
    session.expunge_all()
    balance = await session.scalar(select(InventoryBalance))
    assert Decimal(balance.on_hand) == Decimal("9")  # 10 - 2 sold + 1 returned, once


async def test_a_return_is_published_to_the_outbox(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """A return moves revenue and stock, so it has to reach the same consumers.

    ``sale.created`` and ``sale.voided`` both publish; the return did not, which
    made the commonest correction the one that never left the database and let a
    branch's figures drift by every refund it took.
    """
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id, quantity="10")
    data = await _create_sale(client, tenant, _sale_body([sp.id]))
    returned = await client.post(
        f"/sales/{data['id']}/returns",
        json={"reason": "Damaged", "lines": [{"saleItemId": data["items"][0]["id"], "quantity": "1"}]},
        headers=_return_headers(tenant, "outbox"),
    )
    assert returned.status_code == 201, returned.text

    events = {
        event.event_type: event
        for event in await session.scalars(select(OutboxEvent))
    }
    assert "sale.returned" in events, "a return must be published like a sale or a void"
    payload = events["sale.returned"].payload
    # ``store_id`` travels with the event for the same reason it does on a void: a
    # correction that cannot be routed to a branch cannot correct that branch.
    assert payload["sale_id"] == data["id"]
    assert payload["store_id"] == str(tenant["store"].id)
    assert payload["total"] == "-10.00"


async def test_void_on_due_sale_clears_the_debt(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """Voiding a credit sale must not leave the customer owing for it.

    The sale drops out of every revenue report the moment it is voided, so a
    surviving ``due_balance`` is a debt with nothing behind it -- the customer
    is asked to pay for a transaction the books say never happened.
    """
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id)
    customer = Customer(
        organization_id=tenant["organization"].id, name="Voided", normalized_phone="+8801744444444"
    )
    session.add(customer)
    await session.commit()

    data = await _create_sale(
        client,
        tenant,
        {
            "customerId": str(customer.id),
            "items": [{"storeProductId": str(sp.id), "quantity": "2"}],
            "payments": [{"method": "due", "amount": "20.00"}],
        },
    )
    session.expunge_all()
    assert Decimal((await session.get(Customer, customer.id)).due_balance) == Decimal("20.00")

    voided = await client.post(
        f"/sales/{data['id']}/void", json={"reason": "Rang up twice"}, headers=_headers(tenant)
    )
    assert voided.status_code == 200, voided.text

    session.expunge_all()
    refreshed = await session.get(Customer, customer.id)
    assert Decimal(refreshed.due_balance) == Decimal("0.00")
    refunds = list(await session.scalars(select(PaymentRefund)))
    assert sum(Decimal(refund.amount) for refund in refunds) == Decimal("20.00")


# --- void -----------------------------------------------------------------------------


async def _cashier_headers(session: Any, tenant: dict[str, Any], make_user: Callable[..., Any], make_membership: Callable[..., Any]) -> dict[str, str]:
    cashier = await make_user(phone="+8801777777777", display_name="Cashier")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])
    auth_session = SessionModel(
        user_id=cashier.id,
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        refresh_token_hash=hash_token(generate_token()),
        expires_at=utc_now() + timedelta(days=30),
    )
    session.add(auth_session)
    await session.commit()
    token = access_token_for(
        session_id=auth_session.id,
        user_id=cashier.id,
        organization_id=tenant["organization"].id,
        role=Role.CASHIER,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}


async def test_same_day_void_follows_the_branch_trading_day_not_utc(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """"Same day" is the shop's trading day, and the two are not the same date.

    The store is on ``Asia/Dhaka`` (UTC+6), so the local day and the UTC day
    disagree for six hours out of every twenty-four -- and the old check compared
    UTC dates, which broke in both directions at once:

    * A sale rung up at 01:30 local carries *yesterday's* UTC date, so it became
      unvoidable partway through the very shift that made it. The cashier who
      mis-scanned it at 01:30 could no longer undo it at 09:00.
    * A sale from 23:00 last night shares *today's* UTC date until 06:00 local, so
      it stayed voidable after the day was closed and counted -- letting yesterday's
      revenue change behind the till once the cash had already been reconciled.

    ``business_date`` is what reports cut on, so pinning the void window to it makes
    the voidable sales exactly the ones today's report still counts.
    """
    store = tenant["store"]
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id, quantity="10")
    data = await _create_sale(
        client,
        tenant,
        {
            "items": [{"storeProductId": str(sp.id), "quantity": "2"}],
            "payments": [{"method": "cash", "amount": "20.00", "receivedAmount": "20.00"}],
        },
    )
    sale_id = UUID(data["id"])

    # An early-morning moment at the branch: same trading day as now, but a
    # different UTC date. Which side of the boundary we anchor on depends on the
    # wall clock so the straddle holds at every hour the suite might run:
    # before 06:00 local the whole trading day shares "now"'s UTC date, so the
    # sale is pinned to 07:30 local (still today's trading day, already
    # tomorrow's-side UTC); otherwise the classic 01:30 local keeps its
    # previous-day UTC stamp.
    now_local = local_now(store)
    if now_local.hour < 6:
        early_local = now_local.replace(hour=7, minute=30, second=0, microsecond=0)
    else:
        early_local = now_local.replace(hour=1, minute=30, second=0, microsecond=0)
    assert business_date(store, moment=early_local) == business_date(store)
    assert early_local.astimezone(UTC).date() != utc_now().date(), (
        "the fixture store must straddle the UTC boundary for this test to mean anything"
    )
    sale = await session.get(Sale, sale_id)
    sale.created_at = early_local.astimezone(UTC)
    await session.commit()

    voided = await client.post(
        f"/sales/{data['id']}/void", json={"reason": "Mis-scanned"}, headers=_headers(tenant)
    )
    assert voided.status_code == 200, voided.text

    # And the converse: yesterday evening at the branch is a closed day, even while
    # it still shares today's UTC date.
    other = await _make_store_product(session, tenant, sku="SKU-S2")
    await _receive(session, tenant, other.id, quantity="10", batch_number="B2")
    stale_response = await client.post(
        "/sales",
        json={
            "items": [{"storeProductId": str(other.id), "quantity": "2"}],
            "payments": [{"method": "cash", "amount": "20.00", "receivedAmount": "20.00"}],
        },
        headers={**_headers(tenant), "Idempotency-Key": "sale-key-000000000000002"},
    )
    assert stale_response.status_code == 201, stale_response.text
    stale = stale_response.json()["data"]
    yesterday_evening = (now_local - timedelta(days=1)).replace(
        hour=22, minute=30, second=0, microsecond=0
    )  # 22:30 the previous local day
    assert business_date(store, moment=yesterday_evening) != business_date(store)
    stale_sale = await session.get(Sale, UUID(stale["id"]))
    stale_sale.created_at = yesterday_evening.astimezone(UTC)
    await session.commit()

    refused = await client.post(
        f"/sales/{stale['id']}/void", json={"reason": "Too late"}, headers=_headers(tenant)
    )
    assert refused.status_code == 409, refused.text


async def test_void_restores_stock_but_cashier_is_forbidden(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id, quantity="10")
    data = await _create_sale(client, tenant, _sale_body([sp.id]))
    sale_id = data["id"]

    denied = await client.post(
        f"/sales/{sale_id}/void",
        json={"reason": "Mistake"},
        headers=await _cashier_headers(session, tenant, make_user, make_membership),
    )
    assert denied.status_code == 403

    voided = await client.post(
        f"/sales/{sale_id}/void", json={"reason": "Mistake"}, headers=_headers(tenant)
    )
    assert voided.status_code == 200
    assert voided.json()["data"]["status"] == SaleStatus.VOIDED.value
    assert voided.json()["data"]["voidReason"] == "Mistake"

    balance = await session.scalar(select(InventoryBalance))
    assert Decimal(balance.on_hand) == Decimal("10")  # fully restored

    # The void event must be routable to the branch it reverses. ``sale.created``
    # carries ``store_id`` and moved a branch figure; a void missing it cannot
    # correct that figure, because the row itself is only scoped to the tenant.
    event = await session.scalar(
        select(OutboxEvent).where(OutboxEvent.event_type == "sale.voided")
    )
    assert event is not None
    assert event.payload["store_id"] == str(tenant["store"].id)
    assert event.payload["total"] == "20.00"

    again = await client.post(
        f"/sales/{sale_id}/void", json={"reason": "Twice"}, headers=_headers(tenant)
    )
    assert again.status_code == 409


# --- reads / isolation ---------------------------------------------------------------


async def test_list_filter_and_cross_tenant_is_404(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_store: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    sp = await _make_store_product(session, tenant)
    await _receive(session, tenant, sp.id)
    customer = Customer(
        organization_id=tenant["organization"].id, name="Karim", normalized_phone="+8801733333333"
    )
    session.add(customer)
    await session.commit()
    data = await _create_sale(
        client,
        tenant,
        {"customerId": str(customer.id), **_sale_body([sp.id])},
    )

    listing = await client.get(
        f"/sales?customerId={customer.id}&status=completed", headers=_headers(tenant)
    )
    assert listing.status_code == 200
    page = listing.json()["data"]
    assert page["total"] == 1 and page["items"][0]["id"] == data["id"]

    detail = await client.get(f"/sales/{data['id']}", headers=_headers(tenant))
    assert detail.status_code == 200

    other_org = await make_organization(name="Rival", slug="rival-sales")
    rival = await make_user(phone="+8801788888888", display_name="Rival Owner")
    rival_store = await make_store(other_org, code="RIVAL")
    await make_membership(other_org, rival, Role.OWNER, rival_store)
    auth_session = SessionModel(
        user_id=rival.id,
        organization_id=other_org.id,
        store_id=rival_store.id,
        refresh_token_hash=hash_token(generate_token()),
        expires_at=utc_now() + timedelta(days=30),
    )
    session.add(auth_session)
    await session.commit()
    rival_headers = {
        "Authorization": f"Bearer {access_token_for(session_id=auth_session.id, user_id=rival.id, organization_id=other_org.id, role=Role.OWNER, store_id=rival_store.id)}"
    }

    assert (await client.get(f"/sales/{data['id']}", headers=rival_headers)).status_code == 404


async def test_missing_idempotency_key_header_is_422(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    sp = await _make_store_product(session, tenant)
    response = await client.post("/sales", json=_sale_body([sp.id]), headers=_headers(tenant))
    assert response.status_code == 422
