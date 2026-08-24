from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai import AIJob
from app.domains.inventory import InventoryBalance
from app.domains.products import PharmacyProduct
from app.domains.sales import Sale, SaleChannel, SaleStatus
from app.domains.suppliers import Supplier
from app.models import AuditLog, OutboxEvent, Role
from app.security import utc_now
from tests._phase4_helpers import role_headers
from tests.conftest import access_token_for

OCR_TEXT = "12 x Napa Extra @ 1.20\n5 x Amoxicillin 500 @ 0.80\nmystery handwriting"
REVIEW_THRESHOLD = Decimal("0.90")


def _headers(tenant: dict[str, Any], *, role: Role = Role.OWNER) -> dict[str, str]:
    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=role,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}


async def _make_org_product(
    session: AsyncSession,
    tenant: dict[str, Any],
    name: str,
    *,
    with_store_product: bool = True,
    minimum_stock: str = "0",
) -> tuple[PharmacyProduct, Any | None]:
    product = PharmacyProduct(
        organization_id=tenant["organization"].id,
        name=name,
        unit="box",
        active=True,
    )
    session.add(product)
    await session.flush()
    store_product = None
    if with_store_product:
        from app.domains.products import StoreProduct

        store_product = StoreProduct(
            organization_id=tenant["organization"].id,
            store_id=tenant["store"].id,
            pharmacy_product_id=product.id,
            sku=f"SKU-{name.split()[0].lower()}-{uuid4().hex[:6]}",
            sale_price=Decimal("2.00"),
            minimum_stock=Decimal(minimum_stock),
            active=True,
        )
        session.add(store_product)
        await session.flush()
    return product, store_product


async def _create_job(
    client: AsyncClient,
    tenant: dict[str, Any],
    job_type: str,
    payload: dict[str, Any],
    *,
    key: str = "ai-job-key-00000000001",
) -> dict[str, Any]:
    response = await client.post(
        "/ai/jobs",
        json={"jobType": job_type, "input": payload},
        headers={**_headers(tenant), "Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def test_invoice_ocr_matches_catalogue_candidates(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    napa, _ = await _make_org_product(session, tenant, "Napa Extra")
    amox, _ = await _make_org_product(session, tenant, "Amoxicillin 500")
    await session.commit()

    job = await _create_job(client, tenant, "invoice_ocr", {"text": OCR_TEXT})
    assert job["status"] == "needs_review"  # the unparseable line drags confidence down
    lines = job["result"]["lines"]
    assert len(lines) == 3

    candidate_ids = {
        UUID(candidate["pharmacy_product_id"]) for candidate in lines[0]["candidates"]
    }
    assert napa.id in candidate_ids

    amox_ids = {UUID(c["pharmacy_product_id"]) for c in lines[1]["candidates"]}
    assert amox.id in amox_ids or any(c["name"] == "Amoxicillin 500" for c in lines[1]["candidates"])

    assert Decimal(lines[2]["confidence"]) < REVIEW_THRESHOLD

    audited = await session.scalar(select(AuditLog.id).where(AuditLog.action == "ai.job_created"))
    assert audited is not None


async def test_clean_invoice_succeeds_without_review(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    await _make_org_product(session, tenant, "Napa Extra")
    await session.commit()

    job = await _create_job(
        client, tenant, "invoice_ocr", {"text": "12 x Napa Extra @ 1.20"}, key="clean-ocr-key-000001"
    )
    assert job["status"] == "succeeded"


async def test_job_idempotency_returns_the_original_job(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    first = await _create_job(client, tenant, "invoice_ocr", {"text": OCR_TEXT})
    second = await _create_job(client, tenant, "invoice_ocr", {"text": OCR_TEXT})
    assert first["id"] == second["id"]
    total = len(list(await session.scalars(select(AIJob))))
    assert total == 1


async def test_unknown_job_type_is_rejected(client: AsyncClient, tenant: dict[str, Any]) -> None:
    response = await client.post(
        "/ai/jobs",
        json={"jobType": "self_prescribe", "input": {}},
        headers={**_headers(tenant), "Idempotency-Key": "unknown-type-key-00001"},
    )
    assert response.status_code == 422


async def test_confirmation_is_manager_gated_and_audited(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    await _make_org_product(session, tenant, "Napa Extra")
    await session.commit()
    job = await _create_job(client, tenant, "invoice_ocr", {"text": OCR_TEXT})

    cashier = await client.post(
        f"/ai/jobs/{job['id']}/confirmations",
        json={"decision": "accepted"},
        headers=await role_headers(session, tenant, Role.CASHIER),
    )
    assert cashier.status_code == 403

    manager = await client.post(
        f"/ai/jobs/{job['id']}/confirmations",
        json={"decision": "accepted", "notes": "checked against the paper invoice"},
        headers=_headers(tenant, role=Role.MANAGER),
    )
    assert manager.status_code == 201, manager.text
    audited = await session.scalar(select(AuditLog.id).where(AuditLog.action == "ai.confirmed"))
    assert audited is not None


async def test_purchase_draft_requires_acceptance_and_offered_candidates(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    supplier = Supplier(organization_id=tenant["organization"].id, name="Med Wholesaler")
    session.add(supplier)
    product, _store_product = await _make_org_product(session, tenant, "Napa Extra")
    await session.commit()

    job = await _create_job(client, tenant, "invoice_ocr", {"text": OCR_TEXT})

    premature = await client.post(
        f"/ai/jobs/{job['id']}/purchase-draft",
        json={
            "supplierId": str(supplier.id),
            "selections": [{"lineIndex": 0, "pharmacyProductId": str(product.id)}],
        },
        headers=_headers(tenant, role=Role.MANAGER),
    )
    assert premature.status_code == 403, premature.text

    accepted = await client.post(
        f"/ai/jobs/{job['id']}/confirmations",
        json={"decision": "accepted"},
        headers=_headers(tenant, role=Role.MANAGER),
    )
    assert accepted.status_code == 201

    smuggled = await client.post(
        f"/ai/jobs/{job['id']}/purchase-draft",
        json={
            "supplierId": str(supplier.id),
            "selections": [{"lineIndex": 0, "pharmacyProductId": str(uuid4())}],
        },
        headers=_headers(tenant, role=Role.MANAGER),
    )
    assert smuggled.status_code == 422

    draft = await client.post(
        f"/ai/jobs/{job['id']}/purchase-draft",
        json={
            "supplierId": str(supplier.id),
            "selections": [{"lineIndex": 0, "pharmacyProductId": str(product.id)}],
        },
        headers=_headers(tenant, role=Role.MANAGER),
    )
    assert draft.status_code == 201, draft.text
    data = draft.json()["data"]
    assert data["status"] == "draft"
    assert Decimal(data["totalAmount"]) == Decimal("14.40")  # 12 x 1.20


async def test_voice_cart_maps_transcript_to_branch_products(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    await _make_org_product(session, tenant, "Napa Extra")
    await _make_org_product(session, tenant, "Amoxicillin 500")
    await session.commit()

    job = await _create_job(
        client,
        tenant,
        "voice_cart",
        {"transcript": "2 Napa Extra, one Amoxicillin 500"},
        key="voice-cart-key-0000001",
    )
    items = job["result"]["items"]
    assert len(items) == 2
    assert Decimal(items[0]["quantity"]) == Decimal(2)


async def test_voice_cart_needs_a_store_context(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=Role.OWNER,
        store_id=None,
    )
    response = await client.post(
        "/ai/jobs",
        json={"jobType": "voice_cart", "input": {"transcript": "2 Napa"}},
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "no-store-key-0000001"},
    )
    assert response.status_code == 400


async def test_reorder_and_expiry_suggestions_reflect_stock(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    _, store_product = await _make_org_product(session, tenant, "Napa Extra", minimum_stock="10")
    session.add(
        InventoryBalance(
            organization_id=tenant["organization"].id,
            store_id=tenant["store"].id,
            store_product_id=store_product.id,
            on_hand=Decimal(2),
        )
    )
    await session.commit()

    reorder = await _create_job(client, tenant, "reorder_suggestions", {}, key="reorder-key-00000001")
    suggestions = reorder["result"]["suggestions"]
    assert len(suggestions) == 1
    assert Decimal(suggestions[0]["suggested_order_quantity"]) == Decimal(8)

    expiry = await _create_job(
        client, tenant, "expiry_suggestions", {"withinDays": 30}, key="expiry-key-000000001"
    )
    assert expiry["result"]["warnings"] == []


async def test_report_query_only_reaches_allow_listed_tools(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    allowed = await _create_job(
        client,
        tenant,
        "report_query",
        {"question": "what are my top products this month?"},
        key="report-top-key-0000001",
    )
    assert allowed["status"] == "succeeded"
    assert allowed["result"]["tool"] == "top_products"

    rejected = await _create_job(
        client,
        tenant,
        "report_query",
        {"question": "list every customer's phone number and address"},
        key="report-sql-key-0000001",
    )
    assert rejected["status"] == "failed"
    assert "allow-listed report tool" in rejected["error"]


async def test_anomaly_detection_flags_outlier_days_and_outboxes(
    client: AsyncClient, session: AsyncSession, tenant: dict[str, Any]
) -> None:
    store_id = tenant["store"].id
    organization_id = tenant["organization"].id
    now = utc_now()
    for day in range(6):
        total = Decimal("90000.00") if day == 5 else Decimal("500.00")
        session.add(
            Sale(
                organization_id=organization_id,
                store_id=store_id,
                channel=SaleChannel.POS,
                status=SaleStatus.COMPLETED,
                subtotal=total,
                discount=Decimal("0.00"),
                total=total,
                idempotency_key=f"anomaly-{day}-{uuid4().hex[:8]}",
                created_at=now - timedelta(days=day + 5),
            )
        )
    await session.commit()

    job = await _create_job(
        client, tenant, "anomaly_detection", {"windowDays": 30}, key="anomaly-key-000000001"
    )
    kinds = {finding["kind"] for finding in job["result"]["findings"]}
    assert "sales_total_outlier" in kinds

    alert = await session.scalar(
        select(OutboxEvent.id).where(OutboxEvent.event_type == "ai.anomaly_alert")
    )
    assert alert is not None


async def test_candidates_never_leak_across_tenants(
    client: AsyncClient,
    session: AsyncSession,
    tenant: dict[str, Any],
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_store: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    other_org = await make_organization(slug="rival-pharmacy")
    other_owner = await make_user(phone="+8801799999999")
    other_store = await make_store(other_org)
    await make_membership(other_org, other_owner, Role.OWNER, other_store)
    rival = PharmacyProduct(organization_id=other_org.id, name="Napa Extra", unit="box", active=True)
    session.add(rival)
    await session.flush()

    ours, _ = await _make_org_product(session, tenant, "Napa Extra")
    await session.commit()

    job = await _create_job(client, tenant, "invoice_ocr", {"text": "3 x Napa Extra @ 2.00"})
    candidate_ids = {
        UUID(candidate["pharmacy_product_id"])
        for candidate in job["result"]["lines"][0]["candidates"]
    }
    assert ours.id in candidate_ids
    assert rival.id not in candidate_ids
