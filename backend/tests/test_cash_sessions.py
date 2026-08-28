from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.context import RequestContext
from app.domains.payments import Payment, PaymentMethod, PaymentRefund, PaymentStatus
from app.models import Role
from app.security import utc_now
from tests._phase4_helpers import role_headers
from tests.conftest import access_token_for


def _headers(tenant: dict[str, Any], *, role: Role = Role.OWNER) -> dict[str, str]:
    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=role,
        store_id=tenant["store"].id,
    )
    return {"Authorization": f"Bearer {token}"}


async def _cash_payment(
    session: Any,
    tenant: dict[str, Any],
    amount: str,
    *,
    method: PaymentMethod = PaymentMethod.CASH,
) -> Payment:
    payment = Payment(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        reference_type="sale",
        reference_id=uuid4(),
        method=method,
        amount=Decimal(amount),
        status=PaymentStatus.CAPTURED,
        idempotency_key=uuid4().hex,
        created_at=utc_now(),
    )
    session.add(payment)
    # Committed, not flushed: the HTTP client runs on its own session over the
    # same engine, so an uncommitted payment is invisible to every request.
    await session.commit()
    return payment


async def test_open_close_and_expected_cash_math(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    opened = await client.post(
        "/cash-sessions",
        json={"openingCash": "500.00"},
        headers=_headers(tenant),
    )
    assert opened.status_code == 201, opened.text
    data = opened.json()["data"]
    assert data["status"] == "open"
    assert data["openingCash"] == "500.00"
    assert data["openedByName"] == "Owner"
    assert data["cashIn"] == "0.00"

    # 600 taken, 100 handed back as change: the drawer's true gain is 500.
    await _cash_payment(session, tenant, "500.00")
    digital = await _cash_payment(session, tenant, "300.00", method=PaymentMethod.BKASH)
    assert digital.method is PaymentMethod.BKASH
    await _cash_payment(session, tenant, "100.00")

    current = await client.get("/cash-sessions/current", headers=_headers(tenant))
    assert current.status_code == 200
    assert current.json()["data"]["cashIn"] == "600.00"

    closed = await client.post(
        f"/cash-sessions/{data['id']}/close",
        json={"countedCash": "1090.00", "note": "evening count"},
        headers=_headers(tenant),
    )
    assert closed.status_code == 200, closed.text
    summary = closed.json()["data"]
    assert summary["status"] == "closed"
    assert summary["cashIn"] == "600.00"
    assert summary["cashOut"] == "0.00"
    assert summary["expectedCash"] == "1100.00"
    assert summary["difference"] == "-10.00"
    assert summary["closedByName"] == "Owner"


async def test_cash_refunds_count_against_the_drawer(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    await client.post("/cash-sessions", json={"openingCash": "100.00"}, headers=_headers(tenant))
    payment = await _cash_payment(session, tenant, "200.00")
    refunded = await _cash_payment(session, tenant, "50.00", method=PaymentMethod.NAGAD)
    session.add(
        PaymentRefund(
            organization_id=tenant["organization"].id,
            store_id=tenant["store"].id,
            payment_id=payment.id,
            amount=Decimal("40.00"),
            idempotency_key=uuid4().hex,
            created_at=utc_now(),
        )
    )
    session.add(
        PaymentRefund(
            organization_id=tenant["organization"].id,
            store_id=tenant["store"].id,
            payment_id=refunded.id,
            amount=Decimal("30.00"),
            idempotency_key=uuid4().hex,
            created_at=utc_now(),
        )
    )
    await session.commit()

    current = await client.get("/cash-sessions/current", headers=_headers(tenant))
    body = current.json()["data"]
    assert body["cashIn"] == "200.00"
    # Only the cash tender's refund hits the drawer; the Nagad one left no bills.
    assert body["cashOut"] == "40.00"


async def test_second_open_conflicts_and_close_twice_conflicts(
    client: Any, tenant: dict[str, Any]
) -> None:
    first = await client.post("/cash-sessions", json={"openingCash": "0.00"}, headers=_headers(tenant))
    assert first.status_code == 201
    duplicate = await client.post("/cash-sessions", json={"openingCash": "10.00"}, headers=_headers(tenant))
    assert duplicate.status_code == 409

    session_id = first.json()["data"]["id"]
    closed = await client.post(
        f"/cash-sessions/{session_id}/close", json={"countedCash": "5.00"}, headers=_headers(tenant)
    )
    assert closed.status_code == 200
    again = await client.post(
        f"/cash-sessions/{session_id}/close", json={"countedCash": "5.00"}, headers=_headers(tenant)
    )
    assert again.status_code == 409

    # With the session closed, the branch may open the next one.
    reopened = await client.post("/cash-sessions", json={"openingCash": "5.00"}, headers=_headers(tenant))
    assert reopened.status_code == 201


async def test_current_is_null_without_an_open_session(
    client: Any, tenant: dict[str, Any]
) -> None:
    current = await client.get("/cash-sessions/current", headers=_headers(tenant))
    assert current.status_code == 200
    assert current.json()["data"] is None


async def test_role_gates_on_open(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    # The context role comes from the live membership row, so a denial needs a
    # real member at that role, not a re-signed owner token.
    denied = await client.post(
        "/cash-sessions",
        json={"openingCash": "10.00"},
        headers=await role_headers(session, tenant, Role.INVENTORY_STAFF),
    )
    assert denied.status_code == 403
    cashier = await client.post(
        "/cash-sessions",
        json={"openingCash": "10.00"},
        headers=await role_headers(session, tenant, Role.CASHIER),
    )
    assert cashier.status_code == 201, cashier.text


async def test_list_returns_history_newest_first(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    context = RequestContext(
        organization_id=tenant["organization"].id,
        user_id=tenant["owner"].id,
        role=Role.OWNER,
        store_id=tenant["store"].id,
    )
    from app.services import cash as cash_service

    first = await cash_service.open_session(session, context, Decimal("10.00"), request_id="t1")
    await cash_service.close_session(
        session, context, first.id, Decimal("10.00"), None, request_id="t2"
    )
    await cash_service.open_session(session, context, Decimal("20.00"), request_id="t3")

    listed = await client.get("/cash-sessions", headers=_headers(tenant))
    assert listed.status_code == 200
    page = listed.json()["data"]
    assert page["total"] == 2
    assert [row["status"] for row in page["items"]] == ["open", "closed"]
    assert page["items"][1]["expectedCash"] == "10.00"


async def test_payments_before_open_do_not_count(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    await _cash_payment(session, tenant, "999.00")
    await client.post("/cash-sessions", json={"openingCash": "0.00"}, headers=_headers(tenant))
    current = await client.get("/cash-sessions/current", headers=_headers(tenant))
    assert current.json()["data"]["cashIn"] == "0.00"
