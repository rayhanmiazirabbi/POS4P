from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.context import RequestContext
from app.domains.customers import Customer
from app.domains.products import PharmacyProduct
from app.domains.sales import Sale, SaleItem, SaleStatus
from app.models import Role, StoreProduct
from tests._phase4_helpers import role_headers


def _context(tenant: dict[str, Any], *, role: Role = Role.OWNER) -> RequestContext:
    return RequestContext(
        organization_id=tenant["organization"].id,
        user_id=tenant["owner"].id,
        role=role,
        store_id=tenant["store"].id,
    )


async def _add_sale(
    session: Any,
    tenant: dict[str, Any],
    *,
    total: Decimal,
    customer_id: Any = None,
) -> Sale:
    sale = Sale(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        status=SaleStatus.COMPLETED,
        subtotal=total,
        total=total,
        customer_id=customer_id,
        idempotency_key=f"r2-{uuid4().hex}",
    )
    session.add(sale)
    await session.flush()
    return sale


async def _seed_product_sale(
    session: Any,
    tenant: dict[str, Any],
    *,
    product_name: str,
    sku: str,
    quantity: int,
    unit_price: str,
    customer_id: Any = None,
) -> None:
    product = PharmacyProduct(
        organization_id=tenant["organization"].id, name=product_name, unit="box", active=True
    )
    session.add(product)
    await session.flush()
    sp = StoreProduct(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        pharmacy_product_id=product.id,
        sku=sku,
        sale_price=Decimal(unit_price),
        minimum_stock=0,
        active=True,
    )
    session.add(sp)
    await session.flush()
    total = Decimal(unit_price) * quantity
    sale = await _add_sale(session, tenant, total=total, customer_id=customer_id)
    session.add(
        SaleItem(
            sale_id=sale.id,
            store_product_id=sp.id,
            product_name=product_name,
            quantity=Decimal(quantity),
            unit_price=Decimal(unit_price),
            line_total=total,
        )
    )


# --- comparison ---------------------------------------------------------------


async def test_comparison_reports_today_and_yesterday(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    auth_headers: Callable[..., dict[str, str]],
) -> None:
    from zoneinfo import ZoneInfo

    headers = auth_headers(tenant)

    async def seed(days_ago: int, total: str) -> None:
        tz = ZoneInfo(tenant["store"].timezone)
        local = datetime.now(tz).replace(hour=12, minute=0) - timedelta(days=days_ago)
        created = local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        sale = Sale(
            organization_id=tenant["organization"].id,
            store_id=tenant["store"].id,
            status=SaleStatus.COMPLETED,
            subtotal=Decimal(total),
            total=Decimal(total),
            idempotency_key=f"cmp-{uuid4().hex}",
        )
        sale.created_at = created
        session.add(sale)

    await seed(0, "120.00")
    await seed(1, "80.00")
    await session.commit()

    response = await client.get("/reports/comparison", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["current"]["salesTotal"] == "120.00"
    assert data["previous"]["salesTotal"] == "80.00"
    assert data["salesChange"] == "50.00"


async def test_comparison_zero_previous_day_is_not_a_crash(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    response = await client.get("/reports/comparison", headers=auth_headers(tenant))
    assert response.status_code == 200
    # Two empty days: no baseline to move from, so the change is flat zero
    # rather than a fabricated spike.
    assert response.json()["data"]["salesChange"] == "0.00"


async def test_comparison_hides_profit_from_cashier(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    response = await client.get("/reports/comparison", headers=await role_headers(session, tenant, Role.CASHIER))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["current"]["profit"] is None
    assert data["previous"]["profit"] is None


# --- branch rollup ------------------------------------------------------------


async def test_branch_rollup_sums_across_stores_owner_only(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    auth_headers: Callable[..., dict[str, str]],
    make_store: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    from app.models import StoreUser

    store_b = await make_store(tenant["organization"], name="B", code="B")
    session.add(
        StoreUser(store_id=store_b.id, user_id=tenant["owner"].id, role=Role.OWNER, active=True)
    )
    for store in (tenant["store"], store_b):
        session.add(
            Sale(
                organization_id=tenant["organization"].id,
                store_id=store.id,
                status=SaleStatus.COMPLETED,
                subtotal=Decimal("10.00"),
                total=Decimal("10.00"),
                idempotency_key=f"roll-{store.id}-{uuid4().hex[:8]}",
            )
        )
    await session.commit()

    owner = await client.get("/reports/branch-rollup", headers=auth_headers(tenant))
    assert owner.status_code == 200, owner.text
    data = owner.json()["data"]
    assert len(data["rows"]) == 2
    assert data["totalSales"] == "20.00"
    assert data["totalTransactions"] == 2

    cashier = await client.get("/reports/branch-rollup", headers=await role_headers(session, tenant, Role.CASHIER))
    assert cashier.status_code == 403


# --- top products / customers -------------------------------------------------


async def test_top_products_ranks_by_revenue(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    await _seed_product_sale(session, tenant, product_name="Napa", sku="NAPA", quantity=2, unit_price="50.00")
    await _seed_product_sale(session, tenant, product_name="Seclo", sku="SECLO", quantity=10, unit_price="10.00")
    await session.commit()

    response = await client.get("/reports/top-products", headers=auth_headers(tenant))
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert [row["productName"] for row in rows] == ["Napa", "Seclo"]
    assert rows[0]["revenue"] == "100.00"


async def test_top_customers_requires_manager_and_ranks_by_spend(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    big = Customer(organization_id=tenant["organization"].id, name="Big Spender", preferences={})
    small = Customer(organization_id=tenant["organization"].id, name="Small", preferences={})
    session.add_all([big, small])
    await session.flush()
    await _add_sale(session, tenant, total=Decimal("500.00"), customer_id=big.id)
    await _add_sale(session, tenant, total=Decimal("5.00"), customer_id=small.id)
    await session.commit()

    denied = await client.get("/reports/top-customers", headers=await role_headers(session, tenant, Role.CASHIER))
    assert denied.status_code == 403

    allowed = await client.get("/reports/top-customers", headers=auth_headers(tenant))
    assert allowed.status_code == 200, allowed.text
    rows = allowed.json()["data"]
    assert [row["customerName"] for row in rows] == ["Big Spender", "Small"]


# --- cross-store purchase history ---------------------------------------------


async def test_purchase_history_lists_completed_sales_owner_only(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    customer = Customer(organization_id=tenant["organization"].id, name="History", preferences={})
    session.add(customer)
    await session.flush()
    completed = await _add_sale(session, tenant, total=Decimal("30.00"), customer_id=customer.id)
    voided = Sale(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        status=SaleStatus.VOIDED,
        subtotal=Decimal("99.00"),
        total=Decimal("99.00"),
        customer_id=customer.id,
        idempotency_key=f"hist-{uuid4().hex}",
    )
    session.add(voided)
    await session.commit()

    denied = await client.get(
        f"/customers/{customer.id}/purchases", headers=await role_headers(session, tenant, Role.CASHIER)
    )
    assert denied.status_code == 403

    allowed = await client.get(f"/customers/{customer.id}/purchases", headers=auth_headers(tenant))
    assert allowed.status_code == 200, allowed.text
    page = allowed.json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["saleId"] == str(completed.id)


async def test_purchase_history_excludes_other_tenants(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    stranger = Customer(organization_id=__import__("uuid").uuid4(), name="Other Org", preferences={})
    session.add(stranger)
    await session.commit()
    response = await client.get(
        f"/customers/{stranger.id}/purchases", headers=auth_headers(tenant)
    )
    assert response.status_code == 404
