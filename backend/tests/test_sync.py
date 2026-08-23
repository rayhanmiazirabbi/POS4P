from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from app.domains.sync import Device, DeviceStatus
from app.models import Organization, Role, SyncFeedItem
from app.security import utc_now
from tests.conftest import access_token_for


def _headers(
    tenant: dict[str, Any],
    *,
    role: Role = Role.OWNER,
    device_id: Any = None,
    with_store: bool = True,
) -> dict[str, str]:
    token = access_token_for(
        session_id=tenant["session"].id,
        user_id=tenant["owner"].id,
        organization_id=tenant["organization"].id,
        role=role,
        store_id=tenant["store"].id if with_store else None,
        device_id=device_id,
    )
    return {"Authorization": f"Bearer {token}"}


async def _make_device(session: Any, tenant: dict[str, Any], *, key: str = "pos-1") -> Device:
    device = Device(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        name="Front counter POS",
        device_key=key,
        status=DeviceStatus.ACTIVE,
    )
    session.add(device)
    await session.flush()
    return device


def _envelope(sequence: int, *, event_type: str = "ping", event_id: Any = None) -> dict[str, Any]:
    return {
        "eventId": str(event_id or uuid4()),
        "eventType": event_type,
        "clientSequence": sequence,
        "payload": {"total": "12.50"},
    }


# --- device registration --------------------------------------------------------


async def test_register_device_and_duplicate_key_conflict(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    response = await client.post(
        "/sync/devices",
        json={"name": "Counter 1", "deviceKey": "pos-1"},
        headers=_headers(tenant),
    )
    assert response.status_code == 201, response.text
    body = response.json()["data"]
    assert body["deviceKey"] == "pos-1"
    assert body["status"] == DeviceStatus.ACTIVE.value

    duplicate = await client.post(
        "/sync/devices",
        json={"name": "Counter 1 again", "deviceKey": "pos-1"},
        headers=_headers(tenant),
    )
    assert duplicate.status_code == 409


async def test_register_requires_manager_role(
    client: Any, session: Any, tenant: dict[str, Any], make_user: Any, make_membership: Any
) -> None:
    from app.models import Session as SessionModel
    from app.security import generate_token, hash_token

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
    cashier_tenant = {**tenant, "owner": cashier, "session": cashier_session}

    response = await client.post(
        "/sync/devices",
        json={"name": "Counter 1", "deviceKey": "pos-1"},
        headers=_headers(cashier_tenant, role=Role.CASHIER),
    )
    assert response.status_code == 403


async def test_revoke_device_sets_revoked_and_audits(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    created = await client.post(
        "/sync/devices",
        json={"name": "Counter 1", "deviceKey": "pos-1"},
        headers=_headers(tenant),
    )
    device_id = created.json()["data"]["id"]

    revoked = await client.post(
        f"/sync/devices/{device_id}/revoke", json={}, headers=_headers(tenant)
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["data"]["status"] == DeviceStatus.REVOKED.value

    from sqlalchemy import select

    from app.models import AuditLog

    audit = await session.scalar(select(AuditLog).where(AuditLog.action == "sync.device.revoked"))
    assert audit is not None and str(audit.entity_id) == device_id


# --- ingest -----------------------------------------------------------------------


async def _ingest(client: Any, tenant: dict[str, Any], events: list[dict[str, Any]], device: Any) -> Any:
    device_id = device.id if device is not None else None
    return await client.post(
        "/sync/events", json={"events": events}, headers=_headers(tenant, device_id=device_id)
    )


async def test_ingest_assigns_increasing_server_sequences(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    device = await _make_device(session, tenant)
    await session.commit()

    response = await _ingest(
        client, tenant, [_envelope(1), _envelope(2), _envelope(3)], device
    )
    assert response.status_code == 200, response.text
    acks = response.json()["data"]["acks"]
    assert [ack["errorCode"] for ack in acks] == [None, None, None]
    sequences = [ack["serverSequence"] for ack in acks]
    assert sequences == [1, 2, 3]
    assert not any(ack["duplicate"] for ack in acks)


async def test_duplicate_replay_acknowledged_without_double_apply(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    device = await _make_device(session, tenant)
    event_id = uuid4()
    await session.commit()

    first = await _ingest(client, tenant, [_envelope(1, event_id=event_id)], device)
    assert first.status_code == 200
    original_sequence = first.json()["data"]["acks"][0]["serverSequence"]

    replay = await _ingest(client, tenant, [_envelope(1, event_id=event_id)], device)
    assert replay.status_code == 200
    ack = replay.json()["data"]["acks"][0]
    assert ack["duplicate"] is True
    assert ack["serverSequence"] == original_sequence

    from sqlalchemy import func, select

    count = await session.scalar(select(func.count()).select_from(SyncFeedItem))
    assert int(count or 0) == 1


async def test_out_of_order_rejected_but_valid_event_applies(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    device = await _make_device(session, tenant)
    await session.commit()

    first = await _ingest(client, tenant, [_envelope(5)], device)
    assert first.status_code == 200

    batch = await _ingest(client, tenant, [_envelope(3), _envelope(6)], device)
    assert batch.status_code == 200
    acks = batch.json()["data"]["acks"]
    assert acks[0]["errorCode"] == "OUT_OF_ORDER"
    assert acks[0]["serverSequence"] is None
    assert acks[1]["errorCode"] is None
    assert acks[1]["serverSequence"] == 2

    unknown = await _ingest(client, tenant, [_envelope(7, event_type="stock.hard_delete")], device)
    assert unknown.status_code == 200
    assert unknown.json()["data"]["acks"][0]["errorCode"] == "UNSUPPORTED_EVENT_TYPE"


async def test_ingest_without_device_context_is_validation_error(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    response = await _ingest(client, tenant, [_envelope(1)], device=None)
    assert response.status_code == 400
    assert response.json()["code"] == "DEVICE_CONTEXT_REQUIRED"


async def test_ingest_with_unknown_event_type_only_fails_that_event(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    device = await _make_device(session, tenant)
    await session.commit()
    response = await _ingest(
        client, tenant, [_envelope(1, event_type="nope"), _envelope(2, event_type="ping")], device
    )
    acks = response.json()["data"]["acks"]
    assert acks[0]["errorCode"] == "UNSUPPORTED_EVENT_TYPE"
    assert acks[1]["serverSequence"] == 1


# --- offline sale replay ----------------------------------------------------------


async def test_offline_sale_create_replays_exactly_once(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    from decimal import Decimal

    from sqlalchemy import func, select

    from app.domains.inventory import InventoryBalance
    from app.domains.sales import Sale
    from app.services.inventory import receive_batch

    device = await _make_device(session, tenant, key="offline-1")
    from app.domains.products import PharmacyProduct

    product = PharmacyProduct(
        organization_id=tenant["organization"].id, name="Napa", unit="box", active=True
    )
    session.add(product)
    await session.flush()
    from app.models import StoreProduct

    store_product = StoreProduct(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        pharmacy_product_id=product.id,
        sku="SKU-OFF",
        sale_price=Decimal("10.00"),
        minimum_stock=Decimal(0),
        active=True,
    )
    session.add(store_product)
    await session.flush()
    from app.context import RequestContext
    from app.models import Role
    from datetime import date, timedelta

    await receive_batch(
        session,
        RequestContext(
            organization_id=tenant["organization"].id,
            user_id=tenant["owner"].id,
            role=Role.OWNER,
            store_id=tenant["store"].id,
        ),
        store_product.id,
        batch_number="OB1",
        expiry_date=date.today() + timedelta(days=100),
        unit_cost=Decimal("5.00"),
        quantity=Decimal("10"),
        idempotency_key="recv-offline",
    )
    await session.commit()

    event_id = uuid4()
    envelope = {
        "eventId": str(event_id),
        "eventType": "sale.create",
        "clientSequence": 1,
        "payload": {
            "items": [{"storeProductId": str(store_product.id), "quantity": "2"}],
            "payments": [{"method": "cash", "amount": "20.00", "receivedAmount": "20.00"}],
        },
    }

    first = await _ingest(client, tenant, [envelope], device)
    assert first.status_code == 200, first.text
    ack = first.json()["data"]["acks"][0]
    # The feed sequence starts at 1: the receipt number is drawn from its own counter,
    # so a sale no longer consumes a feed sequence on its way past.
    assert ack["errorCode"] is None and ack["serverSequence"] == 1

    assert int(await session.scalar(select(func.count()).select_from(Sale))) == 1
    session.expunge_all()
    balance = await session.scalar(select(InventoryBalance))
    assert Decimal(balance.on_hand) == Decimal("8")

    # Device retries the same upload after a crash: acknowledged, never re-applied.
    replay = await _ingest(client, tenant, [envelope], device)
    replay_ack = replay.json()["data"]["acks"][0]
    assert replay_ack["duplicate"] is True
    assert replay_ack["serverSequence"] == ack["serverSequence"]
    assert int(await session.scalar(select(func.count()).select_from(Sale))) == 1

    # The offline sale is pullable by other devices of the store — once, not
    # duplicated by the outbox echo.
    feed = await client.get(
        "/sync/events?cursor=0", headers=_headers(tenant, device_id=device.id)
    )
    assert feed.status_code == 200
    changes = feed.json()["data"]["changes"]
    assert [c["serverSequence"] for c in changes] == [1]
    assert changes[0]["eventType"] == "sale.create"


async def test_online_sale_publishes_to_feed_via_outbox(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    from decimal import Decimal
    from datetime import date, timedelta

    from sqlalchemy import select

    from app.context import RequestContext
    from app.domains.products import PharmacyProduct
    from app.models import Role, StoreProduct
    from app.services.inventory import receive_batch

    device = await _make_device(session, tenant, key="online-1")
    product = PharmacyProduct(
        organization_id=tenant["organization"].id, name="Online Napa", unit="box", active=True
    )
    session.add(product)
    await session.flush()
    store_product = StoreProduct(
        organization_id=tenant["organization"].id,
        store_id=tenant["store"].id,
        pharmacy_product_id=product.id,
        sku="SKU-ONL",
        sale_price=Decimal("10.00"),
        minimum_stock=Decimal(0),
        active=True,
    )
    session.add(store_product)
    await session.flush()
    await receive_batch(
        session,
        RequestContext(
            organization_id=tenant["organization"].id,
            user_id=tenant["owner"].id,
            role=Role.OWNER,
            store_id=tenant["store"].id,
        ),
        store_product.id,
        batch_number="ONB1",
        expiry_date=date.today() + timedelta(days=100),
        unit_cost=Decimal("5.00"),
        quantity=Decimal("5"),
        idempotency_key="recv-online",
    )
    await session.commit()

    sale = await client.post(
        "/sales",
        json={
            "items": [{"storeProductId": str(store_product.id), "quantity": "1"}],
            "payments": [{"method": "cash", "amount": "10.00", "receivedAmount": "10.00"}],
        },
        headers={**_headers(tenant), "Idempotency-Key": "online-sale-0001"},
    )
    assert sale.status_code == 201, sale.text
    sale_id = sale.json()["data"]["id"]

    feed = await client.get(
        "/sync/events?cursor=0", headers=_headers(tenant, device_id=device.id)
    )
    assert feed.status_code == 200
    changes = feed.json()["data"]["changes"]
    assert any(
        c["eventType"] == "sale.created" and c["payload"].get("sale_id") == sale_id
        for c in changes
    )

    # A void is the correction to that sale and has to travel the same road.
    # ``_publish_pending_outbox`` routes on ``payload["store_id"]`` and skips any event
    # that lacks it -- without marking it published, so the row sat pending forever.
    # The tills kept showing a completed sale that the office had already reversed, and
    # the only symptom was silence: no error, no retry, nothing in the feed.
    voided = await client.post(
        f"/sales/{sale_id}/void", json={"reason": "Rung up twice"}, headers=_headers(tenant)
    )
    assert voided.status_code == 200, voided.text

    after_void = await client.get(
        "/sync/events?cursor=0", headers=_headers(tenant, device_id=device.id)
    )
    void_changes = [
        c for c in after_void.json()["data"]["changes"] if c["eventType"] == "sale.voided"
    ]
    assert len(void_changes) == 1, "the void never reached the device"
    assert void_changes[0]["payload"]["sale_id"] == sale_id

    # Published exactly once: a second pull must not re-emit it under a new sequence.
    again = await client.get(
        "/sync/events?cursor=0", headers=_headers(tenant, device_id=device.id)
    )
    assert [
        c for c in again.json()["data"]["changes"] if c["eventType"] == "sale.voided"
    ] == void_changes


async def test_offline_sale_with_invalid_payload_is_rejected_per_event(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    device = await _make_device(session, tenant, key="offline-2")
    await session.commit()

    bad = {
        "eventId": str(uuid4()),
        "eventType": "sale.create",
        "clientSequence": 1,
        "payload": {"items": []},
    }
    ok = _envelope(2, event_type="ping")
    response = await _ingest(client, tenant, [bad, ok], device)
    acks = response.json()["data"]["acks"]
    assert acks[0]["errorCode"] == "VALIDATION_ERROR"
    assert acks[1]["errorCode"] is None


# --- pull ----------------------------------------------------------------------------


async def test_pull_cursor_pagination_advances_gap_free(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    device = await _make_device(session, tenant)
    await session.commit()
    await _ingest(client, tenant, [_envelope(i) for i in range(1, 4)], device)

    page = await client.get(
        "/sync/events?cursor=0&limit=2", headers=_headers(tenant, device_id=device.id)
    )
    assert page.status_code == 200, page.text
    data = page.json()["data"]
    assert [change["serverSequence"] for change in data["changes"]] == [1, 2]
    assert data["nextCursor"] == 2
    assert data["hasMore"] is True

    second = await client.get(
        f"/sync/events?cursor={data['nextCursor']}", headers=_headers(tenant, device_id=device.id)
    )
    second_data = second.json()["data"]
    assert [change["serverSequence"] for change in second_data["changes"]] == [3]
    assert second_data["hasMore"] is False


async def test_revoked_device_forbidden_on_ingest_and_pull(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    device = await _make_device(session, tenant)
    await session.commit()

    await client.post(f"/sync/devices/{device.id}/revoke", json={}, headers=_headers(tenant))

    ingest = await _ingest(client, tenant, [_envelope(1)], device)
    assert ingest.status_code == 403
    pull = await client.get("/sync/events", headers=_headers(tenant, device_id=device.id))
    assert pull.status_code == 403


async def test_cross_tenant_device_access_denied(
    client: Any,
    session: Any,
    tenant: dict[str, Any],
    make_organization: Callable[..., Any],
    make_user: Callable[..., Any],
    make_store: Callable[..., Any],
    make_membership: Callable[..., Any],
) -> None:
    device = await _make_device(session, tenant)
    await session.commit()

    other_org: Organization = await make_organization(name="Rival", slug="rival")
    rival = await make_user(phone="+8801799999999", display_name="Rival Owner")
    rival_store = await make_store(other_org, code="RIVAL")
    await make_membership(other_org, rival, Role.OWNER, rival_store)
    from app.models import Session as SessionModel
    from app.security import generate_token, hash_token

    rival_session = SessionModel(
        user_id=rival.id,
        organization_id=other_org.id,
        store_id=rival_store.id,
        refresh_token_hash=hash_token(generate_token()),
        expires_at=utc_now() + timedelta(days=30),
    )
    session.add(rival_session)
    await session.commit()

    # The rival mints a token carrying tenant A's device id; the claim must not grant access.
    stolen_headers = _headers(
        {**tenant, "owner": rival, "organization": other_org, "session": rival_session},
        device_id=device.id,
    )
    ingest = await client.post(
        "/sync/events", json={"events": [_envelope(1)]}, headers=stolen_headers
    )
    assert ingest.status_code == 403

    # The stolen device claim is rejected at token resolution for another tenant.
    rival_tenant = {
        **tenant,
        "owner": rival,
        "organization": other_org,
        "session": rival_session,
    }
    revoke = await client.post(
        f"/sync/devices/{device.id}/revoke", json={}, headers=_headers(rival_tenant)
    )
    assert revoke.status_code == 403


# --- partial-batch durability -----------------------------------------------------


async def _sellable(session: Any, tenant: dict[str, Any], *, sku: str, quantity: str = "50") -> Any:
    """A store product with stock, so ``sale.create`` envelopes can actually apply."""
    from datetime import date
    from decimal import Decimal

    from app.context import RequestContext
    from app.domains.products import PharmacyProduct
    from app.models import StoreProduct
    from app.services.inventory import receive_batch

    product = PharmacyProduct(
        organization_id=tenant["organization"].id, name=f"Product {sku}", unit="box", active=True
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
    await receive_batch(
        session,
        RequestContext(
            organization_id=tenant["organization"].id,
            user_id=tenant["owner"].id,
            role=Role.OWNER,
            store_id=tenant["store"].id,
        ),
        store_product.id,
        batch_number=f"B-{sku}",
        expiry_date=date.today() + timedelta(days=100),
        unit_cost=Decimal("5.00"),
        quantity=Decimal(quantity),
        idempotency_key=f"recv-{sku}",
    )
    await session.commit()
    return store_product


def _sale_envelope(sequence: int, store_product_id: Any, *, quantity: str = "2") -> dict[str, Any]:
    total = f"{int(quantity) * 10:.2f}"
    return {
        "eventId": str(uuid4()),
        "eventType": "sale.create",
        "clientSequence": sequence,
        "payload": {
            "items": [{"storeProductId": str(store_product_id), "quantity": quantity}],
            "payments": [{"method": "cash", "amount": total, "receivedAmount": total}],
        },
    }


async def test_a_failing_event_does_not_erase_an_earlier_success(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """The success ahead of a failure must survive it, feed row and all.

    Handlers commit their own work, so a batch is not one transaction however much
    the loop looks like it. Rolling back on the second envelope used to discard the
    first one's *bookkeeping* while its sale stayed committed: the ack handed back a
    server sequence, and nothing at that sequence existed. Other terminals never
    pulled the sale, and a retry was acknowledged as a duplicate with no sequence at
    all, so the device could never learn the truth either. Stock had moved and money
    had been taken for a sale the rest of the shop could not see.

    The failing sibling here is ordered *second* on purpose -- with the failure first
    there is nothing pending to lose, which is why the existing partial-batch test
    passed throughout.
    """
    from sqlalchemy import func, select

    from app.domains.sales import Sale
    from app.domains.sync import SyncEvent

    device = await _make_device(session, tenant, key="partial-1")
    store_product = await _sellable(session, tenant, sku="SKU-PART")

    good = _sale_envelope(1, store_product.id)
    bad = {
        "eventId": str(uuid4()),
        "eventType": "sale.create",
        "clientSequence": 2,
        "payload": {"items": []},
    }
    response = await _ingest(client, tenant, [good, bad], device)
    acks = response.json()["data"]["acks"]
    assert acks[0]["errorCode"] is None
    assert acks[1]["errorCode"] == "VALIDATION_ERROR"

    session.expunge_all()
    assert int(await session.scalar(select(func.count()).select_from(Sale))) == 1

    # The acked sequence has to name a row that exists, or the cursor skips the sale.
    feed = list(await session.scalars(select(SyncFeedItem)))
    assert [item.server_sequence for item in feed] == [acks[0]["serverSequence"]]

    # ...and another terminal actually pulls it.
    pull = await client.get(
        "/sync/events?cursor=0", headers=_headers(tenant, device_id=device.id)
    )
    changes = pull.json()["data"]["changes"]
    assert [c["eventType"] for c in changes] == ["sale.create"]

    applied = {
        str(event.event_id): event.applied
        for event in await session.scalars(select(SyncEvent))
    }
    assert applied[good["eventId"]] is True
    assert applied[bad["eventId"]] is False

    # The retry learns the real sequence rather than a phantom duplicate.
    retry = await _ingest(client, tenant, [good], device)
    retry_ack = retry.json()["data"]["acks"][0]
    assert retry_ack["duplicate"] is True
    assert retry_ack["serverSequence"] == acks[0]["serverSequence"]
    assert int(await session.scalar(select(func.count()).select_from(Sale))) == 1


async def test_failure_reason_is_recorded_against_the_event(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """``error_code`` is the only durable record of what a device is stuck on.

    The ack goes back in a response the device may already have dropped -- that is
    the normal case for a terminal on a failing connection -- so a column that is
    declared and never written leaves nobody able to answer why a queue is not
    draining.
    """
    from sqlalchemy import select

    from app.domains.sync import SyncEvent

    device = await _make_device(session, tenant, key="errcode-1")
    await session.commit()

    unknown = _envelope(1, event_type="not.a.real.event")
    response = await _ingest(client, tenant, [unknown], device)
    assert response.json()["data"]["acks"][0]["errorCode"] == "UNSUPPORTED_EVENT_TYPE"

    session.expunge_all()
    recorded = await session.scalar(
        select(SyncEvent).where(SyncEvent.event_id == UUID(unknown["eventId"]))
    )
    assert recorded is not None, "an event that failed must still leave a trace"
    assert recorded.applied is False
    assert recorded.error_code == "UNSUPPORTED_EVENT_TYPE"


async def test_receipt_numbers_stay_gapless_alongside_the_feed(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """Receipts and feed items advance independently, both without gaps.

    One shared counter made each series skip wherever the other advanced. Gapped
    receipt numbers are the expensive half: a shop cannot account to an auditor for
    the numbers between R-00000001 and R-00000004 when nothing was ever issued.
    """
    from sqlalchemy import select

    from app.domains.sales import Sale
    from app.domains.sync import StoreSequence

    device = await _make_device(session, tenant, key="receipt-1")
    store_product = await _sellable(session, tenant, sku="SKU-RCPT")

    envelopes = [_sale_envelope(seq, store_product.id) for seq in (1, 2, 3)]
    response = await _ingest(client, tenant, envelopes, device)
    acks = response.json()["data"]["acks"]
    assert [ack["serverSequence"] for ack in acks] == [1, 2, 3]

    session.expunge_all()
    receipts = sorted(
        sale.receipt_number for sale in await session.scalars(select(Sale))
    )
    assert receipts == ["R-00000001", "R-00000002", "R-00000003"]

    counter = await session.scalar(select(StoreSequence))
    assert counter.last_sequence == 3
    assert counter.last_receipt_sequence == 3


async def test_server_stamps_received_at_over_a_device_clock(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """A device's clock must not be able to order the server's feed.

    A terminal weeks offline, or one with a dead RTC, will happily claim next year.
    Trusting that let a single device place its events anywhere in the store's
    history. The claim is kept as evidence -- it is genuinely useful for spotting
    clock drift and measuring how long a queue sat -- but it is not authoritative.
    """
    from sqlalchemy import select

    from app.domains.sync import SyncEvent

    device = await _make_device(session, tenant, key="clock-1")
    await session.commit()

    absurd = utc_now() + timedelta(days=400)
    envelope = {**_envelope(1), "createdAt": absurd.isoformat()}
    before = utc_now()
    response = await _ingest(client, tenant, [envelope], device)
    assert response.json()["data"]["acks"][0]["errorCode"] is None

    session.expunge_all()
    event = await session.scalar(
        select(SyncEvent).where(SyncEvent.event_id == UUID(envelope["eventId"]))
    )
    assert event.received_at.replace(tzinfo=None) >= before.replace(tzinfo=None)
    assert event.received_at.replace(tzinfo=None) < absurd.replace(tzinfo=None)
    # The claim survives, so drift stays diagnosable.
    assert event.client_created_at is not None


async def test_a_client_local_field_fails_the_whole_batch(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """``extra="forbid"`` means one stray key costs every sale in the request.

    The offline queue tracks an ``idempotencyKey`` per entry for its own
    bookkeeping, and spreading a stored envelope straight onto the wire sends it.
    The result is not a per-event ack but a 422 for the batch -- so well-formed
    sales queued beside it are refused too, and to the counter every one of them
    looks permanently unsendable for a reason it cannot act on. Pinned here because
    ``toWireEnvelope`` in ``@pharmacy/sync`` exists solely to prevent it.
    """
    device = await _make_device(session, tenant, key="wire-1")
    await session.commit()

    good = _envelope(1)
    polluted = {**_envelope(2), "idempotencyKey": "mobile-abcd1234:2"}
    response = await _ingest(client, tenant, [good, polluted], device)
    assert response.status_code == 422, response.text

    # Nothing was applied, including the conformant envelope beside it.
    from app.domains.sync import SyncEvent
    from sqlalchemy import func, select

    assert int(await session.scalar(select(func.count()).select_from(SyncEvent))) == 0


async def test_envelope_identity_must_match_the_token(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """Rule-5 identity fields are accepted, then checked -- never trusted.

    Before they were declared at all, ``extra="forbid"`` rejected a *conformant*
    envelope outright. Merely accepting them would be worse than that: a terminal
    could file its sales against another branch, moving stock and revenue between
    shops that need not share an owner. So a mismatch is refused, and agreement is
    required rather than assumed.
    """
    from app.domains.sync import SyncEvent
    from sqlalchemy import select

    device = await _make_device(session, tenant, key="identity-1")
    await session.commit()

    conformant = {
        **_envelope(1),
        "deviceId": str(device.id),
        "organizationId": str(tenant["organization"].id),
        "storeId": str(tenant["store"].id),
        "userId": str(tenant["owner"].id),
    }
    ok = await _ingest(client, tenant, [conformant], device)
    assert ok.json()["data"]["acks"][0]["errorCode"] is None, ok.text

    foreign = {**_envelope(2), "storeId": str(uuid4())}
    denied = await _ingest(client, tenant, [foreign], device)
    ack = denied.json()["data"]["acks"][0]
    assert ack["errorCode"] == "IDENTITY_MISMATCH"
    assert ack["serverSequence"] is None

    session.expunge_all()
    rejected = await session.scalar(
        select(SyncEvent).where(SyncEvent.event_id == UUID(foreign["eventId"]))
    )
    assert rejected.applied is False and rejected.error_code == "IDENTITY_MISMATCH"


async def test_inventory_staff_cannot_push_sales_but_cashiers_can(
    client: Any, session: Any, tenant: dict[str, Any], make_user: Any, make_membership: Any
) -> None:
    """The ingest gate follows who may ring up a sale, not who may administer.

    Gating on owner/manager -- the unused constant this replaced -- would have
    stranded every cashier's offline queue at exactly the moment the queue matters.
    Inventory staff are excluded because they cannot create a sale online either.

    Each case needs a real membership row: the role comes from the database, not the
    token's claim, so a token cannot assert its way into a capability. That is worth
    a test leaning on rather than a detail to work around.
    """
    from app.models import Session as SessionModel
    from app.security import generate_token, hash_token

    device = await _make_device(session, tenant, key="roles-1")
    await session.commit()

    async def _headers_for(role: Role, phone: str) -> dict[str, str]:
        user = await make_user(phone=phone, display_name=f"Staff {role.value}")
        await make_membership(tenant["organization"], user, role, tenant["store"])
        auth_session = SessionModel(
            user_id=user.id,
            organization_id=tenant["organization"].id,
            store_id=tenant["store"].id,
            refresh_token_hash=hash_token(generate_token()),
            expires_at=utc_now() + timedelta(days=30),
        )
        session.add(auth_session)
        await session.commit()
        return _headers(
            {**tenant, "owner": user, "session": auth_session}, role=role, device_id=device.id
        )

    allowed = await client.post(
        "/sync/events",
        json={"events": [_envelope(1, event_type="ping")]},
        headers=await _headers_for(Role.CASHIER, "+8801799990001"),
    )
    assert allowed.status_code == 200, "a cashier must be able to drain their own queue"

    denied = await client.post(
        "/sync/events",
        json={"events": [_envelope(2, event_type="ping")]},
        headers=await _headers_for(Role.INVENTORY_STAFF, "+8801799990002"),
    )
    assert denied.status_code == 403


async def test_pull_records_the_server_high_water_mark(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """``last_server_sequence`` is written, so a lagging terminal is identifiable.

    The client still supplies its own cursor -- a device that lost local state must
    be able to re-read from zero -- but without a server-side mark there is no way to
    tell a till that is days behind from one that is merely idle.
    """
    from sqlalchemy import select

    from app.domains.sync import SyncCheckpoint

    device = await _make_device(session, tenant, key="hwm-1")
    await session.commit()
    await _ingest(client, tenant, [_envelope(1), _envelope(2)], device)

    headers = _headers(tenant, device_id=device.id)
    pull = await client.get("/sync/events?cursor=0", headers=headers)
    assert pull.json()["data"]["nextCursor"] == 2

    session.expunge_all()
    checkpoint = await session.scalar(
        select(SyncCheckpoint).where(SyncCheckpoint.device_id == device.id)
    )
    assert checkpoint.last_server_sequence == 2

    # Re-reading from zero must not rewind the mark.
    await client.get("/sync/events?cursor=0", headers=headers)
    session.expunge_all()
    checkpoint = await session.scalar(
        select(SyncCheckpoint).where(SyncCheckpoint.device_id == device.id)
    )
    assert checkpoint.last_server_sequence == 2


async def test_revoke_reason_reaches_the_audit_log(
    client: Any, session: Any, tenant: dict[str, Any]
) -> None:
    """Revocation is usually an incident; the reason is the only place it survives."""
    from sqlalchemy import select

    from app.models import AuditLog

    device = await _make_device(session, tenant, key="revoke-reason-1")
    await session.commit()

    response = await client.post(
        f"/sync/devices/{device.id}/revoke",
        json={"reason": "Terminal stolen from the counter"},
        headers=_headers(tenant),
    )
    assert response.status_code == 200

    session.expunge_all()
    entry = await session.scalar(
        select(AuditLog).where(AuditLog.action == "sync.device.revoked")
    )
    assert entry.after_data["reason"] == "Terminal stolen from the counter"
