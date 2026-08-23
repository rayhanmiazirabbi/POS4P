from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.domains.inventory import InventoryBatch
from app.domains.payments import Payment, PaymentMethod, PaymentRefund, PaymentStatus
from app.domains.reports import DailyStoreMetric, StoreExpense
from app.domains.sales import Sale, SaleItem, SaleItemBatchAllocation, SaleReturn, SaleStatus
from app.models import Role, StoreProduct
from app.models import Session as SessionModel
from app.security import generate_token, hash_token, utc_now
from app.services.stores import business_date
from tests.conftest import access_token_for


def _owner_headers(tenant: dict[str, Any]) -> dict[str, str]:
    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=Role.OWNER,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}


async def _role_headers(
    session: Any,
    tenant: dict[str, Any],
    user: Any,
    role: Role,
) -> dict[str, str]:
    """Mint an access token backed by a real auth session row for ``user``."""
    auth_session = SessionModel(
        user_id=user.id,
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        refresh_token_hash=hash_token(generate_token()),
        expires_at=utc_now() + timedelta(days=30),
    )
    session.add(auth_session)
    await session.commit()
    token = access_token_for(
        session_id=auth_session.id,
        user_id=user.id,
        organization_id=tenant["organization"].id,
        role=role,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}


async def _add_sale(
    session: Any,
    tenant: dict[str, Any],
    *,
    total: Decimal,
    status: SaleStatus = SaleStatus.COMPLETED,
    created_at: datetime | None = None,
) -> Sale:
    sale = Sale(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        status=status,
        subtotal=total,
        total=total,
        idempotency_key=f"report-{uuid4().hex}",
    )
    if created_at is not None:
        sale.created_at = created_at
    session.add(sale)
    await session.flush()
    return sale


async def _add_payment(
    session: Any, tenant: dict[str, Any], sale: Sale, method: PaymentMethod, amount: Decimal
) -> Payment:
    payment = Payment(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        reference_type="sale",
        reference_id=sale.id,
        method=method,
        amount=amount,
        status=PaymentStatus.CAPTURED,
        idempotency_key=f"pay-{uuid4().hex}"[:64],
        created_at=sale.created_at or utc_now(),
    )
    session.add(payment)
    await session.flush()
    return payment


def _store_local(tenant: dict[str, Any], *, days: int = 0, hour: int = 0, minute: int = 0) -> datetime:
    """A naive-UTC instant for a store-local wall clock time, as SQLite stores it."""
    tz = ZoneInfo(tenant["store"].timezone)
    local_date = utc_now().astimezone(tz).date() + timedelta(days=days)
    local = datetime(local_date.year, local_date.month, local_date.day, hour, minute, tzinfo=tz)
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


# --- today metrics ---------------------------------------------------------------


async def test_today_metrics_counts_completed_sales_in_store_local_day_only(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    await _add_sale(session, tenant, total=Decimal("100.00"))
    # 23:30 yesterday store-local == 17:30 yesterday UTC; naive to match SQLite text comparison.
    await _add_sale(session, tenant, total=Decimal("500.00"), created_at=_store_local(tenant, days=-1, hour=23, minute=30))
    await _add_sale(session, tenant, total=Decimal("999.00"), status=SaleStatus.VOIDED)
    await session.commit()

    response = await client.get("/reports/today", headers=_owner_headers(tenant))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["salesTotal"] == "100.00"
    assert data["transactionCount"] == 1
    assert "profit" in data


async def test_early_morning_sale_belongs_to_the_store_local_day(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """02:00 store-local today is 20:00 *yesterday* in UTC.

    A window built on UTC dates drops it; the trading day the cashier worked must
    include it, because that is the till they will reconcile against.
    """
    await _add_sale(session, tenant, total=Decimal("140.00"), created_at=_store_local(tenant, hour=2))
    await session.commit()

    response = await client.get("/reports/today", headers=_owner_headers(tenant))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["salesTotal"] == "140.00"
    assert data["transactionCount"] == 1


async def test_business_day_cutoff_hour_shifts_the_window(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """A 6am cutoff puts a 03:00 sale on the previous trading day, per store settings."""
    tenant["store"].settings = {"business_day_cutoff_hour": 6}
    await _add_sale(session, tenant, total=Decimal("70.00"), created_at=_store_local(tenant, hour=3))
    await _add_sale(session, tenant, total=Decimal("11.00"), created_at=_store_local(tenant, hour=9))
    await session.commit()

    response = await client.get("/reports/today", headers=_owner_headers(tenant))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    tz = ZoneInfo(tenant["store"].timezone)
    local_now = utc_now().astimezone(tz)
    if local_now.hour < 6:
        # Before the cutoff the 03:00 sale is today's and the 09:00 one is tomorrow's.
        assert data["salesTotal"] == "70.00"
        assert data["businessDate"] == str((local_now - timedelta(hours=6)).date())
    else:
        assert data["salesTotal"] == "11.00"
        assert data["businessDate"] == str(local_now.date())


async def test_as_of_parameter_reports_a_past_trading_day(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """Explicit as-of is how a store closes yesterday's books after midnight."""
    await _add_sale(session, tenant, total=Decimal("42.00"), created_at=_store_local(tenant, days=-1, hour=12))
    await _add_sale(session, tenant, total=Decimal("8.00"), created_at=_store_local(tenant, hour=12))
    await session.commit()

    tz = ZoneInfo(tenant["store"].timezone)
    yesterday_noon = (utc_now().astimezone(tz) - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    response = await client.get(
        "/reports/today",
        params={"asOf": yesterday_noon.isoformat()},
        headers=_owner_headers(tenant),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["salesTotal"] == "42.00"
    assert data["transactionCount"] == 1
    assert data["businessDate"] == str(yesterday_noon.date())


async def test_payment_breakdown_sums_by_method(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    first = await _add_sale(session, tenant, total=Decimal("60.00"))
    second = await _add_sale(session, tenant, total=Decimal("40.00"))
    await _add_payment(session, tenant, first, PaymentMethod.CASH, Decimal("60.00"))
    await _add_payment(session, tenant, second, PaymentMethod.BKASH, Decimal("25.00"))
    await _add_payment(session, tenant, second, PaymentMethod.BKASH, Decimal("15.00"))
    await session.commit()

    response = await client.get("/reports/today", headers=_owner_headers(tenant))
    assert response.status_code == 200
    breakdown = response.json()["data"]["paymentBreakdown"]
    assert breakdown == {"cash": "60.00", "bkash": "40.00"}


async def test_due_is_reported_separately_from_collected_money(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """A ``due`` payment is a receivable, not cash in the drawer.

    Folding it into the breakdown total makes the till never reconcile, so it is
    surfaced under its own key and excluded from ``collectedTotal``.
    """
    sale = await _add_sale(session, tenant, total=Decimal("100.00"))
    await _add_payment(session, tenant, sale, PaymentMethod.CASH, Decimal("30.00"))
    await _add_payment(session, tenant, sale, PaymentMethod.DUE, Decimal("70.00"))
    await session.commit()

    response = await client.get("/reports/today", headers=_owner_headers(tenant))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["paymentBreakdown"] == {"cash": "30.00", "due": "70.00"}
    assert data["collectedTotal"] == "30.00"
    assert data["dueTotal"] == "70.00"
    assert data["salesTotal"] == "100.00"


async def test_refunds_reduce_net_sales_and_collected_money(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """Returns and payment refunds both have to land in the day's numbers."""
    sale = await _add_sale(session, tenant, total=Decimal("200.00"))
    payment = await _add_payment(session, tenant, sale, PaymentMethod.CASH, Decimal("200.00"))
    session.add(
        SaleReturn(
            organization_id=tenant["organization"].id,
            store_id=tenant["store"].id,
            sale_id=sale.id,
            reason="Damaged strip",
            total=Decimal("-50.00"),
            idempotency_key=f"ret-{uuid4().hex}",
            created_at=sale.created_at or utc_now(),
        )
    )
    session.add(
        PaymentRefund(
            organization_id=tenant["organization"].id,
            store_id=tenant["store"].id,
            payment_id=payment.id,
            amount=Decimal("50.00"),
            idempotency_key=f"rfd-{uuid4().hex}",
            created_at=sale.created_at or utc_now(),
        )
    )
    await session.commit()

    response = await client.get("/reports/today", headers=_owner_headers(tenant))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["salesTotal"] == "200.00"
    assert data["refundTotal"] == "50.00"
    assert data["netSalesTotal"] == "150.00"
    # 200.00 taken at the till minus a 50.00 refund paid back out of it.
    assert data["collectedTotal"] == "150.00"


async def test_voided_sale_leaves_the_breakdown_without_a_negative_line(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """A void refunds its tenders, and both sides have to leave the report together.

    The payment side of the breakdown only counts completed sales, so the refund
    side must be scoped the same way. Netting a void's refund off a payment that
    was never counted drives the line negative, and ``Money`` forbids that -- so the
    whole day's dashboard 500s rather than merely reading wrong.
    """
    sale = await _add_sale(session, tenant, total=Decimal("20.00"))
    payment = await _add_payment(session, tenant, sale, PaymentMethod.CASH, Decimal("20.00"))
    sale.status = SaleStatus.VOIDED
    session.add(
        PaymentRefund(
            organization_id=tenant["organization"].id,
            store_id=tenant["store"].id,
            payment_id=payment.id,
            amount=Decimal("20.00"),
            idempotency_key=f"rfd-{uuid4().hex}",
            created_at=sale.created_at or utc_now(),
        )
    )
    await session.commit()

    response = await client.get("/reports/today", headers=_owner_headers(tenant))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["paymentBreakdown"] == {}
    assert data["collectedTotal"] == "0.00"
    assert data["salesTotal"] == "0.00"


async def test_refund_of_an_earlier_days_sale_lands_in_todays_drawer(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """Cash handed back today is today's shortfall, whatever day the sale was.

    The payment figures deliberately key on the movement, not the sale: the cashier
    reconciles the drawer they worked, and this money physically left it today. It
    also means a line can go negative -- 100.00 taken in against 500.00 paid out is
    a real 400.00 hole, and a report that clamped it at zero would send someone
    looking for cash that was correctly refunded.
    """
    yesterday = _store_local(tenant, days=-1, hour=12)
    old = await _add_sale(session, tenant, total=Decimal("500.00"), created_at=yesterday)
    old_payment = await _add_payment(session, tenant, old, PaymentMethod.CASH, Decimal("500.00"))
    old_payment.created_at = yesterday
    today = await _add_sale(session, tenant, total=Decimal("100.00"))
    await _add_payment(session, tenant, today, PaymentMethod.CASH, Decimal("100.00"))
    session.add(
        PaymentRefund(
            organization_id=tenant["organization"].id,
            store_id=tenant["store"].id,
            payment_id=old_payment.id,
            amount=Decimal("500.00"),
            idempotency_key=f"rfd-{uuid4().hex}",
            created_at=utc_now(),
        )
    )
    await session.commit()

    response = await client.get("/reports/today", headers=_owner_headers(tenant))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    # Revenue still belongs to the day it was earned...
    assert data["salesTotal"] == "100.00"
    # ...but the drawer is down, and says so.
    assert data["paymentBreakdown"] == {"cash": "-400.00"}
    assert data["collectedTotal"] == "-400.00"


async def test_expenses_of_the_day_are_folded_into_metrics(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    await _add_sale(session, tenant, total=Decimal("300.00"))
    await session.commit()
    tz = ZoneInfo(tenant["store"].timezone)
    today_local = utc_now().astimezone(tz).date()
    for amount, expense_date in ((Decimal("120.00"), today_local), (Decimal("999.00"), today_local - timedelta(days=1))):
        created = await client.post(
            "/reports/expenses",
            json={"category": "Utilities", "amount": str(amount), "expenseDate": str(expense_date)},
            headers=_owner_headers(tenant),
        )
        assert created.status_code == 201, created.text

    response = await client.get("/reports/today", headers=_owner_headers(tenant))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["expenseTotal"] == "120.00"


async def _seed_profitable_sale(session: Any, tenant: dict[str, Any]) -> None:
    """A completed sale of 3 units at 10.00 against a batch costing 5.00."""
    from app.domains.products import PharmacyProduct

    product = PharmacyProduct(
        organization_id=tenant["organization"].id, name="Ibuprofen", unit="box", active=True
    )
    session.add(product)
    await session.flush()
    sp = StoreProduct(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        pharmacy_product_id=product.id,
        sku="PRF",
        sale_price=Decimal("10.00"),
        minimum_stock=Decimal(0),
        active=True,
    )
    session.add(sp)
    await session.flush()
    batch = InventoryBatch(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        store_product_id=sp.id,
        batch_number="COSTBATCH",
        unit_cost=Decimal("5.00"),
        received_at=utc_now(),
        active=True,
    )
    session.add(batch)
    await session.flush()
    sale = await _add_sale(session, tenant, total=Decimal("30.00"))
    item = SaleItem(
        sale_id=sale.id,
        store_product_id=sp.id,
        product_name="Ibuprofen",
        quantity=Decimal(3),
        unit_price=Decimal("10.00"),
        line_total=Decimal("30.00"),
    )
    session.add(item)
    await session.flush()
    session.add(
        SaleItemBatchAllocation(sale_item_id=item.id, batch_id=batch.id, quantity=Decimal(3))
    )
    await session.commit()


async def test_profit_visible_to_owner_and_absent_for_cashier(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    await _seed_profitable_sale(session, tenant)

    owner_response = await client.get("/reports/today", headers=_owner_headers(tenant))
    assert owner_response.status_code == 200
    assert owner_response.json()["data"]["profit"] == "15.00"

    cashier = await make_user(phone="+8801700000099", display_name="Cashier")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])
    cashier_headers = await _role_headers(session, tenant, cashier, Role.CASHIER)

    cashier_response = await client.get("/reports/today", headers=cashier_headers)
    assert cashier_response.status_code == 200
    assert cashier_response.json()["data"]["profit"] is None


async def test_profit_counts_a_split_line_once_per_allocation(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """One sale line drawn FEFO from two batches must not multiply its revenue.

    Ten units sell for 100.00; FEFO takes 6 from a batch costing 5.00 and 4 from a
    batch costing 6.00, so cost is 54.00 and profit is 46.00. Joining line_total
    through the allocations without weighting counts the full 100.00 twice.
    """
    from app.domains.products import PharmacyProduct

    product = PharmacyProduct(
        organization_id=tenant["organization"].id, name="Metformin", unit="box", active=True
    )
    session.add(product)
    await session.flush()
    sp = StoreProduct(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        pharmacy_product_id=product.id,
        sku="SPLIT",
        sale_price=Decimal("10.00"),
        minimum_stock=Decimal(0),
        active=True,
    )
    session.add(sp)
    await session.flush()
    batches = []
    for batch_number, unit_cost in (("CHEAP", Decimal("5.00")), ("DEAR", Decimal("6.00"))):
        batch = InventoryBatch(
            organization_id=tenant["organization"].id,
            store_id=tenant["store"].id,
            store_product_id=sp.id,
            batch_number=batch_number,
            unit_cost=unit_cost,
            received_at=utc_now(),
            active=True,
        )
        session.add(batch)
        batches.append(batch)
    await session.flush()

    sale = await _add_sale(session, tenant, total=Decimal("100.00"))
    item = SaleItem(
        sale_id=sale.id,
        store_product_id=sp.id,
        product_name="Metformin",
        quantity=Decimal(10),
        unit_price=Decimal("10.00"),
        line_total=Decimal("100.00"),
    )
    session.add(item)
    await session.flush()
    for batch, quantity in zip(batches, (Decimal(6), Decimal(4)), strict=True):
        session.add(
            SaleItemBatchAllocation(sale_item_id=item.id, batch_id=batch.id, quantity=quantity)
        )
    await session.commit()

    response = await client.get("/reports/today", headers=_owner_headers(tenant))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["profit"] == "46.00"


# --- expenses ----------------------------------------------------------------------


async def test_expense_create_requires_manager_role(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    body = {
        "category": "Rent",
        "amount": "1500.00",
        "expenseDate": str(date.today()),
        "note": "August rent",
    }
    cashier = await make_user(phone="+8801700000077", display_name="Cashier Two")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])

    denied = await client.post(
        "/reports/expenses",
        json=body,
        headers=await _role_headers(session, tenant, cashier, Role.CASHIER),
    )
    assert denied.status_code == 403

    created = await client.post("/reports/expenses", json=body, headers=_owner_headers(tenant))
    assert created.status_code == 201, created.text
    expense = created.json()["data"]
    assert expense["category"] == "Rent"
    assert expense["amount"] == "1500.00"

    listed = await client.get("/reports/expenses", headers=_owner_headers(tenant))
    assert listed.status_code == 200
    page = listed.json()["data"]
    assert page["total"] == 1
    assert [item["id"] for item in page["items"]] == [expense["id"]]


async def test_expenses_paginate_and_filter_by_date_range(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """``@pharmacy/api`` calls this through ``client.list``, so it must be a page."""
    today = date.today()
    for offset_days, amount in ((0, "10.00"), (1, "20.00"), (2, "30.00")):
        created = await client.post(
            "/reports/expenses",
            json={
                "category": "Misc",
                "amount": amount,
                "expenseDate": str(today - timedelta(days=offset_days)),
            },
            headers=_owner_headers(tenant),
        )
        assert created.status_code == 201, created.text

    first = await client.get("/reports/expenses?limit=2", headers=_owner_headers(tenant))
    assert first.status_code == 200, first.text
    page = first.json()["data"]
    assert page["total"] == 3
    assert [item["amount"] for item in page["items"]] == ["10.00", "20.00"]

    second = await client.get("/reports/expenses?limit=2&offset=2", headers=_owner_headers(tenant))
    assert [item["amount"] for item in second.json()["data"]["items"]] == ["30.00"]

    ranged = await client.get(
        "/reports/expenses",
        params={"from": str(today - timedelta(days=1)), "to": str(today - timedelta(days=1))},
        headers=_owner_headers(tenant),
    )
    assert ranged.status_code == 200, ranged.text
    assert [item["amount"] for item in ranged.json()["data"]["items"]] == ["20.00"]


async def test_expenses_exclude_other_tenants(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_store: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    other_org = await make_organization(name="Rival Two", slug="rival-two")
    other_store = await make_store(other_org, code="RIVAL2")
    session.add(
        StoreExpense(
            organization_id=other_org.id,
            store_id=other_store.id,
            category="Rent",
            amount=Decimal("5000.00"),
            expense_date=date.today(),
        )
    )
    await session.commit()

    listed = await client.get("/reports/expenses", headers=_owner_headers(tenant))
    assert listed.status_code == 200
    assert listed.json()["data"]["items"] == []


# --- daily metric projection -------------------------------------------------------


async def test_rebuild_daily_metric_matches_live_metrics(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """Integration check 6: the projection must agree with the live rollup.

    ``daily_store_metrics`` is a rebuildable read model, so recomputing it from the
    ledgers has to reproduce exactly what ``/reports/today`` computes on the fly.
    """
    sale = await _add_sale(session, tenant, total=Decimal("250.00"))
    await _add_payment(session, tenant, sale, PaymentMethod.CASH, Decimal("180.00"))
    await _add_payment(session, tenant, sale, PaymentMethod.DUE, Decimal("70.00"))
    session.add(
        SaleReturn(
            organization_id=tenant["organization"].id,
            store_id=tenant["store"].id,
            sale_id=sale.id,
            reason="Wrong strength",
            total=Decimal("-40.00"),
            idempotency_key=f"ret-{uuid4().hex}",
            created_at=sale.created_at or utc_now(),
        )
    )
    await session.commit()

    live = await client.get("/reports/today", headers=_owner_headers(tenant))
    assert live.status_code == 200, live.text
    metrics = live.json()["data"]

    rebuilt = await client.post("/reports/daily-metrics/rebuild", headers=_owner_headers(tenant))
    assert rebuilt.status_code == 200, rebuilt.text
    projection = rebuilt.json()["data"]

    assert projection["salesTotal"] == metrics["salesTotal"] == "250.00"
    assert projection["refundTotal"] == metrics["refundTotal"] == "40.00"
    assert projection["paymentBreakdown"] == metrics["paymentBreakdown"]
    # The till figure is derived from the breakdown in one shared place, so the
    # projection cannot drift from the live rollup on what the drawer should hold.
    assert projection["collectedTotal"] == metrics["collectedTotal"] == "180.00"
    assert projection["metricDate"] == metrics["businessDate"]

    stored = list(await session.scalars(select(DailyStoreMetric)))
    assert len(stored) == 1
    assert Decimal(stored[0].sales_total) == Decimal("250.00")
    assert stored[0].rebuilt_at is not None

    # Rebuilding again must be idempotent, not append a second row.
    again = await client.post("/reports/daily-metrics/rebuild", headers=_owner_headers(tenant))
    assert again.status_code == 200
    assert len(list(await session.scalars(select(DailyStoreMetric)))) == 1


async def test_rebuild_daily_metric_requires_manager_role(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    make_user: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    cashier = await make_user(phone="+8801700000055", display_name="Cashier Three")
    await make_membership(tenant["organization"], cashier, Role.CASHIER, tenant["store"])
    denied = await client.post(
        "/reports/daily-metrics/rebuild",
        headers=await _role_headers(session, tenant, cashier, Role.CASHIER),
    )
    assert denied.status_code == 403


# --- low stock / expiry --------------------------------------------------------------


async def _make_store_product(
    session: Any, tenant: dict[str, Any], *, sku: str, minimum_stock: Decimal = Decimal(0)
) -> StoreProduct:
    from app.domains.products import PharmacyProduct

    product = PharmacyProduct(
        organization_id=tenant["organization"].id, name=f"Product {sku}", unit="box", active=True
    )
    session.add(product)
    await session.flush()
    sp = StoreProduct(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        pharmacy_product_id=product.id,
        sku=sku,
        sale_price=Decimal("10.00"),
        minimum_stock=minimum_stock,
        active=True,
    )
    session.add(sp)
    await session.flush()
    return sp


async def _receive(
    session: Any,
    tenant: dict[str, Any],
    store_product: StoreProduct,
    quantity: str,
    **kwargs: Any,
) -> InventoryBatch:
    from app.context import RequestContext
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
        store_product.id,
        batch_number=kwargs.pop("batch_number", "B1"),
        expiry_date=kwargs.pop("expiry_date", date.today() + timedelta(days=365)),
        unit_cost=Decimal("5.00"),
        quantity=Decimal(quantity),
        idempotency_key=uuid4().hex * 2,
        **kwargs,
    )
    return batch


async def test_low_stock_endpoint_lists_below_minimum_products(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    low_sp = await _make_store_product(session, tenant, sku="LOW", minimum_stock=Decimal("20"))
    healthy = await _make_store_product(session, tenant, sku="OK", minimum_stock=Decimal("5"))
    await _receive(session, tenant, low_sp, "10")
    await _receive(session, tenant, healthy, "50")
    await session.commit()

    response = await client.get("/reports/low-stock", headers=_owner_headers(tenant))
    assert response.status_code == 200
    items = response.json()["data"]
    assert [item["sku"] for item in items] == ["LOW"]
    assert items[0]["available"] == "10.0000"
    assert items[0]["minimumStock"] == "20.0000"


async def test_expiry_endpoint_filters_by_within_days(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """Days-until-expiry is counted from the *store's* calendar, not the server's.

    Dated off ``business_date`` rather than ``date.today()`` on purpose: the two
    differ for a few hours every day on ``Asia/Dhaka``, and a test anchored on the
    machine clock passes or fails depending on the hour it is run.
    """
    store_today = business_date(tenant["store"])
    sp = await _make_store_product(session, tenant, sku="EXP")
    await _receive(session, tenant, sp, "5", batch_number="SOON", expiry_date=store_today + timedelta(days=10))
    await _receive(session, tenant, sp, "5", batch_number="FAR", expiry_date=store_today + timedelta(days=400))
    await session.commit()

    response = await client.get("/reports/expiry?withinDays=30", headers=_owner_headers(tenant))
    assert response.status_code == 200
    items = response.json()["data"]
    assert [item["batchNumber"] for item in items] == ["SOON"]
    assert items[0]["daysUntilExpiry"] == 10
    assert items[0]["productName"] == "Product EXP"


@pytest.mark.parametrize("utc_hour", [1, 12, 17, 20, 23])
async def test_expiry_countdown_is_stable_across_the_day_boundary(
    client: Any, session: Any, tenant: dict[str, Any], monkeypatch: Any, utc_hour: int
) -> None:
    """A 10-day batch reads as 10 days at every hour, not 9 for part of the day.

    Dhaka is UTC+6, so from 18:00 UTC the branch is already on tomorrow's calendar.
    Anchoring the countdown on UTC while the shop works off its own date made the
    figure jump by one for a quarter of every day -- and "expires in 0 days" on a
    batch that in fact expired yesterday is the kind of error this report exists to
    prevent. Freezing the clock is what makes the boundary reachable at all: the
    unparameterised test above only sees whichever side of it the suite happens to
    run on.
    """
    frozen = datetime(2026, 8, 22, utc_hour, 30, tzinfo=UTC)
    monkeypatch.setattr("app.services.stores.utc_now", lambda: frozen)

    store_today = business_date(tenant["store"])
    sp = await _make_store_product(session, tenant, sku=f"EXP{utc_hour}")
    await _receive(
        session, tenant, sp, "5", batch_number="SOON", expiry_date=store_today + timedelta(days=10)
    )
    await session.commit()

    response = await client.get("/reports/expiry?withinDays=30", headers=_owner_headers(tenant))
    assert response.status_code == 200, response.text
    assert [item["daysUntilExpiry"] for item in response.json()["data"]] == [10]


# --- cross-tenant isolation ------------------------------------------------------------


async def test_today_metrics_exclude_other_tenant_sales(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_store: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    other_org = await make_organization(name="Rival", slug="rival")
    rival = await make_user(phone="+8801799999998", display_name="Rival Owner")
    other_store = await make_store(other_org, code="RIVAL")
    await make_membership(other_org, rival, Role.OWNER, other_store)
    session.add(
        Sale(
            organization_id=other_org.id,
            store_id=other_store.id,
            status=SaleStatus.COMPLETED,
            subtotal=Decimal("777.00"),
            total=Decimal("777.00"),
            idempotency_key=f"rival-{uuid4().hex}",
        )
    )
    own = await _add_sale(session, tenant, total=Decimal("25.00"))
    await _add_payment(session, tenant, own, PaymentMethod.NAGAD, Decimal("25.00"))
    await session.commit()

    response = await client.get("/reports/today", headers=_owner_headers(tenant))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["salesTotal"] == "25.00"
    assert data["transactionCount"] == 1
    assert list(data["paymentBreakdown"]) == ["nagad"]

    rows = list(await session.scalars(select(Sale)))
    assert len(rows) == 2
