from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from app.context import RequestContext
from app.domains.customers import Customer
from app.domains.loyalty import LoyaltyAccount
from app.models import Role
from app.schemas.loyalty import LoyaltyTransactionRequest
from app.services import loyalty as service
from tests._phase4_helpers import role_headers


def _context(tenant: dict[str, Any], *, role: Role = Role.OWNER) -> RequestContext:
    return RequestContext(
        organization_id=tenant["organization"].id,
        user_id=tenant["owner"].id,
        role=role,
        store_id=tenant["store"].id,
    )


async def _make_customer(
    session: Any, tenant: dict[str, Any], *, organization_id: UUID | None = None
) -> Customer:
    customer = Customer(
        organization_id=organization_id or tenant["organization"].id,
        name="Walk In",
        normalized_phone=None,
        preferences={},
        active=True,
    )
    session.add(customer)
    await session.flush()
    return customer


def _tx(tx_type: str, points: int) -> dict[str, Any]:
    return {
        "transactionType": tx_type,
        "points": points,
        "sourceType": "sale",
        "sourceId": str(uuid4()),
    }


async def _enrolled(
    session: Any, client: Any, tenant: dict[str, Any], headers: dict[str, str]
) -> LoyaltyAccount:
    customer = await _make_customer(session, tenant)
    await session.commit()
    response = await client.post(
        "/loyalty/accounts",
        json={"customerId": str(customer.id)},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    account = await session.get(LoyaltyAccount, UUID(response.json()["data"]["id"]))
    assert account is not None
    return account


# --- enrollment ---------------------------------------------------------------


async def test_enrollment_is_idempotent_per_customer(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    headers = auth_headers(tenant)
    customer = await _make_customer(session, tenant)
    await session.commit()
    first = await client.post(
        "/loyalty/accounts", json={"customerId": str(customer.id)}, headers=headers
    )
    assert first.status_code == 201, first.text
    again = await client.post(
        "/loyalty/accounts", json={"customerId": str(customer.id)}, headers=headers
    )
    assert again.status_code == 201
    assert again.json()["data"]["id"] == first.json()["data"]["id"]
    accounts = list(await session.scalars(select(LoyaltyAccount)))
    assert len(accounts) == 1


async def test_enrollment_rejects_other_tenant_customer(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    stranger = await _make_customer(session, tenant, organization_id=uuid4())
    await session.commit()
    response = await client.post(
        "/loyalty/accounts",
        json={"customerId": str(stranger.id)},
        headers=auth_headers(tenant),
    )
    assert response.status_code == 404


# --- earn/redeem ledger -------------------------------------------------------


async def test_earn_then_redeem_updates_balance_and_is_idempotent(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    headers = auth_headers(tenant)
    account = await _enrolled(session, client, tenant, headers)

    earn_key = f"earn-{uuid4().hex}"[:32]
    first = await client.post(
        f"/loyalty/accounts/{account.id}/transactions",
        params={"idempotencyKey": earn_key},
        json=_tx("earn", 50),
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["data"]["points"] == 50

    replay = await client.post(
        f"/loyalty/accounts/{account.id}/transactions",
        params={"idempotencyKey": earn_key},
        json=_tx("earn", 50),
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["data"]["id"] == first.json()["data"]["id"]

    redeem_key = f"redeem-{uuid4().hex}"[:32]
    redeem = await client.post(
        f"/loyalty/accounts/{account.id}/transactions",
        params={"idempotencyKey": redeem_key},
        json=_tx("redeem", 30),
        headers=headers,
    )
    assert redeem.status_code == 200, redeem.text
    assert redeem.json()["data"]["points"] == -30
    assert redeem.json()["data"]["balanceAfter"] == 20

    await session.refresh(account)
    assert account.balance == 20


async def test_overdraw_is_rejected(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    headers = auth_headers(tenant)
    account = await _enrolled(session, client, tenant, headers)
    response = await client.post(
        f"/loyalty/accounts/{account.id}/transactions",
        params={"idempotencyKey": f"key-{uuid4().hex}"[:32]},
        json=_tx("redeem", 1),
        headers=headers,
    )
    assert response.status_code == 422


async def test_cashier_cannot_adjust_but_can_earn(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    cashier_headers = await role_headers(session, tenant, Role.CASHIER)
    owner_headers = auth_headers(tenant)
    account = await _enrolled(session, client, tenant, owner_headers)

    earn = await client.post(
        f"/loyalty/accounts/{account.id}/transactions",
        params={"idempotencyKey": f"cashier-earn-{uuid4().hex}"[:36]},
        json=_tx("bonus", 5),
        headers=cashier_headers,
    )
    assert earn.status_code == 200

    adjust = await client.post(
        f"/loyalty/accounts/{account.id}/transactions",
        params={"idempotencyKey": f"cashier-adj-{uuid4().hex}"[:36]},
        json=_tx("adjust", -2),
        headers=cashier_headers,
    )
    assert adjust.status_code == 403


async def test_owner_adjustment_can_correct_either_way(
    client: Any, session: Any, tenant: dict[str, Any], auth_headers: Callable[..., dict[str, str]]
) -> None:
    headers = auth_headers(tenant)
    account = await _enrolled(session, client, tenant, headers)
    down = await client.post(
        f"/loyalty/accounts/{account.id}/transactions",
        params={"idempotencyKey": f"adj-{uuid4().hex}"[:32]},
        json=_tx("adjust", -3),
        headers=headers,
    )
    assert down.status_code == 422  # would drive the balance negative from zero

    up = await client.post(
        f"/loyalty/accounts/{account.id}/transactions",
        params={"idempotencyKey": f"adj-{uuid4().hex}"[:32]},
        json=_tx("adjust", 7),
        headers=headers,
    )
    assert up.status_code == 200, up.text
    assert up.json()["data"]["balanceAfter"] == 7


# --- expiry -------------------------------------------------------------------


async def test_expiry_posts_expire_rows_and_respects_redemptions(
    session: Any, tenant: dict[str, Any]
) -> None:
    context = _context(tenant)
    customer = await _make_customer(session, tenant)
    account = await service.enroll_customer(
        session, context, customer.id, request_id="test"
    )
    past = datetime.now(UTC) - timedelta(days=1)
    future = datetime.now(UTC) + timedelta(days=30)

    await service.apply_transaction(
        session,
        context,
        account.id,
        LoyaltyTransactionRequest(
            transaction_type="earn", points=40, source_type="sale",
            source_id=uuid4(), expires_at=past,
        ),
        idempotency_key=f"exp-old-{uuid4().hex}"[:32], request_id="test",
    )
    await service.apply_transaction(
        session,
        context,
        account.id,
        LoyaltyTransactionRequest(
            transaction_type="earn", points=60, source_type="sale",
            source_id=uuid4(), expires_at=future,
        ),
        idempotency_key=f"exp-new-{uuid4().hex}"[:32], request_id="test",
    )
    # Spend 25 of the old lot before it expires.
    await service.apply_transaction(
        session,
        context,
        account.id,
        LoyaltyTransactionRequest(
            transaction_type="redeem", points=25, source_type="sale", source_id=uuid4(),
        ),
        idempotency_key=f"exp-spend-{uuid4().hex}"[:32], request_id="test",
    )

    expired = await service.expire_due_points(session, context, account.id)
    assert len(expired) == 1
    assert expired[0].points == -15
    await session.refresh(account)
    assert account.balance == 60


# --- rebuild ------------------------------------------------------------------


async def test_rebuild_matches_incremental_balance(
    session: Any, tenant: dict[str, Any]
) -> None:
    context = _context(tenant)
    customer = await _make_customer(session, tenant)
    account = await service.enroll_customer(session, context, customer.id, request_id="t")
    for index, (tx_type, points) in enumerate(
        [("earn", 100), ("redeem", 40), ("bonus", 10), ("adjust", -5)]
    ):
        request = LoyaltyTransactionRequest(
            transaction_type=tx_type, points=points, source_type="manual", source_id=uuid4()
        )
        if tx_type == "adjust":
            await service.apply_adjustment(
                session, context, account.id, request, idempotency_key=f"rb-{index}-{uuid4().hex}"[:36],
             request_id="test",
            )
        else:
            await service.apply_transaction(
                session, context, account.id, request, idempotency_key=f"rb-{index}-{uuid4().hex}"[:36],
             request_id="test",
            )
    expected = int(account.balance)

    result = await service.rebuild_balance(session, context, account.id)
    assert result.ledger_total == expected == 65
